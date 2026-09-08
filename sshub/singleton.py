"""Single-instance guard.

Windows: named mutex via ctypes (zero deps). POSIX: exclusive create of
a PID lockfile. Both degrade to "allow" on any failure — a guard must
never be the reason the app fails to start.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys

log = logging.getLogger(__name__)


class SingleInstance:
    def __init__(self, name: str = "SmartShutdownHub") -> None:
        self._handle = None
        self._lock_path = None
        self.acquired = False
        try:
            if sys.platform == "win32":
                import ctypes

                self._handle = ctypes.windll.kernel32.CreateMutexW(
                    None, False, f"Global\\{name}"
                )
                self.acquired = (
                    ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
                )
            else:
                lock = os.path.join(
                    os.environ.get("XDG_RUNTIME_DIR", "/tmp"), f".{name}.lock"
                )
                self._lock_path = lock
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.acquired = True
                atexit.register(self._release)
        except Exception as exc:
            log.debug("single-instance check failed: %s", exc)
            self.acquired = True  # fail open

    def _release(self) -> None:
        try:
            if self._lock_path and os.path.exists(self._lock_path):
                os.unlink(self._lock_path)
        except OSError:
            pass
