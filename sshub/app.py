"""Application bootstrap v2: settings, single-instance, capabilities."""

from __future__ import annotations

import contextlib
import logging
import signal
import sys

from . import config
from .core.engine import MonitoringEngine
from .core.hub import IntelligenceHub
from .core.storage import ProfileStore
from .events import EventBus
from .gui.main_window import MainWindow
from .gui.tray import TrayIcon
from .i18n import detect_language, set_language, t
from .settings import Settings
from .singleton import SingleInstance


def _enable_dpi_awareness() -> None:
    """Windows HiDPI: crisp CustomTkinter rendering. Must run BEFORE the
    first Tk root is created or the process stays bitmap-scaled (blurry
    at 125-200% displays). No-op elsewhere; never raises."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(Exception):  # pragma: no cover - Win32 only
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system-DPI aware
    with contextlib.suppress(Exception):  # pragma: no cover - fallback API
        ctypes.windll.user32.SetProcessDPIAware()


def _setup_logging() -> None:
    config.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        ],
    )


def main() -> None:
    if sys.platform != "win32":
        print("Smart Shutdown Hub solo es compatible con Windows.")
        return
    _enable_dpi_awareness()  # must precede any Tk root
    _setup_logging()
    log = logging.getLogger("sshub.app")

    guard = SingleInstance()
    if not guard.acquired:
        log.warning("another instance is running; exiting")
        # No GUI yet: plain print avoids depending on tkinter state.
        print(t("single_instance"))
        return

    settings = Settings()
    set_language(settings.get("language") or detect_language())

    # SSE4.2 capability gate: without it, relax the Smart telemetry
    # cadence to protect entry-level CPUs. Everything else is unchanged.
    from .platform_layer import cpu_sse4_2

    if not cpu_sse4_2():
        config.SMART_TICK_S = 3.0
        log.info("CPU sin SSE4.2: cadencia Smart relajada a %.1fs",
                 config.SMART_TICK_S)
    else:
        log.info("CPU con SSE4.2: cadencia Smart nominal %.1fs",
                 config.SMART_TICK_S)

    bus = EventBus()
    store = ProfileStore()
    engine = MonitoringEngine(bus, store)
    hub = IntelligenceHub(bus, settings)  # advisory only; see core/hub.py
    hub.start()

    window = MainWindow(bus, store, engine, settings, hub=hub)
    tray = TrayIcon(
        restore_cb=window.restore,
        quit_cb=lambda: _quit(window, tray),
        tooltip=f"{config.APP_NAME} v{config.APP_VERSION}",
    )
    tray.start()

    def _sig(_n, _f) -> None:
        _quit(window, tray)

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, _sig)

    # Optional capability report for the log (cheap, cached afterwards).
    from .platform_layer import probe_capabilities

    probe_capabilities()

    log.info("Smart Shutdown Hub started (v%s)", config.APP_VERSION)
    window.mainloop()
    tray.stop()
    hub.stop()
    engine.disarm(silent=True)


def _quit(window: MainWindow, tray: TrayIcon) -> None:
    tray.stop()
    with contextlib.suppress(Exception):
        window.after(0, window.shutdown)


if __name__ == "__main__":
    main()
