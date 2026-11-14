"""候補の経路の収集。課題ごとに、候補へ入った経路（`selection_decide.ROUTES`）の集まりを作る。

| 経路 | 入る課題 |
| --- | --- |
| `manual` | 利用者が `--add` で渡した open の課題 |
| `carried` | 前回の候補の収集の持ち越しと、前回の深さの打ち切りの残りのうち、今も open の課題（#1640） |
| `commit-subject` | 起点（`--since-ref`）から HEAD までのコミットの件名が `#番号` で指す課題 |
| `diff-path` | 起点から HEAD までに変わったファイルのパスを本文に持つ課題 |
| `no-milestone` | マイルストーンの無い課題 |
| `unscored` | 見積の記録が無い・見積の後に書き換わった課題 |
| `all` | 見直しの回の open の全件 |
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters.git import Git
from ndf.domains.backlog import estimates_decide, model
from ndf.domains.backlog import selection_decide as sel

_ISSUE_REF = re.compile(r"#(\d+)\b")
# 本文の言及として数えるパスの最短の長さ（短い名前は無関係の語に当たる）
MIN_PATH = 6


class Found:
    """課題ごとの経路の集まりと、収集の途中の注記。"""

    def __init__(self, open_numbers: Iterable[int]) -> None:
        self.open = set(open_numbers)
        self.routes: dict[int, set[str]] = {}
        self.notes: list[str] = []

    def add(self, numbers: Iterable[int], route: str) -> None:
        for n in numbers:
            if n in self.open:
                self.routes.setdefault(n, set()).add(route)


def commit_refs(git: Git, since_ref: str) -> set[int]:
    """起点から HEAD までのコミットの件名が指す番号。"""
    subjects = git.run("log", "--format=%s", f"{since_ref}..HEAD")
    return {int(m) for m in _ISSUE_REF.findall(subjects)}


def path_mentions(issues: Iterable[Mapping[str, Any]], paths: Iterable[str]) -> set[int]:
    """変わったファイルのパスを本文に持つ課題。"""
    needles = [p for p in paths if len(p) >= MIN_PATH]
    return {i["number"] for i in issues if any(p in i["body"] for p in needles)}


class Ask(NamedTuple):
    """候補の収集の求め。"""

    since_ref: Optional[str]
    manual: list[int]
    carried: list[int]
    depth: str


def collect(issues: list[dict[str, Any]], cwd: Path, ask: Ask, stored: Mapping[str, Any]) -> Found:
    """経路ごとに候補を集める。"""
    since_ref, manual, carried, depth = ask
    found = Found(i["number"] for i in issues)
    missing = sorted(set(manual) - found.open)
    if missing:
        found.notes.append("--add のうち open でない課題は外した: " + " ".join(f"#{n}" for n in missing))
    found.add(manual, sel.MANUAL)
    found.add(carried, sel.CARRIED)
    found.add((i["number"] for i in issues if i["milestone"] is None), sel.NO_MILESTONE)
    digests = {i["number"]: model.snapshot_digest(i) for i in issues}
    found.add(estimates_decide.unscored(sorted(found.open), stored, digests), sel.UNSCORED)
    if depth == model.REVIEW:
        found.add(found.open, sel.ALL)
    if since_ref:
        git = Git(cwd)
        found.add(commit_refs(git, since_ref), sel.COMMIT_SUBJECT)
        found.add(path_mentions(issues, git.changed_files(since_ref, "HEAD")), sel.DIFF_PATH)
    return found
