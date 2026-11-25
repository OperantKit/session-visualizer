"""Execute the READMEs' examples so the documented commands keep working.

Every ```python block of one README runs in order in a shared namespace inside
a temporary directory. A block whose first line starts with ``# requires``
needs external hardware or binaries and is skipped.

In the ```bash blocks, every ``session-visualizer replay`` line is then run in
the same directory (so it can read the files the python blocks wrote), and its
output is compared with the ``# <expected output>`` comment on the next line.
``tail`` and ``serve`` lines start a server and are not run.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.ja.md")
_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)
_BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_REPLAY_PREFIX = "session-visualizer replay "
_RUN_CLI = "import sys; from session_visualizer.cli import main; sys.exit(main(sys.argv[1:]))"


def _blocks(name: str) -> list[str]:
    blocks = _BLOCK.findall((ROOT / name).read_text(encoding="utf-8"))
    return [b for b in blocks if not b.startswith("# requires")]


def _replay_commands(name: str) -> list[tuple[list[str], str]]:
    """Return ``(argv after the program name, expected output)`` per replay line."""
    commands: list[tuple[list[str], str]] = []
    for block in _BASH_BLOCK.findall((ROOT / name).read_text(encoding="utf-8")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            if not line.startswith(_REPLAY_PREFIX):
                continue
            following = lines[index + 1] if index + 1 < len(lines) else ""
            assert following.startswith("# "), (
                f"{name}: `{line}` needs its expected output as a `# ...` line below it"
            )
            commands.append((shlex.split(line)[1:], following[2:].strip()))
    return commands


def _run_python_blocks(name: str) -> None:
    blocks = _blocks(name)
    assert blocks
    namespace: dict[str, object] = {}
    for index, block in enumerate(blocks):
        exec(compile(block, f"{name}#{index}", "exec"), namespace)


@pytest.mark.parametrize("name", READMES)
def test_readme_examples_run(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run_python_blocks(name)


@pytest.mark.parametrize("name", READMES)
def test_readme_replay_commands_run(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = _replay_commands(name)
    assert commands, f"{name}: no `session-visualizer replay` line with an expected output"
    monkeypatch.chdir(tmp_path)
    _run_python_blocks(name)
    for argv, expected in commands:
        result = subprocess.run(
            [sys.executable, "-c", _RUN_CLI, *argv],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
