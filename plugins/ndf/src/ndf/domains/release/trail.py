"""リリースの PR の承認の証跡を読み書きし、判定を PR の HEAD の commit status へ付ける。

- 提示（`present`）: 承認資料を、提示した HEAD の SHA を添えて PR へコメントする（会話のセッションが打つ）。資料が対象にした
  コミットが今の HEAD でなければ提示しない
- 回答（`answer`）: 人の回答を証跡のコメントに書き、書いた後に PR を読み直して、回答が今の HEAD への判定として
  届いたことを確かめる。届いていなければ `TrailError`（回答したつもりで止まり続けない）
- 確かめ（`check`）: 証跡を読んで判定し、判定を commit status `approval_decide.STATUS_CONTEXT` に付ける（workflow が打つ）
- 迂回の記録（`write_admin_record`）: 本番の PR を管理者の迂回でマージしたことを、照らした証跡を指すコメントで残す（#3506）。
  マーカー `ADMIN_MARK` は証跡のマーカーと別で、証跡の判定に数えない。旧いプラグイン（`release_lib/approval_trail.py`）と同じマーカーと行を持つ
- 書き手（`trail_writer`）: 承認の証跡を書くのはケースごとに 1 つだけ（#3672 の I12・#3671）。10 系の
  `approval_trail.trail_writer` と同じ表で、ブランチ戦略の設定（`.ndf/project.json` の `branch_strategy`）の
  `approval.via` と `merge.actor` で決める

| ケース | 書き手 |
| --- | --- |
| `approval.via: review` | `github`（GitHub のレビューの承認。NDF は書かない） |
| `via: gate` で人が先にマージした、または `merge.actor: human` | `land`（人のマージの記録 `write_human_record`） |
| `via: gate`・`merge.actor: ndf`・人がマージしていない | `answer`（リリース承認ゲートの回答） |

回答は提示した HEAD へのもので、回答するときに HEAD が動いていれば書かずに断る（見ていないコミットを承認しない）。
今の HEAD への最新の提示と同じ資料への回答が既にあれば、提示（質問）を送らない（同じ質問を繰り返さない。#3671）。
資料が違えば、回答があっても提示し直す。利用者の回答は提示した資料の SHA-256 を持ち、最新の提示と違えば書かない（#4446）。
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import NamedTuple

from ndf import identity
from ndf.adapters.github import WRITE_PERMISSIONS, Authored, GitHub, GitHubError, Pull, text_of
from ndf.adapters.publish import Publisher
from ndf.contracts import facts
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import approval_decide

# 迂回の記録のマーカーと行の見出し（旧いプラグインと同じ文字列。作り直しは旧いプラグインを読み込まないため別に持つ）
ADMIN_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:admin-merge -->"
# 人のマージの記録のマーカーと行の見出し（10 系の `release_lib/land.py` の `HUMAN_MARK` と同じ文字列）
HUMAN_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:human-merge -->"
HUMAN_SHA_LINE = "人がマージした HEAD:"

ADMIN_SHA_LINE = "マージした HEAD:"
ADMIN_TRAIL_LINE = "照らした承認の証跡:"
# 証跡の担い手の行。迂回の前提に数えるのは利用者の証跡だけ（#3506 の I9）
BY_LINE = approval_decide.BY_LINE


class By(StrEnum):
    """証跡の担い手（旧いプラグインの `approve --by` と同じ値）。"""

    USER = "user"  # 利用者
    MVV = "mvv"  # MVV 判定


USER_BY, MVV_BY = By.USER.value, By.MVV.value


class Writer(StrEnum):
    """承認の証跡の書き手（10 系の `approval_trail.WRITER_*` と同じ値）。"""

    ANSWER = "answer"  # リリース承認ゲートの回答を書く手順（`release-steps answer`）
    LAND = "land"  # 人のマージの記録（`release-steps merge` がマージ済みを見つけたとき）
    GITHUB = "github"  # GitHub のレビューの承認（NDF は書かない）


WRITER_ANSWER, WRITER_LAND, WRITER_GITHUB = Writer.ANSWER.value, Writer.LAND.value, Writer.GITHUB.value
BY_CHOICES = tuple(b.value for b in By)
_PROJECT_FILE = f"{identity.FIXED_WORDS['ndf_settings_dir']}/project.json"
_SHA = re.compile(r"\b([0-9a-f]{40})\b")

# 判定 → commit status の状態の表（`contracts/domains/approval-states.json`）
APPROVAL_STATES = "approval-states"

# レビューの判定を変える状態（COMMENTED は変えない。GitHub のレビューの判定と同じ）
REVIEW_VERDICTS = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


class TrailError(Exception):
    """証跡を書けない・書いた回答が判定に届かない・HEAD が提示から動いた・書き手が回答の手順でない。"""


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
    presented: tuple[approval_decide.Presentation, ...] = ()


def material_digest(material: str) -> str:
    """承認資料のファイル全体の SHA-256（`ndf document-rebuild gate` の `outputs.material.file_sha256` と同じ計算）。"""
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _by_of(body: str) -> str | None:
    """証跡の本文の担い手の行の値。無ければ None。"""
    for line in body.splitlines():
        if line.startswith(BY_LINE):
            return line[len(BY_LINE) :].strip()
    return None


def _counts(hub: GitHub, c: Authored, human_only: bool) -> bool:
    """証跡の判定に数えるコメントか。信用する書き手が書き、作成の後に編集されていない（書き込みの権限を持つ人は他人の
    コメントを編集できる）。`human_only` なら承認は担い手が利用者（`USER_BY`）と明示された証跡だけを数え、変更要求は
    担い手を問わず数える（利用者の承認の後の変更要求を捨てて迂回しない。#3506 の I9）。"""
    if c.edited or (human_only and _by_of(c.body) != USER_BY and not _rejects(c)):
        return False
    return hub.trusts(c)


def _rejects(c: Authored) -> bool:
    """コメントが変更要求（`approval_decide.REJECTED`）の証跡か。"""
    got = approval_decide.parse(0, c.author, c.body)
    return got is not None and got.answer == approval_decide.REJECTED


def read(hub: GitHub, number: int, human_only: bool = False) -> Read:
    """PR と、信用する書き手の証跡からの判定。`human_only` なら利用者の証跡だけを数える（迂回の前提。#3506 の I9）。"""
    return judged(hub, hub.pull(number), human_only)


def judged(hub: GitHub, pull: Pull, human_only: bool = False) -> Read:
    """読んだ PR `pull` と、信用する書き手の証跡からの判定（PR を読み直さない。読んだばかりの呼び手が使う）。
    `human_only` は `read` と同じ。"""
    found = hub.comments(pull.number)
    comments = [(c.author, c.body, _counts(hub, c, human_only)) for c in found]
    shown = approval_decide.presentations((c.author, c.body, not c.edited and hub.trusts(c)) for c in found)
    return Read(pull, approval_decide.judge(approval_decide.trails(comments), shown, pull.head_sha), tuple(shown))


def user_judgment_at(hub: GitHub, number: int, sha: str) -> approval_decide.Judgment | None:
    """利用者の証跡（`read` の `human_only`）の判定。判定に使った証跡が `sha` へのものでなければ None。"""
    got = read(hub, number, human_only=True).judgment
    return got if got.trail is not None and got.trail.sha == sha else None


class Presented(NamedTuple):
    """提示の結果。`asked` が偽なら質問を送っていない（今の HEAD への回答が既にある `judgment` を返す）。"""

    head: str
    asked: bool
    judgment: approval_decide.Judgment


def present(hub: GitHub, number: int, material: str) -> Presented:
    """承認資料を提示する。今の HEAD への最新の提示が同じ資料で、それへの回答が既にあれば提示せず、その判定を返す
    （同じ質問を繰り返さない。#3671）。資料が違えば回答があっても提示し直す（#4446）。

    資料が対象にしたコミット（行 `approval_decide.HEAD_LINE`）が PR の今の HEAD でなければ、提示せずに `TrailError`
    （資料の後に積んだ変更を含まない資料から、新しい HEAD を承認させない。資料を組み直してから提示する）。
    """
    got = read(hub, number)
    pull, sha = got.pull, material_digest(material)
    latest = approval_decide.latest_presentation(got.presented, pull.head_sha)
    if got.judgment.state in approval_decide.ANSWERS and latest is not None and latest.material == sha:
        return Presented(pull.head_sha, False, got.judgment)
    shown = approval_decide.material_head(material)
    if shown != pull.head_sha:
        target = shown[:12] if shown else "（資料に無い）"
        raise TrailError(f"#{number} の承認資料が対象にしたコミット {target} が今の HEAD {pull.head_sha[:12]} でない（資料を組み直す）")
    Publisher(hub).comment(number, approval_decide.render_request(material, pull.head_sha, sha))
    return Presented(pull.head_sha, True, got.judgment)


class Shown(NamedTuple):
    """回答した者が見た提示: 提示した HEAD と、利用者が回答した資料のファイル全体の SHA-256（MVV 判定の回答は None）。"""

    head: str
    material_sha256: str | None = None


def answer(hub: GitHub, number: int, value: str, by: str, presented: Shown, writer: str = WRITER_ANSWER) -> approval_decide.Judgment:
    """回答を証跡に書き、届いたことを読み直して確かめる。

    `writer` は設定から決めた書き手（`trail_writer`）。回答の手順でなければ書かずに `TrailError`（書き手を 1 つに保つ。#3671）。
    利用者の回答（`--by user`）は回答した資料の SHA-256 が要り、今の HEAD への最新の提示の資料と違えば書かない。MVV 判定の
    回答は資料の SHA-256 を受けず、提示が無くても今の HEAD で書く（#4446）。
    """
    if writer != WRITER_ANSWER:
        raise TrailError(
            f"#{number} の承認の証跡の書き手は {writer} で、リリース承認ゲートの回答を書かない（設定の approval.via・merge.actor）"
        )
    head, material_sha256 = presented
    if by == MVV_BY and material_sha256 is not None:
        raise TrailError(f"#{number} への MVV 判定の回答は資料の SHA-256 を受けない（人に資料を示さずに通す経路）")
    got = read(hub, number)
    pull = got.pull
    if pull.head_sha != head:
        raise TrailError(f"#{number} の HEAD が提示した {head[:12]} から {pull.head_sha[:12]} へ動いた（提示し直す）")
    if by == USER_BY:
        latest = approval_decide.latest_presentation(got.presented, pull.head_sha)
        if material_sha256 is None or latest is None or latest.material != material_sha256:
            shown = latest.material[:12] if latest is not None and latest.material else "（提示が無い）"
            raise TrailError(
                f"#{number} の回答の資料 {str(material_sha256)[:12]} が最新の提示 {shown} と違う（提示し直した資料に回答を取り直す）"
            )
    Publisher(hub).comment(number, approval_decide.render_trail(value, pull.head_sha, by, material_sha256))
    after = read(hub, number)
    if after.judgment.state != value or after.pull.head_sha != pull.head_sha:
        raise TrailError(f"#{number} へ書いた回答 {value} が判定に届いていない（判定: {after.judgment.detail}）")
    return after.judgment


def post_status(hub: GitHub, sha: str, judgment: approval_decide.Judgment) -> None:
    """判定を commit status に付ける。"""
    payload = {
        "state": facts.value(APPROVAL_STATES, judgment.state, "commit_state"),
        "context": approval_decide.STATUS_CONTEXT,
        "description": judgment.detail[:140],
    }
    hub.rest("POST", f"/repos/{hub.repo}/statuses/{sha}", payload)


def review_commit(hub: GitHub, number: int) -> str | None:
    """`approval.via: review` の承認: write 以上の権限を持つ人の最新の承認のレビューのコミット（無ければ None）。

    人ごとに最後の判定（`REVIEW_VERDICTS`）だけを数え、承認の後に同じ人が変更要求していれば前の承認を使わない（10 系の
    `approval_trail.approved_review_commit` と同じ）。"""
    last: dict[str, dict] = {}
    for r in sorted((r for r in hub.reviews(number) if isinstance(r, dict)), key=lambda r: text_of(r.get("submitted_at"))):
        if r.get("state") in REVIEW_VERDICTS:
            user = r.get("user")
            last[text_of(user.get("login")) if isinstance(user, dict) else ""] = r
    got = [
        r
        for login, r in last.items()
        if r.get("state") == "APPROVED" and text_of(r.get("commit_id")) and login and hub.permission(login) in WRITE_PERMISSIONS
    ]
    if not got:
        return None
    return text_of(max(got, key=lambda r: text_of(r.get("submitted_at")))["commit_id"]).lower()


def check(hub: GitHub, number: int, human_only: bool = False) -> Read:
    """証跡を読んで判定し、PR の HEAD へ commit status を付ける。"""
    got = read(hub, number, human_only)
    post_status(hub, got.pull.head_sha, got.judgment)
    return got


def render_admin_record(sha: str, trail_url: str) -> str:
    """迂回の記録の本文（証跡のマーカーを持たない）。"""
    if not _SHA.fullmatch(sha):
        raise ValueError(f"マージした HEAD は 40 桁の SHA（渡された値: {sha!r}）")
    return (
        f"{ADMIN_MARK}\n## 管理者の迂回でマージした\n\n{ADMIN_SHA_LINE} `{sha}`\n"
        f"{ADMIN_TRAIL_LINE} {trail_url if trail_url else '（読めない）'}\n設定: `{_PROJECT_FILE}` の `branch_strategy.merge.admin`\n"
    )


def write_admin_record(hub: GitHub, number: int, sha: str) -> tuple[bool, str]:
    """迂回の記録を置く。同じ HEAD の記録が既にあれば置かない（#3506 の I7）。(置いたか, コメントの URL)。

    既にある記録に数えるのは、信用する書き手（`GitHub.trusts`）が書き、編集されていないものだけ（コメントできる誰かが
    マーカーと SHA の入ったコメントを先に置いても、本物の記録を止められない）。

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
    """信用する書き手が書き、編集されていない、マーカー `mark` と HEAD `sha` を持つコメントがあるか。"""
    return any(mark in c.body and f"`{sha}`" in c.body and not c.edited and hub.trusts(c) for c in hub.comments(number))


def write_human_record(hub: GitHub, number: int, sha: str) -> bool:
    """人がマージした HEAD の記録を置く（書き手 `land` の証跡。#3672 の I12）。同じ HEAD の記録が既にあれば置かない。

    置いたら真。読めない・書けなければ `TrailError`。
    """
    if not _SHA.fullmatch(sha):
        raise TrailError(f"#{number} の人のマージの HEAD が 40 桁の SHA でない: {sha!r}")
    try:
        if _has_trusted_mark(hub, number, HUMAN_MARK, sha):
            return False
        Publisher(hub).comment(number, f"{HUMAN_MARK}\n{HUMAN_SHA_LINE} `{sha}`\n")
    except GitHubError as exc:
        raise TrailError(f"#{number} に人のマージの記録を置けない: {exc}") from None
    return True
