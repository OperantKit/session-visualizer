"""Execute every ```python block in the READMEs so the documented examples keep working.

Blocks of one README run in order in a shared namespace inside a temporary
directory. A block whose first line starts with ``# requires`` needs external
hardware or binaries and is skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READMES = ("README.md", "README.ja.md")
_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _blocks(name: str) -> list[str]:
    blocks = _BLOCK.findall((ROOT / name).read_text(encoding="utf-8"))
    return [b for b in blocks if not b.startswith("# requires")]


@pytest.mark.parametrize("name", READMES)
def test_readme_examples_run(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocks = _blocks(name)
    assert blocks
    monkeypatch.chdir(tmp_path)
    namespace: dict[str, object] = {}
    for index, block in enumerate(blocks):
        exec(compile(block, f"{name}#{index}", "exec"), namespace)
