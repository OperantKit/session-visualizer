"""Fixed-cadence analysis scheduler.

``PeriodicTicker`` fires a callback at a regular wall-clock interval on
its own daemon thread. It complements :class:`FitThrottle` in two ways:

1. **Cadence-driven, not data-driven.** A dashboard that wants to
   refresh a moving-window response rate every 10 s (or a provisional
   generalized-matching slope every 1 min) needs a deterministic tick
   even when nothing has changed. ``FitThrottle`` short-circuits on
   unchanged inputs; the ticker does not.
2. **Best-effort, never queued.** If a tick's callback is still running
   when the next deadline elapses, we increment ``skipped`` and wait for
   the next deadline after the callback returns. We never queue up
   backlog ticks — this preserves the package-wide contract that
   visualization work must not accumulate.

Callback exceptions are swallowed (and logged at DEBUG) so a single bad
fit cannot kill the ticker thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class PeriodicTicker:
    """Fire ``callback`` every ``interval`` seconds on a daemon thread."""

    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        self._interval = interval
        self._callback = callback
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._skipped_lock = threading.Lock()
        self._skipped = 0

    @property
    def skipped(self) -> int:
        """Number of deadlines dropped because the previous tick was still running."""
        with self._skipped_lock:
            return self._skipped

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="session-visualizer-ticker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            # Only clear the reference if the thread actually finished.
            # If join() timed out, leave the reference so a subsequent
            # start() sees the thread as still alive and refuses to
            # double-start rather than racing a second daemon.
            if not thread.is_alive():
                self._thread = None

    def _run(self) -> None:
        next_deadline = time.monotonic() + self._interval
        while not self._stop_event.is_set():
            now = time.monotonic()
            wait = next_deadline - now
            if wait > 0:
                if self._stop_event.wait(timeout=wait):
                    return
            # Deadline reached. Run the callback inline; account for any
            # deadlines missed during a long callback.
            try:
                self._callback()
            except Exception as exc:  # noqa: BLE001 — never kill the ticker
                logger.debug("periodic callback failed: %s", exc)
            now = time.monotonic()
            # Advance next_deadline so it is strictly in the future. Each
            # interval we skip past represents one deadline that elapsed
            # while the callback was running and that we therefore did
            # NOT fire on. The first advance is the normal bookkeeping
            # step (it lines up next_deadline with the next deadline
            # after the one we just fired on) and is not a drop.
            advances = 0
            while next_deadline <= now:
                next_deadline += self._interval
                advances += 1
            dropped = max(0, advances - 1)
            if dropped > 0:
                with self._skipped_lock:
                    self._skipped += dropped

    def __enter__(self) -> PeriodicTicker:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
