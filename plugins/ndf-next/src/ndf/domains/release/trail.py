"""リリースの PR の承認の証跡を読み書きし、判定を PR の先端の commit status へ付ける。

- 提示（`present`）: 承認資料を、提示した先端の SHA を添えて PR へコメントする（会話のセッションが打つ）。資料が対象にした
  コミットが今の先端でなければ提示しない
- 答え（`answer`）: 人の答えを証跡のコメントに書き、書いた後に PR を読み直して、答えが今の先端への判定として
  届いたことを確かめる。届いていなければ `TrailError`（答えたつもりで止まり続けない）
- 確かめ（`check`）: 証跡を読んで判定し、判定を commit status `approval_decide.STATUS_CONTEXT` に付ける（workflow が打つ）
- 迂回の記録（`write_admin_record`）: 本番の PR を管理者の迂回でマージしたことを、照らした証跡を指すコメントで残す（#3506）。
  印 `ADMIN_MARK` は証跡の印と別で、証跡の判定に数えない。旧いプラグイン（`release_lib/approval_trail.py`）と同じ印と行を持つ
- 人のマージ（`answer_merged`・`write_merge_record`）: 利用者が GitHub で本番の PR をマージしたら、マージした先端への承認の
  証跡（承認の経路 `github-merge`）と、人のマージの記録（印 `MERGE_MARK`）を書く（#3572 の決定 4・8）

証跡は承認の経路の行（`ROUTE_LINE`）を持つ。行の無い証跡は承認ゲートの答え（`gate`）と読む。

答えは提示した先端へのもので、答えるときに先端が動いていれば書かずに断る（見ていないコミットを承認しない）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

import re

from ndf import identity
from ndf.adapters.github import Authored, GitHub, GitHubError, Pull
from ndf.adapters.publish import Publisher
from ndf.domains.release import approval_decide, merge_decide

# 迂回の記録の印と行の見出し（旧いプラグインと同じ文字列。作り直しは旧いプラグインを読み込まないため別に持つ）
ADMIN_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:admin-merge -->"
ADMIN_SHA_LINE = "マージした先端:"
ADMIN_TRAIL_LINE = "照らした承認の証跡:"
# 証跡の担い手の行。迂回の前提に数えるのは利用者の証跡だけ（#3506 の I9）
BY_LINE = "担い手:"
# 承認の経路と承認した人の行（証跡と人のマージの記録が共有する。#3572）
ROUTE_LINE = "承認の経路:"
APPROVER_LINE = "承認した人:"
# 人のマージの記録の印と行の見出し（マージした先端の行は迂回の記録と同じ見出しを使う）
MERGE_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:merge-approval -->"
MERGED_HEAD_LINE = ADMIN_SHA_LINE
MERGE_SHA_LINE = "マージコミット:"
GATE_HEAD_LINE = "承認ゲートの承認の先端:"


class By(StrEnum):
    """証跡の担い手（旧いプラグインの `approve --by` と同じ値）。"""

    USER = "user"  # 利用者
    MVV = "mvv"  # MVV 判定


USER_BY, MVV_BY = By.USER.value, By.MVV.value
BY_CHOICES = tuple(b.value for b in By)
_PROJECT_FILE = f"{identity.FIXED_WORDS['declaration_dir']}/project.json"
_SHA = re.compile(r"\b([0-9a-f]{40})\b")

# 判定 → commit status の状態（承認だけが success。待ちは pending で必須のチェックがマージを塞ぐ）
STATUS_STATE = {
    approval_decide.APPROVED: "success",
    approval_decide.REJECTED: "failure",
    approval_decide.STALE: "pending",
    approval_decide.MISSING: "pending",
}


class TrailError(Exception):
    """証跡を書けない・書いた答えが判定に届かない・先端が提示から動いた。"""


class NotMerged(Exception):
    """本番の PR がまだマージされていない（人のマージを承認として書けない）。"""


class Read(NamedTuple):
    pull: Pull
    judgment: approval_decide.Judgment


def _line_of(body: str, label: str) -> str | None:
    """本文の見出し `label` の行の値（最初の 1 行）。無ければ None。"""
    for line in body.splitlines():
        if line.startswith(label):
            return line[len(label) :].strip()
    return None


def _by_of(body: str) -> str | None:
    """証跡の本文の担い手の行の値。無ければ None。"""
    return _line_of(body, BY_LINE)


def _route_of(body: str) -> str:
    """証跡の承認の経路（行が無い・知らない値なら承認ゲート `gate`）。"""
    got = _line_of(body, ROUTE_LINE)
    return got if got in merge_decide.ROUTES else merge_decide.GATE_ROUTE


def _counts(hub: GitHub, c: Authored, human_only: bool) -> bool:
    """証跡の判定に数えるコメントか。信用する書き手が書き、作成の後に編集されていない（書き込みの権限を持つ人は他人の
    コメントを編集できる）。`human_only` なら承認は担い手が利用者（`USER_BY`）と明示された証跡だけを数え、差し戻しは
    担い手を問わず数える（利用者の承認の後の差し戻しを捨てて迂回しない。#3506 の I9）。"""
    if c.edited or (human_only and _by_of(c.body) != USER_BY and not _rejects(c)):
        return False
    return hub.trusts(c)


def _rejects(c: Authored) -> bool:
    """コメントが差し戻し（`approval_decide.REJECTED`）の証跡か。"""
    got = approval_decide.parse(0, c.author, c.body)
    return got is not None and got.answer == approval_decide.REJECTED


def read(hub: GitHub, number: int, human_only: bool = False) -> Read:
    """PR と、信用する書き手の証跡からの判定。`human_only` なら利用者の証跡だけを数える（迂回の前提。#3506 の I9）。"""
    return judged(hub, hub.pull(number), human_only)


def judged(hub: GitHub, pull: Pull, human_only: bool = False) -> Read:
    """読んだ PR `pull` と、信用する書き手の証跡からの判定（PR を読み直さない。読んだばかりの呼び手が使う）。
    `human_only` は `read` と同じ。"""
    number = pull.number
    comments = [(c.author, c.body, _counts(hub, c, human_only)) for c in hub.comments(number)]
    return Read(pull, approval_decide.judge(approval_decide.trails(comments), pull.head_sha))


def user_judgment_at(hub: GitHub, number: int, sha: str) -> approval_decide.Judgment | None:
    """利用者の証跡（`read` の `human_only`）の判定。判定に使った証跡が `sha` へのものでなければ None。"""
    got = read(hub, number, human_only=True).judgment
    return got if got.trail is not None and got.trail.sha == sha else None


def present(hub: GitHub, number: int, material: str) -> str:
    """承認資料を提示する。提示した先端の SHA を返す。

    資料が対象にしたコミット（行 `approval_decide.HEAD_LINE`）が PR の今の先端でなければ、提示せずに `TrailError`
    （資料の後に積んだ変更を含まない資料から、新しい先端を承認させない。資料を組み直してから提示する）。
    """
    pull = hub.pull(number)
    shown = approval_decide.material_head(material)
    if shown != pull.head_sha:
        target = shown[:12] if shown else "（資料に無い）"
        raise TrailError(f"#{number} の承認資料が対象にしたコミット {target} が今の先端 {pull.head_sha[:12]} でない（資料を組み直す）")
    Publisher(hub).comment(number, approval_decide.render_request(material, pull.head_sha))
    return pull.head_sha


def answer(hub: GitHub, number: int, value: str, by: str, presented: str) -> approval_decide.Judgment:
    """答えを証跡に書き、届いたことを読み直して確かめる。"""
    pull = hub.pull(number)
    if pull.head_sha != presented:
        raise TrailError(f"#{number} の先端が提示した {presented[:12]} から {pull.head_sha[:12]} へ動いた（提示し直す）")
    body = f"{approval_decide.render_trail(value, pull.head_sha, by)}{ROUTE_LINE} {merge_decide.GATE_ROUTE}\n"
    return _written(hub, number, pull, value, body)


def _written(hub: GitHub, number: int, pull: Pull, value: str, body: str) -> approval_decide.Judgment:
    """証跡 `body` を書き、答え `value` が先端 `pull.head_sha` への判定に届いたことを読み直して確かめる。"""
    Publisher(hub).comment(number, body)
    after = read(hub, number)
    if after.judgment.state != value or after.pull.head_sha != pull.head_sha:
        raise TrailError(f"#{number} へ書いた答え {value} が判定に届いていない（判定: {after.judgment.detail}）")
    return after.judgment


def merge_facts(hub: GitHub, number: int) -> merge_decide.MergeFacts:
    """PR の REST の応答（`merged_by`・`merge_commit_sha` を持つ）からマージの事実を読む。"""
    raw = hub.rest("GET", f"/repos/{hub.repo}/pulls/{int(number)}")
    return merge_decide.facts_of(raw if isinstance(raw, dict) else {})


def answer_merged(hub: GitHub, number: int, production: str) -> tuple[approval_decide.Judgment, merge_decide.MergeFacts]:
    """利用者が GitHub でマージした本番の PR に、マージした先端への承認の証跡（経路 `github-merge`）を書く。

    マージされていなければ `NotMerged`、本番ブランチ宛てでなければ `TrailError`。マージした先端への承認が既にあれば
    書かない。書いた証跡が判定に届かなければ `TrailError`。
    """
    facts = merge_facts(hub, number)
    if not facts.merged:
        raise NotMerged(f"#{number} はまだマージされていない")
    if facts.base_ref != production:
        raise TrailError(f"#{number} は本番ブランチ {production} 宛てでない（{facts.base_ref}）")
    now = read(hub, number)
    if now.judgment.approved and now.pull.head_sha == facts.head:
        return now.judgment, facts
    by = facts.by if facts.by else "（読めない）"
    body = (
        f"{approval_decide.render_trail(approval_decide.APPROVED, facts.head, USER_BY)}"
        f"{ROUTE_LINE} {merge_decide.MERGE_ROUTE}\n{APPROVER_LINE} {by}\n"
    )
    return _written(hub, number, now.pull._replace(head_sha=facts.head), approval_decide.APPROVED, body), facts


def approvals(hub: GitHub, number: int) -> list[merge_decide.Approved]:
    """信用する書き手の承認の証跡を、経路と承認した人つきで古い順に読む（差し戻しを含めない）。"""
    out = []
    for i, c in enumerate(hub.comments(number)):
        got = approval_decide.parse(i, c.author, c.body) if _counts(hub, c, False) else None
        if got is None or got.answer != approval_decide.APPROVED:
            continue
        by = _line_of(c.body, APPROVER_LINE)
        out.append(merge_decide.Approved(i, c.author, got.sha, _route_of(c.body), by if by else c.author))
    return out


def post_status(hub: GitHub, sha: str, judgment: approval_decide.Judgment) -> None:
    """判定を commit status に付ける。"""
    payload = {
        "state": STATUS_STATE[judgment.state],
        "context": approval_decide.STATUS_CONTEXT,
        "description": judgment.detail[:140],
    }
    hub.rest("POST", f"/repos/{hub.repo}/statuses/{sha}", payload)


def check(hub: GitHub, number: int, human_only: bool = False) -> Read:
    """証跡を読んで判定し、PR の先端へ commit status を付ける。"""
    got = read(hub, number, human_only)
    post_status(hub, got.pull.head_sha, got.judgment)
    return got


def render_admin_record(sha: str, trail_url: str) -> str:
    """迂回の記録の本文（証跡の印を持たない）。"""
    if not _SHA.fullmatch(sha):
        raise ValueError(f"マージした先端は 40 桁の SHA（渡された値: {sha!r}）")
    return (
        f"{ADMIN_MARK}\n## 管理者の迂回でマージした\n\n{ADMIN_SHA_LINE} `{sha}`\n"
        f"{ADMIN_TRAIL_LINE} {trail_url if trail_url else '（読めない）'}\n宣言: `{_PROJECT_FILE}` の `production_merge.admin`\n"
    )


def write_admin_record(hub: GitHub, number: int, sha: str) -> tuple[bool, str]:
    """迂回の記録を置く。同じ先端の記録が既にあれば置かない（#3506 の I7）。(置いたか, コメントの URL)。

    既にある記録に数えるのは、信用する書き手（`GitHub.trusts`）が書き、編集されていないものだけ（コメントできる誰かが
    印と SHA の入ったコメントを先に置いても、本物の記録を止められない）。

    照らした証跡は、利用者の証跡（`read` の `human_only`）の判定に使ったコメントの URL。読めない・書けなければ `TrailError`。
    """
    try:
        found = hub.comments(number)
        for c in found:
            if ADMIN_MARK in c.body and f"`{sha}`" in c.body and not c.edited and hub.trusts(c):
                return False, c.url
        judged = user_judgment_at(hub, number, sha)
        used = judged.trail.order if judged is not None and judged.trail is not None else None
        trail_url = found[used].url if used is not None and used < len(found) else ""
        url = Publisher(hub).comment(number, render_admin_record(sha, trail_url)).url
    except (GitHubError, ValueError) as exc:
        raise TrailError(f"#{number} に迂回の記録を置けない: {exc}") from None
    return True, url


def render_merge_record(approval: merge_decide.Approval) -> str:
    """人のマージの記録の本文（証跡の印を持たない）。SHA の形が違えば `ValueError`。"""
    for sha in (approval.merged_head, approval.merge_sha):
        if not _SHA.fullmatch(sha):
            raise ValueError(f"マージした先端とマージコミットは 40 桁の SHA（渡された値: {sha!r}）")
    gate = f"`{approval.gate_head}`" if approval.gate_head else "（無し）"
    return (
        f"{MERGE_MARK}\n## 人のマージを承認として記録した\n\n{ROUTE_LINE} {approval.route}\n{APPROVER_LINE} {approval.by}\n"
        f"{MERGED_HEAD_LINE} `{approval.merged_head}`\n{MERGE_SHA_LINE} `{approval.merge_sha}`\n{GATE_HEAD_LINE} {gate}\n"
    )


def write_merge_record(hub: GitHub, number: int, approval: merge_decide.Approval) -> tuple[bool, str]:
    """人のマージの記録を 1 回だけ書く（決定 8）。(書いたか, コメントの URL)。読めない・書けなければ `TrailError`。

    既にある記録に数えるのは、信用する書き手が書き、編集されていない、同じマージした先端のものだけ。
    """
    try:
        for c in hub.comments(number):
            if MERGE_MARK in c.body and f"`{approval.merged_head}`" in c.body and not c.edited and hub.trusts(c):
                return False, c.url
        url = Publisher(hub).comment(number, render_merge_record(approval)).url
    except (GitHubError, ValueError) as exc:
        raise TrailError(f"#{number} に人のマージの記録を置けない: {exc}") from None
    return True, url
