"""Single-instance guard.

Windows: named mutex via ctypes (zero deps). POSIX: exclusive create of
a PID lockfile. Both degrade to "allow" on any failure — a guard must
never be the reason the app fails to start.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _pid_alive(pid: int) -> bool:
    """True when a process with `pid` exists (POSIX signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # EPERM or unknown: assume alive (conservative)
    return True


class SingleInstance:
    def __init__(self, name: str = "SmartShutdownHub") -> None:
        self._handle = None
        self._lock_path = None
        self.acquired = False
        try:
            if sys.platform == "win32":
                import ctypes

                # use_last_error=True: ctypes captures the error before
                # any other call can clobber it (windll.GetLastError is
                # racy because ctypes itself may call Win32 APIs).
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                self._handle = kernel32.CreateMutexW(
                    None, False, f"Global\\{name}"
                )
                self.acquired = (
                    self._handle is not None
                    and ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
                )
            else:
                lock = os.path.join(
                    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f".{name}.lock"
                )
                self._lock_path = lock
                self.acquired = self._acquire_posix(lock)
                if self.acquired:
                    atexit.register(self._release)
        except Exception as exc:
            log.debug("single-instance check failed: %s", exc)
            self.acquired = True  # fail open

    @staticmethod
    def _acquire_posix(lock: str, tries: int = 2) -> bool:
        """Exclusive-create lockfile with stale-lock recovery.

        A crashed run leaves the lockfile behind; the recorded PID is
        probed and a dead owner's lock is reclaimed instead of blocking
        every future start. Only a *live* owner refuses the guard.
        """
        for _ in range(tries):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(Path(lock).read_text().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and _pid_alive(pid):
                    return False  # genuinely another live instance
                with contextlib.suppress(OSError):
                    os.unlink(lock)  # stale: reclaim and retry
                continue
            except OSError:
                return True  # cannot even probe: fail open per design
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            return True
        return True  # reclaim failed: fail open per design

    def _release(self) -> None:
        try:
            if self._lock_path and os.path.exists(self._lock_path):
                os.unlink(self._lock_path)
        except OSError:
            pass
