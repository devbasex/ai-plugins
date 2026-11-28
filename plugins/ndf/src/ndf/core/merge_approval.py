"""マージの承認の証跡（マーカー `ndf:merge-approval`）の行を組み、読み、判定し、書く（#4409 の決定 7）。

`ndf ci merge`・`ndf ci answer` と、工程の承認ゲート（`progress.gate`）・承認ゲートの直前の MVV 判定（`domains.records.mvv_gate`）・
リリースの手順（`domains.release.trail`）が同じ行の形と判定を使う。工程（`ndf.progress`）とドメイン（`ndf.domains`）の
両方から読むので、その下の層に置く。証跡を書けないときの例外 `TrailError` もここが持つ（担い手の語は `core.approval_words`）。

承認の対象は 4 つの値（ベースリポジトリ・PR の番号・マージ先・HEAD）で、証跡の行はその対象・回答・担い手・
「本番へ反映されるか」・回答の記録の識別子を持つ。
工程の開き直しは、承認した対象へ担い手 `reopen` の却下の行を足して承認を取り消し、PR をマージキューから外す
（`write_revocations`）。残す承認が新しいときは、失効させる承認だけを指す却下の行（失効の行）を足す。
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Callable, Iterable, Iterator, NamedTuple, Sequence

from ndf import identity
from ndf.adapters.github import Authored, GitHub, Viewer, same_repo
from ndf.adapters.publish import Publisher
from ndf.core.approval_words import ANSWERS, APPROVED, BY_CHOICES, BY_LINE, MVV_BY, REJECTED, REOPEN_BY, USER_BY, By  # noqa: F401

# マージの承認の証跡のマーカー（#4409 の決定 7）。リリースの PR の証跡（`ndf:release-approval`）と別で、`ndf ci merge` だけが読む。
# CD の workflow の承認の検査はリリースのマーカーを含むコメントだけで起動するので、この文字列はそれを含まない
MERGE_MARK = f"<!-- {identity.MERGE_MARK_WORD} -->"
MERGE_HEADING = "## マージの承認の証跡"
ANSWER_LINE = "回答:"
TARGET_LINE = "対象:"
PRODUCTION_LINE = "本番へ反映される:"
RECORD_LINE = "回答の記録:"
# 担い手 `reopen` の却下の行が、指した承認だけを失効させるときに持つ行（値は承認を指す値の JSON の並び）
VOIDS_LINE = "失効する承認:"
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
    """マージの承認の証跡の 1 行。`production` は承認資料に示した「本番へ反映されるか」、`record_id` は回答の記録の識別子。

    `url` は PR から読んだ行だけが持つ、行を書いたコメントの URL（本文には書かない）。`voids` は失効の行だけが持つ、
    失効させる承認を指す値（記録の識別子かコメントの URL）。空の却下は、先の承認をすべて取り消す。"""

    answer: str
    by: str
    target: MergeTarget
    production: bool
    record_id: str
    url: str = ""
    voids: frozenset[str] = frozenset()


class Revocation(NamedTuple):
    """取り消す承認。`approvals` は開き直す工程が書いた承認を指す値（回答は記録の識別子、MVV 判定はコメントの URL）、
    `record_id` は取り消しの行の記録の識別子。`unconfirmed` は、投稿を試みたことしか分からない承認（回答が DB に確定して
    いない）で、PR に証跡か失効の行がある場合だけ取り消す。"""

    target: MergeTarget
    approvals: frozenset[str]
    record_id: str
    unconfirmed: bool = False


def presented_record_id(gate_id: str, presentation: str | None) -> str:
    """承認ゲートの回答の記録の識別子（質問の id と提示の識別子。提示の識別子が無ければ質問の id のまま）。"""
    return f"{gate_id}-{presentation}" if presentation else gate_id


def render_merge_row(row: MergeRow) -> str:
    """証跡のコメントの本文（マーカーと行）。"""
    t = row.target
    voids = f"{VOIDS_LINE} {json.dumps(sorted(row.voids), ensure_ascii=False)}\n" if row.voids else ""
    return (
        f"{MERGE_MARK}\n{MERGE_HEADING}\n\n{ANSWER_LINE} {row.answer}\n{BY_LINE} {row.by}\n"
        f"{TARGET_LINE} `{t.repo}` {int(t.number)} `{t.base}` `{t.head}`\n"
        f"{PRODUCTION_LINE} {_YES if row.production else _NO}\n{RECORD_LINE} {row.record_id}\n{voids}"
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
    # 開き直しの担い手は却下の行でだけ数える
    known = by in BY_CHOICES or (by == REOPEN_BY and answer == REJECTED)
    if answer not in ANSWERS or not known or got is None or production not in (_YES, _NO) or not record:
        return None
    if by == MVV_BY and MVV_NOTE_HEADING not in body:
        return None
    t = MergeTarget(got["repo"], int(got["number"]), got["base"], got["head"])
    voids = _parse_voids(body) if by == REOPEN_BY else frozenset()
    return MergeRow(str(answer), str(by), t, production == _YES, str(record), voids=voids)


def _parse_voids(body: str) -> frozenset[str]:
    """失効の行が指す承認。行が無い・読めなければ空（先の承認をすべて取り消す却下として数える）。"""
    held = _line(body, VOIDS_LINE)
    if held is None:
        return frozenset()
    try:
        values = json.loads(held)
    except json.JSONDecodeError:
        return frozenset()
    return frozenset(v for v in values if isinstance(v, str)) if isinstance(values, list) else frozenset()


# token の利用者を読めないときの理由（`approval_stale` と別の止まり方にする。承認し直しても数えられないため）
NO_LOGIN = (
    "この token の利用者のアカウント ID を読めない（GitHub の GraphQL の viewer が空）ので、マージの承認の証跡の書き手を照らせず、"
    "証跡を数えも書きもしない。利用者を読める token（利用者の個人の token か GitHub App の token）で打ち直す"
)


def same_writer(c: Authored, viewer: Viewer) -> bool:
    """コメント `c` の書き手が token の利用者 `viewer` か。GitHub が発行するアカウント ID で比べる。

    名前（login）では比べない。個人アカウントと GitHub App は名前の空間が別で、ボット `<名前>[bot]` と同じ名前の
    個人アカウント `<名前>` を作れるため、名前を寄せて比べると別のアカウントの証跡を数える。"""
    return viewer.account_id > 0 and c.author_id == viewer.account_id


def merge_rows(hub: GitHub, number: int, viewer: Viewer) -> list[MergeRow]:
    """PR のコメントから数える証跡を古い順に返す。書き手が `viewer`（`ndf ci merge` を打つ token の利用者）と同じ
    アカウントで、編集されていないコメントだけを数える（書き込み権限を持つだけの別の人のコメントは数えない）。

    `viewer` のアカウント ID が読めていない（0）なら、数えずに `TrailError`（理由は `NO_LOGIN`）。"""
    _require_login(viewer)
    return [row for c, row in _unedited_rows(hub.comments(number)) if same_writer(c, viewer)]


def _require_login(viewer: Viewer) -> None:
    if viewer.account_id <= 0:
        raise TrailError(NO_LOGIN)


def _unedited_rows(comments: Iterable[Authored]) -> Iterator[tuple[Authored, MergeRow]]:
    """編集されていないコメントの証跡の行と、そのコメント。書き手は問わない。"""
    for c in comments:
        row = None if c.edited else _parse_merge_row(c.body)
        if row is not None:
            yield c, row._replace(url=c.url)


def merge_judgment(rows: Sequence[MergeRow], now: MergeTarget) -> MergeRow | None:
    """今の承認の対象 `now` への、まだ数える承認（`_live_approvals`）のうち最新の 1 件。無ければ None。"""
    live = _live_approvals(rows, now)
    return live[-1] if live else None


def _live_approvals(rows: Sequence[MergeRow], now: MergeTarget) -> list[MergeRow]:
    """`now` への「本番へ反映される: はい」の承認のうち、後の却下で取り消されていないもの（古い順）。

    却下は本番へ反映されない期間のものも先の承認をすべて取り消す。失効の行（`voids` を持つ却下）は、指した承認だけを外す。"""
    live: list[MergeRow] = []
    for r in rows:
        if not r.target.same(now):
            continue
        if r.answer == REJECTED:
            live = [kept for kept in live if not _points_at(r.voids, kept)] if r.voids else []
        elif r.production:
            live.append(r)
    return live


def _points_at(approvals: frozenset[str], row: MergeRow) -> bool:
    """`approvals`（記録の識別子かコメントの URL）が承認の行 `row` を指すか。"""
    return not approvals.isdisjoint((row.record_id, row.url))


def write_merge_row(hub: GitHub, row: MergeRow, viewer: Viewer) -> bool:
    """同じ `record_id` の証跡が無ければ投稿して真、あれば投稿せずに偽（送り直しで重ならない）。値が不正なら `TrailError`。

    `viewer` のアカウント ID が読めていなければ、重なりを確かめられないので投稿せずに `TrailError`（`merge_rows` が投げる）。"""
    if row.answer not in ANSWERS or row.by not in BY_CHOICES:
        raise TrailError(f"#{row.target.number} の証跡の回答 {row.answer!r}・担い手 {row.by!r} を書けない")
    if any(r.record_id == row.record_id for r in merge_rows(hub, row.target.number, viewer)):
        return False
    Publisher(hub).comment(row.target.number, render_merge_row(row))
    return True


def write_revocations(hub: GitHub, revocations: Sequence[Revocation], viewer: Viewer) -> None:
    """対象ごとに、`approvals` の承認を担い手 `reopen` の却下の行で取り消し、PR をマージキューから外す。

    最新の承認が `approvals` の承認なら、先の承認をすべて取り消す却下を投稿する。それ以外（残る承認が別のものか、
    後の通常の却下で承認が無い）なら、`approvals` の承認だけを指す失効の行を投稿する。残す承認や通常の却下のコメントが
    後で編集・削除されても、取り消した承認を数え直さないためである。`approvals` の承認が無いか、担い手 `reopen` の行で
    失効済みなら書かない（`_not_voided`）。

    `unconfirmed` の取り消しは、PR に指した承認の行か失効の行が無ければ（投稿の前に失敗した）、行も書かず、キューから
    外すかの判定にも使わない（`_traced`）。

    キューから外すのは、残る承認の無い PR と、別のアカウントが `approvals` を今も承認と数える PR（キューに入れたのが
    どの承認かは分からない）。ただし PR の今のマージ先と HEAD が取り消す対象と違い、有効な承認を持つ PR は外さない
    （`_queued_on_another`）。行を書いた後に外し（外した後に始まる `ndf ci merge` は入れ直さない）、行を書き終えた
    打ち直しでも外す。

    別のアカウントに `reopen` の行で失効させていない `approvals` の承認があるなら、token の利用者の分を書いてキューから外した後に `TrailError`
    （そのアカウントの `ndf ci merge` は通るため）。同じ識別子の承認を token の利用者が書いていても同じ。コメントの一覧は
    PR ごとに 1 回だけ読み、公開範囲は全体で 1 回だけ照会する。打ち直しで行は増えない。`viewer` のアカウント ID が
    読めていないか、承認の対象のリポジトリが `hub.repo` と違えば、PR のコメントもキューの状態も読み書きせずに `TrailError`。"""
    _require_login(viewer)
    foreign = sorted({r.target.repo for r in revocations if not same_repo(r.target.repo, hub.repo)})
    if foreign:
        raise TrailError(f"承認の対象のリポジトリ {'・'.join(foreign)} が今の {hub.repo} と違う。承認の対象のリポジトリで打ち直す")
    publisher = Publisher(hub)
    read: dict[int, list[tuple[Authored, MergeRow]]] = {}
    leaving: dict[int, None] = {}
    others: dict[str, None] = {}
    applied: list[MergeTarget] = []
    for target, approvals, record_id, unconfirmed in revocations:
        if target.number not in read:
            read[target.number] = list(_unedited_rows(hub.comments(target.number)))
        found = read[target.number]
        if unconfirmed and not _traced(found, target, approvals, record_id):
            continue
        applied.append(target)
        mine = [row for c, row in found if same_writer(c, viewer)]
        live = _live_approvals(mine, target)
        holders = _other_holders(found, viewer, target, approvals)
        others.update(dict.fromkeys(holders))
        latest_revoked = bool(live) and _points_at(approvals, live[-1])
        kept = bool(live) and not latest_revoked
        if not kept or holders:
            leaving[target.number] = None
        if _not_voided(mine, target, approvals):
            voids = frozenset() if latest_revoked else approvals
            row = MergeRow(REJECTED, REOPEN_BY, target, True, record_id, voids=voids)
            publisher.comment(target.number, render_merge_row(row))
    for number in leaving:
        revoked = [t for t in applied if t.number == number]
        hub.dequeue(number, keep=_queued_on_another(hub.repo, number, viewer, read[number], revoked))
    if others:
        names = "・".join(others)
        raise TrailError(f"取り消す承認の証跡を別のアカウント（{names}）が書いている。承認を書いたアカウントで打ち直す")


def _traced(found: Sequence[tuple[Authored, MergeRow]], target: MergeTarget, approvals: frozenset[str], record_id: str) -> bool:
    """`target` への `approvals` の承認の行か、その失効の行（キューから外すのに失敗した打ち直し）が PR にあるか。書き手は問わない。"""
    for _, row in found:
        if not row.target.same(target):
            continue
        if row.answer == APPROVED and _points_at(approvals, row):
            return True
        if row.by == REOPEN_BY and (row.record_id == record_id or not row.voids.isdisjoint(approvals)):
            return True
    return False


def _queued_on_another(
    repo: str, number: int, viewer: Viewer, found: Sequence[tuple[Authored, MergeRow]], revoked: Sequence[MergeTarget]
) -> Callable[[str, str], bool]:
    """PR をキューに残すかを、今のマージ先と HEAD（キューに入っている PR はこれが対象）から決める関数（`GitHub.dequeue` の `keep`）。
    その組が取り消す対象 `revoked` のどれとも一致せず、token の利用者の有効な承認を持つなら残す。"""
    mine = [row for c, row in found if same_writer(c, viewer)]

    def keep(base: str, head: str) -> bool:
        now = MergeTarget(repo, number, base, head)
        return not any(now.same(r) for r in revoked) and bool(_live_approvals(mine, now))

    return keep


def _not_voided(rows: Sequence[MergeRow], target: MergeTarget, approvals: frozenset[str]) -> bool:
    """`rows` に、担い手 `reopen` の行でまだ失効させていない `approvals` の承認があるか。

    通常の却下は数えない。その却下のコメントが後で編集・削除されると、承認がまた効くためである。"""
    counted = [r for r in rows if r.answer == APPROVED or r.by == REOPEN_BY]
    return any(_points_at(approvals, row) for row in _live_approvals(counted, target))


def _other_holders(found: Sequence[tuple[Authored, MergeRow]], viewer: Viewer, target: MergeTarget, approvals: frozenset[str]) -> list[str]:
    """`reopen` の行で失効させていない `approvals` の承認（`_not_voided`）を持つ、別のアカウントの名前。

    識別子はアカウントの間で差し引かない（投稿の後に確定が失敗した回答を別のアカウントで送り直すと、同じ識別子が両方に残る）。"""
    theirs: defaultdict[int, list[tuple[str, MergeRow]]] = defaultdict(list)
    for c, row in found:
        if c.author_id > 0 and not same_writer(c, viewer):
            theirs[c.author_id].append((c.author, row))
    return [written[-1][0] for written in theirs.values() if _not_voided([row for _, row in written], target, approvals)]


class TrailError(Exception):
    """証跡を書けない・書いた回答が判定に届かない・HEAD が提示から動いた・書き手が回答の手順でない。"""
