"""Core Web Vitals (LCP/CLS/TTFB/longest_task) を 1 URL に対して計測する CLI。

`playwright_kit.web_vitals` モジュールの薄いラッパ。runner は testcase 内蔵で同 module
を呼ぶため、本 CLI は外部 URL の単発計測専用。

Usage:
    python check_cwv.py --url https://example.com
    python check_cwv.py --url-list urls.txt --output web_vitals.json
    python check_cwv.py --url https://example.com --device "Pixel 5"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from playwright_kit.web_vitals import THRESHOLDS, judge, measure_page  # noqa: E402


def measure(
    url: str,
    *,
    storage_state: str | None = None,
    device_name: str | None = None,
    timeout_ms: int = 30_000,
    headless: bool = True,
    observe_ms: int = 5000,
) -> dict[str, Any]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx_kwargs: dict[str, Any] = {"ignore_https_errors": True}
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        if device_name:
            device = p.devices.get(device_name)
            if not device:
                browser.close()
                return {"url": url, "error": f"unknown device: {device_name}"}
            ctx_kwargs.update(device)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as exc:
            browser.close()
            return {"url": url, "error": str(exc)}

        metrics = measure_page(page, observe_ms=observe_ms)
        browser.close()
        return {
            "url": url,
            "device": device_name or "desktop",
            "metrics": metrics,
            "judgement": {k: judge(k, v) for k, v in metrics.items()},
            "thresholds": THRESHOLDS,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Core Web Vitals を計測")
    parser.add_argument("--url", help="計測対象 URL")
    parser.add_argument("--url-list", type=Path, help="URL を 1 行 1 件で書いたファイル")
    parser.add_argument("--storage-state", default=None)
    parser.add_argument("--device", default=None,
                        help="Playwright device 名 (例: 'Pixel 5')")
    parser.add_argument("--observe-ms", type=int, default=5000,
                        help="PerformanceObserver 観測時間 (ms)")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--fail-on-poor", action="store_true",
                        help="poor 判定 1 件以上で exit 1")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    if not args.url and not args.url_list:
        parser.error("--url または --url-list が必要です")

    urls = (
        [line.strip() for line in args.url_list.read_text().splitlines() if line.strip()]
        if args.url_list else [args.url]
    )

    results = [
        measure(u, storage_state=args.storage_state, device_name=args.device,
                headless=not args.headed, observe_ms=args.observe_ms)
        for u in urls
    ]

    text = json.dumps(results, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"OK: web_vitals → {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text + "\n")

    has_poor = any(
        any(v == "poor" for v in r.get("judgement", {}).values()) for r in results
    )
    return 1 if args.fail_on_poor and has_poor else 0


if __name__ == "__main__":
    raise SystemExit(main())
