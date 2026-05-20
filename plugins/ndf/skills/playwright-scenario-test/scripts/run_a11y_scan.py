"""axe-core で a11y 違反を検出する CLI (`playwright_kit.accessibility` モジュールの薄いラッパ)。

docs/checklists/checklist-common.md C1 (a11y) の自動走査用。runner はテストケース
内蔵で同 module を呼ぶため、本 CLI は外部 URL の単発スキャン専用。

Usage:
    python run_a11y_scan.py --url https://example.com
    python run_a11y_scan.py --url-list urls.txt --output a11y-report.json
    python run_a11y_scan.py --url https://example.com --storage-state alice.json --tags wcag22aa
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

# playwright_kit 配下を import 可能にする (skill 直下から呼ばれる前提)
_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from playwright_kit.accessibility import DEFAULT_TAGS, scan_page  # noqa: E402


def scan(
    url: str,
    *,
    storage_state: str | None = None,
    tags: list[str] | None = None,
    timeout_ms: int = 30_000,
    headless: bool = True,
) -> dict[str, Any]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_kwargs: dict = {"ignore_https_errors": True}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception as exc:
            browser.close()
            return {"url": url, "error": str(exc)}

        violations = scan_page(page, tags=tuple(tags or DEFAULT_TAGS))
        browser.close()
        return {
            "url": url,
            "violations_count": len(violations),
            "violations": violations,
            "tags": tags or list(DEFAULT_TAGS),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="axe-core で a11y 違反を検出")
    parser.add_argument("--url", help="検査対象 URL")
    parser.add_argument("--url-list", type=Path, help="URL を 1 行 1 件で書いたファイル")
    parser.add_argument("--storage-state", default=None, help="ログイン済 storage_state.json")
    parser.add_argument("--tags", nargs="+", default=list(DEFAULT_TAGS),
                        help=f"axe-core タグ (default: {list(DEFAULT_TAGS)})")
    parser.add_argument("--output", type=Path, default=None,
                        help="JSON 出力先 (省略時 stdout)")
    parser.add_argument("--fail-on-violations", action="store_true",
                        help="violations_count > 0 で exit 1")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    if not args.url and not args.url_list:
        parser.error("--url または --url-list が必要です")

    urls = (
        [line.strip() for line in args.url_list.read_text().splitlines() if line.strip()]
        if args.url_list else [args.url]
    )

    results = [
        scan(u, storage_state=args.storage_state, tags=args.tags,
             headless=not args.headed)
        for u in urls
    ]

    text = json.dumps(results, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"OK: a11y scan → {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text + "\n")

    total_violations = sum(r.get("violations_count", 0) for r in results)
    print(f"violations: {total_violations}", file=sys.stderr)
    return 1 if args.fail_on_violations and total_violations > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
