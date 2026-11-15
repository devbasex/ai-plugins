"""`development-workflow/references/scripts-lookup.md` の、`ndf` の有無を確かめる 1 行の振る舞い（#3569 の不変条件 I10）。

文書の bash のコードブロックのうち `command -v ndf` を含む行を 1 つだけ取り出し、そのまま `bash -c` で打つ。文言は照らさず、
`ndf` が見つかれば通り、見つからなければ終了コード 3 で止まることだけを確かめる。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ndf.adapters import markdown

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DOC = PLUGIN_ROOT / "skills" / "development-workflow" / "references" / "scripts-lookup.md"
NEEDLE = "command -v ndf"


def _lookup_line() -> str:
    text = DOC.read_text(encoding="utf-8")
    in_code = markdown.code_lines(text)
    found = [line for line, code in zip(text.splitlines(), in_code) if code and NEEDLE in line and not line.lstrip().startswith("```")]
    assert len(found) == 1, found
    return found[0]


def _run(line: str, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["/bin/bash", "-c", line], capture_output=True, text=True, env={**os.environ, "PATH": path}, check=False)


def test_passes_when_ndf_is_on_path(tmp_path):
    fake = tmp_path / "ndf"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    got = _run(_lookup_line(), f"{tmp_path}:/usr/bin:/bin")
    assert got.returncode == 0
    assert got.stderr == ""


def test_stops_with_3_when_ndf_is_missing(tmp_path):
    got = _run(_lookup_line(), "/usr/bin:/bin")
    assert got.returncode == 3
    assert got.stderr.strip()
