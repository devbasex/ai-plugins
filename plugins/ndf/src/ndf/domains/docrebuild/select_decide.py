"""組み直す文書の選び方（#4337 の決定 2・4・9、I1）。ファイルも git も読まない。

通常ファイルかどうかと実装計画の本文は、`drive.inspect` が作業場所で確かめて `Inspection` で渡す。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Optional

from ndf.core import issue_docs

KINDS = ("design", "plan", "manuscript")
OMITTED = "省いた文書: 設計書"
ISSUES_DIR = "issues/"


@dataclass(frozen=True)
class Target:
    """対象の文書のパスと種類（`KINDS` のどれか）。"""

    path: str
    kind: str


@dataclass(frozen=True)
class Selection:
    """対象と、外したパスの説明（`<パス>: <理由>`。`warnings` へそのまま載せる）。"""

    targets: tuple[Target, ...]
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class Inspection:
    """`drive.inspect` が作業場所で確かめた結果。

    `regular` は通常ファイルの候補、`present` は作業場所に何かがある候補、`excluded` は `regular` に無い候補の理由、
    `plans` は本文を読めた通常ファイルの実装計画、`unreadable` は本文を読めなかった実装計画の理由。
    """

    regular: frozenset[str]
    present: frozenset[str]
    excluded: Mapping[str, str]
    plans: Mapping[str, str]
    unreadable: Mapping[str, str]


def kind_of(path: str, issues: Collection[int], kinds: Collection[str]) -> Optional[str]:
    """差分のパス 1 つの種類。`kinds` に無い種類と、どれにも当たらないパス（要求仕様のコピー・実装計画を含む）は None。"""
    if issue_docs.design_issues(path) & set(issues):
        kind: Optional[str] = "design"
    elif not path.startswith(ISSUES_DIR) and path.endswith(".md"):
        kind = "manuscript"
    else:
        kind = None
    return kind if kind in kinds else None


def plan_paths(issues: Sequence[int]) -> tuple[str, ...]:
    """課題ごとの実装計画のパス（課題の順・重なりなし）。"""
    return tuple(dict.fromkeys(issue_docs.PLAN_FILE.format(number=n) for n in issues))


def omits_design(text: str) -> bool:
    """最初の `##` の見出しより前に、行頭が `省いた文書: 設計書` で直後が `（` か行末の行があるか。"""
    for line in text.splitlines():
        if line.startswith("##"):
            return False
        if line.startswith(OMITTED) and line[len(OMITTED) :][:1] in ("", "（"):
            return True
    return False


def candidates(changed: Sequence[str], issues: Sequence[int], kinds: Sequence[str]) -> tuple[Target, ...]:
    """差分から選んだ設計書と原稿の後に、`plan` が `kinds` にあれば課題の実装計画を並べる（通常ファイルかは確かめる前）。"""
    found: dict[str, Target] = {}
    for path in changed:
        kind = kind_of(path, issues, kinds)
        if kind is not None and path not in found:
            found[path] = Target(path, kind)
    if "plan" in kinds:
        for path in plan_paths(issues):
            found.setdefault(path, Target(path, "plan"))
    return tuple(found.values())


def select(found: Sequence[Target], seen: Inspection) -> Selection:
    """通常ファイルでない候補と本文を読めなかった実装計画を理由とともに外し、実装計画は設計書を省いたものだけを残す。

    作業場所に無い実装計画（書かない課題）と、省いた行を持たない実装計画は選ぶ条件に合わないだけなので `skipped` に入れない。
    """
    targets: list[Target] = []
    skipped: list[str] = []
    for t in found:
        if t.path not in seen.regular:
            if t.kind != "plan" or t.path in seen.present:
                skipped.append(f"{t.path}: {seen.excluded[t.path]}")
            continue
        if t.kind == "plan":
            if t.path in seen.unreadable:
                skipped.append(f"{t.path}: {seen.unreadable[t.path]}")
                continue
            if not omits_design(seen.plans[t.path]):
                continue
        targets.append(t)
    return Selection(tuple(targets), tuple(skipped))
