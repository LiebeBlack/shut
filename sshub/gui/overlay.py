"""Emergency overlay v2: 30s countdown + giant CANCEL (Escape works).

Shown by the GUI thread when an EventType.OVERLAY arrives. If it reaches
zero without cancellation, finalize() fires the real OS command (unless
dry-run is active, in which case nothing is executed).
"""

from __future__ import annotations

import contextlib
import tkinter as tk

from .. import config
from ..core.executor import ActionExecutor
from ..events import EventBus
from ..i18n import t


class EmergencyOverlay(tk.Toplevel):
    """Borderless, topmost, modal emergency window."""

    def __init__(self, master, bus: EventBus, executor: ActionExecutor,
                 action: str, source: str, seconds: int,
                 dry_run: bool = False) -> None:
        super().__init__(master)
        self._bus = bus
        self._executor = executor
        self._remaining = seconds

        self.title("Emergency Shutdown")
        self.configure(bg="#1a1d26")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        with contextlib.suppress(tk.TclError):
            self.attributes("-alpha", 0.94)

        w, h = 470, 270
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        container = tk.Frame(self, bg="#1a1d26", padx=24, pady=16)
        container.pack(expand=True, fill="both")

        tk.Label(
            container, text=t("emergency"), fg="#ff5c5c", bg="#1a1d26",
            font=("Segoe UI", 16, "bold"),
        ).pack()

        tk.Label(
            container, text=t(f"act_{action}") if action in config.ACTION_LABELS
            else action,
            fg="#e8eaf0", bg="#1a1d26", font=("Segoe UI", 12),
        ).pack(pady=(4, 0))

        tk.Label(
            container, text=t("fired_by", src=source), fg="#8b93a7",
            bg="#1a1d26", font=("Segoe UI", 9),
        ).pack()

        if dry_run:  # safety banner in rehearsal mode
            tk.Label(
                container, text="🧪 " + t("dry_run"), fg="#ffd166",
                bg="#1a1d26", font=("Segoe UI", 9, "bold"),
            ).pack()

        self._countdown_lbl = tk.Label(
            container, text=str(self._remaining), fg="#ffd166",
            bg="#1a1d26", font=("Segoe UI", 42, "bold"),
        )
        self._countdown_lbl.pack(pady=4)

        self._cancel_btn = tk.Button(
            container, text=t("cancel"), command=self._on_cancel,
            bg="#2ecc71", fg="#0b0d12", activebackground="#27ae60",
            activeforeground="#0b0d12", font=("Segoe UI", 15, "bold"),
            relief="flat", cursor="hand2", padx=30, pady=12,
        )
        self._cancel_btn.pack(fill="x", ipady=4)

        tk.Label(
            container, text=t("cancel_hint"), fg="#5c6478", bg="#1a1d26",
            font=("Segoe UI", 8),
        ).pack(pady=(6, 0))

        self.bind("<Escape>", lambda _e: self._on_cancel())
        self.focus_force()
        try:
            self.grab_set_global()
        except tk.TclError:
            self.focus_set()
        self._countdown_lbl.configure(text=str(self._remaining))
        self.after(1000, self._tick)

    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._finalize()
            return
        self._countdown_lbl.configure(
            text=str(self._remaining), fg="#ff5c5c" if self._remaining <= 5
            else "#ffd166"
        )
        self.after(1000, self._tick)

    def _on_cancel(self) -> None:
        self._executor.cancel()
        self._close()

    def _finalize(self) -> None:
        self._executor.finalize()
        self._close()

    def _close(self) -> None:
        with contextlib.suppress(Exception):
            self.grab_release()
        self.destroy()
