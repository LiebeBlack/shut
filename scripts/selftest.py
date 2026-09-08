"""Self-test battery for Smart Shutdown Hub.

Run:
    SSHUB_DRY_RUN=1 python scripts/selftest.py

Covers: i18n, settings, singleton, storage CRUD + migration + import/export,
event bus, executor, timer math, sensor fire-once, smart scoring, engine
arm/disarm + watchdog, and the full GUI lifecycle (render, accordion,
profiles, language rebuild, settings window, overlay cancel).

Exits non-zero if any check fails. Never touches the real OS or user data
(temp dirs, SSHUB_DRY_RUN=1).
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SSHUB_DRY_RUN", "1")
os.environ.setdefault("SSHUB_LANG", "es")

try:  # emoji/log text on legacy Windows consoles
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from sshub import config  # noqa: E402
from sshub.core.engine import MonitoringEngine, _SensorSlot  # noqa: E402
from sshub.core.executor import ActionExecutor  # noqa: E402
from sshub.core.smart import DEFAULT_WEIGHTS, SmartSignals  # noqa: E402
from sshub.core.storage import Profile, ProfileStore  # noqa: E402
from sshub.events import EventBus, EventType  # noqa: E402
from sshub.i18n import detect_language, set_language, t  # noqa: E402
from sshub.sensors import AbsoluteTimerSensor, Sensor  # noqa: E402
from sshub.settings import Settings  # noqa: E402
from sshub.singleton import SingleInstance  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {extra}".rstrip())
        failures.append(label)


def fresh_profile(**kw) -> Profile:
    base = dict(
        id=None, name="T", mode="absolute", countdown_minutes=0, at_time="",
        idle_minutes=30, process_name="", network_max_kbps=50.0,
        thermal_max_c=90.0, battery_min=10, action="shutdown", enabled=1,
    )
    base.update(kw)
    return Profile(**base)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="sshub_selftest_"))

    # ------------------------------------------------------------------ #
    print("[1/9] i18n + settings + singleton")
    set_language("es")
    check("es primary", t("arm") == "▶  ARMAR MONITOREO")
    set_language("en")
    check("en switch", t("arm") == "▶  ARM MONITORING")
    check("missing key fallback", t("__no_such_key__") == "__no_such_key__")
    check("format + fallback", "x" in t("fired_by", src="x"))
    os.environ["SSHUB_LANG"] = "es"
    check("autodetect es", detect_language() == "es")
    os.environ["SSHUB_LANG"] = "en"
    check("autodetect en", detect_language() == "en")
    os.environ["SSHUB_LANG"] = "es"

    s = Settings(tmp / "settings.json")
    s.set("start_minimized", True)
    check("settings persist", Settings(tmp / "settings.json").get("start_minimized"))
    corrupt = tmp / "corrupt.json"
    corrupt.write_text("{not json!!", encoding="utf-8")
    sc = Settings(corrupt)
    check("corrupt settings recover", sc.get("language") == "")
    check("corrupt renamed aside", (tmp / "corrupt.corrupt").exists())

    g1 = SingleInstance("SelftestGuard")
    g2 = SingleInstance("SelftestGuard")
    check("single-instance enforced", g1.acquired and not g2.acquired)

    # ------------------------------------------------------------------ #
    print("[2/9] storage CRUD + migration + import/export")
    db = tmp / "profiles.db"
    store = ProfileStore(db)
    check("default profile seeded", len(store.list_profiles()) >= 1)
    pid = store.add(fresh_profile(name="A", mode="idle", idle_minutes=5))
    check("add returns id", pid is not None)
    check("list finds profile", any(p.name == "A" for p in store.list_profiles()))
    p = store.get(pid)
    assert p is not None
    p.idle_minutes = 9
    store.update(p)
    check("update persists", store.get(pid).idle_minutes == 9)
    store.delete(pid)
    check("delete works", store.get(pid) is None)

    legacy = tmp / "legacy.db"
    import sqlite3

    with sqlite3.connect(legacy) as c:  # v1 schema: no battery_min
        c.execute(
            "CREATE TABLE profiles (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, mode TEXT NOT NULL, "
            "countdown_minutes INTEGER NOT NULL DEFAULT 0, "
            "at_time TEXT NOT NULL DEFAULT '', "
            "idle_minutes INTEGER NOT NULL DEFAULT 30, "
            "process_name TEXT NOT NULL DEFAULT '', "
            "network_max_kbps REAL NOT NULL DEFAULT 50.0, "
            "thermal_max_c REAL NOT NULL DEFAULT 90.0, "
            "action TEXT NOT NULL DEFAULT 'shutdown', "
            "enabled INTEGER NOT NULL DEFAULT 1)"
        )
        c.execute("INSERT INTO profiles (name, mode) VALUES ('Legacy', 'idle')")
    legacy_store = ProfileStore(legacy)
    lp = next(p for p in legacy_store.list_profiles() if p.name == "Legacy")
    check("v1->v2 migration", lp.battery_min == 10 and lp.idle_minutes == 30)

    exp = tmp / "exp.json"
    n = store.export_json(exp)
    check("export writes", n >= 1 and exp.exists())
    before = len(store.list_profiles())
    n = store.import_json(exp)
    check("import skips dups", n == 0 and len(store.list_profiles()) == before)
    store.import_json(exp)  # nothing new; dups skipped

    # ------------------------------------------------------------------ #
    print("[3/9] event bus (anti-leak semantics)")
    bus = EventBus(maxsize=8)
    for i in range(20):
        bus.publish(EventType.STATE, i=i)
    got = bus.drain(100)
    check("bounded queue drops oldest", len(got) <= 8 and bus.dropped > 0)
    check("drain empties", len(bus.drain(100)) == 0)
    st = bus.stats()
    check("bus stats counters", st["published"] >= 20 and st["consumed"] >= 8
          and st["dropped"] > 0)

    # ------------------------------------------------------------------ #
    print("[4/9] executor (dry-run)")
    ex = ActionExecutor(bus)
    ex.request("shutdown", "test", "T")
    check("request pending", ex.pending)
    ev = next(e for e in bus.drain(100) if e.type is EventType.OVERLAY)
    check("overlay payload", ev.payload.get("seconds") == config.OVERLAY_SECONDS)
    ex.request("shutdown", "test", "T")
    check("duplicate request ignored", ex.pending)
    ex.cancel()
    check("cancel clears", not ex.pending)
    check("cooldown latched", ex._cooldown_until > 0)
    ex.request("shutdown", "test", "T")
    check("cooldown mutes re-trigger", not ex.pending)
    ex._cooldown_until = 0.0
    ex.request("reboot", "test", "T")
    ex.finalize()
    check("finalize clears pending", not ex.pending)
    ev = next(e for e in bus.drain(100) if e.type is EventType.EXECUTED)
    check("executed event ok (dry)", ev.payload.get("ok") is True)
    ex._cooldown_until = 0.0
    ex.request("bogus-action", "test", "T")
    check("action validated", ex._pending_action == "shutdown")
    ex.cancel()

    # ------------------------------------------------------------------ #
    print("[5/9] timer math + fire-once semantics")
    abs_ok = AbsoluteTimerSensor._next_wallclock("22:30")
    check("wallclock valid", abs_ok > 0)
    for bad in ("25:00", "12:60", "abc", "12:30:45", ""):
        try:
            AbsoluteTimerSensor._next_wallclock(bad)
            check(f"rejects {bad!r}", False)
        except ValueError:
            check(f"rejects {bad!r}", True)

    fired: list[str] = []
    sen = AbsoluteTimerSensor(
        bus, fresh_profile(countdown_minutes=0), lambda src, act: fired.append(src)
    )
    # Deadline in the PAST: poll() re-reads the real monotonic clock, so
    # the test cannot fake `now`; it must use an already-expired deadline.
    sen._deadline = time.monotonic() - 0.1
    sen._last_poll = 0.0
    sen.poll_if_due(time.monotonic() + 0.5)
    check("timer fires once", fired == ["absolute"] and sen._deadline is None)
    sen.poll_if_due(time.monotonic() + 1.5)
    check("timer never refires", fired == ["absolute"])

    bad_sen = AbsoluteTimerSensor(
        bus, fresh_profile(at_time="99:99"), lambda *a: None
    )
    check("bad at_time stays inert", bad_sen._deadline is None)

    # ------------------------------------------------------------------ #
    print("[6/9] smart scoring")
    s = SmartSignals(idle_s=60, idle_threshold_s=60)
    score, why = s.score(DEFAULT_WEIGHTS)
    check("idle alone below threshold", score == 30 and why == ["idle 60s"])
    s2 = SmartSignals(
        idle_s=60, idle_threshold_s=60, monitor_off=True, net_kbps=1.0,
        net_threshold=50.0, proc_status="exited", temp_c=90, temp_max=90,
    )
    score2, _ = s2.score(DEFAULT_WEIGHTS)
    check("all signals cap at 100", score2 == 100)
    s3 = SmartSignals()
    check("no signals score 0", s3.score(DEFAULT_WEIGHTS) == (0, []))

    # ------------------------------------------------------------------ #
    print("[7/9] intelligence hub + SSE4.2")
    from sshub.core.hub import IntelligenceHub
    from sshub.platform_layer import cpu_sse4_2

    set_language("es")  # the i18n section left the global language on EN

    check("sse4_2 probe is bool", isinstance(cpu_sse4_2(), bool))
    hs = Settings(tmp / "hub_settings.json")
    hub = IntelligenceHub(bus, hs)
    check("hub disabled by default", not hub.enabled)
    hs.set("hub_enabled", True)
    hub.observe(dict(score=85, battery_pct=99, plugged=True, temp_c=61,
                     down_kbps=10))
    hub.set_profile(fresh_profile(idle_minutes=30, network_max_kbps=50,
                                  thermal_max_c=90, battery_min=10))
    adv = hub._local_advice(dict(hub._signals))
    check("local advice high-risk", adv.action == "shutdown"
          and adv.confidence >= 70)
    check("low battery override", hub._local_advice(
        dict(battery_pct=8, plugged=False, battery_min=10)).action
        == "hibernate")
    check("hot override", hub._local_advice(
        dict(temp_c=95, temp_max=90)).action == "shutdown")
    check("busy net stays", hub._local_advice(
        dict(down_kbps=999, net_threshold=50)).action == "stay")
    check("all nominal stays", hub._local_advice(dict()).action == "stay")
    hub._publish(adv)
    hub._publish(adv)  # same signature: must be deduplicated
    advs = [e for e in bus.drain(100) if e.type is EventType.ADVICE]
    check("advice dedup", len(advs) == 1
          and advs[0].payload.get("action") == "shutdown")
    hs.set("hub_enabled", False)
    n_before = len(hub._signals)
    hub.observe(dict(score=99))
    check("disabled hub ignores telemetry", len(hub._signals) == n_before)
    hs.set("hub_enabled", True)

    os.environ["SSHUB_HUB_URL"] = "http://127.0.0.1:9/hub"
    os.environ["SSHUB_HUB_KEY"] = "test-key"
    hub_remote = IntelligenceHub(bus, hs)  # reads env at construction
    check("remote mode active", hub_remote.remote)
    r = hub_remote._remote_advice(dict(score=50))
    check("remote fail-open returns None", r is None)
    logs = [e for e in bus.drain(100) if e.type is EventType.LOG]
    check("fallback logged once",
          any("Hub IA no disponible" in e.payload.get("msg", "")
              for e in logs))
    del os.environ["SSHUB_HUB_URL"]
    del os.environ["SSHUB_HUB_KEY"]

    # ------------------------------------------------------------------ #
    print("[8/9] engine: arm/disarm + watchdog + multi-profile")
    store2 = ProfileStore(tmp / "engine.db")
    eng = MonitoringEngine(bus, store2)
    check("disarmed initially", not eng.state.armed)
    p_smart = fresh_profile(name="S", mode="smart")
    p_abs = fresh_profile(name="A2", mode="absolute", countdown_minutes=0)
    eng.arm([p_abs, p_smart])
    names = [s.sensor.name for s in eng._slots]
    check("smart+thermal sensors", "smart" in names and "thermal_battery" in names)
    smart_slot = next(s for s in eng._slots if s.sensor.name == "smart")
    check("smart uses smart profile", smart_slot.sensor.profile.name == "S")
    check("armed state", eng.state.armed)
    eng.disarm(silent=True)
    check("disarmed state", not eng.state.armed and not eng._slots)

    class Flaky(Sensor):
        name = "flaky"
        tick = 0.2

        def __init__(self, b, profile, fire) -> None:
            super().__init__(b, profile, fire)
            self.count = 0

        def poll(self) -> None:
            self.count += 1
            raise RuntimeError("boom")

    old_tick = config.ENGINE_TICK_S
    config.ENGINE_TICK_S = 0.2
    try:
        eng.disarm(silent=True)
        eng.state.armed = True
        eng.state.profile = p_abs
        orig = Flaky(eng.bus, p_abs, eng._on_trigger)
        eng._slots = [_SensorSlot(orig)]
        eng._stop.clear()
        eng._thread = threading.Thread(target=eng._run, daemon=True,
                                       name="sshub-test")
        eng._thread.start()
        time.sleep(1.4)
        replaced = any(s.sensor is not orig for s in eng._slots)
        eng.disarm(silent=True)
    finally:
        config.ENGINE_TICK_S = old_tick
    check("watchdog replaced sensor", replaced)
    evs = bus.drain(200)  # single drain; filter once, never lose events
    errs = [e for e in evs if e.type is EventType.ERROR]
    check("errors surfaced to GUI", len(errs) >= 3)
    logs = [e for e in evs if e.type is EventType.LOG]
    check("watchdog log published", any("Watchdog" in e.payload.get("msg", "") for e in logs))

    # ------------------------------------------------------------------ #
    print("[9/9] GUI: render, accordion, profiles, arm, language, settings")
    import customtkinter as ctk
    from sshub.gui.main_window import MainWindow
    from sshub.gui.overlay import EmergencyOverlay

    store3 = ProfileStore(tmp / "gui.db")
    settings3 = Settings(tmp / "gui_settings.json")
    settings3.set("language", "es")
    win = MainWindow(bus, store3, MonitoringEngine(bus, store3), settings3)
    win.update()
    check("window renders", win.winfo_exists() and win.winfo_reqwidth() > 400)
    geo = win.geometry()
    check("geometry centered+clamped", "x" in geo and "+" in geo
          and int(geo.split("x")[0]) <= win.winfo_screenwidth())
    check("minsize adaptive", win.winfo_width() >= win.wm_minsize()[0])

    # -- the whole UI must scroll (wheel + scrollbar), nothing clipped -- #
    import sys as _sys

    check("scroll frame present", hasattr(win, "_scroll")
          and win._scroll.winfo_exists())
    win.geometry("540x520")  # shrink below the content height
    win.update()
    check("content overflows viewport",
          win._scroll._parent_canvas.yview()[1] < 1.0)
    if _sys.platform.startswith("win"):
        win._scroll._parent_canvas.event_generate("<MouseWheel>", delta=-120)
    else:
        win._scroll._parent_canvas.event_generate("<Button-5>", x=20, y=20)
    win.update()
    check("wheel scrolls the UI",
          win._scroll._parent_canvas.yview()[0] > 0.0)
    win._scroll._parent_canvas.yview_moveto(1.0)
    win.update()
    check("scrollbar reaches the bottom",
          win._scroll._parent_canvas.yview()[1] >= 1.0)
    win._scroll._parent_canvas.yview_moveto(0.0)
    win.update()
    check("scroll resets to top",
          win._scroll._parent_canvas.yview()[0] == 0.0)

    # -- keyboard navigation of the whole UI ---------------------------- #
    win._scroll._parent_canvas.yview_moveto(1.0)
    win.update()
    win.event_generate("<Prior>")  # PageUp
    win.update()
    check("PageUp scrolls up", win._scroll._parent_canvas.yview()[0] < 1.0)
    win.event_generate("<End>")
    win.update()
    check("End reaches bottom", win._scroll._parent_canvas.yview()[1] >= 1.0)
    win.event_generate("<Home>")
    win.update()
    check("Home reaches top", win._scroll._parent_canvas.yview()[0] == 0.0)

    # -- resize reflow: the UI must track the window at every size ----- #
    win.geometry("1200x700")
    win.update()
    canvas = win._scroll._parent_canvas
    cw = canvas.winfo_width()
    iw = int(canvas.itemcget(win._scroll._create_window_id, "width"))
    check("content stretches full width", iw == cw and cw > 900)
    win.geometry("700x600")
    win.update()
    cw2 = canvas.winfo_width()
    check("canvas shrinks with window", 600 < cw2 < cw)
    win.geometry("580x720")
    win.update()
    cw3 = canvas.winfo_width()
    check("canvas tracks small size", cw3 < cw2)
    check("item width always matches canvas",
          int(canvas.itemcget(win._scroll._create_window_id, "width")) == cw3)
    win._scroll._parent_canvas.yview_moveto(1.0)
    win.update()
    check("scroll works after resizes",
          win._scroll._parent_canvas.yview()[1] >= 1.0)
    win._scroll._parent_canvas.yview_moveto(0.0)
    win.update()

    # -- fullscreen toggle (F11 / ⛶ / Escape) -------------------------- #
    win._toggle_fullscreen()
    win.update()
    check("fullscreen engages", bool(win.attributes("-fullscreen")))
    win._toggle_fullscreen(exit_only=True)  # same as Escape
    win.update()
    check("Escape exits fullscreen", not bool(win.attributes("-fullscreen")))
    win.event_generate("<F11>")
    win.update()
    check("F11 toggles fullscreen", bool(win.attributes("-fullscreen")))
    win._toggle_fullscreen(exit_only=True)
    win.update()
    win.geometry("540x640")
    win.update()
    check("arm button text", win._arm_btn.cget("text") == t("arm"))
    check("tiles present", set(win._tiles) == {"cpu", "ram", "temp", "batt"})

    win._acc_triggers.toggle()
    win.update()
    check("accordion collapses", not win._acc_triggers._expanded)
    win._acc_triggers.toggle()
    win.update()
    check("accordion expands", win._acc_triggers._expanded)

    check("hhmm valid", win._valid_hhmm("22:30") and win._valid_hhmm("9:5"))
    check("hhmm invalid", not win._valid_hhmm("24:00") and not win._valid_hhmm("12:60")
          and not win._valid_hhmm("x"))

    procs = store3.list_profiles()
    check("profiles loaded", len(procs) >= 1)
    win._profile_var.set(procs[0].name)
    win._on_profile_selected(procs[0].name)
    check("profile fields populate", win._countdown_entry.get() == "0")

    win._proc_var.set("")
    win._refresh_processes()
    check("process picker lists", len(win._proc_menu.cget("values")) > 0)

    win._on_arm()
    win.update()
    check("arm engages engine", win._engine.state.armed and win._arm_btn.cget("text") == t("disarm"))
    win._on_arm()
    win.update()
    check("disarm via button", not win._engine.state.armed)

    win._render_state(dict(source="smart", score=85, why=["idle 300s"],
                           cpu_pct=12, ram_pct=40, temp_c=61, battery_pct=99,
                           plugged=True))
    win.update()
    check("dashboard score", win._dash_score.cget("text") == "🧠 85%")
    check("dashboard tiles", "12%" in win._tiles["cpu"].cget("text")
          and "61°" in win._tiles["temp"].cget("text"))

    win._open_menu()
    first = win._settings_win
    check("settings window opens", first is not None and first.winfo_exists())
    win.update()
    check("settings window tall enough", first.winfo_height() >= 400)
    win._open_menu()
    check("settings singleton", win._settings_win is first)
    win._settings_win.destroy()
    win._settings_win = None

    win._on_language("en")
    win.update()
    check("language rebuild", win._arm_btn.cget("text") == t("arm")
          == "▶  ARM MONITORING")
    check("no orphan settings after rebuild", win._settings_win is None)

    bus.publish(EventType.OVERLAY, action="shutdown", source="test",
                seconds=3, profile="T")
    win._drain_events()
    win.update()
    check("overlay created from bus", win._overlay is not None
          and win._overlay.winfo_exists())
    check("overlay countdown shows full", win._overlay._countdown_lbl.cget("text") == "3")
    ov_ref = win._overlay
    ov_ref._on_cancel()
    # Drain the ABORT BEFORE any update() can let the scheduled
    # _drain_events consume it (deterministic ordering, no timing race).
    aborts = [e for e in bus.drain(100) if e.type is EventType.ABORT]
    check("abort event published", len(aborts) >= 1)
    win.update()
    check("overlay cancel destroys", not ov_ref.winfo_exists())
    win._overlay = None
    win._drain_events()

    bus.publish(EventType.ADVICE, action="hibernate", confidence=95,
                reason="Batería crítica — hibernar", origin="local")
    win._drain_events()
    win.update()
    check("advice rendered in dashboard",
          "Batería crítica" in win._dash_advice.cget("text")
          and "95%" in win._dash_advice.cget("text")
          and "Hibernate" in win._dash_advice.cget("text"))
    win._on_advice_click()
    check("advice click applies action",
          win._action_values.get(win._action_var.get()) == "hibernate")

    # -- top-left countdown badge --------------------------------------- #
    check("badge off by default", settings3.get("countdown_overlay") is False)
    win._toggle_badge()
    check("badge toggle persists", settings3.get("countdown_overlay") is True)
    win._render_state(dict(source="absolute", remaining_s=125))
    win._drain_events()
    win.update()
    check("badge shows remaining time", win._badge is not None
          and win._badge.winfo_exists()
          and "02:05" in win._badge._lbl.cget("text"))
    check("badge is top-left corner",
          "+12+12" in win._badge.geometry())
    win._render_state(dict(source="absolute", remaining_s=3725))
    win._drain_events()
    win.update()
    check("badge formats HH:MM:SS",
          "01:02:05" in win._badge._lbl.cget("text"))
    win._set_armed_ui(False)  # disarm clears the timer
    win._drain_events()
    win.update()
    check("badge hides when disarmed",
          win._badge is not None and win._badge._shown is False)
    win._on_language("en")  # rebuild must not break the badge plumbing
    win._drain_events()
    win.update()
    check("badge survives language rebuild",
          win._badge is None or not win._badge.winfo_exists())
    win._toggle_badge()  # back off
    check("badge toggle off", settings3.get("countdown_overlay") is False)
    win._on_language("es")
    win._on_close_request()  # close button: remember geometry + hide to tray
    check("close saves geometry", "x" in str(settings3.get("window_geometry"))
          and win.state() == "withdrawn")
    win.restore()
    win.update()
    check("tray restore shows window", win.state() == "normal")
    win.shutdown()
    try:  # the Tk app itself is gone once the last window is destroyed
        alive = win.winfo_exists()
    except Exception:
        alive = False
    check("window shutdown clean", not alive)

    # start_minimized: window must boot hidden (no login flash)
    sm_settings = Settings(tmp / "sm_settings.json")
    sm_settings.set("start_minimized", True)
    sm_win = MainWindow(
        EventBus(), ProfileStore(tmp / "sm.db"),
        MonitoringEngine(EventBus(), ProfileStore(tmp / "sme.db")),
        sm_settings,
    )
    check("start minimized boots hidden", sm_win.state() == "withdrawn")
    sm_win.shutdown()

    # ------------------------------------------------------------------ #
    print("[10/10] overlay standalone + dry-run finalize")
    root = ctk.CTk()
    root.withdraw()
    bus2 = EventBus()
    ex2 = ActionExecutor(bus2)
    def walk_text(w: object) -> list[str]:
        out: list[str] = []
        for c in w.winfo_children():
            try:
                out.append(str(c.cget("text")))
            except Exception:
                pass
            out.extend(walk_text(c))
        return out

    ov = EmergencyOverlay(root, bus2, ex2, action="shutdown", source="t",
                          seconds=3, dry_run=True)
    root.update()
    check("overlay banner dry-run",
          any("Modo ensayo" in txt for txt in walk_text(ov)))
    ov._on_cancel()
    root.update()
    check("overlay standalone cancel", not ov.winfo_exists())
    ex2.request("hibernate", "t", "T")
    ov2 = EmergencyOverlay(root, bus2, ex2, action="hibernate", source="t",
                           seconds=1, dry_run=True)
    ov2._remaining = 0
    ov2._tick()
    root.update()
    check("overlay finalize (dry)", not ex2.pending and not ov2.winfo_exists())
    root.destroy()

    # ------------------------------------------------------------------ #
    print()
    if failures:
        print(f"{len(failures)} FAILURES: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())