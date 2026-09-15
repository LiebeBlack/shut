"""Emergency overlay v3: 30s countdown + depleting bar + giant CANCEL.

Shown by the GUI thread when an EventType.OVERLAY arrives. If it reaches
zero without cancellation, finalize() fires the real OS command (unless
dry-run is active, in which case nothing is executed). Redesigned look:
dark card, red accent, mono countdown, linear depletion bar.
"""

from __future__ import annotations

import contextlib
import tkinter as tk

from .. import config
from ..core.executor import ActionExecutor
from ..events import EventBus
from ..i18n import t
from . import theme


class EmergencyOverlay(tk.Toplevel):
    """Borderless, topmost, modal emergency window."""

    def __init__(self, master, bus: EventBus, executor: ActionExecutor,
                 action: str, source: str, seconds: int,
                 dry_run: bool = False) -> None:
        super().__init__(master)
        self._bus = bus
        self._executor = executor
        self._remaining = seconds
        self._total = max(1, seconds)

        self.title("Emergency Shutdown")
        self.configure(bg=theme.BG_ROOT)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        with contextlib.suppress(tk.TclError):
            self.attributes("-alpha", 0.96)

        w, h = 480, 300
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        card = tk.Frame(self, bg=theme.BG_CARD, highlightthickness=1,
                        highlightbackground=theme.DANGER)
        card.pack(expand=True, fill="both", padx=14, pady=14)

        inner = tk.Frame(card, bg=theme.BG_CARD, padx=24, pady=16)
        inner.pack(expand=True, fill="both")

        tk.Label(
            inner, text=t("emergency"), fg=theme.DANGER, bg=theme.BG_CARD,
            font=("Segoe UI", 16, "bold"),
        ).pack()

        tk.Label(
            inner, text=t(f"act_{action}") if action in config.ACTION_LABELS
            else action,
            fg=theme.TEXT_PRIMARY, bg=theme.BG_CARD,
            font=("Segoe UI", 12),
        ).pack(pady=(4, 0))

        tk.Label(
            inner, text=t("fired_by", src=source), fg=theme.TEXT_MUTED,
            bg=theme.BG_CARD, font=("Segoe UI", 9),
        ).pack()

        if dry_run:  # safety banner in rehearsal mode
            tk.Label(
                inner, text="🧪 " + t("dry_run"), fg=theme.WARN,
                bg=theme.BG_CARD, font=("Segoe UI", 9, "bold"),
            ).pack()

        self._countdown_lbl = tk.Label(
            inner, text=str(self._remaining), fg=theme.WARN,
            bg=theme.BG_CARD, font=theme.F_COUNTDOWN,
        )
        self._countdown_lbl.pack(pady=4)

        # Depleting bar: full at start, gone at zero — readable at a glance.
        self._bar = tk.Canvas(inner, height=6, bg=theme.BG_CARD,
                              highlightthickness=0)
        self._bar.pack(fill="x", pady=(2, 6))
        self._draw_bar()

        self._cancel_btn = tk.Button(
            inner, text=t("cancel"), command=self._on_cancel,
            bg=theme.ACCENT, fg=theme.ACCENT_TEXT,
            activebackground=theme.ACCENT_HOVER,
            activeforeground=theme.ACCENT_TEXT,
            font=("Segoe UI", 15, "bold"),
            relief="flat", cursor="hand2", padx=30, pady=12,
            borderwidth=0,
        )
        self._cancel_btn.pack(fill="x", ipady=4)

        tk.Label(
            inner, text=t("cancel_hint"), fg=theme.TEXT_MUTED,
            bg=theme.BG_CARD, font=("Segoe UI", 8),
        ).pack(pady=(6, 0))

        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.focus_force()
        try:
            self.grab_set_global()
        except tk.TclError:
            self.focus_set()
        self._countdown_lbl.configure(text=str(self._remaining))
        self._tick_id = self.after(1000, self._tick)
        self._bar_id = self.after(80, self._draw_bar)  # geometry repaint

    def _draw_bar(self) -> None:
        """Render the depletion bar at the current remaining fraction."""
        frac = max(0.0, min(1.0, self._remaining / self._total))
        self._bar.delete("all")
        w = int(self._bar.winfo_width() or 1)
        color = theme.DANGER if frac <= 0.17 else theme.WARN
        self._bar.create_rectangle(0, 0, max(2, int(w * frac)), 6,
                                   fill=color, width=0)

    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._finalize()
            return
        self._countdown_lbl.configure(
            text=str(self._remaining), fg=theme.DANGER if self._remaining <= 5
            else theme.WARN
        )
        self._draw_bar()
        self._tick_id = self.after(1000, self._tick)

    def _on_cancel(self) -> None:
        if not self.winfo_exists():
            return  # double-escape / repeated event: cancel must run once
        self._executor.cancel()
        self._close()

    def _finalize(self) -> None:
        if not self.winfo_exists():
            return  # already closed: finalize was handled
        self._executor.finalize()
        self._close()

    def _close(self) -> None:
        for attr in ("_tick_id", "_bar_id"):
            if hasattr(self, attr) and getattr(self, attr):
                with contextlib.suppress(Exception):
                    self.after_cancel(getattr(self, attr))
                setattr(self, attr, None)
        with contextlib.suppress(Exception):
            self.grab_release()
        with contextlib.suppress(Exception):
            self.destroy()
