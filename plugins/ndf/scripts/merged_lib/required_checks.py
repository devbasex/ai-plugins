"""本番チャネルの宛先が要求するチェックの名前を読む（#3506）。

管理者の迂回は必須のチェックも飛ばすため、迂回で本番チャネルへ入れる前に、宛先の規則（ruleset）と従来の保護が
要求するチェックが先端に現れて通ったことを `GreenWatch` で確かめる。読めなければ止める（迂回の前提を読めないまま進めない）。
"""

from __future__ import annotations

import json

import ci_workflows
import gh_parts
from merged_lib.checks import TOOL
from step_result import EXIT_UNREADABLE, StepError, emit, result


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


def required_checks_of(root, branch: str) -> list[str]:
    """宛先 `branch` の規則（ruleset）と従来の保護の必須のチェックの名前（重複なく整列）。

    管理者の迂回は必須のチェックも飛ばすため、迂回で本番チャネルへ入れる前にこれらが先端に現れて通ったことを
    GreenWatch で確かめる（#3506。作り直しの `Lander.required` と同じ 2 つの置き場を読む）。読めなければ StepError。"""
    try:
        names = ci_workflows.required_contexts(_gh_json(root, f"repos/{{owner}}/{{repo}}/rules/branches/{branch}") or []) or []
    except ValueError as e:
        raise StepError(f"{branch} の規則を読めない: {e}", EXIT_UNREADABLE) from e
    got = _gh_json(root, f"repos/{{owner}}/{{repo}}/branches/{branch}") or {}
    checks = (got.get("protection") or {}).get("required_status_checks") if isinstance(got, dict) else None
    if isinstance(checks, dict):
        names += [str(c) for c in checks.get("contexts") or [] if c]
        names += [str(c["context"]) for c in checks.get("checks") or [] if isinstance(c, dict) and c.get("context")]
    return sorted(set(names))


def admin_required_checks(root, n: int, base: str) -> list[str]:
    """迂回で本番チャネルへ入れる PR #n の、宛先 `base` が要求するチェック（GreenWatch が全件を待つ）。`base` が空なら空。
    規則か保護を読めなければ stopped を emit して抜ける（迂回で必須のチェックを飛ばさない）。"""
    if not base:
        return []
    try:
        return required_checks_of(root, base)
    except StepError as e:
        emit(result(TOOL, "stopped", str(e), [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": str(e)[:200]}]), e.code)
