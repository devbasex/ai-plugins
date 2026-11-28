"""課題の文書の名前（#4337 の決定 2）。

設計書と要求仕様書のパスの形・名前に含む課題の番号・実装計画のパスの書式・要求仕様書の「元の Issue」の行を
1 か所で持つ。`ndf loop design-tests`・リファクタリングの既定（`progress.refactor_values`）・ドキュメント再構成
（`domains.docrebuild`）・要求仕様書の解析（`core.requirements_decide`）がここを読む。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ndf.core.plan.inputs_decide import body_lines, outside_fences

# 複数の課題を 1 つの設計で扱う `issue-7-8-design.md` も、番号のどれかが指定と合えば読む（PR 3537 の指摘）
DESIGN_FILE = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-design[^/]*\.md$")
PLAN_FILE = "issues/issue-{number}-plan.md"
# 読む側の実装計画のパス。名前の番号の並びは設計書と同じ
PLAN_PATH = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-plan\.md$")
# 要求仕様書のパス。1 本が複数の課題を持つときは番号を `-` でつなぐ（#4735）
REQUIREMENTS_PATH = re.compile(r"^issues/issue-(\d+(?:-\d+)*)-requirements\.md$")


def _name_numbers(pattern: re.Pattern[str], path: str) -> frozenset[int]:
    m = pattern.match(path)
    return frozenset(int(n) for n in m.group(1).split("-")) if m else frozenset()


def design_issues(path: str) -> frozenset[int]:
    """パスが設計書の形なら、名前の番号の並びを整数の集合で返す。違えば空集合（課題 433 は `issue-4337` に当たらない）。"""
    return _name_numbers(DESIGN_FILE, path)


def plan_issues(path: str) -> frozenset[int]:
    """パスが実装計画の形（`issues/issue-<番号の並び>-plan.md`）なら、名前の番号の集合を返す。違えば空集合。"""
    return _name_numbers(PLAN_PATH, path)


def requirements_issues(path: str) -> frozenset[int]:
    """パスが要求仕様書の形（`issues/issue-<番号の並び>-requirements.md`）なら、名前の番号の集合を返す。違えば空集合。"""
    return _name_numbers(REQUIREMENTS_PATH, path)


def requirements_path(numbers: Iterable[int]) -> str:
    """番号を昇順に `-` でつないだ要求仕様書のパス。"""
    return f"issues/issue-{'-'.join(str(n) for n in sorted(numbers))}-requirements.md"


def representative_issue(path: str) -> int:
    """要求仕様書の名前の番号のうち最も小さい番号（代表の Issue）。要求仕様書の形でなければ `ValueError`。"""
    numbers = requirements_issues(path)
    if not numbers:
        raise ValueError(f"要求仕様書のパスでない: {path}")
    return min(numbers)


def origin_issues(text: str, repo: str) -> frozenset[int]:
    """最初の `##` より前の `元の Issue: https://github.com/<repo>/issues/<N>` の行が指す番号の集合。

    `repo` の大文字と小文字の違いは同じものとして扱う。コードブロックの中の行は読まない。
    """
    line_of = re.compile(rf"元の Issue: https://github\.com/(?i:{re.escape(repo)})/issues/(\d+)\s*")
    found: set[int] = set()
    for _, line in outside_fences(body_lines(text)):
        if line.startswith("##"):
            break
        m = line_of.fullmatch(line)
        if m:
            found.add(int(m.group(1)))
    return frozenset(found)
