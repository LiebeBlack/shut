"""SQLite-backed CRUD for shutdown profiles.

One file, no ORM: thin connection-per-call with a process-wide lock.
SQLite connections are never shared across threads; short-lived
connections are cheap here and keep the design leak-free.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .. import config


@dataclass(slots=True)
class Profile:
    id: int | None
    name: str
    mode: str
    countdown_minutes: int
    at_time: str
    idle_minutes: int
    process_name: str
    network_max_kbps: float
    thermal_max_c: float
    battery_min: int
    action: str
    enabled: int


_PROFILE_COLS = [f.name for f in fields(Profile) if f.name != "id"]


class ProfileStore:
    """CRUD access to the `profiles` table (thread-confined per call)."""

    def __init__(self, db_path=config.DB_PATH) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        self._init_db()

    # -- schema ------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        config.ensure_dirs()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    name             TEXT NOT NULL UNIQUE,
                    mode             TEXT NOT NULL,
                    countdown_minutes INTEGER NOT NULL DEFAULT 0,
                    at_time          TEXT NOT NULL DEFAULT '',
                    idle_minutes     INTEGER NOT NULL DEFAULT 30,
                    process_name     TEXT NOT NULL DEFAULT '',
                    network_max_kbps REAL NOT NULL DEFAULT 50.0,
                    thermal_max_c    REAL NOT NULL DEFAULT 90.0,
                    battery_min      INTEGER NOT NULL DEFAULT 10,
                    action           TEXT NOT NULL DEFAULT 'shutdown',
                    enabled          INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            self._migrate(conn)
            row = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()
            if row["n"] == 0:
                conn.execute(
                    "INSERT INTO profiles (name, mode) VALUES (?, ?)",
                    (config.DEFAULT_PROFILE["name"], config.DEFAULT_PROFILE["mode"]),
                )

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Idempotent schema migration for upgrades between versions."""
        cols = {
            r["name"] for r in
            conn.execute("PRAGMA table_info(profiles)").fetchall()
        }
        if "battery_min" not in cols:
            conn.execute(
                "ALTER TABLE profiles ADD COLUMN battery_min "
                "INTEGER NOT NULL DEFAULT 10"
            )

    # -- CRUD -------------------------------------------------------------- #
    def list_profiles(self) -> list[Profile]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM profiles ORDER BY id").fetchall()
        return [self._to_profile(r) for r in rows]

    def get(self, profile_id: int) -> Profile | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return self._to_profile(row) if row else None

    def add(self, p: Profile) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO profiles ({', '.join(_PROFILE_COLS)}) "
                f"VALUES ({', '.join('?' for _ in _PROFILE_COLS)})",
                [getattr(p, c) for c in _PROFILE_COLS],
            )
            return int(cur.lastrowid)

    def update(self, p: Profile) -> None:
        assert p.id is not None
        sets = ", ".join(f"{c} = ?" for c in _PROFILE_COLS)
        vals = [getattr(p, c) for c in _PROFILE_COLS] + [p.id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE profiles SET {sets} WHERE id = ?", vals)

    def delete(self, profile_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    # -- persistence helpers (v2) ------------------------------------ #
    def enabled_profiles(self) -> list[Profile]:
        return [p for p in self.list_profiles() if p.enabled]

    def export_json(self, path) -> int:
        """Write all profiles to a JSON file. Returns count written."""
        import json

        data = [asdict(p) for p in self.list_profiles()]
        Path(path).write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return len(data)

    def import_json(self, path) -> int:
        """Merge profiles from a JSON file (skip duplicates by name)."""
        import json

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("expected a list of profiles")
        existing = {p.name for p in self.list_profiles()}
        added = 0
        for item in raw:
            try:
                if not isinstance(item, dict):
                    continue
                name = str(item["name"]).strip()
                mode = str(item.get("mode", "absolute")).strip()
                action = str(item.get("action", "shutdown")).strip()
                if not name or mode not in {
                    "absolute", "idle", "monitor", "process",
                    "network", "smart", "hybrid",
                } or action not in {
                    "shutdown", "reboot", "sleep", "hibernate",
                }:
                    continue
                enabled_value = item.get("enabled", 1)
                if isinstance(enabled_value, str):
                    enabled_value = enabled_value.strip().lower() in {
                        "1", "true", "yes", "on"
                    }
                p = Profile(
                    id=None,
                    name=name,
                    mode=mode,
                    countdown_minutes=max(
                        0, int(item.get("countdown_minutes", 0))
                    ),
                    at_time=str(item.get("at_time", "")),
                    idle_minutes=max(0, int(item.get("idle_minutes", 30))),
                    process_name=str(item.get("process_name", "")),
                    network_max_kbps=max(
                        0.0, float(item.get("network_max_kbps", 50.0))
                    ),
                    thermal_max_c=max(
                        0.0, float(item.get("thermal_max_c", 90.0))
                    ),
                    battery_min=min(
                        100, max(0, int(item.get("battery_min", 10)))
                    ),
                    action=action,
                    enabled=1 if bool(enabled_value) else 0,
                )
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed entries, never abort the batch
            if p.name in existing:
                continue
            self.add(p)
            existing.add(p.name)
            added += 1
        return added

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def _to_profile(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"],
            name=row["name"],
            mode=row["mode"],
            countdown_minutes=row["countdown_minutes"],
            at_time=row["at_time"],
            idle_minutes=row["idle_minutes"],
            process_name=row["process_name"],
            network_max_kbps=row["network_max_kbps"],
            thermal_max_c=row["thermal_max_c"],
            battery_min=row["battery_min"],
            action=row["action"],
            enabled=row["enabled"],
        )

    def to_dict(self, p: Profile) -> dict:
        return asdict(p)
