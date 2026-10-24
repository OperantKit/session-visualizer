"""The start command in docs/{en,ja}/http-api.md must start a working server.

The command under "Starting the server" is run as written (only the port is
swapped for a free one) and the documented URLs must answer 200.
"""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")
pytest.importorskip("uvicorn")

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _start_command(doc: Path) -> str:
    text = doc.read_text(encoding="utf-8")
    match = re.search(r"^## (?:Starting the server|サーバ起動)\n+```bash\n(.+?)\n```", text, re.M | re.S)
    assert match, f"no start command in {doc}"
    return match.group(1).strip()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get_status(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except OSError:
        return None


@pytest.mark.integration
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_documented_start_command_serves_snapshot(lang: str) -> None:
    command = _start_command(DOCS / lang / "http-api.md")
    port = _free_port()
    argv = shlex.split(command.replace("8765", str(port)))
    bin_dir = Path(sys.executable).parent
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        status = None
        while time.monotonic() < deadline and proc.poll() is None:
            status = _get_status(f"{base}/snapshot")
            if status is not None:
                break
            time.sleep(0.2)
        if status is None:
            proc.kill()
            output = proc.communicate(timeout=5)[0].decode(errors="replace")
            pytest.fail(f"{command!r} did not serve:\n{output}")
        assert status == 200
        assert _get_status(f"{base}/theme.json") == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
