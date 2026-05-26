"""Tests for the cross-process tail source.

Exercises the OKL v1 reader, the JSONL reader, and the LogTailSource
pump. The pump tests use very short poll intervals so the suite stays
fast.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from experiment_core import ResponseEvent
from session_recorder.format import (
    CANONICAL_CODEBOOK,
    HEADER_TERMINATOR,
    MAGIC,
    encode_event,
)

from session_visualizer.sink import NonBlockingEventSink
from session_visualizer.tail_source import (
    JSONLTailReader,
    LogTailSource,
    OKLTailReader,
)

# ---------------------------------------------------------------------------
# OKL helpers
# ---------------------------------------------------------------------------


def _okl_header_text() -> str:
    """Build a minimal valid OKL v1 header using the canonical codebook."""
    lines = [
        MAGIC,
        '# session_name = "test"',
        '# clock_type = "TestClock"',
        "# session_start = 0.0",
        "# events:",
    ]
    for type_name, fs in CANONICAL_CODEBOOK.items():
        rendered = " ".join(f"{f.name}:{f.ty}{'?' if f.optional else ''}" for f in fs)
        lines.append(f"#   {type_name:<18}: {rendered}")
    lines.append(HEADER_TERMINATOR)
    return "\n".join(lines) + "\n"


def _okl_body(events: list[ResponseEvent]) -> str:
    return "".join(encode_event(ev) + "\n" for ev in events)


def _write_okl(path: Path, events: list[ResponseEvent]) -> int:
    text = _okl_header_text() + _okl_body(events)
    path.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# JSONLTailReader
# ---------------------------------------------------------------------------


def test_jsonl_missing_file_returns_empty(tmp_path: Path) -> None:
    reader = JSONLTailReader()
    records, offset = reader.read_new(tmp_path / "absent.jsonl", 0)
    assert records == []
    assert offset == 0


def test_jsonl_reads_complete_lines(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    payload = (
        json.dumps({"type": "response", "timestamp": 1.0, "id": 1})
        + "\n"
        + json.dumps({"type": "response", "timestamp": 1.5, "id": 2})
        + "\n"
    )
    p.write_text(payload)
    reader = JSONLTailReader()
    records, new_offset = reader.read_new(p, 0)
    assert len(records) == 2
    assert records[0]["type"] == "response"
    assert records[0]["timestamp"] == 1.0
    assert new_offset == len(payload.encode("utf-8"))


def test_jsonl_drops_partial_trailing_line(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    complete = json.dumps({"type": "response", "timestamp": 1.0}) + "\n"
    partial = '{"type": "respo'
    p.write_text(complete + partial)
    reader = JSONLTailReader()
    records, new_offset = reader.read_new(p, 0)
    assert len(records) == 1
    # Offset must NOT include the partial line — it will be re-read.
    assert new_offset == len(complete.encode("utf-8"))


def test_jsonl_incremental_offset(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    line1 = json.dumps({"type": "response", "timestamp": 1.0}) + "\n"
    p.write_text(line1)
    reader = JSONLTailReader()
    records1, offset1 = reader.read_new(p, 0)
    assert len(records1) == 1
    line2 = json.dumps({"type": "response", "timestamp": 2.0}) + "\n"
    with p.open("a") as f:
        f.write(line2)
    records2, offset2 = reader.read_new(p, offset1)
    assert len(records2) == 1
    assert records2[0]["timestamp"] == 2.0
    assert offset2 == offset1 + len(line2.encode("utf-8"))


def test_jsonl_skips_invalid_lines(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    p.write_text(
        json.dumps({"type": "response", "timestamp": 1.0})
        + "\n"
        + "not json at all\n"
        + json.dumps([1, 2, 3])  # array, not object
        + "\n"
        + json.dumps({"type": "response", "timestamp": 2.0})
        + "\n"
    )
    reader = JSONLTailReader()
    records, _ = reader.read_new(p, 0)
    assert len(records) == 2
    assert records[0]["timestamp"] == 1.0
    assert records[1]["timestamp"] == 2.0


def test_jsonl_skips_comment_lines(tmp_path: Path) -> None:
    p = tmp_path / "events.jsonl"
    p.write_text(
        "# generator: embedded-rig-7\n"
        + json.dumps({"type": "response", "timestamp": 1.0})
        + "\n"
    )
    reader = JSONLTailReader()
    records, _ = reader.read_new(p, 0)
    assert len(records) == 1


# ---------------------------------------------------------------------------
# OKLTailReader
# ---------------------------------------------------------------------------


def test_okl_missing_file_returns_empty(tmp_path: Path) -> None:
    reader = OKLTailReader()
    records, offset = reader.read_new(tmp_path / "absent.txt", 0)
    assert records == []
    assert offset == 0


def test_okl_reads_header_and_body_from_zero(tmp_path: Path) -> None:
    p = tmp_path / "session.txt"
    size = _write_okl(
        p,
        [ResponseEvent(id=1, timestamp=1.0), ResponseEvent(id=2, timestamp=1.5)],
    )
    reader = OKLTailReader()
    records, new_offset = reader.read_new(p, 0)
    assert len(records) == 2
    assert records[0]["type"] == "response"
    assert records[0]["timestamp"] == 1.0
    assert records[0]["id"] == 1
    assert new_offset == size


def test_okl_incremental_only_new_body_lines(tmp_path: Path) -> None:
    p = tmp_path / "session.txt"
    size1 = _write_okl(p, [ResponseEvent(id=1, timestamp=1.0)])
    reader = OKLTailReader()
    records1, offset1 = reader.read_new(p, 0)
    assert len(records1) == 1

    extra = _okl_body([ResponseEvent(id=2, timestamp=2.0), ResponseEvent(id=3, timestamp=2.5)])
    with p.open("a", encoding="utf-8") as f:
        f.write(extra)

    records2, offset2 = reader.read_new(p, offset1)
    assert len(records2) == 2
    assert records2[0]["id"] == 2
    assert records2[1]["id"] == 3
    assert offset2 == size1 + len(extra.encode("utf-8"))


def test_okl_partial_trailing_body_line_not_consumed(tmp_path: Path) -> None:
    p = tmp_path / "session.txt"
    text = _okl_header_text() + _okl_body([ResponseEvent(id=1, timestamp=1.0)])
    # Append a torn (no LF) prefix
    p.write_text(text + "2.0\trespo", encoding="utf-8")
    reader = OKLTailReader()
    records, new_offset = reader.read_new(p, 0)
    assert len(records) == 1
    # Offset must stop at the LF that ends the body line we did parse,
    # so the partial 2.0\trespo prefix is re-read on the next poll.
    assert new_offset == len(text.encode("utf-8"))


def test_okl_incomplete_header_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "session.txt"
    # Header without the `# ---` terminator yet.
    incomplete = "\n".join(
        [
            MAGIC,
            '# session_name = "test"',
            '# clock_type = "TestClock"',
            "# session_start = 0.0",
        ]
    ) + "\n"
    p.write_text(incomplete, encoding="utf-8")
    reader = OKLTailReader()
    records, offset = reader.read_new(p, 0)
    assert records == []
    # No advance until header completes.
    assert offset == 0


def test_okl_from_end_skips_existing_body(tmp_path: Path) -> None:
    p = tmp_path / "session.txt"
    size = _write_okl(p, [ResponseEvent(id=1, timestamp=1.0)])
    reader = OKLTailReader()
    # Caller starts from end-of-file (mimicking `--from-end`).
    records, new_offset = reader.read_new(p, size)
    assert records == []
    # Header parsing happens internally; the new offset is at file end.
    assert new_offset == size

    # Append a new body line and verify it is picked up.
    extra = _okl_body([ResponseEvent(id=2, timestamp=2.0)])
    with p.open("a", encoding="utf-8") as f:
        f.write(extra)
    records2, _ = reader.read_new(p, new_offset)
    assert len(records2) == 1
    assert records2[0]["id"] == 2


# ---------------------------------------------------------------------------
# LogTailSource pump
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_logtailsource_emits_jsonl_into_sink(tmp_path: Path) -> None:
    p = tmp_path / "stream.jsonl"
    p.write_text("")  # create empty
    sink = NonBlockingEventSink(maxsize=64)
    source = LogTailSource(
        p,
        sinks=[sink],
        reader=JSONLTailReader(),
        poll_interval=0.05,
        use_watchdog=False,
    )
    source.start()
    try:
        # Producer thread writes 5 lines with small gaps to simulate a
        # remote process.
        def _producer() -> None:
            for i in range(5):
                with p.open("a") as f:
                    f.write(json.dumps({"type": "response", "timestamp": float(i)}) + "\n")
                time.sleep(0.02)

        t = threading.Thread(target=_producer)
        t.start()
        t.join()
        # Give the source one or two polls to drain.
        deadline = time.monotonic() + 1.5
        while sink.qsize() < 5 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        source.stop(timeout=1.0)

    drained = sink.drain()
    assert len(drained) == 5
    for i, ev in enumerate(drained):
        assert ev["timestamp"] == float(i)


@pytest.mark.integration
def test_logtailsource_emits_okl_into_sink(tmp_path: Path) -> None:
    p = tmp_path / "session.txt"
    # Pre-write the header so the OKL reader can consume it on first poll.
    p.write_text(_okl_header_text(), encoding="utf-8")
    sink = NonBlockingEventSink(maxsize=64)
    source = LogTailSource(
        p,
        sinks=[sink],
        reader=OKLTailReader(),
        poll_interval=0.05,
        use_watchdog=False,
    )
    source.start()
    try:

        def _producer() -> None:
            for i in range(4):
                with p.open("a", encoding="utf-8") as f:
                    f.write(encode_event(ResponseEvent(id=i + 1, timestamp=float(i))) + "\n")
                time.sleep(0.02)

        t = threading.Thread(target=_producer)
        t.start()
        t.join()
        deadline = time.monotonic() + 1.5
        while sink.qsize() < 4 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        source.stop(timeout=1.0)

    drained = sink.drain()
    assert len(drained) == 4
    for i, ev in enumerate(drained):
        assert ev["type"] == "response"
        assert ev["timestamp"] == float(i)
        assert ev["id"] == i + 1


def test_logtailsource_stats_track_progress(tmp_path: Path) -> None:
    p = tmp_path / "stream.jsonl"
    payload = json.dumps({"type": "response", "timestamp": 1.0}) + "\n"
    p.write_text(payload)
    sink = NonBlockingEventSink(maxsize=64)
    source = LogTailSource(
        p,
        sinks=[sink],
        reader=JSONLTailReader(),
        poll_interval=0.05,
        use_watchdog=False,
    )
    source.start()
    try:
        deadline = time.monotonic() + 1.0
        while source.stats().records_emitted < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        source.stop(timeout=1.0)
    stats = source.stats()
    assert stats.records_emitted == 1
    assert stats.bytes_read == len(payload.encode("utf-8"))
    assert stats.poll_count >= 1


def test_logtailsource_context_manager(tmp_path: Path) -> None:
    p = tmp_path / "stream.jsonl"
    p.write_text(json.dumps({"type": "response", "timestamp": 1.0}) + "\n")
    sink = NonBlockingEventSink(maxsize=64)
    with LogTailSource(
        p,
        sinks=[sink],
        reader=JSONLTailReader(),
        poll_interval=0.05,
        use_watchdog=False,
    ) as source:
        deadline = time.monotonic() + 1.0
        while sink.qsize() < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert source.stats().records_emitted >= 1
    # Source must be stopped on context exit.
    assert sink.drain()  # at least one event
