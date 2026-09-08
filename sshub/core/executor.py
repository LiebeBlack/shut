"""Safe action executor: issues OS power commands with an abort window.

The engine never calls the OS directly when the countdown is running.
Instead it asks the GUI to show the 30-second emergency overlay
(EventType.OVERLAY). Only when the overlay expires un-cancelled does
`finalize` fire the real command. On Windows the issued `shutdown`
command itself carries a 60s grace timeout, so even a hard crash of the
app leaves the user with a last-resort `shutdown /a` escape hatch.
"""

from __future__ import annotations

import logging
import time

from .. import config
from ..events import EventBus, EventType
from ..platform_layer import abort_os_shutdown, execute_power_action


def abort_pending_os_shutdown() -> None:
    """Public escape hatch: cancels a pending OS-level shutdown."""
    abort_os_shutdown()

log = logging.getLogger(__name__)


class ActionExecutor:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._pending_action: str | None = None
        self._cooldown_until = 0.0  # monotonic; suppresses re-trigger storms

    # ------------------------------------------------------------------ #
    def request(self, action: str, source: str, profile: str) -> None:
        """Arm the pending action and surface the emergency overlay."""
        if self._pending_action:
            return  # already pending; debounce duplicate triggers
        if time.monotonic() < self._cooldown_until:
            return  # user recently cancelled; latched sensors stay muted
        self._pending_action = ActionExecutor._validate(action)
        self._bus.publish(
            EventType.OVERLAY,
            action=self._pending_action,
            source=source,
            profile=profile,
            seconds=config.OVERLAY_SECONDS,
        )

    @staticmethod
    def _validate(action: str) -> str:
        return action if action in config.ACTION_LABELS else "shutdown"

    @property
    def pending(self) -> bool:
        return self._pending_action is not None

    # ------------------------------------------------------------------ #
    def cancel(self) -> None:
        """User pressed CANCEL / Escape in the overlay."""
        self._pending_action = None
        self._cooldown_until = time.monotonic() + config.TRIGGER_COOLDOWN_S
        self._bus.publish(EventType.ABORT)

    # ------------------------------------------------------------------ #
    def finalize(self) -> None:
        """Overlay expired: execute for real."""
        action, self._pending_action = self._pending_action, None
        if not action:
            return
        self._cooldown_until = time.monotonic() + config.TRIGGER_COOLDOWN_S
        ok = execute_power_action(action)
        self._bus.publish(EventType.EXECUTED, action=action, ok=ok)
        log.info("executed action=%s ok=%s", action, ok)
