"""OS abstraction layer with capability probing and fallback chains.

Every getter tries methods in order of cheapness and *remembers* which
one worked, so a broken/expensive path is never retried on every poll
(critical on a Celeron N4120). `probe_capabilities()` runs once at
startup and the GUI shows what is available.

Windows chains
--------------
idle      : GetLastInputInfo (Win32)
monitor   : EnumDisplayDevicesW | GetSystemMetrics
thermal   : WMI MSAcpi_ThermalZoneTemperature | Win32_TemperatureProbe
battery   : GetSystemPowerStatus (ctypes) | psutil.sensors_battery
network   : psutil.net_io_counters
processes : psutil.process_iter | tasklist
"""

from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
IS_MACOS = platform.system() == "Darwin"


# --------------------------------------------------------------------------- #
# Capability map (probed once, cached forever)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Capabilities:
    idle: bool = False
    idle_method: str = ""
    monitor: bool = False
    monitor_method: str = ""
    thermal: bool = False
    thermal_method: str = ""
    battery: bool = False
    battery_method: str = ""
    network: bool = False
    network_method: str = ""
    processes: bool = False
    processes_method: str = ""
    sse4_2: bool = True  # assume capable; False only when proven absent

    def as_dict(self) -> dict:
        return {
            "idle": (self.idle, self.idle_method),
            "monitor": (self.monitor, self.monitor_method),
            "thermal": (self.thermal, self.thermal_method),
            "battery": (self.battery, self.battery_method),
            "network": (self.network, self.network_method),
            "processes": (self.processes, self.processes_method),
            "sse4_2": self.sse4_2,
        }


_caps: Capabilities | None = None
# Per-getter "method that worked" memo: 0 = auto, 1..n = chain index.
_memo: dict[str, int] = {}


def cpu_flags() -> set[str]:
    """CPU feature flags where detectable (Linux /proc/cpuinfo, macOS
    sysctl). Empty set means "unknown" (Windows has no stable public
    API for raw feature flags)."""
    flags: set[str] = set()
    try:
        if IS_LINUX:
            for line in Path("/proc/cpuinfo").read_text(
                errors="ignore"
            ).splitlines():
                if line.lower().startswith("flags") and ":" in line:
                    flags = {f.lower() for f in line.split(":", 1)[1].split()}
                    break
        elif IS_MACOS:
            out = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.features"],
                capture_output=True, text=True, timeout=2,
            )
            if out.returncode == 0:
                flags = {f.lower() for f in out.stdout.split()}
    except Exception as exc:
        log.debug("cpu flags probe failed: %s", exc)
    return flags


def cpu_sse4_2() -> bool:
    """True when SSE4.2 is present or undetectable (fail open).

    Every Windows 10/11-capable x86 CPU ships SSE4.2, so an unknown
    result never down-tunes the engine.
    """
    flags = cpu_flags()
    if not flags:
        return True
    return any(f in ("sse4_2", "sse42") for f in flags)


def probe_capabilities() -> Capabilities:
    """One-shot capability probe (safe to call multiple times: cached)."""
    global _caps
    if _caps is not None:
        return _caps
    caps = Capabilities()
    caps.sse4_2 = cpu_sse4_2()

    idle = get_idle_seconds()
    caps.idle, caps.idle_method = idle is not None, _memo.get("idle", "?")

    mon = is_monitor_off()
    caps.monitor, caps.monitor_method = mon is not None, _memo.get("monitor", "?")

    temp = get_cpu_temperature()
    caps.thermal, caps.thermal_method = (
        temp is not None, _memo.get("thermal", "?")
    )

    batt = get_battery()
    caps.battery, caps.battery_method = (
        batt is not None, _memo.get("battery", "?")
    )

    net = get_net_counters()
    caps.network, caps.network_method = (
        net is not None, _memo.get("network", "?")
    )

    procs = list_processes()
    caps.processes, caps.processes_method = (
        bool(procs), _memo.get("processes", "?")
    )

    _caps = caps
    log.info("capabilities: %s", caps.as_dict())
    return caps


# --------------------------------------------------------------------------- #
# Idle time
# --------------------------------------------------------------------------- #
def get_idle_seconds() -> float | None:
    """Seconds since last user input, or None if unsupported."""
    if IS_WINDOWS:
        return _win_idle_seconds()
    if IS_LINUX:
        return _linux_idle_seconds()
    return None


def _win_idle_seconds() -> float | None:  # pragma: no cover - Win32 only
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            ticks = ctypes.windll.kernel32.GetTickCount()
            return max(0.0, (ticks - lii.dwTime) / 1000.0)
    except Exception as exc:
        log.debug("idle query failed: %s", exc)
    return None


def _linux_idle_seconds() -> float | None:  # pragma: no cover - X11 only
    """Chain: libXss (fast, ctypes) -> python-xlib -> None."""
    if _memo.get("idle", 0) == 1:
        return _xss_idle_seconds()
    if _memo.get("idle", 0) == 2:
        return _xlib_idle_seconds()
    v = _xss_idle_seconds()
    if v is not None:
        _memo["idle"] = 1
        return v
    v = _xlib_idle_seconds()
    if v is not None:
        _memo["idle"] = 2
    return v


def _xss_idle_seconds() -> float | None:  # pragma: no cover
    try:
        import ctypes

        x11 = ctypes.CDLL("libX11.so.6")
        xss = ctypes.CDLL("libXss.so.1")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        dpy = x11.XOpenDisplay(None)
        if not dpy:
            return None

        class XScreenSaverInfo(ctypes.Structure):
            _fields_ = [
                ("window", ctypes.c_void_p),
                ("state", ctypes.c_int),
                ("kind", ctypes.c_int),
                ("til_or_since", ctypes.c_ulong),
                ("idle", ctypes.c_ulong),
                ("eventMask", ctypes.c_ulong),
            ]

        try:
            xss.XScreenSaverQueryInfo.restype = ctypes.c_int
            xss.XScreenSaverQueryInfo.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
            ]
            info = XScreenSaverInfo()
            root = x11.XDefaultRootWindow(dpy)
            if xss.XScreenSaverQueryInfo(dpy, root, ctypes.byref(info)):
                return info.idle / 1000.0
            return None
        finally:
            x11.XCloseDisplay(dpy)
    except Exception as exc:
        log.debug("libXss idle unavailable: %s", exc)
        return None


def _xlib_idle_seconds() -> float | None:  # pragma: no cover
    try:
        import Xlib.display  # type: ignore[import-not-found]

        d = Xlib.display.Display()
        ext = d.get_extension("MIT-SCREEN-SAVER")
        if ext is None:
            return None
        info = ext.query_info(d.display)
        return (getattr(info, "idle", 0) or 0) / 1000.0
    except Exception as exc:
        log.debug("python-xlib idle unavailable: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Monitor / display state
# --------------------------------------------------------------------------- #
def is_monitor_off() -> bool | None:
    """True = display off/asleep, False = on, None = unknown."""
    if IS_WINDOWS:
        return _win_monitor_off()
    if IS_LINUX:  # pragma: no cover - xset q (DPMS) best-effort
        return _linux_monitor_off()
    return None


def _win_monitor_off() -> bool | None:  # pragma: no cover - Win32 only
    method = _memo.get("monitor", 0)
    if method == 2:
        return _win_monitor_off_metrics()
    try:
        import ctypes

        class DISPLAY_DEVICE(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128),
                ("StateFlags", ctypes.c_ulong),
                ("DeviceID", ctypes.c_wchar * 128),
                ("DeviceKey", ctypes.c_wchar * 128),
            ]

        dev = DISPLAY_DEVICE()
        dev.cb = ctypes.sizeof(DISPLAY_DEVICE)
        user32 = ctypes.windll.user32
        i, any_active = 0, False
        while user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
            if dev.StateFlags & 0x1:  # DISPLAY_DEVICE_ATTACHED_TO_DESKTOP
                any_active = True
                break
            i += 1
        _memo["monitor"] = 1
        return not any_active
    except Exception as exc:
        log.debug("EnumDisplayDevices monitor query failed: %s", exc)
    return _win_monitor_off_metrics()


def _win_monitor_off_metrics() -> bool | None:  # pragma: no cover
    """Fallback: SM_MONITORPOWER sentinel (best-effort, legacy)."""
    try:
        import ctypes

        # 2 = SM_MONITORPOWER; -1 means the metric is not supported.
        v = ctypes.windll.user32.GetSystemMetrics(2)
        if v == -1:
            return None
        _memo["monitor"] = 2
        return v == 0  # 0 = monitor off (per legacy docs)
    except Exception as exc:
        log.debug("GetSystemMetrics monitor query failed: %s", exc)
    return None


def _linux_monitor_off() -> bool | None:  # pragma: no cover
    try:
        out = subprocess.run(
            ["xset", "q"], capture_output=True, text=True, timeout=2
        )
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                if "Monitor is" in line:
                    _memo["monitor"] = 1
                    return "Off" in line
    except Exception as exc:
        log.debug("xset monitor query failed: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# CPU temperature
# --------------------------------------------------------------------------- #
def get_cpu_temperature() -> float | None:
    """Chain (Win): WMI thermal zone -> WMI probe -> None.
    Chain (Linux): hwmon -> /proc/acpi -> None."""
    if IS_WINDOWS:
        if _memo.get("thermal", 0) == 2:
            return _win_temp_probe()
        v = _win_temp_zone()
        if v is not None:
            _memo["thermal"] = 1
            return v
        v = _win_temp_probe()
        if v is not None:
            _memo["thermal"] = 2
        return v
    if IS_LINUX:
        if _memo.get("thermal", 0) == 2:
            return _linux_temp_acpi()
        v = _linux_temp_hwmon()
        if v is not None:
            _memo["thermal"] = 1
            return v
        v = _linux_temp_acpi()
        if v is not None:
            _memo["thermal"] = 2
        return v
    return None


def _win_temp_zone() -> float | None:  # pragma: no cover
    try:
        import wmi  # type: ignore[import-not-found]

        w = wmi.WMI(namespace="root/wmi")
        for zone in w.MSAcpi_ThermalZoneTemperature():
            if zone.CurrentTemperature:
                return float(zone.CurrentTemperature) / 10.0 - 273.15
    except Exception as exc:
        log.debug("MSAcpi_ThermalZoneTemperature unavailable: %s", exc)
    return None


def _win_temp_probe() -> float | None:  # pragma: no cover
    try:
        import wmi  # type: ignore[import-not-found]

        w = wmi.WMI()
        for probe in w.Win32_TemperatureProbe():
            if probe.CurrentReading:
                return float(probe.CurrentReading) / 10.0 - 273.15
    except Exception as exc:
        log.debug("Win32_TemperatureProbe unavailable: %s", exc)
    return None


def _linux_temp_hwmon() -> float | None:  # pragma: no cover
    try:
        from pathlib import Path

        for base in Path("/sys/class/hwmon").glob("hwmon*"):
            for tf in sorted(base.glob("temp*_input")):
                val = int(tf.read_text().strip())
                if val > 0:
                    return val / 1000.0
    except Exception as exc:
        log.debug("hwmon thermal unavailable: %s", exc)
    return None


def _linux_temp_acpi() -> float | None:  # pragma: no cover
    try:
        from pathlib import Path

        for tz in sorted(Path("/proc/acpi/thermal_zone").glob("*/temperature")):
            # Format: "temperature:   45 C"
            parts = tz.read_text().split()
            return float(parts[1])
    except Exception as exc:
        log.debug("acpi thermal unavailable: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Battery
# --------------------------------------------------------------------------- #
def get_battery() -> tuple[int, bool] | None:
    """(percent, plugged_in) or None if no battery / unsupported."""
    if IS_WINDOWS:
        if _memo.get("battery", 0) == 2:
            return _psutil_battery()
        v = _win_battery_api()
        if v is not None:
            _memo["battery"] = 1
            return v
        v = _psutil_battery()
        if v is not None:
            _memo["battery"] = 2
        return v
    if IS_LINUX:
        return _linux_battery()
    return None


def _win_battery_api() -> tuple[int, bool] | None:  # pragma: no cover
    try:
        import ctypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_ubyte),
                ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte),
                ("Reserved0", ctypes.c_ubyte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        sps = SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
            pct = sps.BatteryLifePercent
            if pct == 255:  # no battery / unknown
                return None
            return int(pct), sps.ACLineStatus == 1
    except Exception as exc:
        log.debug("battery API failed: %s", exc)
    return None


def _psutil_battery() -> tuple[int, bool] | None:  # pragma: no cover
    try:
        import psutil

        b = psutil.sensors_battery()
        if b is None:
            return None
        return int(b.percent), bool(b.power_plugged)
    except Exception as exc:
        log.debug("psutil battery failed: %s", exc)
    return None


def _linux_battery() -> tuple[int, bool] | None:  # pragma: no cover
    try:
        from pathlib import Path

        for cap in Path("/sys/class/power_supply").glob("BAT*/capacity"):
            pct = int(cap.read_text().strip())
            status = (cap.parent / "status").read_text().strip().lower()
            return pct, status.startswith("charg") or status == "full"
    except Exception as exc:
        log.debug("sysfs battery failed: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Network throughput
# --------------------------------------------------------------------------- #
def get_net_counters() -> tuple[int, int] | None:
    """(bytes_sent, bytes_recv) totals or None."""
    try:
        import psutil

        c = psutil.net_io_counters()
        return c.bytes_sent, c.bytes_recv
    except Exception as exc:
        log.debug("net counters failed: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Process listing (psutil -> tasklist/ps fallback)
# --------------------------------------------------------------------------- #
def list_processes() -> list[str]:
    """Unique process names, sorted. Empty list if both methods fail."""
    if _memo.get("processes", 0) == 2:
        return _processes_cli()
    try:
        import psutil

        names = {
            p.info["name"] for p in psutil.process_iter(["name"])
            if p.info["name"]
        }
        _memo["processes"] = 1
        return sorted(names)
    except Exception as exc:
        log.debug("psutil process_iter failed: %s", exc)
    return _processes_cli()


def _processes_cli() -> list[str]:  # pragma: no cover
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=10,
            )
            names = {
                line.split('","')[0].strip('"')
                for line in out.stdout.splitlines() if line
            }
        else:
            out = subprocess.run(
                ["ps", "-eo", "comm="], capture_output=True, text=True, timeout=5
            )
            names = {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}
        if names:
            _memo["processes"] = 2
            return sorted(names)
    except Exception as exc:
        log.debug("CLI process listing failed: %s", exc)
    return []


def find_pid_by_name(name: str) -> int | None:
    """Resolve a PID by (case-insensitive, substring) process name."""
    needle = name.lower()
    try:
        import psutil

        for p in psutil.process_iter(["pid", "name"]):
            if needle in (p.info["name"] or "").lower():
                return p.info["pid"]
    except Exception as exc:
        log.debug("find_pid failed: %s", exc)
    return None


# --------------------------------------------------------------------------- #
# Power actions with fallback chains
# --------------------------------------------------------------------------- #
def execute_power_action(action: str) -> bool:
    """Issue the OS command; tries every known mechanism for the action.

    Set SSHUB_DRY_RUN=1 to log instead of touching the OS (tests/dev).
    """
    if os.getenv("SSHUB_DRY_RUN") == "1":
        log.info("DRY-RUN: would execute power action '%s'", action)
        return True
    if action not in {"shutdown", "reboot", "sleep", "hibernate"}:
        log.error("unsupported power action requested: %r", action)
        return False
    if not IS_WINDOWS:
        log.error("power action requested on unsupported operating system")
        return False
    try:  # pragma: no cover - real OS paths
        if IS_WINDOWS:
            return _win_power_action(action)
    except Exception as exc:
        log.error("power action '%s' failed: %s", action, exc)
    return False


def _win_power_action(action: str) -> bool:  # pragma: no cover
    raw_timeout = os.getenv("SSHUB_OS_TIMEOUT", "60")
    if not re.fullmatch(r"\d{1,5}", raw_timeout):
        log.warning("invalid SSHUB_OS_TIMEOUT=%r; using 60 seconds", raw_timeout)
        raw_timeout = "60"
    timeout = str(min(315360000, max(0, int(raw_timeout))))

    def run(command: list[str]) -> bool:
        try:
            completed = subprocess.run(
                command, check=False, capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode != 0:
                log.error(
                    "power command failed rc=%s stderr=%s",
                    completed.returncode,
                    completed.stderr.decode(errors="replace").strip(),
                )
                return False
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("power command could not start: %s", exc)
            return False

    if action == "shutdown":
        return run(["shutdown", "/s", "/t", timeout])
    if action == "reboot":
        return run(["shutdown", "/r", "/t", timeout])
    if action == "sleep":
        # Chain: SetSuspendState (works when hibernate disabled too)
        return run([
            "rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"
        ])
    if action == "hibernate":
        # Chain: shutdown /h -> SetSuspendState 1,1,0 (hiberate variant)
        return run(["shutdown", "/h"]) or run([
            "rundll32.exe", "powrprof.dll,SetSuspendState", "1,1,0"
        ])
    return False


def _linux_power_action(action: str) -> bool:  # pragma: no cover
    # Chain: systemctl -> loginctl -> consolekit
    chains = {
        "shutdown": [["systemctl", "poweroff"], ["loginctl", "poweroff"]],
        "reboot": [["systemctl", "reboot"], ["loginctl", "reboot"]],
        "sleep": [["systemctl", "suspend"], ["loginctl", "suspend"]],
        "hibernate": [["systemctl", "hibernate"], ["loginctl", "hibernate"]],
    }
    import shutil

    for cmd in chains.get(action, chains["shutdown"]):
        if shutil.which(cmd[0]):
            try:
                result = subprocess.run(cmd, check=False, timeout=15)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("power command %s failed to start: %s", cmd[0], exc)
                continue
            if result.returncode == 0:
                return True
            log.warning("power command %s exited with %s", cmd[0], result.returncode)
    return False


def _mac_power_action(action: str) -> bool:  # pragma: no cover
    import shutil

    if not shutil.which("osascript"):
        return False
    scripts = {
        "shutdown": 'tell app "System Events" to shut down',
        "reboot": 'tell app "System Events" to restart',
        "sleep": 'tell app "System Events" to sleep',
        "hibernate": 'tell app "System Events" to sleep',
    }
    try:
        result = subprocess.run(
            ["osascript", "-e", scripts.get(action, scripts["shutdown"])],
            check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("osascript failed to start: %s", exc)
        return False
    return result.returncode == 0


def abort_os_shutdown() -> None:
    """Cancel a pending Windows shutdown issued with a timeout."""
    if IS_WINDOWS and os.getenv("SSHUB_DRY_RUN") != "1":  # pragma: no cover
        try:
            result = subprocess.run(
                ["shutdown", "/a"], check=False, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                log.warning("shutdown abort exited with %s", result.returncode)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("shutdown abort failed: %s", exc)
