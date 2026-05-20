"""URL の page role を DOM の (implicit + explicit) role 集計から判定する。

実装はブラウザ DOM クエリで `<button>` / `<a href>` / `<input>` 等を集計し、
`page.evaluate` で取得した role 出現回数 + URL pattern スコアリングで分類する
(完全な a11y tree ではなく軽量な近似)。docs/02-page-roles.md の識別ヒューリスティック
を機械化したもの。

`page.accessibility.snapshot()` ベースのより厳密な分類は将来検討。

Usage:
    python classify_page_role.py --url https://example.com/items
    python classify_page_role.py --url https://example.com/items --storage-state alice.json
    python classify_page_role.py --url-list urls.txt --output classifications.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright


# 役割ごとのスコアリングルール (シンプルな足し算)。
# docs/02-page-roles.md の識別ヒューリスティックを機械可読化したもの。
@dataclass
class RoleSignal:
    role: str
    score: float
    evidence: list[str] = field(default_factory=list)


# URL pattern → role hint
URL_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"^/?$"), "lp", 1.0),
    (re.compile(r"/lp/|/campaign/|/about|/pricing|/features"), "lp", 1.0),
    (re.compile(r"/login|/signin|/register|/forgot-password|/auth/"), "auth", 1.5),
    (re.compile(r"/cart$"), "cart", 1.5),
    (re.compile(r"/checkout"), "checkout", 1.5),
    (re.compile(r"/search|\?q=|\?query="), "search", 1.0),
    (re.compile(r"/dashboard|/analytics|/reports"), "dashboard", 1.0),
    (re.compile(r"/settings|/account|/profile"), "settings", 1.0),
    (re.compile(r"/[^/]+/\d+/edit$|/edit$"), "edit", 1.5),
    (re.compile(r"/[^/]+/\d+$|/[^/]+/[a-z0-9-]{8,}$"), "item", 1.0),
    (re.compile(r"/contact|/signup|/apply|/subscribe"), "form", 0.8),
]


def _evaluate_role_summary(page: Page) -> dict[str, int]:
    """ページから role の登場回数を集計する。"""
    return page.evaluate("""() => {
        const roles = {};
        // role 属性 (明示)
        document.querySelectorAll("[role]").forEach(el => {
            const r = el.getAttribute("role");
            if (r) roles[r] = (roles[r] || 0) + 1;
        });
        // implicit role を簡易判定
        const implicit = {
            'a[href]': 'link', 'button': 'button',
            'h1': 'heading_1', 'h2': 'heading_2', 'h3': 'heading_3',
            'input[type="text"], input[type="email"], input[type="password"], input:not([type]), textarea': 'textbox',
            'input[type="search"]': 'searchbox',
            'input[type="checkbox"]': 'checkbox',
            'input[type="radio"]': 'radio',
            'select': 'combobox',
            'table': 'table', 'tr': 'row',
            'ul li, ol li': 'listitem',
            'nav': 'navigation', 'main': 'main', 'header': 'banner', 'footer': 'contentinfo',
            'form': 'form', 'fieldset': 'group',
            'dialog[open], [role="dialog"][aria-modal="true"]': 'dialog',
            'article': 'article',
            '[aria-current]': 'aria_current',
        };
        Object.entries(implicit).forEach(([sel, name]) => {
            roles[name] = (roles[name] || 0) + document.querySelectorAll(sel).length;
        });
        return roles;
    }""")


def _score_from_dom(roles: dict[str, int], url: str) -> list[RoleSignal]:
    """DOM の role 集計 + URL から各 page role の score を算出。"""
    signals: dict[str, RoleSignal] = {}

    def add(role: str, score: float, evidence: str) -> None:
        s = signals.setdefault(role, RoleSignal(role=role, score=0.0))
        s.score += score
        s.evidence.append(evidence)

    n = roles.get
    # auth: textbox + Email/Password label (label は別途 page から取得)
    if n("textbox", 0) >= 2 and n("button", 0) >= 1:
        add("auth", 0.5, f"textbox×{n('textbox',0)} + button")
    # dialog
    if n("dialog", 0) >= 1:
        add("modal", 1.5, f"dialog×{n('dialog',0)}")
    # list: row >= 5 (table) or listitem >= 5 or article >= 3
    if n("row", 0) >= 5:
        add("list", 1.5, f"row×{n('row',0)}")
    if n("listitem", 0) >= 5:
        add("list", 1.0, f"listitem×{n('listitem',0)}")
    if n("article", 0) >= 3:
        add("list", 0.8, f"article×{n('article',0)}")
    # search
    if n("searchbox", 0) >= 1:
        add("search", 1.5, f"searchbox×{n('searchbox',0)}")
    # form: textbox >= 3 + form 要素
    if n("textbox", 0) >= 3 and n("form", 0) >= 1:
        add("form", 1.0, f"textbox×{n('textbox',0)} + form")
    if n("aria_current", 0) >= 1:
        add("form", 0.5, "aria-current 検出 (step ?)")
        add("wizard", 0.5, "aria-current 検出 (step ?)")
    # edit: textbox + button "保存/Save" (textboxbox 個別判定は generate 側で)
    # item: heading_1 + 説明文 + アクションボタン
    if n("heading_1", 0) == 1 and n("button", 0) >= 1 and n("table", 0) == 0:
        add("item", 0.7, "h1 + button (table なし)")
    # dashboard: region + 多数の値表示
    region_count = n("region", 0)
    if region_count >= 3:
        add("dashboard", 1.0, f"region×{region_count}")
    # lp: link 多数 + heading + button (CTA)
    if n("link", 0) >= 8 and n("heading_1", 0) == 1 and n("textbox", 0) <= 2:
        add("lp", 1.0, f"link×{n('link',0)} + h1 + textbox≤2")

    # URL pattern からの加算
    parsed = urlparse(url)
    path = parsed.path or "/"
    for pat, role, score in URL_PATTERNS:
        if pat.search(path):
            add(role, score, f"URL pattern: {pat.pattern}")

    return sorted(signals.values(), key=lambda s: -s.score)


def _check_login_form(page: Page) -> bool:
    """Email + Password の組合せがあれば auth 候補。"""
    try:
        has_email = page.get_by_label(re.compile(r"email|メール", re.I)).count() > 0
        has_pw = page.get_by_label(re.compile(r"password|パスワード", re.I)).count() > 0
        return has_email and has_pw
    except Exception:
        return False


def classify(
    url: str,
    *,
    storage_state: str | None = None,
    timeout_ms: int = 30_000,
    headless: bool = True,
) -> dict[str, Any]:
    """URL を開いて role を判定し、結果を dict で返す。"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_kwargs: dict = {"ignore_https_errors": True}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        try:
            response = page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception as exc:
            return {"url": url, "error": str(exc)}

        roles = _evaluate_role_summary(page)
        signals = _score_from_dom(roles, url)
        if _check_login_form(page):
            # 強い証拠なので auth スコアを底上げ
            for s in signals:
                if s.role == "auth":
                    s.score += 1.0
                    s.evidence.append("Email + Password label 検出")
                    break
            else:
                signals.insert(0, RoleSignal(role="auth", score=2.0,
                                             evidence=["Email + Password label 検出"]))

        # 上位を取り、score ≥ 1.0 のみ採用
        primary = signals[0] if signals and signals[0].score >= 1.0 else None
        alternates = [s for s in signals[1:] if s.score >= 0.5][:3]

        result: dict[str, Any] = {
            "url": url,
            "status": response.status if response else None,
            "primary_role": primary.role if primary else "unknown",
            "primary_score": primary.score if primary else 0.0,
            "primary_evidence": primary.evidence if primary else [],
            "alternates": [asdict(s) for s in alternates],
            "role_counts": dict(sorted(roles.items(), key=lambda kv: -kv[1])[:20]),
        }
        browser.close()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="URL の page role を判定する")
    parser.add_argument("--url", help="判定対象 URL")
    parser.add_argument("--url-list", type=Path, help="URL を 1 行 1 件で書いたファイル")
    parser.add_argument("--storage-state", default=None,
                        help="ログイン済み storage_state.json (任意)")
    parser.add_argument("--output", type=Path, default=None,
                        help="JSON 出力先 (省略時は stdout)")
    parser.add_argument("--headed", action="store_true", help="ブラウザを headed で起動")
    args = parser.parse_args()

    if not args.url and not args.url_list:
        parser.error("--url または --url-list が必要です")

    urls: list[str]
    if args.url_list:
        urls = [line.strip() for line in args.url_list.read_text().splitlines() if line.strip()]
    else:
        urls = [args.url]

    results = [
        classify(u, storage_state=args.storage_state, headless=not args.headed)
        for u in urls
    ]

    output_text = json.dumps(results, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(output_text, encoding="utf-8")
        print(f"OK: {len(results)} URL を分類 → {args.output}")
    else:
        print(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
