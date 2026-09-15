"""Main window v3: redesigned cockpit — sticky command bar, live telemetry
with progress bars, tricolor risk meter, restyled accordions, bilingual
support and leak-free event draining. Rendering contract with
scripts/selftest.py is preserved (attribute names, text formats).
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import config
from ..core.engine import MonitoringEngine
from ..core.storage import Profile, ProfileStore
from ..events import EventBus, EventType
from ..i18n import detect_language, set_language, t
from . import theme
from .countdown_badge import CountdownBadge
from .overlay import EmergencyOverlay
from .toast import Toast


class AccordionSection(ctk.CTkFrame):
    """Collapsible section with a rounded card look and chevron header."""

    def __init__(self, master, title: str, expanded: bool = True) -> None:
        super().__init__(master, fg_color=theme.BG_CARD, corner_radius=14,
                         border_width=1, border_color=theme.BORDER)
        self._expanded = expanded
        self._title = title
        self._header = ctk.CTkButton(
            self, text=f"{'▾' if expanded else '▸'}  {title}",
            fg_color="transparent", hover_color=theme.BG_HOVER,
            text_color=theme.TEXT_PRIMARY, font=theme.F_SECTION,
            anchor="w", command=self.toggle, height=38,
        )
        self._header.pack(fill="x", padx=6, pady=(4, 0))
        self._header.bind("<Return>", lambda _event: self.toggle())
        self._header.bind("<space>", lambda _event: self.toggle())
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        if expanded:
            self.body.pack(fill="x", padx=8, pady=(0, 8))

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self.set_title(self._title)

    def set_title(self, title: str) -> None:
        """Update the section title without changing its expanded state."""
        self._title = title
        self._header.configure(
            text=f"{'▾' if self._expanded else '▸'}  {self._title}"
        )
        if self._expanded:
            self.body.pack(fill="x", padx=8, pady=(0, 8))
        else:
            self.body.pack_forget()


class MainWindow(ctk.CTk):
    """Dark-mode cockpit. Owns the engine and the emergency overlay."""

    def __init__(self, bus: EventBus, store: ProfileStore,
                 engine: MonitoringEngine, settings,
                 hub=None) -> None:
        super().__init__()
        self._bus = bus
        self._store = store
        self._engine = engine
        self._settings = settings
        self._hub = hub  # IntelligenceHub | None (advisory only)
        self._advice_payload: dict | None = None
        self._last_dropped = 0
        self._profiles: list[Profile] = []
        self._current: Profile | None = None
        self._overlay: EmergencyOverlay | None = None
        self._toast: Toast | None = None
        self._settings_win: ctk.CTkToplevel | None = None
        self._badge: CountdownBadge | None = None
        self._tray_available = True
        self._badge_enabled = bool(settings.get("countdown_overlay"))
        self._timer_remaining: float | None = None
        self._dry_run = bool(settings.get("dry_run_default"))
        self._old_none_proc = t("none_proc")
        self._drain_after_id: str | None = None
        self._closing = False
        if self._dry_run:
            os.environ["SSHUB_DRY_RUN"] = "1"  # honor saved dry-run at boot

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        lang = settings.get("language") or detect_language()
        set_language(lang)

        self.title(f"{config.APP_NAME} — {config.APP_VERSION}")
        self._apply_geometry()
        self.configure(fg_color=theme.BG_ROOT)
        self._set_window_icon()
        self._build_ui()
        self._reload_profiles()
        self._drain_after_id = self.after(config.GUI_POLL_MS, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self._fullscreen = False
        self.bind("<F11>", lambda _e: self._toggle_fullscreen())
        self.bind("<Escape>", lambda _e: self._toggle_fullscreen(exit_only=True))
        # Keyboard navigation of the whole UI (fires from any child).
        self.bind("<Prior>", lambda _e: self._scroll_page(-1))
        self.bind("<Next>", lambda _e: self._scroll_page(1))
        self.bind("<Home>", lambda _e: self._scroll_to(0.0))
        self.bind("<End>", lambda _e: self._scroll_to(1.0))
        if self._settings.get("start_minimized"):
            self.withdraw()  # boot hidden: no window flash on login

    # ------------------------------------------------------------------ #
    # Window geometry: remembered size/position, clamped to the screen
    # ------------------------------------------------------------------ #
    def _apply_geometry(self) -> None:
        """Restore the saved geometry, or center an adaptive default.

        Height adapts to the screen and remembered geometry from another
        monitor is validated to ensure it fits.
        """
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        saved = str(self._settings.get("window_geometry") or "")
        geo = saved if saved and "+" in saved and "x" in saved else ""
        if geo:
            try:
                w = int(geo.split("x")[0])
                rest = geo.split("x")[1]
                h = int(rest.split("+")[0])
                if not (540 <= w <= sw and 560 <= h <= sh):
                    geo = ""  # saved size no longer fits this screen
            except (ValueError, IndexError):
                geo = ""
        if not geo:
            w = 640
            h = min(740, max(620, sh - 100))
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2 - 24)
            geo = f"{w}x{h}+{x}+{y}"
        self.geometry(geo)
        self.minsize(580, min(640, max(560, sh - 120)))

    def _set_window_icon(self) -> None:
        """Taskbar/titlebar icon: sshub.ico on Windows, PhotoImage elsewhere."""
        icon = Path(__file__).parent / "assets" / "sshub.ico"
        if not icon.exists():
            return
        try:
            if sys.platform.startswith("win"):
                self.iconbitmap(str(icon))
            else:
                from PIL import Image, ImageTk

                self._icon_img = ImageTk.PhotoImage(Image.open(icon))
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass  # cosmetic: a missing/invalid icon never breaks startup

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        # Whole UI lives in a scrollable frame: responsive across screen sizes
        self._scroll = ctk.CTkScrollableFrame(
            self, width=640, height=520, fg_color="transparent",
            corner_radius=0,
        )
        self._scroll.pack(fill="both", expand=True, padx=10, pady=(6, 10))

        # -- Top Bar / Header -------------------------------------------- #
        header = ctk.CTkFrame(self._scroll, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 2))

        logo = ctk.CTkFrame(header, fg_color=theme.ACCENT, corner_radius=9,
                            width=36, height=36)
        logo.pack(side="left", padx=(0, 10))
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo, text="⏻", font=theme.F_TITLE, text_color=theme.ACCENT_TEXT,
        ).pack(expand=True)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.pack(side="left", fill="y")
        ctk.CTkLabel(
            title_box, text=config.APP_NAME.upper(), font=theme.F_HERO,
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        self._tagline = ctk.CTkLabel(
            title_box, text=t("app_tagline"), font=theme.F_META,
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self._tagline.pack(anchor="w")

        # Status pill: gray DISARMED / mint ARMED / amber countdown MM:SS
        self._status_pill = ctk.CTkFrame(
            header, fg_color=theme.BG_ELEVATED, corner_radius=12,
            border_width=1, border_color=theme.BORDER,
        )
        self._status_pill.pack(side="right")
        self._status_dot = ctk.CTkLabel(
            self._status_pill, text=f"● {t('disarmed')}",
            font=theme.F_SMALL_BOLD, text_color=theme.TEXT_MUTED,
        )
        self._status_dot.pack(padx=12, pady=5)

        # -- Menu / Toolbar row ------------------------------------------ #
        menu_row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        menu_row.pack(fill="x", padx=12, pady=(8, 4))

        self._menu_btn = ctk.CTkButton(
            menu_row, text=t("menu"), width=100, height=32,
            font=theme.F_SMALL, fg_color=theme.BG_ELEVATED,
            hover_color=theme.BG_HOVER, corner_radius=8,
            command=self._open_menu,
        )
        self._menu_btn.pack(side="left")

        self._fullscreen_btn = ctk.CTkButton(
            menu_row, text=t("fullscreen"), width=130, height=32,
            font=theme.F_SMALL, fg_color=theme.BG_ELEVATED,
            hover_color=theme.BG_HOVER, corner_radius=8,
            command=self._toggle_fullscreen,
        )
        self._fullscreen_btn.pack(side="left", padx=(6, 0))

        self._badge_btn = ctk.CTkButton(
            menu_row, text="⏱  " + t("badge_short"), width=96, height=32,
            font=theme.F_SMALL,
            fg_color=theme.GHOST if self._badge_enabled else theme.BG_ELEVATED,
            hover_color=theme.BG_HOVER, corner_radius=8,
            command=self._toggle_badge,
        )
        self._badge_btn.pack(side="left", padx=(6, 0))

        self._dry_var = ctk.StringVar(
            value=("🧪 " + t("dry_run")) if self._dry_run else ""
        )
        ctk.CTkLabel(
            menu_row, textvariable=self._dry_var, font=theme.F_SMALL_BOLD,
            text_color=theme.WARN,
        ).pack(side="left", padx=(10, 0))

        # -- Dashboard (Hero telemetry & AI Advice) ----------------------- #
        dash = ctk.CTkFrame(
            self._scroll, fg_color=theme.BG_CARD, corner_radius=14,
            border_width=1, border_color=theme.BORDER,
        )
        dash.pack(fill="x", padx=12, pady=6)

        score_row = ctk.CTkFrame(dash, fg_color="transparent")
        score_row.pack(fill="x", padx=14, pady=(10, 4))

        self._dash_score = ctk.CTkLabel(
            score_row, text="🧠 —", font=theme.F_HERO,
            text_color=theme.TEXT_MUTED,
        )
        self._dash_score.pack(side="left", padx=(0, 12))

        self._dash_why = ctk.CTkLabel(
            score_row, text="", font=theme.F_SMALL,
            text_color=theme.TEXT_SECONDARY,
            justify="left", anchor="w", wraplength=440,
        )
        self._dash_why.pack(side="left", fill="x", expand=True)

        # AI advice line (clickable), packed dynamically when available
        self._dash_advice = ctk.CTkLabel(
            dash, text="", font=theme.F_SMALL_BOLD, text_color=theme.INFO,
            justify="left", anchor="w", cursor="hand2", wraplength=540,
        )
        self._dash_advice.bind("<Button-1>", lambda _e: self._on_advice_click())

        tiles = ctk.CTkFrame(dash, fg_color="transparent")
        self._tiles_frame = tiles
        tiles.pack(fill="x", padx=12, pady=(4, 12))

        self._tiles: dict[str, ctk.CTkLabel] = {}
        self._tile_bars: dict[str, ctk.CTkProgressBar] = {}
        for key in ("cpu", "ram", "temp", "batt"):
            tile_card = ctk.CTkFrame(
                tiles, fg_color=theme.BG_CARD_INNER, corner_radius=10,
                border_width=1, border_color=theme.BORDER,
            )
            tile_card.pack(side="left", fill="x", expand=True, padx=4)
            tile_box = ctk.CTkFrame(tile_card, fg_color="transparent")
            tile_box.pack(fill="both", expand=True, padx=8, pady=7)
            lbl = ctk.CTkLabel(
                tile_box, text=f"{t(key)} —", font=theme.F_VALUE,
                text_color=theme.TEXT_SECONDARY, anchor="w",
            )
            lbl.pack(fill="x")
            bar = ctk.CTkProgressBar(
                tile_box, height=4, corner_radius=2,
                fg_color=theme.BG_CONTROL, progress_color=theme.ACCENT,
            )
            bar.set(0)
            bar.pack(fill="x", pady=(5, 0))
            self._tiles[key] = lbl
            self._tile_bars[key] = bar

        # -- Profile selector ------------------------------------------- #
        sel = ctk.CTkFrame(
            self._scroll, fg_color=theme.BG_CARD, corner_radius=14,
            border_width=1, border_color=theme.BORDER,
        )
        sel.pack(fill="x", padx=12, pady=6)

        sel_row = ctk.CTkFrame(sel, fg_color="transparent")
        sel_row.pack(fill="x", padx=12, pady=10)

        self._profile_lbl = ctk.CTkLabel(
            sel_row, text=t("profile"), font=theme.F_SECTION,
        )
        self._profile_lbl.pack(side="left", padx=(0, 10))

        self._profile_var = ctk.StringVar()
        self._profile_menu = ctk.CTkOptionMenu(
            sel_row, variable=self._profile_var, command=self._on_profile_selected,
            fg_color=theme.BG_CONTROL, button_color=theme.GHOST,
            button_hover_color=theme.BG_HOVER, text_color=theme.TEXT_PRIMARY,
            height=32, corner_radius=8,
        )
        self._profile_menu.pack(side="left", fill="x", expand=True)

        self._add_profile_btn = ctk.CTkButton(
            sel_row, text=t("new"), width=78, height=32, command=self._on_add_profile,
            font=theme.F_SMALL, fg_color=theme.GHOST,
            hover_color=theme.BG_HOVER, corner_radius=8,
        )
        self._add_profile_btn.pack(side="left", padx=(8, 4))

        self._delete_profile_btn = ctk.CTkButton(
            sel_row, text=t("delete"), width=78, height=32,
            font=theme.F_SMALL, command=self._on_delete_profile,
            fg_color=theme.GHOST, hover_color=theme.BG_HOVER, corner_radius=8,
        )
        self._delete_profile_btn.pack(side="left")

        # -- Accordion: trigger conditions (distributed 2-column grid) -- #
        self._acc_triggers = AccordionSection(
            self._scroll, f"⚡ {t('sec_triggers')}"
        )
        self._acc_triggers.pack(fill="x", padx=12, pady=4)
        body = self._acc_triggers.body

        self._mode_var = ctk.StringVar(value=t("mode_absolute"))
        self._mode_values = {
            t("mode_absolute"): "absolute",
            t("mode_idle"): "idle",
            t("mode_monitor"): "monitor",
            t("mode_process"): "process",
            t("mode_network"): "network",
            t("mode_smart"): "smart",
            t("mode_hybrid"): "hybrid",
        }
        self._mode_radios: list[ctk.CTkRadioButton] = []

        modes_grid = ctk.CTkFrame(body, fg_color="transparent")
        modes_grid.pack(fill="x", padx=8, pady=(4, 8))
        modes_grid.grid_columnconfigure((0, 1), weight=1)

        modes_list = list(self._mode_values.keys())
        for idx, label in enumerate(modes_list):
            r = idx if idx < 4 else (idx - 4)
            c = 0 if idx < 4 else 1
            radio = ctk.CTkRadioButton(
                modes_grid, text=label, variable=self._mode_var, value=label,
                font=theme.F_LABEL, fg_color=theme.ACCENT,
                hover_color=theme.ACCENT_HOVER,
            )
            radio.grid(row=r, column=c, sticky="w", padx=8, pady=3)
            self._mode_radios.append(radio)

        inputs_box = ctk.CTkFrame(
            body, fg_color=theme.BG_CARD_INNER, corner_radius=10,
            border_width=1, border_color=theme.BORDER,
        )
        inputs_box.pack(fill="x", padx=8, pady=6)
        inputs_box.grid_columnconfigure((0, 2), weight=0)
        inputs_box.grid_columnconfigure((1, 3), weight=1)

        self._lbl_countdown = ctk.CTkLabel(inputs_box, text=t("countdown_min"))
        self._lbl_countdown.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=6)
        self._countdown_entry = ctk.CTkEntry(
            inputs_box, width=70, justify="center", height=30,
            fg_color=theme.BG_CONTROL, border_color=theme.BORDER,
            corner_radius=8,
        )
        self._countdown_entry.insert(0, "0")
        self._countdown_entry.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=6)

        self._lbl_at_time = ctk.CTkLabel(inputs_box, text=t("at_time"))
        self._lbl_at_time.grid(row=0, column=2, sticky="w", padx=(12, 6), pady=6)
        self._at_time_entry = ctk.CTkEntry(
            inputs_box, width=70, justify="center", placeholder_text="22:30",
            height=30, fg_color=theme.BG_CONTROL, border_color=theme.BORDER,
            corner_radius=8,
        )
        self._at_time_entry.grid(row=0, column=3, sticky="w", padx=(0, 12), pady=6)

        self._lbl_idle = ctk.CTkLabel(inputs_box, text=t("idle_min"))
        self._lbl_idle.grid(row=1, column=0, sticky="w", padx=(12, 6), pady=(0, 8))
        self._idle_entry = ctk.CTkEntry(
            inputs_box, width=70, justify="center", height=30,
            fg_color=theme.BG_CONTROL, border_color=theme.BORDER,
            corner_radius=8,
        )
        self._idle_entry.insert(0, "30")
        self._idle_entry.grid(row=1, column=1, sticky="w", padx=(0, 12), pady=(0, 8))

        # -- Accordion: advanced (2-column balanced grid) ---------------- #
        self._acc_advanced = AccordionSection(
            self._scroll, f"🎛 {t('sec_advanced')}"
        )
        self._acc_advanced.pack(fill="x", padx=12, pady=4)
        adv = self._acc_advanced.body

        adv_grid = ctk.CTkFrame(adv, fg_color="transparent")
        adv_grid.pack(fill="x", padx=4, pady=4)
        adv_grid.grid_columnconfigure((0, 1), weight=1)

        col_left = ctk.CTkFrame(adv_grid, fg_color="transparent")
        col_left.grid(row=0, column=0, sticky="nsew", padx=6)
        col_right = ctk.CTkFrame(adv_grid, fg_color="transparent")
        col_right.grid(row=0, column=1, sticky="nsew", padx=6)

        self._slider_row(col_left, t("net_threshold"), "50 KB/s", 1, 2000, 199, 50,
                         " KB/s", "_net")
        self._slider_row(col_left, t("debounce"), "120 s", 5, 600, 119, 120,
                         " s", "_deb")
        self._slider_row(col_right, t("thermal_max"), "90 °C", 60, 105, 45, 90,
                         " °C", "_temp")
        self._slider_row(col_right, t("battery_min"), "10 %", 5, 50, 45, 10,
                         " %", "_batt")

        # -- Accordion: process watcher --------------------------------- #
        self._acc_proc = AccordionSection(
            self._scroll, f"🔎 {t('sec_process')}"
        )
        self._acc_proc.pack(fill="x", padx=12, pady=4)
        prow = ctk.CTkFrame(self._acc_proc.body, fg_color="transparent")
        prow.pack(fill="x", padx=10, pady=6)
        self._proc_var = ctk.StringVar(value=t("none_proc"))
        self._proc_menu = ctk.CTkOptionMenu(
            prow, variable=self._proc_var, width=280, height=32,
            fg_color=theme.BG_CONTROL, button_color=theme.GHOST,
            button_hover_color=theme.BG_HOVER, corner_radius=8,
        )
        self._proc_menu.pack(side="left", fill="x", expand=True)
        self._refresh_proc_btn = ctk.CTkButton(
            prow, text="⟳", width=42, height=32, command=self._refresh_processes,
            font=theme.F_LABEL_BOLD, fg_color=theme.GHOST,
            hover_color=theme.BG_HOVER, corner_radius=8,
        )
        self._refresh_proc_btn.pack(side="left", padx=(8, 0))

        # -- Action & Arm Section ---------------------------------------- #
        act = ctk.CTkFrame(
            self._scroll, fg_color=theme.BG_CARD, corner_radius=14,
            border_width=1, border_color=theme.BORDER,
        )
        act.pack(fill="x", padx=12, pady=6)

        self._action_lbl = ctk.CTkLabel(
            act, text=t("action_label"), font=theme.F_SECTION,
        )
        self._action_lbl.pack(anchor="w", padx=12, pady=(10, 4))
        self._action_values = {t(f"act_{k}"): k for k in config.ACTION_LABELS}
        self._action_var = ctk.StringVar()
        seg = ctk.CTkSegmentedButton(
            act, values=list(self._action_values),
            variable=self._action_var,
            selected_color=theme.ACCENT, selected_hover_color=theme.ACCENT_HOVER,
            fg_color=theme.BG_CONTROL, unselected_color=theme.BG_CONTROL,
            text_color=theme.TEXT_PRIMARY, height=34, corner_radius=8,
        )
        self._action_seg = seg
        seg.set(next(iter(self._action_values)))
        seg.pack(fill="x", padx=12, pady=(0, 12))

        # The one loud element: the ARM command, pinned at the bottom of
        # the scroll so it is always reachable without hunting.
        self._arm_btn = ctk.CTkButton(
            self._scroll, text=t("arm"), height=50,
            font=theme.F_TITLE,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            text_color=theme.ACCENT_TEXT, corner_radius=12,
            command=self._on_arm,
        )
        self._arm_btn.pack(fill="x", padx=12, pady=(6, 8))
        self._arm_btn.bind("<Return>", lambda _event: self._on_arm())

        # -- Telemetry console ------------------------------------------- #
        self._acc_log = AccordionSection(
            self._scroll, f"📡 {t('sec_telemetry')}", expanded=False
        )
        self._acc_log.pack(fill="x", padx=12, pady=(2, 10))

        log_bar = ctk.CTkFrame(self._acc_log.body, fg_color="transparent")
        log_bar.pack(fill="x", padx=6, pady=(0, 4))
        ctk.CTkButton(
            log_bar, text="🗑 " + t("clear"), width=84, height=24,
            font=theme.F_SMALL, fg_color=theme.BG_CONTROL,
            hover_color=theme.BG_HOVER, corner_radius=6,
            command=self._clear_log,
        ).pack(side="right")

        self._log_box = ctk.CTkTextbox(
            self._acc_log.body, height=130, fg_color=theme.BG_ROOT,
            text_color=theme.TEXT_SECONDARY, font=theme.F_VALUE,
            border_width=1, border_color=theme.BORDER, corner_radius=10,
        )
        self._log_box.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def _slider_row(self, parent, label, init, frm, to, steps, val, unit, tag):
        """Labeled slider row: title, live value, trough with mint fill."""
        title = ctk.CTkLabel(parent, text=label, font=theme.F_LABEL)
        title.pack(anchor="w", padx=12, pady=(4, 0))
        setattr(self, f"{tag}_title", title)
        lbl = ctk.CTkLabel(
            parent, text=init, font=theme.F_VALUE, text_color=theme.ACCENT,
        )
        lbl.pack(anchor="w", padx=12)
        slider = ctk.CTkSlider(
            parent, from_=frm, to=to, number_of_steps=steps,
            button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
            progress_color=theme.ACCENT, fg_color=theme.BG_CONTROL,
            command=lambda v: lbl.configure(text=f"{int(float(v))}{unit}"),
        )
        slider.set(val)
        slider.pack(fill="x", padx=12, pady=(0, 8))
        setattr(self, f"{tag}_slider", slider)
        setattr(self, f"{tag}_lbl", lbl)

    # ------------------------------------------------------------------ #
    # Menu (settings modal)
    # ------------------------------------------------------------------ #
    def _open_menu(self) -> None:
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.deiconify()
            self._settings_win.focus_force()
            return  # singleton: never stack two settings windows
        top = ctk.CTkToplevel(self)
        self._settings_win = top
        top.title(t("settings"))
        top.geometry("420x520")
        top.minsize(380, 460)
        top.configure(fg_color=theme.BG_ROOT)
        top.transient(self)
        top.grab_set()

        ctk.CTkLabel(top, text=t("settings"),
                     font=theme.F_TITLE).pack(pady=(12, 6))

        # Group 1: General & Idioma
        g1 = ctk.CTkFrame(top, fg_color=theme.BG_CARD, corner_radius=12,
                          border_width=1, border_color=theme.BORDER)
        g1.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(
            g1, text=t("language"), font=theme.F_SECTION,
        ).pack(anchor="w", padx=12, pady=(8, 2))
        langs = {"Español": "es", "English": "en"}
        lang_var = ctk.StringVar(
            value=next((k for k, v in langs.items()
                        if v == (self._settings.get("language")
                                 or detect_language())), "Español")
        )
        seg = ctk.CTkSegmentedButton(
            g1, values=list(langs), variable=lang_var,
            command=lambda v: self._on_language(langs[v]),
            fg_color=theme.BG_CONTROL, selected_color=theme.ACCENT,
        )
        seg.pack(fill="x", padx=12, pady=4)

        self._min_var = ctk.BooleanVar(
            value=bool(self._settings.get("start_minimized")))
        ctk.CTkSwitch(
            g1, text=t("start_minimized"), variable=self._min_var,
            command=self._on_toggle_minimized,
        ).pack(anchor="w", padx=12, pady=(6, 8))

        # Group 2: Modos de Asistencia
        g2 = ctk.CTkFrame(top, fg_color=theme.BG_CARD, corner_radius=12,
                          border_width=1, border_color=theme.BORDER)
        g2.pack(fill="x", padx=14, pady=4)

        self._dry_switch_var = ctk.BooleanVar(value=self._dry_run)
        ctk.CTkSwitch(
            g2, text=t("dry_run"), variable=self._dry_switch_var,
            command=self._on_toggle_dry, progress_color=theme.WARN,
        ).pack(anchor="w", padx=12, pady=(8, 4))

        self._hub_var = ctk.BooleanVar(value=bool(self._hub and self._hub.enabled))
        ctk.CTkSwitch(
            g2, text=t("hub"), variable=self._hub_var,
            command=self._on_toggle_hub, progress_color=theme.INFO,
        ).pack(anchor="w", padx=12, pady=4)

        self._badge_var = ctk.BooleanVar(value=self._badge_enabled)
        ctk.CTkSwitch(
            g2, text=t("countdown_badge"), variable=self._badge_var,
            command=self._on_toggle_badge_switch, progress_color=theme.WARN,
        ).pack(anchor="w", padx=12, pady=(4, 8))

        # Group 3: Datos e Integración
        g3 = ctk.CTkFrame(top, fg_color=theme.BG_CARD, corner_radius=12,
                          border_width=1, border_color=theme.BORDER)
        g3.pack(fill="x", padx=14, pady=4)

        from ..platform_layer import cpu_sse4_2
        ctk.CTkLabel(
            g3, text=t("sse_report", ok="✓" if cpu_sse4_2() else "✗"),
            font=theme.F_SMALL, text_color=theme.TEXT_SECONDARY,
        ).pack(anchor="w", padx=12, pady=(6, 2))

        row = ctk.CTkFrame(g3, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(4, 8))
        ctk.CTkButton(
            row, text=t("export"), height=30, command=self._on_export,
            font=theme.F_SMALL, fg_color=theme.GHOST,
            hover_color=theme.BG_HOVER, corner_radius=8,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(
            row, text=t("import"), height=30, command=self._on_import,
            font=theme.F_SMALL, fg_color=theme.GHOST,
            hover_color=theme.BG_HOVER, corner_radius=8,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        ctk.CTkLabel(
            top, text=f"v{config.APP_VERSION}",
            font=theme.F_META, text_color=theme.TEXT_MUTED,
        ).pack(side="bottom", pady=6)

    def _on_language(self, code: str) -> None:
        set_language(code)
        self._settings.set("language", code)
        self._apply_language()  # translate in place: zero widget churn
        self._toast_msg("🌐 " + code.upper())

    def _apply_language(self) -> None:
        """Re-translate every label in place without widget destruction."""
        self._tagline.configure(text=t("app_tagline"))
        self._menu_btn.configure(text=t("menu"))
        self._fullscreen_btn.configure(
            text=t("exit_fullscreen") if self._fullscreen else t("fullscreen")
        )
        self._badge_btn.configure(text="⏱  " + t("badge_short"))
        self._add_profile_btn.configure(text=t("new"))
        self._delete_profile_btn.configure(text=t("delete"))
        self._profile_lbl.configure(text=t("profile"))
        self._acc_triggers.set_title(f"⚡ {t('sec_triggers')}")
        self._acc_advanced.set_title(f"🎛 {t('sec_advanced')}")
        self._acc_proc.set_title(f"🔎 {t('sec_process')}")
        self._acc_log.set_title(f"📡 {t('sec_telemetry')}")
        self._lbl_countdown.configure(text=t("countdown_min"))
        self._lbl_at_time.configure(text=t("at_time"))
        self._lbl_idle.configure(text=t("idle_min"))
        self._net_title.configure(text=t("net_threshold"))
        self._deb_title.configure(text=t("debounce"))
        self._temp_title.configure(text=t("thermal_max"))
        self._batt_title.configure(text=t("battery_min"))
        self._action_lbl.configure(text=t("action_label"))
        self._dry_var.set(("🧪 " + t("dry_run")) if self._dry_run else "")

        # Update telemetry tile labels
        for key in ("cpu", "ram", "temp", "batt"):
            if key in self._tiles:
                curr = self._tiles[key].cget("text")
                val = curr.split(" ", 1)[1] if " " in curr else "—"
                self._tiles[key].configure(text=f"{t(key)} {val}")

        # Mode radios: the radio VALUES are the translated labels.
        prev_mode_code = self._mode_values.get(
            self._mode_var.get(), "absolute"
        )
        mode_order = ("absolute", "idle", "monitor", "process",
                      "network", "smart", "hybrid")
        self._mode_values = {t(f"mode_{c}"): c for c in mode_order}
        for radio, code in zip(self._mode_radios, mode_order, strict=True):
            label = t(f"mode_{code}")
            radio.configure(text=label, value=label)
        self._mode_var.set(
            next(lbl for lbl, c in self._mode_values.items()
                 if c == prev_mode_code)
        )

        # Action segmented button: remap the selection to the new language.
        prev_action = self._action_values.get(
            self._action_var.get(), "shutdown"
        )
        self._action_values = {
            t(f"act_{k}"): k for k in config.ACTION_LABELS
        }
        self._action_seg.configure(values=list(self._action_values))
        self._action_var.set(
            next(lbl for lbl, c in self._action_values.items()
                 if c == prev_action)
        )

        # Process picker "none" placeholder follows the language.
        if self._proc_var.get() == self._old_none_proc:
            self._proc_var.set(t("none_proc"))
        self._old_none_proc = t("none_proc")

        # Strings tied to live state.
        self._set_armed_ui(self._engine.state.armed)
        if self._advice_payload:
            self._render_advice(self._advice_payload)

        # The settings window holds stale-language widgets: close it safely.
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.destroy()
        self._settings_win = None

    def _on_toggle_minimized(self) -> None:
        self._settings.set("start_minimized", bool(self._min_var.get()))

    def _on_toggle_dry(self) -> None:
        self._dry_run = bool(self._dry_switch_var.get())
        self._settings.set("dry_run_default", self._dry_run)
        os.environ["SSHUB_DRY_RUN"] = "1" if self._dry_run else "0"
        self._dry_var.set(("🧪 " + t("dry_run")) if self._dry_run else "")

    def _on_toggle_hub(self) -> None:
        """Advisory only: toggling never triggers any action by itself."""
        self._settings.set("hub_enabled", bool(self._hub_var.get()))
        self._log(t("hub") + (" ✓" if self._hub_var.get() else " ✗"))

    def _toggle_badge(self) -> None:
        """Menu-row ⏱: show the remaining time in the top-left corner."""
        self._badge_enabled = not self._badge_enabled
        self._settings.set("countdown_overlay", self._badge_enabled)
        if hasattr(self, "_badge_btn"):
            self._badge_btn.configure(
                fg_color=theme.GHOST if self._badge_enabled
                else theme.BG_ELEVATED
            )
        if not self._badge_enabled and self._badge is not None:
            self._badge.hide()
        self._log(t("badge_toggle",
                    state="✓" if self._badge_enabled else "✗"))

    def _on_toggle_badge_switch(self) -> None:
        """Settings window mirror of the ⏱ toggle."""
        self._badge_enabled = bool(self._badge_var.get())
        self._settings.set("countdown_overlay", self._badge_enabled)
        if hasattr(self, "_badge_btn"):
            self._badge_btn.configure(
                fg_color=theme.GHOST if self._badge_enabled
                else theme.BG_ELEVATED
            )
        if not self._badge_enabled and self._badge is not None:
            self._badge.hide()

    def _on_export(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="sshub_profiles.json",
        )
        if path:
            n = self._store.export_json(path)
            self._toast_msg(t("exported", path=path))
            self._log(f"⬇ {n} perfiles → {path}")

    def _on_import(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")], title=t("import")
        )
        if path:
            try:
                n = self._store.import_json(path)
                self._reload_profiles()
                self._toast_msg(t("imported", n=n))
            except Exception:
                messagebox.showerror("JSON", t("import_err"))

    # ------------------------------------------------------------------ #
    # Profiles CRUD
    # ------------------------------------------------------------------ #
    def _reload_profiles(self) -> None:
        self._profiles = self._store.list_profiles()
        names = [p.name for p in self._profiles]
        self._profile_menu.configure(values=names)
        if names:
            current = self._profile_var.get()
            target = current if current in names else names[0]
            self._profile_var.set(target)
            self._on_profile_selected(target)

    def _find_profile(self, name: str) -> Profile | None:
        return next((p for p in self._profiles if p.name == name), None)

    def _on_profile_selected(self, name: str) -> None:
        p = self._find_profile(name)
        if not p:
            return
        self._current = p
        mode_key = {"absolute": "mode_absolute", "idle": "mode_idle",
                    "monitor": "mode_monitor", "process": "mode_process",
                    "network": "mode_network", "smart": "mode_smart",
                    "hybrid": "mode_hybrid"}.get(p.mode, "mode_absolute")
        self._mode_var.set(t(mode_key))
        self._countdown_entry.delete(0, "end")
        self._countdown_entry.insert(0, str(p.countdown_minutes))
        self._at_time_entry.delete(0, "end")
        if p.at_time:
            self._at_time_entry.insert(0, p.at_time)
        self._idle_entry.delete(0, "end")
        self._idle_entry.insert(0, str(p.idle_minutes))
        self._net_slider.set(p.network_max_kbps)
        self._net_lbl.configure(text=f"{int(p.network_max_kbps)} KB/s")
        self._temp_slider.set(p.thermal_max_c)
        self._temp_lbl.configure(text=f"{int(p.thermal_max_c)} °C")
        self._batt_slider.set(p.battery_min)
        self._batt_lbl.configure(text=f"{int(p.battery_min)} %")
        if p.process_name:
            self._proc_var.set(p.process_name)
        act_key = {"shutdown": "act_shutdown", "reboot": "act_reboot",
                   "sleep": "act_sleep", "hibernate": "act_hibernate"}.get(
                       p.action, "act_shutdown")
        self._action_seg.set(t(act_key))

    def _on_add_profile(self) -> None:
        win = ctk.CTkInputDialog(text=t("new_profile"), title=t("profile"))
        name = (win.get_input() or "").strip()
        if not name:
            return
        if self._find_profile(name):
            messagebox.showwarning("⚠", t("profile_exists"))
            return
        p = Profile(
            id=None, name=name, mode="absolute", countdown_minutes=0,
            at_time="", idle_minutes=30, process_name="",
            network_max_kbps=50.0, thermal_max_c=90.0, battery_min=10,
            action="shutdown", enabled=1,
        )
        p.id = self._store.add(p)
        self._reload_profiles()
        self._profile_var.set(name)
        self._on_profile_selected(name)

    def _on_delete_profile(self) -> None:
        p = self._current
        if not p:
            return
        if len(self._profiles) <= 1:
            messagebox.showinfo("i", t("blocked_delete"))
            return
        if messagebox.askyesno("?", t("confirm_delete", name=p.name)):
            self._store.delete(p.id)
            self._reload_profiles()

    # ------------------------------------------------------------------ #
    # Process picker
    # ------------------------------------------------------------------ #
    def _refresh_processes(self) -> None:
        from ..platform_layer import list_processes

        procs = list_processes()
        if procs:
            self._proc_menu.configure(values=procs)
            self._proc_var.set(procs[0])
        else:
            self._log(t("proc_list_err"))

    # ------------------------------------------------------------------ #
    # Arm / disarm
    # ------------------------------------------------------------------ #
    def _on_arm(self) -> None:
        if self._engine.state.armed:
            self._engine.disarm()
            self._set_armed_ui(False)
            self._log(t("disarm_log"))
            return
        p = self._current
        if not p:
            return
        p.mode = self._mode_label_to_code(self._mode_var.get())
        p.countdown_minutes = self._to_int(self._countdown_entry.get(), 0)
        p.at_time = self._at_time_entry.get().strip()
        if p.at_time and not self._valid_hhmm(p.at_time):
            messagebox.showwarning("⚠", t("bad_time", t=p.at_time))
            return
        p.idle_minutes = self._to_int(self._idle_entry.get(), 30)
        p.network_max_kbps = float(self._net_slider.get())
        p.thermal_max_c = float(self._temp_slider.get())
        p.battery_min = int(self._batt_slider.get())
        proc = self._proc_var.get()
        p.process_name = "" if proc == t("none_proc") else proc
        p.action = self._action_values.get(self._action_var.get(), "shutdown")
        self._store.update(p)
        config.NET_DEBOUNCE_S = self._to_int(self._deb_slider.get(), 120)
        self._timer_remaining = None  # fresh arm: no stale countdown badge
        if self._hub:
            self._hub.set_profile(p)  # advisor matches the armed profile

        self._engine.arm([p])
        self._set_armed_ui(True)
        self._log(t("arm_log", name=p.name, mode=p.mode, action=p.action))

    def _mode_label_to_code(self, label: str) -> str:
        return self._mode_values.get(label, "absolute")

    @staticmethod
    def _to_int(raw: str, default: int) -> int:
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _valid_hhmm(raw: str) -> bool:
        """True if raw is a real 24h clock time like 22:30."""
        try:
            h, m = (int(x) for x in raw.split(":"))
            return 0 <= h <= 23 and 0 <= m <= 59
        except (TypeError, ValueError):
            return False

    def _set_armed_ui(self, armed: bool) -> None:
        if armed:
            self._arm_btn.configure(
                text=t("disarm"), fg_color=theme.DANGER,
                hover_color=theme.DANGER_HOVER,
                text_color=theme.ON_DANGER,
            )
            self._status_dot.configure(
                text=f"● {t('armed')}", text_color=theme.ACCENT)
            self._status_pill.configure(
                fg_color=theme.ARMED_PILL_BG,
                border_color=theme.ARMED_PILL_BORDER)
        else:
            self._arm_btn.configure(
                text=t("arm"), fg_color=theme.ACCENT,
                hover_color=theme.ACCENT_HOVER,
                text_color=theme.ACCENT_TEXT,
            )
            self._status_dot.configure(
                text=f"● {t('disarmed')}", text_color=theme.TEXT_MUTED)
            self._status_pill.configure(
                fg_color=theme.BG_ELEVATED, border_color=theme.BORDER)
            self._timer_remaining = None  # no timer -> no corner badge

    # ------------------------------------------------------------------ #
    # Event bus drain (GUI thread only, zero leaks)
    # ------------------------------------------------------------------ #
    def _drain_events(self) -> None:
        if self._closing:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        if self._overlay is not None and not self._overlay.winfo_exists():
            self._overlay = None
        self._update_badge()
        dropped = self._bus.dropped
        if dropped > self._last_dropped:
            self._log(t("bus_drop", n=dropped))
            self._last_dropped = dropped
        try:
            for ev in self._bus.drain(config.GUI_BATCH_MAX):
                if ev.type is EventType.OVERLAY and self._overlay is None:
                    self._show_overlay(ev.payload)
                elif ev.type is EventType.STATE:
                    if self._hub:
                        self._hub.observe(ev.payload)  # advisory feed
                    self._render_state(ev.payload)
                elif ev.type is EventType.ADVICE:
                    self._render_advice(ev.payload)
                elif ev.type is EventType.LOG:
                    self._log(ev.payload.get("msg", ""))
                elif ev.type is EventType.ABORT:
                    self._log(t("aborted"))
                elif ev.type is EventType.EXECUTED:
                    self._log(t("executed",
                                action=ev.payload.get("action", "?")))
                elif ev.type is EventType.ERROR:
                    self._log(t("sensor_err", sensor=ev.payload.get("sensor"),
                                error=ev.payload.get("error")))
        finally:
            if not self._closing:
                try:
                    if self.winfo_exists():
                        self._drain_after_id = self.after(
                            config.GUI_POLL_MS, self._drain_events
                        )
                except Exception:
                    pass

    def _render_advice(self, payload: dict) -> None:
        """Show the hub verdict; clicking it applies the advised action."""
        self._advice_payload = payload
        action = payload.get("action", "stay")
        conf = int(payload.get("confidence", 0))
        reason = payload.get("reason", "")
        origin = "AI" if payload.get("origin") == "ai" else "local"
        if action == "stay":
            text = f"{t('advice_label')}: {reason}"
        else:
            label = next(
                (lbl for lbl, code in self._action_values.items()
                 if code == action),
                action,
            )
            text = (f"{t('advice_label')}: {reason} · {label} ({conf}%) · "
                    f"{origin}")
        if text and not self._dash_advice.winfo_ismapped():
            self._dash_advice.pack(fill="x", padx=(14, 8), pady=(0, 4),
                                   before=self._tiles_frame)
        elif not text and self._dash_advice.winfo_ismapped():
            self._dash_advice.pack_forget()
        self._dash_advice.configure(text=text)

    def _on_advice_click(self) -> None:
        """One-click apply: set the action selector to the advised action."""
        payload = self._advice_payload
        if not payload or payload.get("action", "stay") == "stay":
            return
        action = payload["action"]
        label = next(
            (lbl for lbl, code in self._action_values.items()
             if code == action),
            None,
        )
        if label:
            self._action_seg.set(label)
            self._toast_msg(t("advice_applied", action=action))

    def _show_overlay(self, payload: dict) -> None:
        self._overlay = EmergencyOverlay(
            self, self._bus, self._engine.executor,
            action=payload.get("action", "shutdown"),
            source=payload.get("source", "?"),
            seconds=int(payload.get("seconds", config.OVERLAY_SECONDS)),
            dry_run=self._dry_run,
        )
        self._overlay.protocol("WM_DELETE_WINDOW", self._overlay._on_cancel)
        self._log(t("overlay_log", s=payload.get("seconds")))

    def _render_state(self, payload: dict) -> None:
        src = payload.get("source")
        if src == "absolute" and "remaining_s" in payload:
            self._timer_remaining = float(payload["remaining_s"])
            m, s = divmod(int(payload["remaining_s"]), 60)
            self._status_dot.configure(
                text=f"● {m:02d}:{s:02d}", text_color=theme.WARN)
        if src == "smart" and "score" in payload:
            score = int(payload["score"])
            color = theme.score_color(score)
            self._dash_score.configure(text=f"🧠 {score}%", text_color=color)
            self._dash_why.configure(text=" · ".join(payload.get("why", [])))
        if src in ("smart", "thermal_battery"):
            self._update_tiles(payload)

    def _update_tiles(self, payload: dict) -> None:
        """Telemetry tiles: value text + tricolor progress bar fill."""
        cpu = payload.get("cpu_pct")
        if cpu is not None:
            self._tiles["cpu"].configure(
                text=f"{t('cpu')} {int(cpu)}%",
                text_color=theme.live_color(cpu),
            )
            self._tile_bars["cpu"].configure(
                progress_color=theme.live_color(cpu))
            self._tile_bars["cpu"].set(max(0.0, min(1.0, cpu / 100.0)))
        ram = payload.get("ram_pct")
        if ram is not None:
            self._tiles["ram"].configure(
                text=f"{t('ram')} {int(ram)}%",
                text_color=theme.live_color(ram),
            )
            self._tile_bars["ram"].configure(
                progress_color=theme.live_color(ram))
            self._tile_bars["ram"].set(max(0.0, min(1.0, ram / 100.0)))
        temp = payload.get("temp_c")
        if temp is not None:
            self._tiles["temp"].configure(
                text=f"{t('temp')} {int(temp)}°",
                text_color=theme.live_color(temp, max_pct=105),
            )
            self._tile_bars["temp"].configure(
                progress_color=theme.live_color(temp, max_pct=105))
            self._tile_bars["temp"].set(max(0.0, min(1.0, temp / 105.0)))
        batt = payload.get("battery_pct")
        if batt is not None:
            plug = payload.get("plugged")
            if plug:
                # On AC power: battery risk is zero by definition.
                icon = "⚡"
                suffix = t("no_battery")
                color = theme.ACCENT
                frac = 0.0
            else:
                icon = "🔋"
                suffix = f"{int(batt)}%"
                color = theme.live_color(100 - batt)
                frac = max(0.0, min(1.0, batt / 100.0))
            self._tiles["batt"].configure(
                text=f"{icon} {suffix}", text_color=color)
            self._tile_bars["batt"].configure(progress_color=color)
            self._tile_bars["batt"].set(frac)

    def _log(self, msg: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"· {msg}\n")
        lines = int(self._log_box.index("end-1c").split(".")[0])
        if lines > 400:
            self._log_box.delete("1.0", "200.0")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _toast_msg(self, msg: str) -> None:
        if self._toast is None or not self._toast.winfo_exists():
            self._toast = Toast(self)
        self._toast.show(msg)

    # ------------------------------------------------------------------ #
    # Close to tray
    # ------------------------------------------------------------------ #
    def _on_close_request(self) -> None:
        with contextlib.suppress(Exception):
            self._settings.set("window_geometry", self.geometry())
        if not self._tray_available:
            self.shutdown()
            return
        self.withdraw()

    # ------------------------------------------------------------------ #
    # Fullscreen + keyboard navigation
    # ------------------------------------------------------------------ #
    def _toggle_fullscreen(self, exit_only: bool = False) -> None:
        """F11 / ⛶ toggles fullscreen; Escape only exits it."""
        if exit_only and not self._fullscreen:
            return
        self._fullscreen = not self._fullscreen if not exit_only else False
        self.attributes("-fullscreen", self._fullscreen)
        if hasattr(self, "_fullscreen_btn"):
            self._fullscreen_btn.configure(
                text=t("exit_fullscreen") if self._fullscreen
                else t("fullscreen")
            )
        if self._fullscreen:
            self.focus_force()

    def _scroll_page(self, direction: int) -> None:
        """PageUp/PageDown: move the whole UI by one viewport."""
        with contextlib.suppress(Exception):
            self._scroll._parent_canvas.yview_scroll(direction, "pages")

    def _scroll_to(self, frac: float) -> None:
        """Home/End: jump to the top/bottom of the UI."""
        with contextlib.suppress(Exception):
            self._scroll._parent_canvas.yview_moveto(frac)

    def restore(self) -> None:
        self.after(0, lambda: (self.deiconify(), self.lift(),
                               self.focus_force()))

    def shutdown(self) -> None:
        self._closing = True
        if self._drain_after_id:
            with contextlib.suppress(Exception):
                self.after_cancel(self._drain_after_id)
            self._drain_after_id = None
        self._engine.disarm(silent=True)
        if self._badge is not None and self._badge.winfo_exists():
            self._badge.destroy()
        self._badge = None
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._settings_win.destroy()
        self._settings_win = None
        with contextlib.suppress(Exception):
            self.destroy()

    # ------------------------------------------------------------------ #
    # Top-left countdown badge
    # ------------------------------------------------------------------ #
    def _ensure_badge(self) -> CountdownBadge:
        """Lazily create the corner badge (only when actually needed)."""
        if self._badge is None or not self._badge.winfo_exists():
            self._badge = CountdownBadge(self)
        return self._badge

    def _update_badge(self) -> None:
        """Drive the corner countdown from the current state."""
        if not self._badge_enabled:
            if self._badge is not None:
                self._badge.hide()
            return
        if self._overlay is not None and self._overlay.winfo_exists():
            self._ensure_badge().show_remaining(self._overlay._remaining)
            return
        if self._timer_remaining is not None:
            self._ensure_badge().show_remaining(self._timer_remaining)
            return
        if self._badge is not None:
            self._badge.hide()
