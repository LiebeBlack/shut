"""Top-left corner countdown badge.

A tiny, always-on-top, borderless window that shows the time remaining
before a scheduled shutdown while the user has the feature enabled.
It never steals focus, never blocks clicks on the rest of the screen
(no grab) and hides itself as soon as there is nothing to count down.

All methods are called from the GUI thread only (driven by the event
bus drain in MainWindow), so no locks are needed.
"""

from __future__ import annotations

import contextlib
import tkinter as tk

from . import theme


class CountdownBadge(tk.Toplevel):
    """Borderless floating timer, top-left corner of the primary screen."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.withdraw()  # start hidden; shown only while a timer runs
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        with contextlib.suppress(tk.TclError):  # not supported everywhere
            self.attributes("-alpha", 0.92)
        with contextlib.suppress(tk.TclError):  # Windows: no taskbar entry
            self.attributes("-toolwindow", True)
        card = tk.Frame(self, bg=theme.BG_CARD, highlightthickness=1,
                        highlightbackground=theme.BORDER_ACTIVE)
        card.pack()
        self._lbl = tk.Label(
            card, text="", bg=theme.BG_CARD, fg=theme.WARN,
            font=("Consolas", 13, "bold"), padx=10, pady=4,
        )
        self._lbl.pack()
        # Keep it above the window chrome without stealing focus.
        self._shown = False
        self._current_s = 0

    # ------------------------------------------------------------------ #
    def show_remaining(self, seconds: float) -> None:
        """Show (or refresh) the badge with `seconds` remaining.

        Negative/zero values are treated as "about to fire" and shown as
        00:00 until the caller hides the badge.
        """
        s = max(0, int(seconds))
        if s == self._current_s and self._shown:
            return  # unchanged: no redraw churn
        self._current_s = s
        m, s_ = divmod(s, 60)
        if m >= 60:
            h, m = divmod(m, 60)
            text = f"⏱ {h:02d}:{m:02d}:{s_:02d}"
        else:
            text = f"⏱ {m:02d}:{s_:02d}"
        # Traffic-light urgency: calm mint when far away, amber, then red
        # when the action is about to fire.
        self._lbl.configure(text=text, fg=self._urgency_color(s))
        if not self._shown:
            self._place_top_left()
            self.deiconify()
            self._shown = True

    @staticmethod
    def _urgency_color(seconds: int) -> str:
        if seconds > 300:
            return theme.ACCENT
        if seconds > 60:
            return theme.WARN
        return theme.DANGER

    def hide(self) -> None:
        if not self._shown:
            return
        self._current_s = 0
        self._shown = False
        self.withdraw()

    def _place_top_left(self) -> None:
        """Position at the top-left corner with a small margin."""
        x = 12
        y = 12
        self.geometry(f"+{x}+{y}")
        self.update_idletasks()

    # ------------------------------------------------------------------ #
    def destroy(self) -> None:  # always safe to call
        with contextlib.suppress(Exception):
            self.withdraw()
        super().destroy()
