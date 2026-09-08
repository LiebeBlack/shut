"""Ephemeral toast notifications (bottom-right), auto-dismissing."""

from __future__ import annotations

import contextlib
import tkinter as tk


class Toast(tk.Toplevel):
    """Small non-modal notification; never steals focus."""

    def __init__(self, master) -> None:
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        with contextlib.suppress(tk.TclError):
            self.attributes("-alpha", 0.96)
        self._lbl = tk.Label(
            self, text="", bg="#1b1f2a", fg="#e8eaf0",
            font=("Segoe UI", 10), padx=16, pady=10,
        )
        self._lbl.pack()
        self._after_id: str | None = None

    def show(self, msg: str, ms: int = 2600) -> None:
        if not self.winfo_exists() or not self.master.winfo_exists():
            return
        self._lbl.configure(text=msg)
        self.deiconify()
        x = self.master.winfo_rootx() + self.master.winfo_width() - 320
        y = self.master.winfo_rooty() + self.master.winfo_height() - 90
        self.geometry(f"+{max(10, x)}+{max(10, y)}")
        if self._after_id:
            with contextlib.suppress(Exception):
                self.after_cancel(self._after_id)
        self._after_id = self.after(ms, self._hide)

    def _hide(self) -> None:
        with contextlib.suppress(Exception):
            self.withdraw()
