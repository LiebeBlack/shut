"""Intelligence Hub: advisory AI layer that talks to the GUI via events.

The hub is **advisory only** — it never executes anything. It watches
the telemetry flowing through the event bus (fed by the GUI drain) and:

1. Runs a deterministic **local advisor** (always available, pure math)
   that maps the latest signals onto a recommended action, confidence
   and a human reason.
2. If ``SSHUB_HUB_URL`` is configured, POSTs a compact snapshot to the
   remote hub (Bearer key from ``SSHUB_HUB_KEY``) and prefers its
   verdict; any failure falls back to the local advisor (**fail-open**,
   matching the project's fallback-chain design).

Verdicts are published as ``EventType.ADVICE`` on the bus (deduplicated
by signature) so the GUI renders them without polling. Remote calls run
in a daemon worker with a hard timeout; the GUI thread only ever calls
the cheap ``observe()``/``set_profile()`` methods.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

from .. import config
from ..events import EventBus, EventType
from ..i18n import t

log = logging.getLogger(__name__)

# "stay" is the only purely advisory action; the rest map to power
# commands a user may apply with one click.
ADVISED_ACTIONS = ("shutdown", "hibernate", "sleep", "reboot", "stay")


class Advice:
    """A single hub verdict (action + confidence + reason + origin)."""

    __slots__ = ("action", "confidence", "origin", "reason")

    def __init__(self, action: str, confidence: int, reason: str,
                 origin: str = "local") -> None:
        self.action = action
        self.confidence = max(0, min(100, int(confidence)))
        self.reason = reason
        self.origin = origin

    def signature(self) -> str:
        return f"{self.action}|{self.confidence}|{self.reason}|{self.origin}"


class IntelligenceHub:
    """Watches telemetry and publishes ADVICE events (never blocks GUI)."""

    def __init__(self, bus: EventBus, settings) -> None:
        self._bus = bus
        self._settings = settings
        self._lock = threading.Lock()
        self._signals: dict = {}
        self._last_signature: str | None = None
        self._remote_ok = True  # first remote failure logs a fallback note
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._remote_url = os.getenv("SSHUB_HUB_URL", "").strip()
        self._remote_key = os.getenv("SSHUB_HUB_KEY", "").strip()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return bool(self._settings.get("hub_enabled"))

    @property
    def remote(self) -> bool:
        return bool(self._remote_url)

    def start(self) -> None:
        """Daemon advisor loop; sleeps, so it costs ~0% CPU while idle."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._loop, name="sshub-hub", daemon=True
        )
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.0)

    # ------------------------------------------------------------------ #
    # GUI-thread feed (cheap, lock-protected, never blocking)
    # ------------------------------------------------------------------ #
    def observe(self, payload: dict) -> None:
        """Fold the latest telemetry payload into the signal map."""
        if not self.enabled:
            return
        with self._lock:
            self._signals.update(payload)

    def set_profile(self, profile) -> None:
        """Provide per-profile thresholds so advice matches the arm."""
        with self._lock:
            self._signals.update(
                {
                    "idle_threshold_s": profile.idle_minutes * 60,
                    "net_threshold": profile.network_max_kbps,
                    "temp_max": profile.thermal_max_c,
                    "battery_min": profile.battery_min,
                }
            )

    # ------------------------------------------------------------------ #
    # Local advisor (deterministic, always available)
    # ------------------------------------------------------------------ #
    def _local_advice(self, s: dict) -> Advice:
        batt = s.get("battery_pct")
        plugged = s.get("plugged")
        batt_min = s.get("battery_min", config.BATTERY_LOW_PCT)
        if batt is not None and plugged is False and batt <= batt_min:
            return Advice("hibernate", 95, t("advice_low_batt"))
        temp = s.get("temp_c")
        temp_max = s.get("temp_max")
        if temp is not None and temp_max and temp >= temp_max:
            return Advice("shutdown", 90, t("advice_hot"))
        score = s.get("score")
        if score is not None and score >= config.SMART_THRESHOLD:
            return Advice("shutdown", min(95, score), t("advice_smart", score=score))
        net = s.get("down_kbps")
        net_thr = s.get("net_threshold")
        if net is not None and net_thr and net >= net_thr * 5:
            return Advice("stay", 60, t("advice_busy", net=int(net)))
        idle = s.get("idle_s")
        idle_thr = s.get("idle_threshold_s")
        if idle is not None and idle_thr and idle >= idle_thr * 3:
            return Advice("sleep", 70, t("advice_idle", s=int(idle)))
        return Advice("stay", 0, t("advice_all_ok"))

    # ------------------------------------------------------------------ #
    # Remote hub (optional; fail-open to the local advisor)
    # ------------------------------------------------------------------ #
    def _remote_advice(self, s: dict) -> Advice | None:
        body = json.dumps(
            {
                "app": "SmartShutdownHub",
                "ts": time.time(),
                "signals": {
                    k: s[k] for k in (
                        "score", "idle_s", "monitor_off", "down_kbps",
                        "temp_c", "battery_pct", "plugged", "cpu_pct",
                        "ram_pct",
                    ) if k in s
                },
                "thresholds": {
                    k: s[k] for k in (
                        "idle_threshold_s", "net_threshold", "temp_max",
                        "battery_min",
                    ) if k in s
                },
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._remote_key:
            headers["Authorization"] = f"Bearer {self._remote_key}"
        req = urllib.request.Request(
            self._remote_url, data=body, method="POST", headers=headers
        )
        try:
            with urllib.request.urlopen(
                req, timeout=config.HUB_TIMEOUT_S
            ) as resp:
                raw = json.loads(
                    resp.read(config.HUB_MAX_BODY).decode("utf-8", "replace")
                )
            action = str(raw.get("action", "stay"))
            if action not in ADVISED_ACTIONS:
                action = "stay"
            confidence = int(raw.get("confidence", 0) or 0)
            reason = str(raw.get("reason", ""))[:200]
            self._remote_ok = True
            return Advice(action, confidence, reason, "ai")
        except Exception as exc:
            if self._remote_ok:  # log the fallback only once per outage
                self._remote_ok = False
                log.warning("remote hub unavailable: %s", exc)
                self._bus.publish(
                    EventType.LOG,
                    msg=t("hub_fallback", error=str(exc)[:120]),
                )
            return None

    # ------------------------------------------------------------------ #
    # Loop + publish
    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop.wait(config.HUB_POLL_S):
            with self._lock:
                signals = dict(self._signals)
            if not signals:
                continue
            advice = (
                self._remote_advice(signals)
                if self._remote_url
                else None
            ) or self._local_advice(signals)
            self._publish(advice)

    def _publish(self, advice: Advice) -> None:
        with self._lock:
            if advice.signature() == self._last_signature:
                return  # no change: don't spam the bus
            self._last_signature = advice.signature()
        self._bus.publish(
            EventType.ADVICE,
            action=advice.action,
            confidence=advice.confidence,
            reason=advice.reason,
            origin=advice.origin,
        )
