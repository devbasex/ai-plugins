"""本番チャネルの宛先が要求するチェックを読み、迂回の前に発行元まで照らす（#3506）。

管理者の迂回は必須のチェックも飛ばすため、迂回で本番チャネルへ入れる前に、宛先の規則（ruleset）と従来の保護が
要求するチェックが先端に現れて通ったことを `GreenWatch` で確かめる。規則が発行元のアプリ（ruleset の `integration_id`・
従来の保護の `app_id`）を指定していれば、そのアプリの check run が先端で通ったことも確かめる（同じ名前の別の発行元の
成功を数えない）。規則は全ページを読み、読めなければ止める（迂回の前提を読めないまま進めない）。
"""

from __future__ import annotations

import json
from typing import NamedTuple
from urllib.parse import quote

import gh_parts
from merged_lib.checks import TOOL
from step_result import EXIT_UNREADABLE, StepError, emit, result

# 一覧の 1 ページの件数（REST の上限）
PER_PAGE = 100
# 従来の保護の `app_id` で「どの発行元でもよい」を表す値
ANY_APP = -1
# 必須のチェックを満たす check run の結論（rollup の判定と同じく SKIPPED・NEUTRAL は通過）
PASSED = frozenset({"success", "neutral", "skipped"})


class Required(NamedTuple):
    """宛先の必須のチェック。`names` は名前（重複なく整列）、`apps` は発行元を指定された名前ごとのアプリの ID。"""

    names: list[str]
    apps: dict[str, frozenset[int]]


NONE = Required([], {})


def _decode_pages(text: str) -> list:
    """`gh api --paginate` の出力（ページごとの JSON を続けたもの）→ ページの値の並び。壊れていれば ValueError。"""
    dec, pos, pages = json.JSONDecoder(), 0, []
    text = text.strip()
    while pos < len(text):
        value, pos = dec.raw_decode(text, pos)
        pages.append(value)
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return pages


def _gh_pages(root, path: str) -> list | None:
    """`gh api --paginate <path>` の全ページ。404 は None、ほかの失敗（途中のページを含む）と壊れた応答は StepError。"""
    sep = "&" if "?" in path else "?"
    p = gh_parts.gh(["api", "--paginate", f"{path}{sep}per_page={PER_PAGE}"], cwd=root)
    if p.returncode != 0:
        if "HTTP 404" in (p.stderr or ""):
            return None
        raise StepError(f"gh api {path} の全ページを読めない: {(p.stderr or '').strip()[:200]}", EXIT_UNREADABLE)
    try:
        return _decode_pages(p.stdout or "")
    except ValueError as e:
        raise StepError(f"gh api {path} の応答を読めない: {e}", EXIT_UNREADABLE) from e


def _gh_json(root, path: str):
    """`gh api <path>` の JSON。404 は None、ほかの失敗と壊れた応答は StepError（迂回の前提を読めないまま進めない）。"""
    p = gh_parts.gh(["api", path], cwd=root)
    if p.returncode != 0:
        if "HTTP 404" in (p.stderr or ""):
            return None
        raise StepError(f"gh api {path} が失敗: {(p.stderr or '').strip()[:200]}", EXIT_UNREADABLE)
    try:
        return json.loads(p.stdout or "null")
    except ValueError as e:
        raise StepError(f"gh api {path} の応答を読めない: {e}", EXIT_UNREADABLE) from e


def _app(value) -> int | None:
    """発行元のアプリの ID（指定が無い・どれでもよい（-1）なら None）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) and value != ANY_APP and value > 0 else None


def _rule_checks(root, branch: str) -> list[tuple[str, int | None]]:
    """宛先の規則（ruleset）の全ページの必須のチェック（名前, 発行元）。"""
    pages = _gh_pages(root, f"repos/{{owner}}/{{repo}}/rules/branches/{quote(branch, safe='')}") or []
    rules: list = []
    for page in pages:
        if not isinstance(page, list):
            raise StepError(f"{branch} の規則を読めない: 規則の配列でない: {type(page).__name__}", EXIT_UNREADABLE)
        rules += page
    return [
        (str(c["context"]), _app(c.get("integration_id")))
        for r in rules
        if isinstance(r, dict) and r.get("type") == "required_status_checks"
        for c in (r.get("parameters") or {}).get("required_status_checks") or []
        if isinstance(c, dict) and c.get("context")
    ]


def _protection_checks(root, branch: str) -> list[tuple[str, int | None]]:
    """宛先の従来の保護の必須のチェック（名前, 発行元）。`contexts` は発行元を持たない。"""
    got = _gh_json(root, f"repos/{{owner}}/{{repo}}/branches/{quote(branch, safe='')}") or {}
    checks = (got.get("protection") or {}).get("required_status_checks") if isinstance(got, dict) else None
    if not isinstance(checks, dict):
        return []
    out = [(str(c), None) for c in checks.get("contexts") or [] if c]
    return out + [
        (str(c["context"]), _app(c.get("app_id"))) for c in checks.get("checks") or [] if isinstance(c, dict) and c.get("context")
    ]


def required_checks_of(root, branch: str) -> Required:
    """宛先 `branch` の規則（ruleset。全ページ）と従来の保護の必須のチェック。

    管理者の迂回は必須のチェックも飛ばすため、迂回で本番チャネルへ入れる前にこれらが先端に現れて通ったことを
    GreenWatch で確かめ、発行元を指定されたものは `check_sources` で照らす（#3506。作り直しの `Lander.required` と同じ
    2 つの置き場を読む）。読めなければ StepError。"""
    pairs = _rule_checks(root, branch) + _protection_checks(root, branch)
    apps: dict[str, set[int]] = {}
    for name, app in pairs:
        if app is not None:
            apps.setdefault(name, set()).add(app)
    return Required(sorted({name for name, _ in pairs}), {k: frozenset(v) for k, v in apps.items()})


def admin_required_checks(root, n: int, base: str) -> Required:
    """迂回で本番チャネルへ入れる PR #n の、宛先 `base` が要求するチェック（GreenWatch が全件を待つ）。`base` が空なら空。
    規則か保護を読めなければ stopped を emit して抜ける（迂回で必須のチェックを飛ばさない）。"""
    if not base:
        return NONE
    try:
        return required_checks_of(root, base)
    except StepError as e:
        emit(result(TOOL, "stopped", str(e), [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": str(e)[:200]}]), e.code)


def _latest_passed(runs: list, app: int) -> bool:
    """発行元 `app` の check run のうち最新（ID が最大）が終わって通ったか。"""
    mine = [r for r in runs if isinstance(r, dict) and ((r.get("app") or {}).get("id")) == app]
    if not mine:
        return False
    last = max(mine, key=lambda r: int(r.get("id") or 0))
    return last.get("status") == "completed" and str(last.get("conclusion") or "").lower() in PASSED


def unmet_sources(root, sha: str, req: Required) -> list[str]:
    """発行元を指定された必須のチェックのうち、指定のアプリの check run が先端 `sha` で通っていないもの（`名前（アプリ ID）`）。
    読めなければ StepError（全ページを読めないまま迂回しない）。"""
    unmet = []
    for name in sorted(req.apps):
        pages = _gh_pages(root, f"repos/{{owner}}/{{repo}}/commits/{sha}/check-runs?check_name={quote(name, safe='')}") or []
        runs = [r for page in pages if isinstance(page, dict) for r in page.get("check_runs") or []]
        unmet += [f"{name}（アプリ {app}）" for app in sorted(req.apps[name]) if not _latest_passed(runs, app)]
    return unmet


def check_sources(root, n: int, sha: str | None, req: Required, items: list) -> None:
    """迂回でマージする前に、発行元を指定された必須のチェックを先端で照らす。通っていないか読めなければ stopped を emit する
    （同じ名前の別の発行元の成功で迂回しない。#3506）。発行元の指定が無ければ gh を打たない。"""
    if not req.apps:
        return
    if not sha:
        why = "先端を読めないため、発行元を指定された必須のチェックを照らせない"
        emit(
            result(
                TOOL,
                "stopped",
                f"#{n} は{why}。迂回でマージしない",
                items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": why}],
            )
        )
    try:
        unmet = unmet_sources(root, sha, req)
    except StepError as e:
        emit(
            result(TOOL, "stopped", str(e), items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": str(e)[:200]}]), e.code
        )
    if unmet:
        emit(
            result(
                TOOL,
                "stopped",
                f"#{n} の必須のチェックが指定の発行元で通っていない: {', '.join(unmet)}。迂回でマージしない",
                items + [{"kind": "check", "name": u, "result": "unmet_source"} for u in unmet],
                next=f"指定のアプリのチェックが {sha[:12]} で通ったかを確かめる",
            )
        )
