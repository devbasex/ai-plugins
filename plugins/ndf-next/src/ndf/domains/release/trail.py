"""リリースの PR の承認の証跡を読み書きし、判定を PR の先端の commit status へ付ける。

- 提示（`present`）: 承認資料を、提示した先端の SHA を添えて PR へコメントする（会話のセッションが打つ）。資料が対象にした
  コミットが今の先端でなければ提示しない
- 答え（`answer`）: 人の答えを証跡のコメントに書き、書いた後に PR を読み直して、答えが今の先端への判定として
  届いたことを確かめる。届いていなければ `TrailError`（答えたつもりで止まり続けない）
- 確かめ（`check`）: 証跡を読んで判定し、判定を commit status `approval_decide.STATUS_CONTEXT` に付ける（workflow が打つ）
- 迂回の記録（`write_admin_record`）: 本番の PR を管理者の迂回でマージしたことを、照らした証跡を指すコメントで残す（#3506）。
  印 `ADMIN_MARK` は証跡の印と別で、証跡の判定に数えない。旧いプラグイン（`release_lib/approval_trail.py`）と同じ印と行を持つ
- 書き手（`trail_writer`）: 承認の証跡を書くのはケースごとに 1 つだけ（#3672 の I12・#3671）。10 系の
  `approval_trail.trail_writer` と同じ表で、ブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）の
  `approval.via` と `merge.actor` で決める

| ケース | 書き手 |
| --- | --- |
| `approval.via: review` | `github`（GitHub のレビューの承認。NDF は書かない） |
| `via: gate` で人が先にマージした、または `merge.actor: human` | `land`（人のマージの記録 `write_human_record`） |
| `via: gate`・`merge.actor: ndf`・人がマージしていない | `answer`（リリース承認ゲートの答え） |

答えは提示した先端へのもので、答えるときに先端が動いていれば書かずに断る（見ていないコミットを承認しない）。
今の先端への答えが既にあれば、提示（問い）を送らない（同じ問いを繰り返さない。#3671）。
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

import re

from ndf import identity
from ndf.adapters.github import Authored, GitHub, GitHubError, Pull
from ndf.adapters.publish import Publisher
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import approval_decide

# 迂回の記録の印と行の見出し（旧いプラグインと同じ文字列。作り直しは旧いプラグインを読み込まないため別に持つ）
ADMIN_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:admin-merge -->"
# 人のマージの記録の印と行の見出し（10 系の `release_lib/land.py` の `HUMAN_MARK` と同じ文字列）
HUMAN_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:human-merge -->"
HUMAN_SHA_LINE = "人がマージした先端:"
# 証跡の書き手（10 系の `approval_trail.WRITER_*` と同じ値）
WRITER_ANSWER = "answer"  # リリース承認ゲートの答えを書く手順（`release-steps answer`）
WRITER_LAND = "land"  # 人のマージの記録（`release-steps merge` がマージ済みを見つけたとき）
WRITER_GITHUB = "github"  # GitHub のレビューの承認（NDF は書かない）
ADMIN_SHA_LINE = "マージした先端:"
ADMIN_TRAIL_LINE = "照らした承認の証跡:"
# 証跡の担い手の行。迂回の前提に数えるのは利用者の証跡だけ（#3506 の I9）
BY_LINE = "担い手:"


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
    """証跡を書けない・書いた答えが判定に届かない・先端が提示から動いた・書き手が答えの手順でない。"""


def trail_writer(strategy: Strategy, merged_by_human: bool) -> str:
    """承認の証跡の書き手をケースごとに 1 つ返す（モジュールの説明の表。10 系の `approval_trail.trail_writer` と同じ）。"""
    sec = strategy.section
    if sec["approval"]["via"] == "review":
        return WRITER_GITHUB
    if merged_by_human or sec["merge"]["actor"] == "human":
        return WRITER_LAND
    return WRITER_ANSWER


class Read(NamedTuple):
    pull: Pull
    judgment: approval_decide.Judgment


def _by_of(body: str) -> str | None:
    """証跡の本文の担い手の行の値。無ければ None。"""
    for line in body.splitlines():
        if line.startswith(BY_LINE):
            return line[len(BY_LINE) :].strip()
    return None


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


class Presented(NamedTuple):
    """提示の結果。`asked` が偽なら問いを送っていない（今の先端への答えが既にある `judgment` を返す）。"""

    head: str
    asked: bool
    judgment: approval_decide.Judgment


def present(hub: GitHub, number: int, material: str) -> Presented:
    """承認資料を提示する。今の先端への答えが既にあれば提示せず、その判定を返す（同じ問いを繰り返さない。#3671）。

    資料が対象にしたコミット（行 `approval_decide.HEAD_LINE`）が PR の今の先端でなければ、提示せずに `TrailError`
    （資料の後に積んだ変更を含まない資料から、新しい先端を承認させない。資料を組み直してから提示する）。
    """
    got = read(hub, number)
    pull = got.pull
    if got.judgment.state in approval_decide.ANSWERS:
        return Presented(pull.head_sha, False, got.judgment)
    shown = approval_decide.material_head(material)
    if shown != pull.head_sha:
        target = shown[:12] if shown else "（資料に無い）"
        raise TrailError(f"#{number} の承認資料が対象にしたコミット {target} が今の先端 {pull.head_sha[:12]} でない（資料を組み直す）")
    Publisher(hub).comment(number, approval_decide.render_request(material, pull.head_sha))
    return Presented(pull.head_sha, True, got.judgment)


def answer(hub: GitHub, number: int, value: str, by: str, presented: str, writer: str = WRITER_ANSWER) -> approval_decide.Judgment:
    """答えを証跡に書き、届いたことを読み直して確かめる。

    `writer` は宣言から決めた書き手（`trail_writer`）。答えの手順でなければ書かずに `TrailError`（書き手を 1 つに保つ。#3671）。
    """
    if writer != WRITER_ANSWER:
        raise TrailError(f"#{number} の承認の証跡の書き手は {writer} で、リリース承認ゲートの答えを書かない（宣言の approval.via・merge.actor）")
    pull = hub.pull(number)
    if pull.head_sha != presented:
        raise TrailError(f"#{number} の先端が提示した {presented[:12]} から {pull.head_sha[:12]} へ動いた（提示し直す）")
    Publisher(hub).comment(number, approval_decide.render_trail(value, pull.head_sha, by))
    after = read(hub, number)
    if after.judgment.state != value or after.pull.head_sha != pull.head_sha:
        raise TrailError(f"#{number} へ書いた答え {value} が判定に届いていない（判定: {after.judgment.detail}）")
    return after.judgment


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
        f"{ADMIN_TRAIL_LINE} {trail_url if trail_url else '（読めない）'}\n宣言: `{_PROJECT_FILE}` の `branch_strategy.merge.admin`\n"
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


def _has_trusted_mark(hub: GitHub, number: int, mark: str, sha: str) -> bool:
    """信用する書き手が書き、編集されていない、印 `mark` と先端 `sha` を持つコメントがあるか。"""
    return any(mark in c.body and f"`{sha}`" in c.body and not c.edited and hub.trusts(c) for c in hub.comments(number))


def write_human_record(hub: GitHub, number: int, sha: str) -> bool:
    """人がマージした先端の記録を置く（書き手 `land` の証跡。#3672 の I12）。同じ先端の記録が既にあれば置かない。

    置いたら真。読めない・書けなければ `TrailError`。
    """
    if not _SHA.fullmatch(sha):
        raise TrailError(f"#{number} の人のマージの先端が 40 桁の SHA でない: {sha!r}")
    try:
        if _has_trusted_mark(hub, number, HUMAN_MARK, sha):
            return False
        Publisher(hub).comment(number, f"{HUMAN_MARK}\n{HUMAN_SHA_LINE} `{sha}`\n")
    except GitHubError as exc:
        raise TrailError(f"#{number} に人のマージの記録を置けない: {exc}") from None
    return True
