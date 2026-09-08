"""Sensor framework and implementations (Module 1).

Every sensor:
- runs in its own daemon thread OR is driven by the engine loop,
- publishes state/events through the EventBus only,
- sleeps between samples to keep CPU near 0%.

Sensors receive a `fire` callback; they never touch the executor or the
GUI directly.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from .. import config
from ..events import EventBus, EventType
from ..i18n import t
from ..platform_layer import (
    get_battery,
    get_cpu_temperature,
    get_idle_seconds,
    get_net_counters,
    is_monitor_off,
)

log = logging.getLogger(__name__)

FireCallback = Callable[[str, str], None]  # (source, action)


class Sensor(ABC):
    """Base class: cadence-managed, thread-safe, never blocks the GUI."""

    name = "sensor"
    tick: float = config.SENSOR_SLOW_TICK_S

    def __init__(self, bus: EventBus, profile, fire: FireCallback) -> None:
        self.bus = bus
        self.profile = profile
        self._fire = fire
        self._last_poll = 0.0

    def poll_if_due(self, now: float) -> None:
        """Cadence gate; exceptions propagate to the engine watchdog."""
        if now - self._last_poll >= self.tick:
            self._last_poll = now
            self.poll()

    @abstractmethod
    def poll(self) -> None: ...


# --------------------------------------------------------------------------- #
# 1. Absolute timer: countdown or wall-clock scheduled time
# --------------------------------------------------------------------------- #
class AbsoluteTimerSensor(Sensor):
    name = "absolute"
    tick = 1.0

    def __init__(self, bus, profile, fire) -> None:
        super().__init__(bus, profile, fire)
        p = profile
        self._deadline: float | None = None
        if p.countdown_minutes and p.countdown_minutes > 0:
            self._deadline = time.monotonic() + p.countdown_minutes * 60
        elif p.at_time:
            try:
                self._deadline = self._next_wallclock(p.at_time)
            except ValueError:
                self.bus.publish(
                    EventType.LOG, msg=t("bad_time", t=p.at_time)
                )
                self._deadline = None  # never crash; just stay inert

    @staticmethod
    def _next_wallclock(hhmm: str) -> float:
        """Epoch seconds of the next occurrence of HH:MM.

        Raises ValueError for anything that is not a valid 24h HH:MM.
        """
        import datetime as dt

        parts = hhmm.split(":")
        if len(parts) != 2:
            raise ValueError(f"bad schedule time: {hhmm!r}")
        h, m = (int(x) for x in parts)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"bad schedule time: {hhmm!r}")
        now = dt.datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += dt.timedelta(days=1)
        return target.timestamp()

    def poll(self) -> None:
        if self._deadline is None:
            return
        remaining = max(0.0, self._deadline - time.monotonic())
        self.bus.publish(EventType.STATE, source=self.name, remaining_s=remaining)
        if remaining <= 0:
            # Fire exactly once per arm: a deadline in the past must not
            # re-execute the action every engine tick forever.
            self._deadline = None
            self._fire(self.name, self.profile.action)


# --------------------------------------------------------------------------- #
# 2. Peripheral idle
# --------------------------------------------------------------------------- #
class IdleSensor(Sensor):
    name = "idle"
    tick = config.IDLE_POLL_S

    def __init__(self, bus, profile, fire) -> None:
        super().__init__(bus, profile, fire)
        self._threshold_s = profile.idle_minutes * 60
        self._last_value: float | None = None

    def poll(self) -> None:
        idle = get_idle_seconds()
        if idle is None:
            self._last_value = None
            return
        self._last_value = idle
        self.bus.publish(
            EventType.STATE, source=self.name, idle_s=idle,
            threshold_s=self._threshold_s,
        )
        if idle >= self._threshold_s:
            self._fire(self.name, self.profile.action)


# --------------------------------------------------------------------------- #
# 3. Monitor power state
# --------------------------------------------------------------------------- #
class MonitorSensor(Sensor):
    name = "monitor"
    tick = config.MONITOR_POLL_S

    def __init__(self, bus, profile, fire) -> None:
        super().__init__(bus, profile, fire)
        self._off_since: float | None = None

    def poll(self) -> None:
        off = is_monitor_off()
        if off is None:
            return
        if off:
            self._off_since = self._off_since or time.monotonic()
            held = time.monotonic() - self._off_since
            self.bus.publish(EventType.STATE, source=self.name, off_s=held)
            if held >= config.MONITOR_OFF_DEBOUNCE_S:
                self._fire(self.name, self.profile.action)
        else:
            self._off_since = None
            self.bus.publish(EventType.STATE, source=self.name, off_s=0)


# --------------------------------------------------------------------------- #
# 4. Process watcher (psutil)
# --------------------------------------------------------------------------- #
class ProcessSensor(Sensor):
    """Fires when the watched process exits OR its CPU stays at ~0%."""

    name = "process"
    tick = config.SENSOR_FAST_TICK_S

    def __init__(self, bus, profile, fire) -> None:
        super().__init__(bus, profile, fire)
        self._proc = None
        self._pid: int | None = None
        self._zero_since: float | None = None
        self._resolve_retry_at: float | None = None  # PID lookup backoff

    def _resolve(self) -> None:
        name = (self.profile.process_name or "").strip()
        if not name:
            return
        try:
            import psutil
        except ImportError:
            return
        for p in psutil.process_iter(["pid", "name"]):
            pname = (p.info["name"] or "").lower()
            if name.lower() in pname:
                self._pid = p.info["pid"]
                self._proc = psutil.Process(self._pid)
                self._resolve_retry_at = None
                self.bus.publish(
                    EventType.LOG,
                    msg=t("watching_pid", pid=self._pid, name=pname),
                )
                return
        self._pid = None
        self._proc = None

    def poll(self) -> None:
        if self._proc is None:
            # Backoff: only retry the (expensive) process scan every 15 s.
            now = time.monotonic()
            if (
                self._resolve_retry_at is not None
                and now < self._resolve_retry_at
            ):
                return
            self._resolve()
            if self._proc is None:
                self._resolve_retry_at = now + 15.0
                self.bus.publish(
                    EventType.STATE, source=self.name, status="not_found"
                )
                return
        try:
            cpu = self._proc.cpu_percent(interval=None)  # non-blocking
        except Exception:
            cpu = None  # process vanished between resolve and read

        if cpu is None or not self._proc.is_running():
            self.bus.publish(EventType.STATE, source=self.name, status="exited")
            self._fire(self.name, self.profile.action)
            return

        if cpu <= 0.1:
            self._zero_since = self._zero_since or time.monotonic()
            held = time.monotonic() - self._zero_since
            if held >= config.CPU_ZERO_DEBOUNCE_S:
                self.bus.publish(
                    EventType.STATE,
                    source=self.name,
                    status="idle_cpu",
                    cpu=cpu,
                )
                self._fire(self.name, self.profile.action)
        else:
            self._zero_since = None
        self.bus.publish(EventType.STATE, source=self.name, status="running", cpu=cpu)


# --------------------------------------------------------------------------- #
# 5. Network throughput
# --------------------------------------------------------------------------- #
class NetworkSensor(Sensor):
    """EWMA-smoothed download rate; fires when it stays under threshold."""

    name = "network"
    tick = config.SENSOR_SLOW_TICK_S

    def __init__(self, bus, profile, fire) -> None:
        super().__init__(bus, profile, fire)
        self._last_counters: tuple[int, int] | None = None
        self._last_time: float | None = None
        self._ewma_kbps: float | None = None
        self._below_since: float | None = None

    def poll(self) -> None:
        counters = get_net_counters()
        now = time.monotonic()
        if counters is None or self._last_counters is None:
            self._last_counters, self._last_time = counters, now
            return
        _sent, recv = counters
        _ps, pr = self._last_counters
        dt = max(0.001, now - (self._last_time or now))
        self._last_counters, self._last_time = counters, now

        down_kbps = max(0.0, (recv - pr) / dt / 1024.0)
        a = config.NET_SMOOTH_ALPHA
        self._ewma_kbps = (
            down_kbps if self._ewma_kbps is None
            else a * down_kbps + (1 - a) * self._ewma_kbps
        )
        self.bus.publish(
            EventType.STATE,
            source=self.name,
            down_kbps=round(self._ewma_kbps, 1),
        )

        threshold = self.profile.network_max_kbps
        if self._ewma_kbps < threshold:
            self._below_since = self._below_since or now
            if now - self._below_since >= config.NET_DEBOUNCE_S:
                self._fire(self.name, self.profile.action)
        else:
            self._below_since = None


# --------------------------------------------------------------------------- #
# 6. Thermal + battery safety net
# --------------------------------------------------------------------------- #
class ThermalBatterySensor(Sensor):
    name = "thermal_battery"
    tick = config.SENSOR_SLOW_TICK_S

    def __init__(self, bus, profile, fire) -> None:
        super().__init__(bus, profile, fire)
        self._hot_since: float | None = None
        self._low_since: float | None = None

    def poll(self) -> None:
        temp = get_cpu_temperature()
        if temp is not None:
            self.bus.publish(EventType.STATE, source=self.name, temp_c=round(temp, 1))
            if temp >= self.profile.thermal_max_c:
                self._hot_since = self._hot_since or time.monotonic()
                if time.monotonic() - self._hot_since >= config.THERMAL_DEBOUNCE_S:
                    self._fire(self.name, "shutdown")  # thermal => hard stop
                    return
            else:
                self._hot_since = None

        batt = get_battery()
        if batt is not None:
            pct, plugged = batt
            self.bus.publish(
                EventType.STATE,
                source=self.name,
                battery_pct=pct,
                plugged=plugged,
            )
            # Per-profile threshold (falls back to the global default).
            limit = getattr(self.profile, "battery_min", None)
            if limit is None:
                limit = config.BATTERY_LOW_PCT
            if not plugged and pct <= limit:
                self._low_since = self._low_since or time.monotonic()
                held = time.monotonic() - self._low_since
                if held >= config.BATTERY_DEBOUNCE_S:
                    self._fire(self.name, "hibernate")  # emergency => keep state
            else:
                self._low_since = None
