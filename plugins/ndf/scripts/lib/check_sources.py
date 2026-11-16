"""check_sources.py: 宛先が要求するチェックの名前と発行元（GitHub App）を読み、先端の結果を発行元まで照らす（#3506）。

管理者の迂回は必須のチェックも飛ばすため、迂回の前に宛先の規則（ruleset の `integration_id`）と従来の保護（`app_id`）が
指定した発行元で通ったかを確かめる。照会を打たない純粋な関数だけを置き、読み方は呼ぶ側（`merged_lib.required_checks`・
`release_lib.land_checks`）が持つ。
"""

from __future__ import annotations

from typing import Any, Callable

# 従来の保護の `app_id` で「どの発行元でもよい」を表す値
ANY_APP = -1
# 必須のチェックを満たす check run の結論（rollup の判定と同じく SKIPPED・NEUTRAL は通過）
PASSED = frozenset({"success", "neutral", "skipped"})
# 必須のチェックを満たす commit status の状態
STATUS_PASSED = "success"
# GitHub App の bot の login の末尾（`<アプリの slug>[bot]`。commit status の書き手からアプリを引く）
BOT_SUFFIX = "[bot]"


def app_id(value) -> int | None:
    """発行元のアプリの ID（指定が無い・どれでもよい（-1）なら None）。"""
    return value if isinstance(value, int) and not isinstance(value, bool) and value != ANY_APP and value > 0 else None


def rule_pairs(rules: list) -> list[tuple[str, int | None]]:
    """`rules/branches/<b>` の規則の配列の必須のチェック（名前, 発行元）。"""
    return [
        (str(c["context"]), app_id(c.get("integration_id")))
        for r in rules
        if isinstance(r, dict) and r.get("type") == "required_status_checks"
        for c in (r.get("parameters") or {}).get("required_status_checks") or []
        if isinstance(c, dict) and c.get("context")
    ]


def protection_pairs(branch_info) -> list[tuple[str, int | None]]:
    """`branches/<b>` の応答の従来の保護の必須のチェック（名前, 発行元）。`contexts` は発行元を持たない。"""
    protection = branch_info.get("protection") if isinstance(branch_info, dict) else None
    checks = (protection or {}).get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(checks, dict):
        return []
    out = [(str(c), None) for c in checks.get("contexts") or [] if c]
    return out + [
        (str(c["context"]), app_id(c.get("app_id"))) for c in checks.get("checks") or [] if isinstance(c, dict) and c.get("context")
    ]


def latest_passed(runs: list, app: int) -> bool:
    """発行元 `app` の check run のうち最新（ID が最大）が終わって通ったか。"""
    mine = [r for r in runs if isinstance(r, dict) and ((r.get("app") or {}).get("id")) == app]
    if not mine:
        return False
    last = max(mine, key=lambda r: int(r.get("id") or 0))
    return last.get("status") == "completed" and str(last.get("conclusion") or "").lower() in PASSED


def bot_slug(login: str) -> str | None:
    """GitHub App の bot の login（`<slug>[bot]`）のアプリの slug。bot でなければ None。"""
    slug = login.removesuffix(BOT_SUFFIX)
    return slug if slug and slug != login else None


def bot_app(read: Callable[[str], Any], login: str, cache: dict[str, int | None]) -> int | None:
    """GitHub App の bot（`<slug>[bot]`）が属するアプリの ID。bot でない・アプリが無ければ None。
    `read` は REST のパス → JSON（無ければ None）。同じ書き手は `cache` で 1 回だけ引く。"""
    from urllib.parse import quote

    if login not in cache:
        slug = bot_slug(login)
        got = read(f"apps/{quote(slug, safe='')}") if slug else None
        cache[login] = app_id(got.get("id")) if isinstance(got, dict) else None
    return cache[login]


def status_passed(statuses: list, name: str, app: int, bot_app: Callable[[str], int | None]) -> bool:
    """名前 `name` の commit status のうち、発行元 `app` の bot が付けた最新（ID が最大）が success か。
    `bot_app` は書き手の login → アプリの ID（bot でなければ None）。"""
    mine = [
        s
        for s in statuses
        if isinstance(s, dict) and s.get("context") == name and bot_app(str((s.get("creator") or {}).get("login") or "")) == app
    ]
    return bool(mine) and max(mine, key=lambda s: int(s.get("id") or 0)).get("state") == STATUS_PASSED
