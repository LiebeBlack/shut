"""Thread-safe event bus decoupling daemon sensors from the GUI.

Sensors only ever *publish*; the GUI only ever *drains*. A bounded queue
with drop-on-full semantics guarantees the engine never blocks on a
frozen or minimized UI (anti-leak architecture requirement #3).

Event contract (payload keys are documented for each consumer):
    STATE     source, remaining_s | idle_s | off_s | down_kbps |
              status | temp_c | battery_pct | plugged | score | why |
              cpu_pct | ram_pct
    TRIGGER   (reserved; sensors call the engine callback directly)
    OVERLAY   action, source, profile, seconds
    ABORT     (no payload)
    EXECUTED  action, ok
    LOG       msg
    ERROR     sensor, error
    ADVICE    action, confidence, reason, origin  (Intelligence Hub)
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

log = logging.getLogger(__name__)


class EventType(Enum):
    STATE = auto()          # periodic engine snapshot for the GUI
    TRIGGER = auto()        # a sensor fired
    OVERLAY = auto()        # request the 30s emergency overlay
    ABORT = auto()          # user cancelled from overlay
    EXECUTED = auto()       # OS command was issued
    LOG = auto()            # free-form diagnostic line
    ERROR = auto()          # recoverable error in a sensor
    ADVICE = auto()         # Intelligence Hub recommendation


@dataclass(slots=True)
class Event:
    type: EventType
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class EventBus:
    """Bounded, thread-safe pub/sub channel (single consumer: the GUI)."""

    def __init__(self, maxsize: int = 256) -> None:
        self._q: queue.Queue[Event] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._published = 0
        self._consumed = 0
        self._dropped = 0

    # -- producer API (daemon threads) ------------------------------------ #
    def publish(self, type_: EventType, **payload) -> None:
        ev = Event(type_, payload)
        with self._lock:
            self._published += 1
            try:
                self._q.put_nowait(ev)
            except queue.Full:
                self._dropped += 1
                # Oldest-first drop keeps the newest state visible to the UI.
                try:
                    self._q.get_nowait()
                    self._q.put_nowait(ev)
                except (queue.Empty, queue.Full):
                    pass

    # -- consumer API (GUI main thread) ----------------------------------- #
    def drain(self, max_items: int = 40) -> list[Event]:
        out: list[Event] = []
        while len(out) < max_items:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        if out:
            with self._lock:
                self._consumed += len(out)
        return out

    # -- diagnostics ------------------------------------------------------- #
    def stats(self) -> dict:
        """Live counters: published / consumed / dropped (thread-safe)."""
        with self._lock:
            return {
                "published": self._published,
                "consumed": self._consumed,
                "dropped": self._dropped,
            }

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped


Subscriber = Callable[[Event], None]
