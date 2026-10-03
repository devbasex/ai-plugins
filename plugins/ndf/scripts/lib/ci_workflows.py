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


def job_ids(text: str) -> list[str]:
    """ワークフローの本文 → `jobs:` の直下の job id の並び（書いた順）。"""
    out: list[str] = []
    inside, indent = False, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            inside = line.rstrip() == "jobs:"
            continue
        if inside:
            indent = lead if indent is None else indent
            if lead == indent and line.strip().endswith(":"):
                out.append(line.strip()[:-1].strip().strip("\"'"))
    return out
