"""課題の文書の名前（#4337 の決定 2）。

設計書のパスの形・名前に含む課題の番号・実装計画のパスの書式を 1 か所で持つ。`ndf loop design-tests`・
リファクタリングの既定（`progress.refactor_values`）・ドキュメント再構成（`domains.docrebuild`）がここを読む。
"""

from __future__ import annotations

import re

# 複数の課題を 1 つの設計で扱う `issue-7-8-design.md` も、番号のどれかが指定と合えば読む（PR 3537 の指摘）
DESIGN_FILE = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-design[^/]*\.md$")
PLAN_FILE = "issues/issue-{number}-plan.md"


def design_issues(path: str) -> frozenset[int]:
    """パスが設計書の形なら、名前の番号の並びを整数の集合で返す。違えば空集合（課題 433 は `issue-4337` に当たらない）。"""
    m = DESIGN_FILE.match(path)
    return frozenset(int(n) for n in m.group(1).split("-")) if m else frozenset()
