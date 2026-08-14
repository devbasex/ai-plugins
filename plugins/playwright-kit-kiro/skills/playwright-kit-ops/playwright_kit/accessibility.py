"""runner 内蔵の Web アクセシビリティ (axe-core) スキャンモジュール。

Web アクセシビリティ (旧称 a11y) は WCAG 準拠の機械検査を指し、本モジュールは
axe-core (axe-playwright-python) で WCAG 2.0/2.1/2.2 AA レベルの違反を検出する。

`scripts/run_a11y_scan.py` (CLI) はこのモジュールの薄いラッパで、
runner からは `scan_page(page, ...)` を直接呼び出して `EvidenceCollectors`
の `axe_violations` に格納する。

page_role が `lp / list / form / dashboard / cart / checkout / settings / auth`
のとき runner が自動実行する (config.accessibility.auto_roles で上書き可能)。
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page


# WCAG 2.0/2.1/2.2 AA 準拠を最低基準として走査する。
# WCAG 2.0 AAA は適合義務がない (一般的に過剰) ため除外。
DEFAULT_TAGS: tuple[str, ...] = ("wcag2a", "wcag2aa", "wcag21aa", "wcag22aa")

# page_role × a11y 自動実行のデフォルト対象。フォーム / 商取引 / 認証系は a11y 影響大。
DEFAULT_AUTO_ROLES: frozenset[str] = frozenset({
    "lp", "list", "form", "dashboard", "cart", "checkout", "settings", "auth",
})


def is_available() -> bool:
    """axe-playwright-python がインストール済かを確認する (Maj-9: silent fail 対策)。"""
    try:
        import axe_playwright_python.sync_playwright  # noqa: F401
    except ImportError:
        return False
    return True


def scan_page(
    page: Page,
    *,
    tags: tuple[str, ...] | list[str] = DEFAULT_TAGS,
) -> list[dict[str, Any]]:
    """既にロード済みの Page に対し axe-core を実行し violations の list を返す。

    axe-playwright-python が未インストールなら空 list を返す。呼出側は事前に
    `is_available()` で判定し、未インストールならスキップを明示すること。
    """
    try:
        from axe_playwright_python.sync_playwright import Axe
    except ImportError:
        return []

    axe = Axe()
    try:
        results = axe.run(
            page, options={"runOnly": {"type": "tag", "values": list(tags)}},
        )
    except TypeError:
        # axe-playwright-python の旧版は options 非対応
        results = axe.run(page)

    violations: list[dict[str, Any]] = []
    for v in results.response.get("violations", []):
        violations.append({
            "id": v.get("id"),
            "impact": v.get("impact"),
            "tags": v.get("tags", []),
            "help": v.get("help"),
            "helpUrl": v.get("helpUrl"),
            "nodes": [
                {
                    "html": n.get("html", "")[:200],
                    "target": n.get("target", []),
                    "failureSummary": n.get("failureSummary", "")[:300],
                }
                for n in v.get("nodes", [])
            ],
        })
    return violations


def should_auto_scan(
    page_roles: list[str],
    *,
    auto_roles: frozenset[str] = DEFAULT_AUTO_ROLES,
) -> bool:
    """testcase の page_role に基づき axe-core を自動実行すべきか判定する。"""
    return any(r in auto_roles for r in page_roles)
