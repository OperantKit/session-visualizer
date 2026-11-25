"""CLI input errors are one readable line on stderr with exit code 2, never a traceback."""

from __future__ import annotations

from pathlib import Path

import pytest

from session_visualizer.cli import main

OKL = '# OKL v1\n# session_name = "fr3"\n# ---\n0.0\tstart\n'


def _err_lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    return [line for line in captured.err.splitlines() if line]


def test_replay_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "events.jsonl"
    assert main(["replay", str(path)]) == 2
    assert _err_lines(capsys) == [f"session-visualizer: file not found: {path}"]


def test_replay_okl_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "fr3.txt"
    path.write_text(OKL, encoding="utf-8")
    assert main(["replay", str(path)]) == 2
    lines = _err_lines(capsys)
    assert len(lines) == 1
    assert lines[0].startswith(f"session-visualizer: {path}:1: not JSONL")
    assert "OKL" in lines[0] and "tail" in lines[0]


def test_replay_non_json_line_reports_line_number(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"type": "response", "timestamp": 0.0}\n\nnot json\n', encoding="utf-8")
    assert main(["replay", str(path)]) == 2
    lines = _err_lines(capsys)
    assert lines == [f"session-visualizer: {path}:3: not JSONL (expected one JSON object per line)"]


def test_replay_json_that_is_not_an_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")
    assert main(["replay", str(path)]) == 2
    assert _err_lines(capsys) == [
        f"session-visualizer: {path}:1: not JSONL (expected one JSON object per line)"
    ]


def test_replay_valid_jsonl_still_works(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        '{"type": "response", "timestamp": 0.0}\n{"type": "response", "timestamp": 0.0}\n',
        encoding="utf-8",
    )
    assert main(["replay", str(path), "--speed", "1000"]) == 0
    captured = capsys.readouterr()
    assert "responses=2" in captured.out
    assert captured.err == ""


def test_tail_jsonl_given_okl_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "fr3.txt"
    path.write_text(OKL, encoding="utf-8")
    assert main(["tail", str(path), "--format", "jsonl"]) == 2
    lines = _err_lines(capsys)
    assert len(lines) == 1
    assert lines[0].startswith(f"session-visualizer: {path}:1: not JSONL")
    assert "--format okl" in lines[0]
