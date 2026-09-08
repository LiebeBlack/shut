"""Central configuration: constants, defaults and runtime tuning.

Everything performance-related lives here so the polling cadence can be
tuned for low-power CPUs (e.g. Intel Celeron N4120) without touching
sensor logic.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
APP_NAME = "Smart Shutdown Hub"
APP_VERSION = "1.0.0"

# Portable-friendly data dir: %LOCALAPPDATA%/SmartShutdownHub, or ./data
def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "SmartShutdownHub"
    return Path.home() / ".local" / "share" / "SmartShutdownHub"


DATA_DIR = default_data_dir()
DB_PATH = DATA_DIR / "profiles.db"
LOG_PATH = DATA_DIR / "sshub.log"

# --------------------------------------------------------------------------- #
# Performance tuning (RAM / CPU budget for entry-level CPUs)
# --------------------------------------------------------------------------- #
ENGINE_TICK_S = 1.0            # engine heartbeat while armed (seconds)
ENGINE_IDLE_TICK_S = 4.0       # heartbeat while disarmed (seconds)
SENSOR_FAST_TICK_S = 1.0       # active sensors (countdown, PID watcher)
SENSOR_SLOW_TICK_S = 10.0      # cheap sensors (thermal/battery/network)
GUI_POLL_MS = 250              # GUI drains the event bus 4x per second
GUI_BATCH_MAX = 40             # max events consumed per drain cycle

# --------------------------------------------------------------------------- #
# Heuristic defaults
# --------------------------------------------------------------------------- #
DEFAULT_SHUTDOWN_CMD = "shutdown"
DEFAULT_TIMEOUT_S = 60         # OS shutdown grace timeout
OVERLAY_SECONDS = 30           # emergency-cancel window before executing
TRIGGER_COOLDOWN_S = 60        # after a user cancel: no re-trigger window

# Smart mode (weighted heuristic score)
SMART_THRESHOLD = 70           # 0-100; fires when score >= threshold
SMART_HOLD_S = 30              # score must stay >= threshold this long
SMART_TICK_S = 2.0             # smart telemetry cadence (relaxed w/o SSE4.2)

# Intelligence Hub (advisory AI layer; disabled unless settings say so)
HUB_POLL_S = 5.0               # advisor cadence
HUB_TIMEOUT_S = 3.0            # remote hub HTTP timeout (fail-open)
HUB_MAX_BODY = 64 * 1024       # max response body we accept


NET_DEBOUNCE_S = 120           # network below threshold for N minutes
NET_SMOOTH_ALPHA = 0.25        # EWMA smoothing factor for traffic
CPU_ZERO_DEBOUNCE_S = 30       # watched PID at 0% CPU for N seconds
THERMAL_DEBOUNCE_S = 20        # CPU above max temp for N seconds
BATTERY_LOW_PCT = 10           # emergency hibernate threshold
BATTERY_DEBOUNCE_S = 15

IDLE_POLL_S = 5.0              # GetLastInputInfo / XScreenSaver poll
MONITOR_POLL_S = 4.0
MONITOR_OFF_DEBOUNCE_S = 10    # display off for N seconds before firing

# --------------------------------------------------------------------------- #
# Database defaults
# --------------------------------------------------------------------------- #
DEFAULT_PROFILE: dict = {
    "name": "Default",
    "mode": "absolute",           # absolute|idle|monitor|process|network|hybrid
    "countdown_minutes": 0,
    "at_time": "",                # "HH:MM" 24h
    "idle_minutes": 30,
    "process_name": "",
    "network_max_kbps": 50.0,
    "thermal_max_c": 90.0,
    "battery_min": 10,
    "action": "shutdown",         # shutdown|reboot|sleep|hibernate
    "enabled": 1,
}

ACTION_LABELS = {
    "shutdown": "Apagar equipo",
    "reboot": "Reiniciar",
    "sleep": "Suspender",
    "hibernate": "Hibernar",
}

MODE_LABELS = {
    "absolute": "Temporizador / Hora programada",
    "idle": "Inactividad de periféricos",
    "monitor": "Monitor apagado / suspendido",
    "process": "Proceso terminado (PID watcher)",
    "network": "Red inactiva (KB/s)",
    "hybrid": "Híbrido (todas las condiciones activas)",
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
