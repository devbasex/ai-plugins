"""解消する Issue を、閉じる証拠を GitHub で確かめてから閉じる（`ndf issues settle`。#3395 の「閉じる操作」・IY5）。

解消する Issue を閉じるのはこの操作だけで、子の PR の closing keywords では閉じない。確かめられない行は閉じずに
理由を返し、結果を `stopped`（種類 `refused`）にする。閉じるときは証拠をコメントに書いてから閉じる。
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from ndf.adapters.github import PER_PAGE, GitHub, GitHubError, text_of
from ndf.contracts import limits
from ndf.domains.backlog import issues_gh
from ndf.domains.backlog import settle_decide as sd
from ndf.domains.backlog.settle_decide import IMPROVED, REMOVED, Row


def default_branch(gh: GitHub) -> str:
    return text_of(gh.rest("GET", f"/repos/{gh.repo}")["default_branch"])


def file_text(gh: GitHub, path: str, ref: str) -> Optional[str]:
    """基底のブランチ `ref` のファイルの中身。無ければ（HTTP 404）None。"""
    raw = issues_gh.get_or_none(gh, f"/repos/{gh.repo}/contents/{quote(path)}", params={"ref": ref})
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("encoding") != "base64":
        return ""
    return base64.b64decode(text_of(raw["content"])).decode("utf-8", errors="replace")


def pull_files(gh: GitHub, pr: int) -> dict[str, str]:
    """PR の変更ファイル → 差分（`github.list-pages` ページまで）。"""
    pages = int(limits.value("github.list-pages", "count"))
    out: dict[str, str] = {}
    for page in range(1, pages + 1):
        body = gh.rest("GET", f"/repos/{gh.repo}/pulls/{int(pr)}/files", params={"per_page": PER_PAGE, "page": page})
        out.update({text_of(f["filename"]): text_of(f.get("patch")) for f in body})
        if len(body) < PER_PAGE:
            return out
    raise GitHubError(f"PR #{pr} の変更ファイルが {pages} ページを超えた（github.list-pages）")


def _merged_pull(gh: GitHub, pr: int) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    raw = issues_gh.get_or_none(gh, f"/repos/{gh.repo}/pulls/{int(pr)}")
    if raw is None:
        return None, f"PR #{pr} が無い"
    if not raw["merged"]:
        return None, f"PR #{pr} がマージされていない"
    return raw, None


def _base_ref(raw: dict[str, Any]) -> Optional[str]:
    got = raw["base"] if "base" in raw else None
    return text_of(got["ref"]) if isinstance(got, dict) and "ref" in got else None


def check_improved(gh: GitHub, ev: dict[str, Any], base: str) -> Optional[str]:
    """改善の証拠を確かめる。確かめられなければ理由。"""
    path, func = sd.test_target(ev["test"])
    raw, why = _merged_pull(gh, ev["pr"])
    if why is not None:
        return why
    if _base_ref(raw) != base:
        return f"PR #{ev['pr']} の宛先 {_base_ref(raw)} が基底のブランチ {base} でない"
    files = pull_files(gh, ev["pr"])
    if path not in files:
        return f"PR #{ev['pr']} の変更ファイルにテストのファイル {path} が無い"
    if not sd.adds_def(files[path], func):
        return f"PR #{ev['pr']} の差分がテストの関数 {func} の定義を足していない"
    text = file_text(gh, path, base)
    if text is None or not sd.defines(text, func):
        return f"基底のブランチ {base} の {path} にテストの関数 {func} が無い"
    return None


def check_removed(gh: GitHub, ev: dict[str, Any], base: str) -> Optional[str]:
    """消した証拠を確かめる。コミットが基底のブランチの祖先で、パスが基底のブランチに無い。"""
    missing = (issues_gh.NOT_FOUND, issues_gh.UNPROCESSABLE)
    cmp = issues_gh.get_or_none(gh, f"/repos/{gh.repo}/compare/{quote(ev['commit'])}...{quote(base)}", missing=missing)
    if cmp is None:
        return f"コミット {ev['commit']} が無い"
    if not isinstance(cmp, dict) or cmp.get("status") not in ("ahead", "identical"):
        return f"コミット {ev['commit']} が基底のブランチ {base} の祖先でない"
    if file_text(gh, ev["path"], base) is not None:
        return f"基底のブランチ {base} に {ev['path']} がまだある"
    return None


def check_not_reproduced(gh: GitHub, ev: dict[str, Any], base: str) -> Optional[str]:
    """再現しない証拠を確かめる。終了コード 0・記録が基底のブランチにある・日時がマージの後で今より前。"""
    if ev["exit"] != 0:
        return f"再現の手順の終了コードが 0 でない: {ev['exit']}"
    if file_text(gh, ev["record"], base) is None:
        return f"再現の記録 {ev['record']} が基底のブランチ {base} に無い"
    raw, why = _merged_pull(gh, ev["after_pr"])
    if why is not None:
        return why
    merged_at = sd.parse_time(text_of(raw["merged_at"]))
    at = sd.parse_time(ev["at"])
    if merged_at is None or at is None or at <= merged_at:
        return f"再現の日時 {ev['at']} が PR #{ev['after_pr']} のマージ（{raw['merged_at']}）の後でない"
    if at > datetime.now(timezone.utc):
        return f"再現の日時 {ev['at']} が今より後"
    return None


def check(gh: GitHub, row: Row, base: str) -> Optional[str]:
    if row.kind == IMPROVED:
        return check_improved(gh, row.evidence, base)
    if row.kind == REMOVED:
        return check_removed(gh, row.evidence, base)
    return check_not_reproduced(gh, row.evidence, base)


def settle(gh: GitHub, rows: list[Row], base: str, dry_run: bool) -> list[dict[str, Any]]:
    """行ごとに証拠を確かめ、確かめられた open の Issue を閉じる。行ごとの結果（`result` と理由）を返す。"""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {"kind": "issue", "name": f"#{row.issue}", "issue": row.issue, "child": row.child, "evidence": row.kind}
        if issues_gh.issue_state(gh, row.issue) == "closed":
            out.append({**item, "result": sd.ALREADY_CLOSED})
            continue
        why = check(gh, row, base)
        if why is not None:
            out.append({**item, "result": sd.UNVERIFIED, "reason": why})
            continue
        if not dry_run:
            issues_gh.close_with_comment(gh, row.issue, sd.comment(row))
        out.append({**item, "result": sd.VERIFIED if dry_run else sd.CLOSED})
    return out
