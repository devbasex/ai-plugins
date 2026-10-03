"""GitHub Actions のワークフローの読み取り（#464 決定 5）。

`jobs:` の直下の job id を、YAML ライブラリを使わずに字下げで拾う。入口のスクリプトに外部パッケージを
持ち込まないためで、解析（`project_lib/measure_ci.py`）と cross-refactoring の `init`（`refactor_lib/ci_coverage.py`）が使う。
標準ライブラリだけで書く。
"""

from __future__ import annotations

import re

# ワークフローの置き場（根からの相対）と、ファイルのパス（根からの相対）
WORKFLOW_DIR = ".github/workflows"
WORKFLOW_PATH = re.compile(re.escape(WORKFLOW_DIR) + r"/[^/]+\.ya?ml$")


def _strip_comment(line: str) -> str:
    """行末のコメント（引用の外で、空白の後に続く `#` から先）を落とす。"""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def job_ids(text: str) -> list[str]:
    """ワークフローの本文 → `jobs:` の直下の job id の並び（書いた順）。"""
    out: list[str] = []
    inside, indent = False, None
    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            inside = line == "jobs:"
            continue
        if inside:
            indent = lead if indent is None else indent
            if lead == indent and line.endswith(":"):
                out.append(line.strip()[:-1].strip().strip("\"'"))
    return out
