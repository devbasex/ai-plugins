"""リリースの PR に置く承認の証跡（#2773）。

証跡はリリースの PR へのコメントで、印 `TRAIL_MARK` と `承認したコミット:` の 1 行（40 桁の SHA）を持つ。
承認した人と時刻は GitHub がコメントに付けた作成者（`user.login`）と作成の時刻（`created_at`）で読み、本文の記述を
判定に使わない。作成の後に編集された証跡は数えない（書き込み権限を持つ人は他人のコメントを編集できる）。

- 書式の解釈と判定（`parse_trail`・`trails_of`・`judge`・`approved_review_commit`・`render_*`）は純粋な関数
- GitHub の読み書き（`read_*`・`open_prs_at`・`post_status`・`write_trail`・`write_admin_record`）は `gh` を通す。失敗は `TrailError`

迂回の記録（#3506）は本番の PR を管理者の迂回でマージしたことを示すコメントで、印 `ADMIN_MARK` を持つ。
意図の印（`INTENT_MARK`）は land が迂回でマージする直前に置き、マージ済みの PR を迂回の記録で補ってよいかを決める
（印の無いマージは人のマージとして記録する）。どちらも証跡の印を持たないため、証跡の判定にも
`release-approval.yml` の起動の条件にも数えない。

証跡の書式の印と行の名前はこのモジュールだけが持つ。手順書は書式を写さず、ここを指す。
"""

from __future__ import annotations

import json
import re
from typing import NamedTuple

import approved_commit as ac
import gh_call
import gh_rest

TRAIL_MARK = "<!-- ndf:release-approval -->"
REQUEST_MARK = "<!-- ndf:release-approval-request -->"
STATUS_CONTEXT = "ndf/release-approval"
APPROVER_ROLES = ("admin", "maintain")
# 場合 2 の必須レビューに算入される権限（共同作業者の権限の API の `permission`。maintain は write に丸められる）
REVIEWER_PERMISSIONS = ("admin", "write")
DESCRIPTION_LIMIT = 140  # commit status の説明の長さの上限
TRAIL_LINE = "承認したコミット:"
HEAD_LINE = "先端のコミット:"
BY_LINE = "担い手:"
ADMIN_MARK = "<!-- ndf:admin-merge -->"
ADMIN_SHA_LINE = "マージした先端:"
ADMIN_TRAIL_LINE = "照らした承認の証跡:"
INTENT_MARK = "<!-- ndf:admin-intent -->"
INTENT_SHA_LINE = "迂回でマージする先端:"
_SHA40 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")


class Trail(NamedTuple):
    sha: str
    login: str
    created_at: str
    edited: bool
    comment_id: int | None
    by: str | None = None  # 本文の `担い手:` の値（user / mvv）。照合の絞り込みにだけ使う
    url: str = ""


class Judgment(NamedTuple):
    state: str  # success / failure / error
    description: str
    approver: str | None = None
    at: str | None = None
    url: str | None = None  # 照らした証跡のコメントの URL（success のとき）


class TrailError(Exception):
    """証跡の読み書きの失敗。kind は head_moved（先端が承認したコミットと違う）か unreadable（API を読めない・書けない）。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _line_shas(body: str, label: str) -> list[str]:
    out: list[str] = []
    for line in body.splitlines():
        text = line.strip()
        if text.startswith(label):
            out += _SHA40.findall(text[len(label) :])
    return out


def parse_trail(body: str) -> str | None:
    """コメントの本文から承認したコミット（小文字の 40 桁）を読む。証跡の印が無い・承認資料の印がある・
    `承認したコミット:` の行の 40 桁が 1 つでなければ None。"""
    if not isinstance(body, str) or TRAIL_MARK not in body or REQUEST_MARK in body:
        return None
    shas = _line_shas(body, TRAIL_LINE)
    if len(shas) != 1:
        return None
    try:
        return ac.parse_sha(shas[0].lower())
    except ValueError:
        return None


def parse_by(body: str) -> str | None:
    """本文の `担い手:` の行の値。行が無ければ None。"""
    for line in (body or "").splitlines():
        text = line.strip()
        if text.startswith(BY_LINE):
            return text[len(BY_LINE) :].strip()
    return None


def _author(c: dict) -> str:
    """コメントの作成者の login。無ければ空。"""
    return str((c.get("user") or {}).get("login") or "")


def trails_of(comments: list[dict]) -> list[Trail]:
    """issue のコメントの並び → 証跡（作成の時刻の昇順）。証跡でないコメントは含まない。"""
    out = []
    for c in comments or []:
        if not isinstance(c, dict) or (sha := parse_trail(c.get("body") or "")) is None:
            continue
        created = str(c.get("created_at") or "")
        updated = str(c.get("updated_at") or created)
        body = c.get("body") or ""
        out.append(Trail(sha, _author(c), created, updated != created, c.get("id"), parse_by(body), str(c.get("html_url") or "")))
    return sorted(out, key=lambda t: t.created_at)


def _cut(text: str) -> str:
    return text if len(text) <= DESCRIPTION_LIMIT else text[: DESCRIPTION_LIMIT - 1] + "…"


def _narrow(trails: list[Trail], roles: dict[str, str | None], by: str | None) -> tuple[list[Trail], str]:
    """証跡を担い手・未編集・承認権限の順に絞る。(残った証跡, 失敗の理由)。残れば理由は空。"""
    if by is not None:
        trails = [t for t in trails if t.by == by]
        if not trails:
            return [], f"担い手が {by} の証跡が無い"
    fresh = [t for t in trails if not t.edited]
    if not fresh:
        return [], "編集された証跡だけがある"
    valid = [t for t in fresh if roles.get(t.login) in APPROVER_ROLES]
    if valid:
        return valid, ""
    unread = sorted({t.login for t in fresh if roles.get(t.login) is None})
    if unread:
        return [], _cut(f"権限を読めない {', '.join(unread)}")
    others = sorted({t.login for t in fresh})
    return [], _cut(f"承認権限の無い {', '.join(others)} の証跡だけがある")


def judge(trails: list[Trail], roles: dict[str, str | None], head: str, by: str | None = None) -> Judgment:
    """承認権限（admin・maintain）を持つ人の最新の、編集されていない証跡と先端を照らす（I1〜I4）。
    `by` を渡せば担い手がその値の証跡だけを数える（`cd-admin` は利用者の証跡だけ。#3506 の I9）。"""
    if not trails:
        return Judgment("failure", "証跡が無い")
    valid, why = _narrow(trails, roles, by)
    if not valid:
        return Judgment("failure", why)
    last = max(valid, key=lambda t: t.created_at)
    if last.sha != head.lower():
        return Judgment("failure", f"証跡 {last.sha[:7]} ≠ 先端 {head[:7]}", last.login, last.created_at)
    return Judgment("success", _cut(f"承認: {last.login} {last.created_at}"), last.login, last.created_at, last.url or None)


def approved_trail(trails: list[Trail], roles: dict[str, str | None], by: str | None = None) -> Trail | None:
    """承認権限を持つ人の最新の、編集されていない証跡（`judge` が照らす証跡と同じ 1 件）。無ければ None（#3672 の land）。
    `by` を渡せば担い手がその値の証跡だけを数える（`judge` の `by` と同じ。cd-admin は利用者の証跡だけ）。"""
    valid, _ = _narrow(trails, roles, by)
    return max(valid, key=lambda t: t.created_at) if valid else None


def approved_review_commit(reviews: list[dict], perms: dict[str, str | None]) -> str | None:
    """必須レビューの承認者として有効な人（`permission` が admin か write）の最新の APPROVED のレビューのコミット（I8）。

    人ごとに最後の判定（APPROVED・CHANGES_REQUESTED・DISMISSED。COMMENTED は判定を変えない）だけを数える。承認の後に
    同じ人が差し戻していれば、その人の前の承認は使わない（GitHub のレビューの判定と同じ）。"""
    last: dict[str, dict] = {}
    for r in sorted((r for r in reviews or [] if isinstance(r, dict)), key=lambda r: str(r.get("submitted_at") or "")):
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            last[str((r.get("user") or {}).get("login") or "")] = r
    got = [
        r for login, r in last.items() if r.get("state") == "APPROVED" and perms.get(login) in REVIEWER_PERMISSIONS and r.get("commit_id")
    ]
    if not got:
        return None
    return str(max(got, key=lambda r: str(r.get("submitted_at") or ""))["commit_id"]).lower()


def render_trail(sha: str, by: str) -> str:
    """証跡のコメントの本文。`担い手:` は監査のための記述で、判定に使わない。"""
    return f"{TRAIL_MARK}\n## リリース承認\n\n{TRAIL_LINE} `{ac.parse_sha(sha)}`\n担い手: {by}\n"


def render_admin_record(sha: str, trail_url: str) -> str:
    """迂回の記録の本文。証跡の印を持たない（証跡の判定に数えない）。"""
    return (
        f"{ADMIN_MARK}\n## 管理者の迂回でマージした\n\n{ADMIN_SHA_LINE} `{ac.parse_sha(sha)}`\n"
        f"{ADMIN_TRAIL_LINE} {trail_url or '（読めない）'}\n宣言: `.ndf/project.json` の `branch_strategy.merge.admin`\n"
    )


def render_admin_intent(sha: str) -> str:
    """迂回でマージする意図の印の本文。証跡の印を持たない（証跡の判定に数えない）。"""
    return f"{INTENT_MARK}\n{INTENT_SHA_LINE} `{ac.parse_sha(sha)}`\n宣言: `.ndf/project.json` の `branch_strategy.merge.admin`\n"


def render_request(material: str, head: str) -> str:
    """承認資料のコメントの本文（承認資料の印・先端の行・承認資料の本文）。証跡に数えない。"""
    return f"{REQUEST_MARK}\n{HEAD_LINE} `{head}`\n\n{material.rstrip()}\n"


# ---------- GitHub の読み書き ----------


def _gh_api(args: list[str], what: str):
    r = gh_call.gh(["api", *args])
    if r.returncode != 0:
        raise TrailError("unreadable", f"{what}を読めない: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def _pages(path: str, what: str) -> list[dict]:
    """一覧の API を全ページ読む（`--jq '.[]'` で 1 行 1 件）。"""
    out = _gh_api(["--paginate", path, "--jq", ".[]"], what)
    rows = []
    try:
        for line in out.splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except ValueError as e:
        raise TrailError("unreadable", f"{what}の応答を読めない: {e}")
    return rows


def read_comments(repo: str, pr: int) -> list[dict]:
    return _pages(f"repos/{repo}/issues/{int(pr)}/comments?per_page=100", f"PR #{pr} のコメント")


def read_reviews(repo: str, pr: int) -> list[dict]:
    return _pages(f"repos/{repo}/pulls/{int(pr)}/reviews?per_page=100", f"PR #{pr} のレビュー")


def _read_permission_field(repo: str, logins: list[str], field: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for login in dict.fromkeys(x for x in logins if x):
        r = gh_call.gh(["api", f"repos/{repo}/collaborators/{login}/permission"])
        try:
            got = json.loads(r.stdout).get(field) if r.returncode == 0 else None
        except (ValueError, AttributeError):
            got = None
        out[login] = got if isinstance(got, str) and got else None
    return out


def read_roles(repo: str, logins: list[str]) -> dict[str, str | None]:
    """書いた人ごとの `role_name`（admin・maintain・write など）。読めない人は None。"""
    return _read_permission_field(repo, logins, "role_name")


def read_permissions(repo: str, logins: list[str]) -> dict[str, str | None]:
    """レビューを書いた人ごとの `permission`（admin・write・read。maintain は write）。読めない人は None。"""
    return _read_permission_field(repo, logins, "permission")


def read_review_commit(repo: str, pr: int) -> str | None:
    """PR の上の有効な人（permission が admin・write）の最新の承認のレビューのコミット。無ければ None（場合 2。I8）。"""
    reviews = read_reviews(repo, pr)
    logins = [str((r.get("user") or {}).get("login") or "") for r in reviews if r.get("state") == "APPROVED"]
    return approved_review_commit(reviews, read_permissions(repo, logins))


def judge_trails_on(repo: str, pr: int, head: str, by: str | None = None) -> Judgment:
    """PR の上の証跡を先端 `head` で判定する。編集されていない証跡を書いた人の権限だけを読む（場合 1。I1〜I4）。"""
    trails = trails_of(read_comments(repo, pr))
    return judge(trails, read_roles(repo, [t.login for t in trails if not t.edited]), head, by)


def read_head(repo: str, pr: int) -> tuple[str, str, str]:
    """(先端の SHA, 宛先のブランチ, 状態 open / closed)。"""
    try:
        d = json.loads(_gh_api([f"repos/{repo}/pulls/{int(pr)}"], f"PR #{pr}"))
        head, base, state = d["head"]["sha"], d["base"]["ref"], d["state"]
    except (ValueError, KeyError, TypeError) as e:
        raise TrailError("unreadable", f"PR #{pr} の先端を読めない: {e}")
    if not (head and base and state):
        raise TrailError("unreadable", f"PR #{pr} の先端を読めない")
    return str(head).lower(), str(base), str(state)


def open_prs_at(repo: str, sha: str, base: str) -> list[int]:
    """`base` 宛ての開いた PR のうち、先端が `sha` のものの番号（昇順）。"""
    rows = _pages(f"repos/{repo}/pulls?state=open&base={base}&per_page=100", f"{base} 宛ての PR")
    return sorted(int(r["number"]) for r in rows if str((r.get("head") or {}).get("sha") or "").lower() == sha.lower())


def open_prs_at_base(repo: str, base: str) -> list[dict]:
    """`base` 宛ての開いた PR の番号と先端（番号の昇順）。"""
    rows = _pages(f"repos/{repo}/pulls?state=open&base={base}&per_page=100", f"{base} 宛ての PR")
    return sorted(
        ({"number": int(r["number"]), "head": str((r.get("head") or {}).get("sha") or "").lower()} for r in rows), key=lambda r: r["number"]
    )


def post_status(repo: str, sha: str, j: Judgment) -> None:
    """先端へ `ndf/release-approval` の commit status を付ける（同じ名前は最新が効く）。"""
    args = [f"repos/{repo}/statuses/{sha}", "-X", "POST", "-f", f"state={j.state}"]
    args += ["-f", f"context={STATUS_CONTEXT}", "-f", f"description={_cut(j.description)}"]
    _gh_api(args, "commit status の書き込みの応答")


def write_trail(repo: str, pr: int, sha: str, by: str) -> tuple[bool, str]:
    """先端が `sha` のときだけ証跡のコメントを置く。同じ人・同じ担い手の同じ SHA の編集されていない証跡があれば置かない。
    (置いたか, コメントの URL)。先端が違えば TrailError("head_moved")。"""
    sha = ac.parse_sha(sha)
    head, _base, _state = read_head(repo, pr)
    if head != sha:
        raise TrailError("head_moved", f"PR #{pr} の先端 {head[:8]} が承認したコミット {sha[:8]} と違う")
    me = gh_rest.viewer_login()
    comments = read_comments(repo, pr)
    for t in trails_of(comments):
        if t.login == me and t.sha == sha and t.by == by and not t.edited:
            url = next((c.get("html_url") or "" for c in comments if c.get("id") == t.comment_id), "")
            return False, url
    a = gh_rest.comment(repo, pr, render_trail(sha, by))
    if not a.ok:
        raise TrailError("unreadable", f"証跡のコメントを置けない: {a.error[:200]}")
    return True, (a.value or {}).get("url") or ""


def _trusted_mark(repo: str, pr: int, mark: str, label: str, sha: str) -> dict | None:
    """印 `mark` と `label` の行に `sha` を持つコメントのうち、書き込みの権限（admin・write）を持つ人が書き、作成の後に
    編集されていないもの（コメントできる誰かが印と SHA の入ったコメントを置いても数えない）。無ければ None。"""
    found = []
    for c in read_comments(repo, pr):
        body = str(c.get("body") or "") if isinstance(c, dict) else ""
        if mark in body and sha in [x.lower() for x in _line_shas(body, label)]:
            found.append(c)
    perms = read_permissions(repo, [_author(c) for c in found]) if found else {}
    for c in found:
        if perms.get(_author(c)) in REVIEWER_PERMISSIONS and str(c.get("updated_at") or "") == str(c.get("created_at") or ""):
            return c
    return None


def write_admin_record(repo: str, pr: int, sha: str, trail_url: str) -> tuple[bool, str]:
    """迂回の記録を置く。同じ先端の記録が既にあれば置かない（#3506 の I7。数える記録は `_trusted_mark`）。(置いたか, コメントの URL)。"""
    sha = ac.parse_sha(sha)
    got = _trusted_mark(repo, pr, ADMIN_MARK, ADMIN_SHA_LINE, sha)
    if got is not None:
        return False, str(got.get("html_url") or "")
    a = gh_rest.comment(repo, pr, render_admin_record(sha, trail_url))
    if not a.ok:
        raise TrailError("unreadable", f"迂回の記録を置けない: {a.error[:200]}")
    return True, (a.value or {}).get("url") or ""


def has_admin_intent(repo: str, pr: int, sha: str) -> bool:
    """先端 `sha` を迂回でマージする意図の印があるか（数える印は `_trusted_mark`）。"""
    return _trusted_mark(repo, pr, INTENT_MARK, INTENT_SHA_LINE, ac.parse_sha(sha)) is not None


def write_admin_intent(repo: str, pr: int, sha: str) -> bool:
    """迂回でマージする意図の印を置く。同じ先端の印が既にあれば置かない。(置いたか)。置けなければ TrailError。"""
    if has_admin_intent(repo, pr, sha):
        return False
    a = gh_rest.comment(repo, pr, render_admin_intent(sha))
    if not a.ok:
        raise TrailError("unreadable", f"迂回の意図の印を置けない: {a.error[:200]}")
    return True


# 証跡の書き手（#3672 の I12）。ケースごとに 1 つだけで、ほかの部品は同じ証跡を書かない
WRITER_ANSWER = "answer"  # リリース承認ゲートの答えを書く手順（`release-steps answer`）
WRITER_LAND = "land"  # 人のマージの記録（`release-steps land` がマージ済みを見つけたとき）
WRITER_GITHUB = "github"  # GitHub のレビューの承認（NDF は書かない）


def trail_writer(strategy, merged_by_human: bool) -> str:
    """承認の証跡の書き手をケースごとに 1 つ返す（設計文書の「承認とマージ」の表）。"""
    sec = strategy.section
    if sec["approval"]["via"] == "review":
        return WRITER_GITHUB
    if merged_by_human or sec["merge"]["actor"] == "human":
        return WRITER_LAND
    return WRITER_ANSWER
