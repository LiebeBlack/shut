"""Smart heuristic engine: one weighted risk score from all signals.

Instead of a single hard trigger, each poll feeds the latest readings
into a weighted sum. When the score crosses the threshold and stays
there for `hold_s`, the smart sensor fires. Weights are user-tunable
and every published score carries a `why` breakdown for the GUI.

Signals (all optional: missing ones contribute 0 and are skipped):
    idle_long     — mouse/keyboard inactivity
    monitor_off   — display asleep
    net_quiet     — download rate below threshold
    proc_done     — watched process exited / CPU 0%
    hot           — CPU temperature near the max
    low_batt      — battery at/below minimum (overrides: always fires)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .. import config
from ..events import EventBus, EventType
from ..platform_layer import (
    get_battery,
    get_cpu_temperature,
    get_idle_seconds,
    get_net_counters,
    is_monitor_off,
)

# Default weights (0..1). Low battery is a hard override, not a weight.
DEFAULT_WEIGHTS: dict[str, float] = {
    "idle_long": 0.30,
    "monitor_off": 0.20,
    "net_quiet": 0.25,
    "proc_done": 0.25,
    "hot": 0.15,
}


@dataclass(slots=True)
class SmartSignals:
    """Latest readings from every sensor (updated by SmartSensor)."""

    idle_s: float | None = None
    idle_threshold_s: float | None = None
    monitor_off: bool | None = None
    net_kbps: float | None = None
    net_threshold: float | None = None
    proc_status: str = ""          # "" | running | exited | idle_cpu | not_found
    temp_c: float | None = None
    temp_max: float | None = None
    battery_pct: int | None = None
    battery_min: int | None = None
    plugged: bool | None = None

    def score(self, weights: dict[str, float]) -> tuple[int, list[str]]:
        """Return (0-100 risk score, list of human reasons)."""
        total, why = 0.0, []
        if self.idle_s is not None and self.idle_threshold_s:
            frac = min(1.0, self.idle_s / max(1.0, self.idle_threshold_s))
            if frac >= 0.5:
                total += weights.get("idle_long", 0.0) * frac
                why.append(f"idle {int(self.idle_s)}s")
        if self.monitor_off:
            total += weights.get("monitor_off", 0.0)
            why.append("monitor off")
        if (
            self.net_kbps is not None and self.net_threshold is not None
            and self.net_kbps < self.net_threshold
        ):
            total += weights.get("net_quiet", 0.0)
            why.append(f"net {int(self.net_kbps)} KB/s")
        if self.proc_status in ("exited", "idle_cpu"):
            total += weights.get("proc_done", 0.0)
            why.append(f"proc {self.proc_status}")
        if (
            self.temp_c is not None and self.temp_max is not None
            and self.temp_c >= self.temp_max - 10
        ):
            frac = min(1.0, max(0.0,
                     (self.temp_c - (self.temp_max - 10)) / 10.0))
            total += weights.get("hot", 0.0) * frac
            why.append(f"temp {int(self.temp_c)}°C")
        return min(100, int(total * 100)), why


class SmartSensor:
    """Polls every cheap source and publishes the aggregated score.

    Runs inside the engine loop (no thread of its own: each source is
    already cadence-limited by its own cheapness).
    """

    name = "smart"
    tick = config.SMART_TICK_S  # static default; poll_if_due re-reads it

    def __init__(self, bus: EventBus, profile, fire) -> None:
        self.bus = bus
        self.profile = profile
        self._fire = fire
        self._last_poll = 0.0
        self._weights = dict(DEFAULT_WEIGHTS)
        self._score = 0
        self._why: list[str] = []
        self._over_since: float | None = None
        self._net_last: tuple[int, int] | None = None
        self._net_t: float | None = None
        self._ewma: float | None = None
        self._proc: Any | None = None       # persistent psutil handle
        self._proc_pid: int | None = None
        self._proc_warm = False             # first sample is a warm-up

    # -- plumbing ------------------------------------------------------- #
    def poll_if_due(self, now: float) -> None:
        """Cadence gate (re-reads config so SSE4.2 tuning applies at
        runtime); exceptions propagate to the engine watchdog."""
        if now - self._last_poll >= config.SMART_TICK_S:
            self._last_poll = now
            self.poll()

    # -- main poll ------------------------------------------------------- #
    def poll(self) -> None:
        # System load tiles (cheap psutil singletons, ~0.1ms).
        try:
            import psutil

            self.bus.publish(
                EventType.STATE, source="smart",
                cpu_pct=psutil.cpu_percent(interval=None),
                ram_pct=psutil.virtual_memory().percent,
            )
        except Exception:
            pass

        s = SmartSignals()
        s.idle_threshold_s = self.profile.idle_minutes * 60
        s.idle_s = get_idle_seconds()
        s.monitor_off = is_monitor_off()
        s.temp_max = self.profile.thermal_max_c
        s.temp_c = get_cpu_temperature()
        s.net_threshold = self.profile.network_max_kbps
        # Per-profile battery floor (falls back to the global default).
        s.battery_min = (
            getattr(self.profile, "battery_min", None)
            or config.BATTERY_LOW_PCT
        )

        self._update_net(s)
        self._update_proc(s)
        batt = get_battery()
        if batt is not None:
            s.battery_pct, s.plugged = batt

        score, why = s.score(self._weights)
        self._score, self._why = score, why

        # Publish telemetry (GUI dashboard).
        self.bus.publish(
            EventType.STATE, source="smart", score=score, why=why,
            idle_s=s.idle_s, monitor_off=s.monitor_off,
            net_kbps=s.net_kbps, temp_c=s.temp_c,
            battery_pct=s.battery_pct, plugged=s.plugged,
            proc_status=s.proc_status,
        )

        # Emergency battery override: independent of the score.
        if (
            s.battery_pct is not None and s.battery_pct <= s.battery_min
            and s.plugged is False
        ):
            self._fire(self.name, "hibernate")
            return

        hold = config.SMART_HOLD_S
        if score >= config.SMART_THRESHOLD:
            self._over_since = self._over_since or time.monotonic()
            if time.monotonic() - self._over_since >= hold:
                self._fire(self.name, self.profile.action)
        else:
            self._over_since = None  # hysteresis reset

    # -- helpers --------------------------------------------------------- #
    def _update_net(self, s: SmartSignals) -> None:
        counters = get_net_counters()
        now = time.monotonic()
        if counters and self._net_last:
            _sent, recv = counters
            _ps, pr = self._net_last
            dt = max(0.001, now - (self._net_t or now))
            kbps = max(0.0, (recv - pr) / dt / 1024.0)
            a = config.NET_SMOOTH_ALPHA
            self._ewma = (
                kbps if self._ewma is None else a * kbps + (1 - a) * self._ewma
            )
        if counters:
            self._net_last, self._net_t = counters, now
        s.net_kbps = self._ewma

    def _update_proc(self, s: SmartSignals) -> None:
        name = (self.profile.process_name or "").strip()
        if not name:
            s.proc_status = ""
            return
        try:
            import psutil

            for p in psutil.process_iter(["name", "pid"]):
                if name.lower() in (p.info["name"] or "").lower():
                    pid = p.info["pid"]
                    if self._proc is None or self._proc_pid != pid:
                        # Keep ONE handle: cpu_percent needs history.
                        self._proc = psutil.Process(pid)
                        self._proc_pid = pid
                        self._proc_warm = False
                    cpu = self._proc.cpu_percent(interval=None)
                    if not self._proc_warm:
                        self._proc_warm = True
                        s.proc_status = "running"  # first read is 0.0 by API
                    else:
                        s.proc_status = "running" if cpu > 0.1 else "idle_cpu"
                    return
            self._proc, self._proc_pid = None, None
            s.proc_status = "exited"
        except Exception:
            s.proc_status = ""
