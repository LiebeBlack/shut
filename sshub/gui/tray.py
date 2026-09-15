"""System tray integration v3 (pystray + PIL, both optional at runtime).

If pystray/PIL are unavailable the app still runs; close then quits
instead of hiding to tray.

v3 adds a live status channel: the tray tooltip follows the engine
(armed / counting down / disarmed), the menu exposes a one-click
arm-or-disarm entry, and state changes raise a native balloon. Every
entry point is failure-tolerant — a tray that misbehaves must never take
the monitoring app down with it.
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
    """Tray icon with live status text and a toggle entry."""

    def __init__(self, restore_cb, quit_cb, tooltip: str = "",
                 toggle_cb=None) -> None:
        self._icon = None
        self._toggle_cb = toggle_cb
        self._armed = False
        self._status = ""
        self._base_tip = tooltip or t("tray_tip")
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
            pystray.MenuItem(self._toggle_label, lambda *_: self._toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(t("tray_quit"), lambda *_: quit_cb()),
        )
        self._icon = pystray.Icon(
            "sshub", image, self._tooltip(), menu
        )

    # ------------------------------------------------------------------ #
    # Dynamic labels
    # ------------------------------------------------------------------ #
    def _toggle_label(self, *_args) -> str:
        """Menu text follows the engine state (pystray calls this itself)."""
        return t("tray_disarm") if self._armed else t("tray_arm")

    def _tooltip(self) -> str:
        """Tooltip: app name plus the last status pushed by the GUI."""
        return f"{self._base_tip} · {self._status}" if self._status \
            else self._base_tip

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
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

    # ------------------------------------------------------------------ #
    # Live status channel (called from the GUI thread)
    # ------------------------------------------------------------------ #
    def set_state(self, armed: bool, status: str = "") -> None:
        """Reflect engine state in the tooltip, the menu and a balloon.

        Safe to call at any time and from the GUI thread; a tray that is
        not installed (or already stopped) is simply ignored.
        """
        changed = (bool(armed) != self._armed)
        self._armed = bool(armed)
        self._status = status or ""
        if not self._icon:
            return
        with contextlib.suppress(Exception):
            self._icon.title = self._tooltip()
        with contextlib.suppress(Exception):
            self._icon.update_menu()
        if changed:
            with contextlib.suppress(Exception):  # pragma: no cover
                self._icon.notify(
                    self._status or (t("tray_armed") if self._armed
                                     else t("tray_disarmed")),
                    self._base_tip,
                )

    def _toggle(self) -> None:
        """Run the arm/disarm callback, keeping it on the GUI thread."""
        if self._toggle_cb is None:
            return
        with contextlib.suppress(Exception):
            self._toggle_cb()
