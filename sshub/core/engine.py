"""Monitoring engine v2: owns sensors, watchdogs them, decides when to fire.

v2 additions over v1:
- **Watchdog**: a sensor that raises more than `SENSOR_MAX_ERRORS` times
  is replaced live (new instance, same settings) without disarming.
- **Smart mode**: SmartSensor aggregates all signals into a weighted
  score; see sshub/core/smart.py.
- **Multi-profile arming**: several enabled profiles can run at once.
- **Persistence hygiene**: export/import profiles as JSON.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from .. import config
from ..events import EventBus, EventType
from ..i18n import t
from ..sensors import (
    AbsoluteTimerSensor,
    IdleSensor,
    MonitorSensor,
    NetworkSensor,
    ProcessSensor,
    Sensor,
    ThermalBatterySensor,
)
from .executor import ActionExecutor
from .smart import SmartSensor
from .storage import Profile, ProfileStore

log = logging.getLogger(__name__)

SENSOR_MAX_ERRORS = 3


@dataclass(slots=True)
class EngineState:
    armed: bool = False
    profile: Profile | None = None
    profiles: list[Profile] = field(default_factory=list)


@dataclass(slots=True)
class _SensorSlot:
    sensor: Sensor
    errors: int = 0
    swaps: int = 0  # watchdog replacements; a 2nd failure = lost cause


class MonitoringEngine:
    """Central orchestrator: starts/stops sensors, decides when to fire."""

    def __init__(self, bus: EventBus, store: ProfileStore) -> None:
        self.bus = bus
        self.store = store
        self.executor = ActionExecutor(bus)
        self.state = EngineState()
        self._slots: list[_SensorSlot] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def arm(self, profiles: list[Profile]) -> None:
        """(Re)arm monitoring with one or more enabled profiles."""
        profiles = [p for p in profiles if p.enabled]
        self.disarm(silent=True)
        self.clear_cooldown()
        if not profiles:
            self.bus.publish(EventType.LOG, msg=t("no_active_profiles"))
            return
        self.state.profile = profiles[0]
        self.state.profiles = profiles
        self.state.armed = True
        self._slots = [_SensorSlot(s) for s in self._build_sensors(profiles)]
        for slot in self._slots:  # one-shot latches start clean each arm
            slot.sensor.reset()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="sshub-engine", daemon=True
        )
        self._thread.start()
        names = ", ".join(p.name for p in profiles)
        self.bus.publish(EventType.LOG, msg=t("armed_profiles", names=names))
        log.info("armed profiles=%s", names)

    def disarm(self, silent: bool = False) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._slots.clear()
        self.state.armed = False
        self.state.profiles = []
        self.state.profile = None  # snapshot must not lie after disarm
        if not silent:
            self.bus.publish(EventType.LOG, msg=t("disarm_log"))
            log.info("disarmed")

    def clear_cooldown(self) -> None:
        self.executor._cooldown_until = 0.0

    # ------------------------------------------------------------------ #
    # Sensor factory (v2: smart mode + hybrid)
    # ------------------------------------------------------------------ #
    def _build_sensors(self, profiles: list[Profile]) -> list[Sensor]:
        sensors: list[Sensor] = []
        smart = False
        for p in profiles:
            mode = p.mode
            if mode == "smart":
                smart = True
                continue  # one SmartSensor covers all smart profiles
            hybrid = mode == "hybrid"

            def want(m: str, _mode: str = mode, _hybrid: bool = hybrid) -> bool:
                return _hybrid or _mode == m

            if want("absolute"):
                sensors.append(AbsoluteTimerSensor(self.bus, p, self._on_trigger))
            if want("idle"):
                sensors.append(IdleSensor(self.bus, p, self._on_trigger))
            if want("monitor"):
                sensors.append(MonitorSensor(self.bus, p, self._on_trigger))
            if want("process"):
                sensors.append(ProcessSensor(self.bus, p, self._on_trigger))
            if want("network"):
                sensors.append(NetworkSensor(self.bus, p, self._on_trigger))
        # Smart profiles may not be first; always use an actual smart one.
        smart_profile = next(
            (p for p in profiles if p.mode == "smart"), profiles[0]
        )
        if smart:
            sensors.append(SmartSensor(self.bus, smart_profile, self._on_trigger))
        # Thermal/battery safety net always runs when armed.
        sensors.append(
            ThermalBatterySensor(self.bus, smart_profile, self._on_trigger)
        )
        return sensors

    # ------------------------------------------------------------------ #
    # Engine loop + watchdog
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        # Differential heartbeat: profiles with an absolute timer keep a
        # 1 s cadence for accurate countdowns; anything else idles at a
        # slower tick so an armed-but-passive session costs near zero.
        fast_tick = config.ENGINE_TICK_S
        idle_tick = getattr(config, "ENGINE_IDLE_TICK_S", fast_tick)

        def has_timer() -> bool:
            return any(
                isinstance(s.sensor, AbsoluteTimerSensor)
                for s in self._slots
            )

        tick = fast_tick if has_timer() else idle_tick
        while not self._stop.wait(tick):
            if self.executor.pending:
                continue  # overlay owns the countdown now
            tick = fast_tick if has_timer() else idle_tick
            for slot in list(self._slots):
                if self._stop.is_set():
                    break
                try:
                    slot.sensor.poll_if_due(time.monotonic())
                    slot.errors = 0  # healthy again
                except Exception as exc:
                    slot.errors += 1
                    log.warning(
                        "sensor %s error %d/%d: %s",
                        slot.sensor.name, slot.errors, SENSOR_MAX_ERRORS, exc,
                    )
                    self.bus.publish(
                        EventType.ERROR,
                        sensor=slot.sensor.name,
                        error=str(exc),
                    )
                    if slot.errors >= SENSOR_MAX_ERRORS:
                        self._replace_sensor(slot)
        # loop end: clear armed state for consistency
        self.state.armed = False
        self.state.profiles = []

    def _replace_sensor(self, slot: _SensorSlot) -> None:
        """Watchdog: live-swap a failing sensor without disarming.

        A sensor type that keeps failing after one replacement is
        dropped for the rest of the arm: an infinite swap-loop would
        burn CPU and spam the log over a lost cause.
        """
        if slot.swaps >= 1:  # already replaced once and still failing
            if slot in self._slots:
                self._slots.remove(slot)
            return
        old = slot.sensor
        try:
            new = type(old)(self.bus, old.profile, self._on_trigger)
            new.reset()
            slot.sensor = new
            slot.errors = 0
            slot.swaps += 1
            self.bus.publish(
                EventType.LOG,
                msg=t("watchdog_restarted", sensor=old.name),
            )
            log.warning("watchdog replaced sensor %s", old.name)
        except Exception as exc:
            log.error("watchdog could not replace %s: %s", old.name, exc)
            self._slots.remove(slot)

    def _on_trigger(self, source: str, action: str, urgent: bool = False) -> None:
        if self.executor.pending:
            return
        log.warning("trigger fired: %s -> %s%s",
                    source, action, " (urgent)" if urgent else "")
        self.executor.request(
            action, source,
            self.state.profile.name if self.state.profile else "?",
            urgent=urgent,
        )

    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        return {
            "armed": self.state.armed,
            "profile": self.state.profile.name if self.state.profile else None,
            "pending": self.executor.pending,
            "sensors": [s.sensor.name for s in self._slots],
            # Remaining cancel-cooldown seconds: 0 when triggers are live.
            "cooldown_left_s": max(
                0.0, self.executor._cooldown_until - time.monotonic()
            ),
        }
