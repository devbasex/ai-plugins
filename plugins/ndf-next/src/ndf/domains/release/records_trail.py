"""リリースの PR へ置く記録（証跡の判定に数えない印つきのコメント）の本文と書き込み。証跡の読み書きと判定は `trail` が持つ。

- 迂回の記録（`write_admin_record`）: 本番の PR を管理者の迂回でマージしたことを、照らした証跡を指すコメントで残す（#3506）。
  印 `ADMIN_MARK` は証跡の印と別で、証跡の判定に数えない。旧いプラグイン（`release_lib/approval_trail.py`）と同じ印と行を持つ
- 人のマージの記録（`write_merge_record`）: 利用者が GitHub で本番の PR をマージしたら、人のマージの記録（印 `MERGE_MARK`）を
  書く（#3572 の決定 8）
- 作った版（`write_version_record`・`read_version_record`）: CD が版上げの PR・タグ・GitHub Release を作った後に、本番の PR へ
  作った版の記録（印 `VERSION_MARK`）を 1 回だけ書き、リリース記録がそこから版を読む（#3572 の決定 7）

`trail` を読む向きだけを持つ（`trail` はこのモジュールを読まない）。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ndf import identity
from ndf.adapters.github import Authored, GitHub, GitHubError
from ndf.adapters.publish import Publisher
from ndf.domains.release import merge_decide, trail, version_decide
from ndf.domains.release.declaration import PROJECT_FILE

# 迂回の記録の印と行の見出し（旧いプラグインと同じ文字列。作り直しは旧いプラグインを読み込まないため別に持つ）
ADMIN_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:admin-merge -->"
ADMIN_SHA_LINE = "マージした先端:"
ADMIN_TRAIL_LINE = "照らした承認の証跡:"
# 人のマージの記録の印と行の見出し（マージした先端の行は迂回の記録と同じ見出しを使う）
MERGE_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:merge-approval -->"
MERGED_HEAD_LINE = ADMIN_SHA_LINE
MERGE_SHA_LINE = "マージコミット:"
GATE_HEAD_LINE = "承認ゲートの承認の先端:"
# 作った版の記録の印と行の見出し（#3572 の決定 7）
VERSION_MARK = f"<!-- {identity.FIXED_WORDS['workflow_namespace']}:release-version -->"
VERSION_LINE = "作った版:"
TAG_LINE = "タグ:"
BUMP_PR_LINE = "版上げの PR:"
_SHA = re.compile(r"\b([0-9a-f]{40})\b")


def _existing_record(comments: list[Authored], hub: GitHub, mark: str, sha: str) -> str | None:
    """印 `mark` と `sha` を含み、編集されておらず、信用する書き手が書いた記録のコメントの URL（無ければ None）。

    コメントできる誰かが印と SHA の入ったコメントを先に置いても、本物の記録を止められないようにする。"""
    for c in comments:
        if mark in c.body and f"`{sha}`" in c.body and not c.edited and hub.trusts(c):
            return c.url
    return None


def _write_once(
    hub: GitHub,
    number: int,
    mark: str,
    key: str,
    render: Callable[[list[Authored]], str],
    what: str,
    errors: tuple[type[Exception], ...] = (GitHubError, ValueError),
) -> tuple[bool, str]:
    """印 `mark` と `key` の記録が既にあれば置かず、無ければ `render`（読んだコメントを受ける）の本文で置く。(置いたか, URL)。

    既にある記録に数えるのは `_existing_record` が数えるものだけ。`errors` の例外は `TrailError` に包む（`what` は記録の呼び名）。
    """
    try:
        found = hub.comments(number)
        existing = _existing_record(found, hub, mark, key)
        if existing is not None:
            return False, existing
        url = Publisher(hub).comment(number, render(found)).url
    except errors as exc:
        raise trail.TrailError(f"#{number} に{what}を置けない: {exc}") from None
    return True, url


def _require_sha(value: str, what: str) -> None:
    """`value` が 40 桁の SHA でなければ `ValueError`（`what` は値の呼び名）。"""
    if not _SHA.fullmatch(value):
        raise ValueError(f"{what}は 40 桁の SHA（渡された値: {value!r}）")


def render_admin_record(sha: str, trail_url: str) -> str:
    """迂回の記録の本文（証跡の印を持たない）。"""
    _require_sha(sha, "マージした先端")
    return (
        f"{ADMIN_MARK}\n## 管理者の迂回でマージした\n\n{ADMIN_SHA_LINE} `{sha}`\n"
        f"{ADMIN_TRAIL_LINE} {trail_url if trail_url else trail.UNREADABLE}\n宣言: `{PROJECT_FILE.as_posix()}` の `production_merge.admin`\n"
    )


def write_admin_record(hub: GitHub, number: int, sha: str) -> tuple[bool, str]:
    """迂回の記録を置く。同じ先端の記録が既にあれば置かない（#3506 の I7）。(置いたか, コメントの URL)。

    既にある記録に数えるのは、信用する書き手（`GitHub.trusts`）が書き、編集されていないものだけ（コメントできる誰かが
    印と SHA の入ったコメントを先に置いても、本物の記録を止められない）。

    照らした証跡は、利用者の証跡（`read` の `human_only`）の判定に使ったコメントの URL。読めない・書けなければ `TrailError`。
    """

    def render(found: list[Authored]) -> str:
        judged = trail.user_judgment_at(hub, number, sha)
        used = judged.trail.order if judged is not None and judged.trail is not None else None
        trail_url = found[used].url if used is not None and used < len(found) else ""
        return render_admin_record(sha, trail_url)

    return _write_once(hub, number, ADMIN_MARK, sha, render, "迂回の記録")


def render_merge_record(approval: merge_decide.Approval) -> str:
    """人のマージの記録の本文（証跡の印を持たない）。SHA の形が違えば `ValueError`。"""
    for sha in (approval.merged_head, approval.merge_sha):
        _require_sha(sha, "マージした先端とマージコミット")
    gate = f"`{approval.gate_head}`" if approval.gate_head else "（無し）"
    return (
        f"{MERGE_MARK}\n## 人のマージを承認として記録した\n\n{trail.ROUTE_LINE} {approval.route}\n{trail.APPROVER_LINE} {approval.by}\n"
        f"{MERGED_HEAD_LINE} `{approval.merged_head}`\n{MERGE_SHA_LINE} `{approval.merge_sha}`\n{GATE_HEAD_LINE} {gate}\n"
    )


def write_merge_record(hub: GitHub, number: int, approval: merge_decide.Approval) -> tuple[bool, str]:
    """人のマージの記録を 1 回だけ書く（決定 8）。(書いたか, コメントの URL)。読めない・書けなければ `TrailError`。

    既にある記録に数えるのは、信用する書き手が書き、編集されていない、同じマージした先端のものだけ。
    """
    return _write_once(hub, number, MERGE_MARK, approval.merged_head, lambda _found: render_merge_record(approval), "人のマージの記録")


def render_version_record(version: str, tag: str, bump_pr: int) -> str:
    """作った版の記録の本文（証跡の印を持たない）。接尾辞のある版なら `ValueError`。"""
    if version_decide.base_of(version) != version:
        raise ValueError(f"作った版は接尾辞の無い版（渡された値: {version!r}）")
    return f"{VERSION_MARK}\n## CD が版を作った\n\n{VERSION_LINE} `{version}`\n{TAG_LINE} `{tag}`\n{BUMP_PR_LINE} #{int(bump_pr)}\n"


def write_version_record(hub: GitHub, number: int, version: str, tag: str, bump_pr: int) -> tuple[bool, str]:
    """本番の PR へ作った版の記録を 1 回だけ書く。(書いたか, コメントの URL)。読めない・書けなければ `TrailError`。

    既にある記録に数えるのは、信用する書き手が書き、編集されていない、同じ版のものだけ。
    """
    return _write_once(
        hub,
        number,
        VERSION_MARK,
        version,
        lambda _found: render_version_record(version, tag, bump_pr),
        "作った版の記録",
        (GitHubError, ValueError, version_decide.VersionError),
    )


def read_version_record(hub: GitHub, number: int) -> str | None:
    """本番の PR の作った版の記録のうち、信用する書き手の最新のものの版（無ければ None）。"""
    found = None
    for c in hub.comments(number):
        if VERSION_MARK not in c.body or c.edited or not hub.trusts(c):
            continue
        got = trail._line_of(c.body, VERSION_LINE)
        if got:
            found = got.strip("`")
    return found
