"""System tray integration v2 (pystray + PIL, both optional at runtime).

If pystray/PIL are unavailable the app still runs; close then quits
instead of hiding to tray.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from pathlib import Path

from ..i18n import t

log = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw

    _AVAILABLE = True
except ImportError:  # pragma: no cover
    _AVAILABLE = False


def _fallback_icon() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (20, 23, 31, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((14, 20, 50, 56), outline=(0, 229, 160, 255), width=5)
    d.line((32, 8, 32, 26), fill=(0, 229, 160, 255), width=5)
    return img


class TrayIcon:
    def __init__(self, restore_cb, quit_cb, tooltip: str = "") -> None:
        self._icon = None
        if not _AVAILABLE:
            log.info("pystray not installed; tray disabled")
            return
        icon_path = Path(__file__).parent / "assets" / "sshub.ico"
        image = (
            Image.open(icon_path) if icon_path.exists() else _fallback_icon()
        )
        menu = pystray.Menu(
            pystray.MenuItem(t("tray_show"), lambda *_: restore_cb(),
                             default=True),
            pystray.MenuItem(t("tray_quit"), lambda *_: quit_cb()),
        )
        self._icon = pystray.Icon(
            "sshub", image, tooltip or t("tray_tip"), menu
        )

    def start(self) -> None:
        if self._icon:
            threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self) -> None:
        if self._icon:
            with contextlib.suppress(Exception):  # pragma: no cover
                self._icon.stop()

    @property
    def available(self) -> bool:
        return _AVAILABLE
