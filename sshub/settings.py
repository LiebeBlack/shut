"""Persistent user settings (JSON) with atomic writes and full defaults.

Settings are separate from profiles: they are global preferences
(language, accent, start-minimized, global debounce). A corrupt file is
renamed aside and rebuilt from defaults — never crashes the app.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "language": "",              # "" = autodetect
    "start_minimized": False,
    "dry_run_default": False,    # default state of the dry-run toggle
    "hub_enabled": False,        # Intelligence Hub advisor on/off
    "countdown_overlay": False,  # top-left corner countdown badge
    "window_geometry": "",       # "WxH+X+Y" remembered across runs
    "network_debounce_s": 120,
    "accent": "mint",           # accent family name (see gui/theme.ACCENTS)
    "autostart": False,          # mirror of the HKCU Run entry (re-applied at boot)
}


class Settings:
    """JSON-backed dict with defaults merge and atomic saves."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config.DATA_DIR / "settings.json")
        self._data: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    merged = dict(DEFAULTS)
                    merged.update(
                        {k: v for k, v in raw.items() if k in DEFAULTS}
                    )
                    self._data = merged
        except Exception as exc:
            log.warning("settings corrupt (%s); renaming aside", exc)
            with contextlib.suppress(OSError):
                self.path.rename(self.path.with_suffix(".corrupt"))
            self._data = dict(DEFAULTS)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self.path.parent), suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)  # atomic on POSIX and Windows
        except Exception as exc:
            log.warning("settings save failed: %s", exc)

    def get(self, key: str):
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if key not in DEFAULTS:
            log.warning("unknown setting ignored: %s", key)
            return
        if self._data.get(key) == value:
            return  # no-op: don't rewrite the file on every slider tick
        self._data[key] = value
        self.save()
