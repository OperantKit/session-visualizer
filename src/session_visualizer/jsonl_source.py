"""JSONL tail source for cross-process session visualization.

Provides a non-blocking input source that reads new bytes from a JSONL
file as the producer (`session-recorder`, an embedded device, a separate
machine writing to a shared filesystem, ...) appends them, and forwards
each parsed event dict to one or more :class:`NonBlockingEventSink`
instances.

This is the cross-process counterpart of :class:`SessionEventBridge`.
Both feed the same sink → :class:`LiveAggregator` → snapshot pipeline,
so the SSE / HTTP surface is identical regardless of input source.
Use this module when the experiment runs in a different process or on
different hardware than the visualizer; use :class:`SessionEventBridge`
when both live in the same Python process.

Like the rest of the package, the tail source is best-effort:

- Partial trailing lines (writer flushed mid-line) are NOT consumed and
  are re-read on the next poll once the writer completes them.
- Lines that fail JSON parsing are silently skipped (counted in
  :class:`TailStats`).
- The sink may drop events when the aggregator falls behind; that is
  by design and preserves the experiment-side non-blocking guarantee.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def read_new(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read newly appended JSONL records starting at *offset*.

    Reads only bytes after *offset* via ``seek``/``read``, avoiding a
    full re-read on every poll. Only bytes up to and including the last
    newline are consumed; a partial trailing line is left in the buffer
    so it can be re-read on the next call once the writer flushes.

    Lines starting with ``#`` are treated as header / comment lines and
    skipped (compatible with OKL v1 headers from ``session-recorder``).

    Args:
        path: Path to the JSONL file. Need not exist; a missing file is
            treated as "no new bytes".
        offset: Byte offset from which to start reading.

    Returns:
        ``(records, new_offset)`` where:

        - ``records`` is a list of parsed JSON dicts, one per complete
          new line. Lines that fail JSON parsing or are not objects are
          dropped.
        - ``new_offset`` is the byte position immediately after the last
          complete line (i.e. after the last ``\\n``). It lags the file
          size when the buffer ends mid-line.
    """
    try:
        with path.open("rb") as f:
            f.seek(offset)
            raw = f.read()
    except FileNotFoundError:
        return [], offset

    if not raw:
        return [], offset

    last_newline = raw.rfind(b"\n")
    if last_newline == -1:
        # No complete line yet — keep the partial buffer for next poll.
        return [], offset

    consumed = last_newline + 1
    new_offset = offset + consumed
    text = raw[:consumed].decode("utf-8", errors="replace")

    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records, new_offset


@dataclass(frozen=True)
class TailStats:
    """Immutable snapshot of tail-source counters."""

    bytes_read: int
    """Total bytes consumed (across complete lines) since start."""
    records_emitted: int
    """Total dicts forwarded to sinks."""
    parse_errors: int
    """Lines dropped due to JSON decode failure or non-object payload."""
    poll_count: int
    """Number of poll cycles executed by the background thread."""


class _Sink(Protocol):
    def emit(self, event: Any) -> None: ...


class JSONLTailSource:
    """Background thread that tails a JSONL file into one or more sinks.

    Designed for the case where the experiment process is separate from
    the visualizer process — different machine, embedded device writing
    to a shared mount, supervised subprocess, etc. — and writes its
    event stream to a JSONL file the visualizer can read.

    Parameters
    ----------
    path:
        Path to the JSONL file. May not exist at start time; the source
        retries on every poll until it appears.
    sinks:
        One or more sinks implementing ``emit(event)``. Each parsed
        record dict is forwarded to every sink in order.
    poll_interval:
        Maximum seconds the polling thread sleeps between reads when
        no filesystem watcher signal is available. Default 0.25 s.
    use_watchdog:
        When True (default) and the optional ``watchdog`` package is
        installed, register a filesystem observer that wakes the
        polling thread on writes (lower latency than time-based
        polling alone). Falls back silently to plain polling when
        watchdog is missing or fails to start.
    start_offset:
        Initial byte offset. Default 0 (read whole file). Pass the
        current file size to skip historical lines and only stream
        events appended after start.
    """

    def __init__(
        self,
        path: Path,
        sinks: list[_Sink],
        poll_interval: float = 0.25,
        use_watchdog: bool = True,
        start_offset: int = 0,
    ) -> None:
        self._path = Path(path)
        self._sinks = list(sinks)
        self._poll_interval = poll_interval
        self._offset = start_offset
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats_lock = threading.Lock()
        self._bytes_read = 0
        self._records_emitted = 0
        self._parse_errors = 0
        self._poll_count = 0
        self._observer: Any | None = None
        self._use_watchdog = use_watchdog

    # Lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        if self._use_watchdog:
            self._try_start_watchdog()
        self._thread = threading.Thread(
            target=self._run,
            name="session-visualizer-jsonl-tail",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=timeout)
            except Exception:  # noqa: BLE001 — defensive shutdown
                logger.debug("watchdog observer shutdown failed", exc_info=True)
            self._observer = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # Read-side ---------------------------------------------------------
    def stats(self) -> TailStats:
        with self._stats_lock:
            return TailStats(
                bytes_read=self._bytes_read,
                records_emitted=self._records_emitted,
                parse_errors=self._parse_errors,
                poll_count=self._poll_count,
            )

    @property
    def offset(self) -> int:
        """Current byte offset into the file. Monotonic."""
        return self._offset

    # Internal ----------------------------------------------------------
    def _try_start_watchdog(self) -> None:
        try:
            from watchdog.events import (  # type: ignore[import-not-found]
                FileSystemEvent,
                FileSystemEventHandler,
            )
            from watchdog.observers import Observer  # type: ignore[import-not-found]
        except ImportError:
            logger.debug("watchdog not installed; falling back to polling")
            return

        target = self._path.resolve()
        wake = self._wake_event

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                if Path(str(event.src_path)).resolve() == target:
                    wake.set()

            def on_created(self, event: FileSystemEvent) -> None:
                if event.is_directory:
                    return
                if Path(str(event.src_path)).resolve() == target:
                    wake.set()

        watch_dir = self._path.resolve().parent
        try:
            watch_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.debug("cannot prepare watch dir %s", watch_dir, exc_info=True)
            return

        observer = Observer()
        observer.schedule(_Handler(), str(watch_dir), recursive=False)
        observer.daemon = True
        try:
            observer.start()
        except Exception:  # noqa: BLE001 — defensive: watchdog failure must not abort the source
            logger.debug("watchdog observer start failed", exc_info=True)
            return
        self._observer = observer

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            woke = self._wake_event.wait(self._poll_interval)
            if woke:
                self._wake_event.clear()
        # Final drain so tail bytes flushed just before stop() are not lost.
        self._poll_once()

    def _poll_once(self) -> None:
        records, new_offset = read_new(self._path, self._offset)
        consumed = new_offset - self._offset
        if records:
            for rec in records:
                for sink in self._sinks:
                    try:
                        sink.emit(rec)
                    except Exception:  # noqa: BLE001 — a faulty sink must not crash the source
                        logger.debug("sink.emit failed", exc_info=True)
        with self._stats_lock:
            self._poll_count += 1
            self._bytes_read += consumed
            self._records_emitted += len(records)
        self._offset = new_offset

    # Context manager ---------------------------------------------------
    def __enter__(self) -> JSONLTailSource:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
