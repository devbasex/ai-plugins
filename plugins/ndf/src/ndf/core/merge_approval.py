"""マージの承認の証跡（マーカー `ndf:merge-approval`）の行を組み、読み、判定し、書く（#4409 の決定 7）。

`ndf ci merge`・`ndf ci answer` と、工程の承認ゲート（`progress.gate`）・承認ゲートの直前の MVV 判定（`domains.records.mvv_gate`）・
リリースの手順（`domains.release.trail`）が同じ行の形と判定を使う。工程（`ndf.progress`）とドメイン（`ndf.domains`）の
両方から読むので、その下の層に置く。証跡の担い手の語と、証跡を書けないときの例外 `TrailError` もここが持つ。

承認の対象は 4 つの値（ベースリポジトリ・PR の番号・マージ先・HEAD）で、証跡の行はその対象・回答・担い手・
「本番へ反映されるか」・回答の記録の識別子を持つ。
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import NamedTuple, Sequence

from ndf import identity
from ndf.adapters.github import GitHub
from ndf.adapters.publish import Publisher
from ndf.core.approval_words import ANSWERS, APPROVED

# 証跡の担い手の行。迂回の前提に数えるのは利用者の証跡だけ（#3506 の I9）
BY_LINE = "担い手:"


class By(StrEnum):
    """証跡の担い手（旧いプラグインの `approve --by` と同じ値）。"""

    USER = "user"  # 利用者
    MVV = "mvv"  # MVV 判定


USER_BY, MVV_BY = By.USER.value, By.MVV.value
BY_CHOICES = tuple(b.value for b in By)


# マージの承認の証跡のマーカー（#4409 の決定 7）。リリースの PR の証跡（`ndf:release-approval`）と別で、`ndf ci merge` だけが読む。
# CD の workflow の承認の検査はリリースのマーカーを含むコメントだけで起動するので、この文字列はそれを含まない
MERGE_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:merge-approval -->"
MERGE_HEADING = "## マージの承認の証跡"
ANSWER_LINE = "回答:"
TARGET_LINE = "対象:"
PRODUCTION_LINE = "本番へ反映される:"
RECORD_LINE = "回答の記録:"
_YES, _NO = "はい", "いいえ"
_TARGET = re.compile(r"^`(?P<repo>[^`]+)` (?P<number>\d+) `(?P<base>[^`]+)` `(?P<head>[^`]+)`$")
# 承認資料の、承認でマージが本番へ反映され得ることを示す行（`ndf ci ask`・`ndf ci merge` と工程の承認ゲートの承認資料が使う）
PRODUCTION_MATERIAL_LINE = "この承認で PR がマージされ、本番へ反映され得る"
# 担い手 `mvv` の証跡を数えるのは、MVV 判定の記録（`progress.gate.checks.note` の見出し）と同じコメントだけ
MVV_NOTE_HEADING = "## MVV 判定（"


class MergeTarget(NamedTuple):
    """承認を結び付ける 4 つの値（ベースリポジトリ・PR の番号・マージ先・HEAD）。`repo` は大文字小文字を区別せずに比べる。"""

    repo: str
    number: int
    base: str
    head: str

    def same(self, other: "MergeTarget") -> bool:
        return (self.repo.lower(), self.number, self.base, self.head) == (other.repo.lower(), other.number, other.base, other.head)


class MergeRow(NamedTuple):
    """マージの承認の証跡の 1 行。`production` は承認資料に示した「本番へ反映されるか」、`record_id` は回答の記録の識別子。"""

    answer: str
    by: str
    target: MergeTarget
    production: bool
    record_id: str


def render_merge_row(row: MergeRow) -> str:
    """証跡のコメントの本文（マーカーと行）。"""
    t = row.target
    return (
        f"{MERGE_MARK}\n{MERGE_HEADING}\n\n{ANSWER_LINE} {row.answer}\n{BY_LINE} {row.by}\n"
        f"{TARGET_LINE} `{t.repo}` {int(t.number)} `{t.base}` `{t.head}`\n"
        f"{PRODUCTION_LINE} {_YES if row.production else _NO}\n{RECORD_LINE} {row.record_id}\n"
    )


def _line(body: str, head: str) -> str | None:
    for line in body.splitlines():
        if line.startswith(head):
            return line[len(head) :].strip()
    return None


def _parse_merge_row(body: str) -> MergeRow | None:
    """本文の証跡の行。マーカーが無い・行が読めなければ None（数えない）。"""
    if MERGE_MARK not in body:
        return None
    answer, by, target = _line(body, ANSWER_LINE), _line(body, BY_LINE), _line(body, TARGET_LINE)
    production, record = _line(body, PRODUCTION_LINE), _line(body, RECORD_LINE)
    got = _TARGET.match(target) if target is not None else None
    if answer not in ANSWERS or by not in BY_CHOICES or got is None or production not in (_YES, _NO) or not record:
        return None
    if by == MVV_BY and MVV_NOTE_HEADING not in body:
        return None
    t = MergeTarget(got["repo"], int(got["number"]), got["base"], got["head"])
    return MergeRow(str(answer), str(by), t, production == _YES, str(record))


# token の利用者の login を読めないときの理由（`approval_stale` と別の止まり方にする。承認し直しても数えられないため）
NO_LOGIN = (
    "この token の利用者の login を読めない（GitHub の GraphQL の viewer が空）ので、マージの承認の証跡の書き手を照らせず、"
    "証跡を数えも書きもしない。login を読める token（利用者の個人の token か GitHub App の token）で打ち直す"
)


def same_writer(author: str, login: str) -> bool:
    """コメントの書き手 `author` が token の利用者 `login` か。大文字小文字と、ボットの login の末尾の `[bot]` の有無を区別しない
    （REST のコメントの書き手は `<名前>[bot]`、GraphQL の `viewer` は `<名前>` で返すことがある）。"""

    def key(name: str) -> str:
        return name.lower().removesuffix("[bot]")

    return bool(login) and key(author) == key(login)


def merge_rows(hub: GitHub, number: int, login: str) -> list[MergeRow]:
    """PR のコメントから数える証跡を古い順に返す。書き手が `login`（`ndf ci merge` を打つ token の利用者）と同じで、
    編集されていないコメントだけを数える（書き込み権限を持つだけの別の人のコメントは数えない）。

    `login` が空（token の利用者を読めない）なら、数えずに `TrailError`（理由は `NO_LOGIN`）。"""
    if not login:
        raise TrailError(NO_LOGIN)
    rows = []
    for c in hub.comments(number):
        if c.edited or not same_writer(c.author, login):
            continue
        row = _parse_merge_row(c.body)
        if row is not None:
            rows.append(row)
    return rows


def merge_judgment(rows: Sequence[MergeRow], now: MergeTarget) -> MergeRow | None:
    """今の承認の対象 `now` への、「本番へ反映される: はい」の証跡の最新の 1 件が承認ならその行。無いか却下なら None。"""
    kept = [r for r in rows if r.production and r.target.same(now)]
    if not kept or kept[-1].answer != APPROVED:
        return None
    return kept[-1]


def write_merge_row(hub: GitHub, row: MergeRow, login: str) -> bool:
    """同じ `record_id` の証跡が無ければ投稿して真、あれば投稿せずに偽（送り直しで重ならない）。値が不正なら `TrailError`。

    `login` が空なら、重なりを確かめられないので投稿せずに `TrailError`（`merge_rows` が投げる）。"""
    if row.answer not in ANSWERS or row.by not in BY_CHOICES:
        raise TrailError(f"#{row.target.number} の証跡の回答 {row.answer!r}・担い手 {row.by!r} を書けない")
    if any(r.record_id == row.record_id for r in merge_rows(hub, row.target.number, login)):
        return False
    Publisher(hub).comment(row.target.number, render_merge_row(row))
    return True


class TrailError(Exception):
    """証跡を書けない・書いた回答が判定に届かない・HEAD が提示から動いた・書き手が回答の手順でない。"""
