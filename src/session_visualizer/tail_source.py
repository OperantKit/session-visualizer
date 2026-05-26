"""Cross-process tail source: read a session log file as the producer appends.

This module provides the cross-process counterpart of
:class:`SessionEventBridge`. The bridge mirrors a same-process
``experiment_core.Session`` into one or more sinks; the tail source
reads a file being written by a *separate* process or machine and
forwards each new record through the same sink → aggregator → snapshot
pipeline.

Use the tail source when:

- the experiment runs on a different machine or an embedded device that
  writes its session log to a shared filesystem;
- the experiment is supervised in a separate Python process whose
  crashes must not bring down the visualizer;
- multiple visualizer clients want to follow the same on-disk log.

Two log formats are supported:

- **OKL v1** (default) — the canonical format produced by
  ``session-recorder``. A TAB-separated text format with a self-
  describing header that declares the per-file event codebook. See
  ``OperantKitLog/spec/wire-format.md`` for the spec.
- **plain JSONL** — one JSON object per line. Useful for embedded
  producers that don't speak OKL v1 and just emit JSON.

Like the rest of the package, the tail source is best-effort:

- Partial trailing lines (writer flushed mid-line) are NOT consumed and
  are re-read on the next poll once the writer completes them.
- Lines that fail to parse are silently skipped.
- The downstream sink may drop events if the aggregator falls behind;
  this is by design and preserves the producer-side non-blocking
  guarantee.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tail readers
# ---------------------------------------------------------------------------


class TailReader(Protocol):
    """Stateful reader that consumes new bytes from a session log file."""

    def read_new(
        self, path: Path, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Read records appended since *offset*; return ``(records, new_offset)``.

        ``new_offset`` is the byte position immediately after the last
        complete line consumed. Implementations MUST NOT advance past a
        partial trailing line.
        """
        ...


def _read_complete_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read raw bytes after *offset* and split into complete lines.

    Drops any partial trailing line (writer flushed mid-line); the next
    poll picks it up once the writer completes the line. Returns
    ``(lines, new_offset)``. ``lines`` strips the terminating newline
    from each entry.
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
        return [], offset
    consumed = last_newline + 1
    new_offset = offset + consumed
    text = raw[:consumed].decode("utf-8", errors="replace")
    return text.splitlines(), new_offset


class JSONLTailReader:
    """Tail reader for plain JSONL files (one JSON object per line).

    Each line MUST be a JSON object. Arrays, scalars, and nulls are
    silently skipped. Lines starting with ``#`` are treated as comments.

    This reader is appropriate for embedded devices or custom producers
    that emit JSON directly without conforming to OKL v1.
    """

    def read_new(
        self, path: Path, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        lines, new_offset = _read_complete_lines(path, offset)
        records: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
        return records, new_offset


class OKLTailReader:
    """Tail reader for OKL v1 (the format produced by ``session-recorder``).

    OKL v1 is a TAB-separated text format with a self-describing header
    that declares a per-file event codebook (see
    ``OperantKitLog/spec/wire-format.md``). The reader:

    1. On the first poll, reads from byte 0 to parse the header. If the
       header is incomplete (writer hasn't flushed the ``# ---``
       terminator yet) the reader returns no records and retries on the
       next poll.
    2. Once the header is parsed, the codebook is cached and the byte
       offset of the body start is computed.
    3. Subsequent polls only read body bytes (no re-parsing of the
       header) and decode each line via the cached codebook.

    Body lines whose event type is not declared in the codebook, or
    whose column shape doesn't match, are silently skipped (consistent
    with ``session-recorder.reader`` ``on_unknown="skip"`` semantics).
    """

    def __init__(self) -> None:
        self._codebook: dict[str, Any] | None = None
        self._body_offset: int = 0

    def read_new(
        self, path: Path, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        try:
            from session_recorder.format import parse_event_line, parse_header
        except ImportError as exc:  # pragma: no cover - exercised when dep missing
            raise RuntimeError(
                "OKLTailReader requires the session-recorder package "
                "(transitively pulled in by session-analyzer)."
            ) from exc

        if self._codebook is None:
            return self._consume_header_then_body(path, offset, parse_header, parse_event_line)
        return self._consume_body(path, offset, parse_event_line)

    def _consume_header_then_body(
        self,
        path: Path,
        requested_offset: int,
        parse_header: Any,
        parse_event_line: Any,
    ) -> tuple[list[dict[str, Any]], int]:
        # Always read from 0 — we need the full header to extract the
        # codebook, regardless of where the source asked us to resume.
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return [], requested_offset
        if not raw:
            return [], requested_offset

        last_newline = raw.rfind(b"\n")
        if last_newline == -1:
            return [], requested_offset

        framed = raw[: last_newline + 1]
        text = framed.decode("utf-8", errors="replace")
        lines_no_terminators = text.splitlines()
        lines_with_terminators = text.splitlines(keepends=True)

        try:
            parsed_header, header_consumed = parse_header(iter(lines_no_terminators))
        except (ValueError, KeyError):
            # Header not yet complete or malformed; retry on next poll.
            return [], requested_offset

        body_byte_offset = sum(
            len(line.encode("utf-8")) for line in lines_with_terminators[:header_consumed]
        )
        self._codebook = parsed_header.codebook
        self._body_offset = body_byte_offset

        # Honour `from_end`-style requests: skip body lines already
        # written before the source was started.
        effective_offset = max(requested_offset, body_byte_offset)
        return self._consume_body(path, effective_offset, parse_event_line)

    def _consume_body(
        self,
        path: Path,
        offset: int,
        parse_event_line: Any,
    ) -> tuple[list[dict[str, Any]], int]:
        if self._codebook is None:  # pragma: no cover - guarded by caller
            return [], offset
        lines, new_offset = _read_complete_lines(path, offset)
        records: list[dict[str, Any]] = []
        for line in lines:
            stripped = line.rstrip("\r")
            if not stripped or not stripped.strip():
                continue
            if stripped.startswith("#"):
                continue
            try:
                rec = parse_event_line(stripped, self._codebook)
            except (KeyError, ValueError):
                continue
            records.append({"type": rec.type, "timestamp": rec.timestamp, **rec.args})
        return records, new_offset


# ---------------------------------------------------------------------------
# Tail source
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TailStats:
    """Immutable snapshot of tail-source counters."""

    bytes_read: int
    records_emitted: int
    poll_count: int


class _Sink(Protocol):
    def emit(self, event: Any) -> None: ...


class LogTailSource:
    """Background thread that tails a session log file into one or more sinks.

    Parameters
    ----------
    path:
        Path to the session log file. May not exist at start time; the
        source retries on every poll until it appears.
    sinks:
        One or more sinks implementing ``emit(event)``. Each parsed
        record dict is forwarded to every sink in order.
    reader:
        Reader implementing :class:`TailReader`. Default is
        :class:`OKLTailReader` (for ``session-recorder`` output). Pass
        :class:`JSONLTailReader` for plain JSONL producers.
    poll_interval:
        Maximum seconds the polling thread sleeps between reads when no
        filesystem-watcher signal is available. Default 0.25 s.
    use_watchdog:
        When True (default) and the optional ``watchdog`` package is
        installed, register a filesystem observer that wakes the
        polling thread on writes. Falls back silently to plain polling
        when watchdog is missing or fails to start.
    start_offset:
        Initial byte offset. Default 0 (read whole file). Pass the
        current file size to skip historical body lines and only stream
        events appended after start (the OKL header is still parsed
        from byte 0 regardless).
    """

    def __init__(
        self,
        path: Path,
        sinks: list[_Sink],
        reader: TailReader | None = None,
        poll_interval: float = 0.25,
        use_watchdog: bool = True,
        start_offset: int = 0,
    ) -> None:
        self._path = Path(path)
        self._sinks = list(sinks)
        self._reader: TailReader = reader if reader is not None else OKLTailReader()
        self._poll_interval = poll_interval
        self._offset = start_offset
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._stats_lock = threading.Lock()
        self._bytes_read = 0
        self._records_emitted = 0
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
            name="session-visualizer-tail",
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
                poll_count=self._poll_count,
            )

    @property
    def offset(self) -> int:
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

        class _Handler(FileSystemEventHandler):  # type: ignore[misc] # watchdog stubs unavailable
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
        except Exception:  # noqa: BLE001 — watchdog failure must not abort the source
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
        try:
            records, new_offset = self._reader.read_new(self._path, self._offset)
        except Exception:  # noqa: BLE001 — a faulty reader must not crash the thread
            logger.debug("reader.read_new failed", exc_info=True)
            return
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
            self._bytes_read += max(0, consumed)
            self._records_emitted += len(records)
        self._offset = new_offset

    # Context manager ---------------------------------------------------
    def __enter__(self) -> LogTailSource:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


__all__ = [
    "JSONLTailReader",
    "LogTailSource",
    "OKLTailReader",
    "TailReader",
    "TailStats",
]
