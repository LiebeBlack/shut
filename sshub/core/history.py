"""Persistent activity journal: what the app did, and when.

A tiny SQLite table — or a pure in-memory list when no path is given —
that records every meaningful event (arms, disarms, triggers,
executions, cancellations, sensor errors) so the dashboard can show a
real activity trail plus lifetime counters that survive restarts.

Design notes
------------
- Connection-per-call plus a process-wide lock, mirroring ProfileStore.
- The table is capped at `max_rows`: the journal is a rolling window,
  never an unbounded log (anti-leak architecture requirement).
- `path=None` keeps everything in memory. Tests and callers that only
  want live counters never touch the user's disk, and a journal write
  can never break a shutdown run: every failure is swallowed + logged.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

# Journal entry kinds. These are persisted, so they are stable codes —
# never translated, only rendered through KIND_CODES.
KIND_ARM = "arm"
KIND_DISARM = "disarm"
KIND_TRIGGER = "trigger"
KIND_EXECUTED = "executed"
KIND_CANCELLED = "cancelled"
KIND_ERROR = "error"

KIND_CODES: dict[str, str] = {
    KIND_ARM: "ARM",
    KIND_DISARM: "DIS",
    KIND_TRIGGER: "TRG",
    KIND_EXECUTED: "RUN",
    KIND_CANCELLED: "CAN",
    KIND_ERROR: "ERR",
}


class ActivityLog:
    """Rolling activity journal with lifetime counters (thread-safe)."""

    def __init__(self, path: Path | None = None,
                 max_rows: int = config.ACTIVITY_MAX_ROWS) -> None:
        self._path = Path(path) if path is not None else None
        self._max = max(20, int(max_rows))
        self._lock = threading.Lock()
        self._mem: list[tuple] = []  # in-memory mode only
        if self._path is not None:
            self._init_db()

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS activity (
                        id      INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts      REAL NOT NULL,
                        kind    TEXT NOT NULL,
                        action  TEXT NOT NULL DEFAULT '',
                        source  TEXT NOT NULL DEFAULT '',
                        profile TEXT NOT NULL DEFAULT '',
                        detail  TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_activity_ts "
                    "ON activity (ts DESC)"
                )
        except (sqlite3.Error, OSError) as exc:
            # A broken journal must never stop the app from monitoring.
            log.warning("activity journal unavailable (%s); in-memory only",
                        exc)
            self._path = None

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def record(self, kind: str, action: str = "", source: str = "",
               profile: str = "", detail: str = "") -> None:
        """Append one entry, trimming the oldest rows past the cap."""
        row = (time.time(), kind, action, source, profile, str(detail)[:200])
        with self._lock:
            if self._path is None:
                self._mem.append(row)
                if len(self._mem) > self._max:
                    del self._mem[: len(self._mem) - self._max]
                return
            try:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO activity (ts, kind, action, source, "
                        "profile, detail) VALUES (?, ?, ?, ?, ?, ?)",
                        row,
                    )
                    conn.execute(
                        "DELETE FROM activity WHERE id NOT IN ("
                        "SELECT id FROM activity ORDER BY id DESC LIMIT ?)",
                        (self._max,),
                    )
            except sqlite3.Error as exc:
                log.warning("activity write failed: %s", exc)

    def clear(self) -> None:
        """Wipe the journal (counters included)."""
        with self._lock:
            if self._path is None:
                self._mem.clear()
                return
            try:
                with self._connect() as conn:
                    conn.execute("DELETE FROM activity")
            except sqlite3.Error as exc:
                log.warning("activity clear failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def recent(self, limit: int = config.JOURNAL_PREVIEW_ROWS) -> list[dict]:
        """Newest-first entries, ready for the dashboard card."""
        limit = max(1, min(int(limit), self._max))
        with self._lock:
            if self._path is None:
                rows = list(reversed(self._mem[-limit:]))
            else:
                try:
                    with self._connect() as conn:
                        cur = conn.execute(
                            "SELECT ts, kind, action, source, profile, detail "
                            "FROM activity ORDER BY id DESC LIMIT ?",
                            (limit,),
                        )
                        rows = [tuple(r) for r in cur.fetchall()]
                except sqlite3.Error as exc:
                    log.warning("activity read failed: %s", exc)
                    return []
        return [self._as_entry(r) for r in rows]

    def counts(self) -> dict[str, int]:
        """Per-kind counters (kinds with no entries report 0)."""
        out: dict[str, int] = {kind: 0 for kind in KIND_CODES}
        with self._lock:
            if self._path is None:
                for row in self._mem:
                    out[row[1]] = out.get(row[1], 0) + 1
                return out
            try:
                with self._connect() as conn:
                    for kind, n in conn.execute(
                        "SELECT kind, COUNT(*) FROM activity GROUP BY kind"
                    ):
                        out[str(kind)] = int(n)
            except sqlite3.Error as exc:
                log.warning("activity stats failed: %s", exc)
        return out

    def total(self) -> int:
        return sum(self.counts().values())

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _as_entry(row: tuple) -> dict:
        ts, kind, action, source, profile, detail = row
        ts = float(ts)
        return {
            "ts": ts,
            "kind": kind,
            "action": action,
            "source": source,
            "profile": profile,
            "detail": detail,
            "clock": ActivityLog.format_ts(ts),
            "code": KIND_CODES.get(kind, "···"),
        }

    @staticmethod
    def format_ts(ts: float) -> str:
        """Local HH:MM:SS for a POSIX timestamp (journal display)."""
        try:
            return time.strftime("%H:%M:%S", time.localtime(ts))
        except (OSError, ValueError, OverflowError):
            return "--:--:--"

    @staticmethod
    def format_entry(entry: dict) -> str:
        """One journal line: `HH:MM:SS  CODE → action [profile] (source)`."""
        bits = [f"{entry.get('clock', '--:--:--')}  "
                f"{entry.get('code', '···')}"]
        action = entry.get("action") or ""
        if action:
            bits.append(f"→ {action}")
        profile = entry.get("profile") or ""
        if profile:
            bits.append(f"[{profile}]")
        source = entry.get("source") or ""
        if source:
            bits.append(f"({source})")
        detail = entry.get("detail") or ""
        if detail:
            bits.append(detail)
        return " ".join(bits)
