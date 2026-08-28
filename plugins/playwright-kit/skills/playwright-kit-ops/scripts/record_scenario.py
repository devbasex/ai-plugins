"""Playwright codegen ラッパー。経験で書く代わりに「録画→生成」する。

Usage:
    # 標準: 公開 URL を開いて操作を記録
    python record_scenario.py https://example.com

    # 認証必要: 既存の storage_state を読み込む
    python record_scenario.py https://example.com --load-storage auth.json

    # 認証 storage を保存しながら記録
    python record_scenario.py https://example.com --save-storage auth.json

    # mobile デバイスエミュレーション
    python record_scenario.py https://example.com --device "iPhone 13"

    # 出力先 (Python ファイル) を指定
    python record_scenario.py https://example.com --output recorded.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Playwright codegen ラッパー (Python sync 出力)"
    )
    parser.add_argument("url", help="対象 URL")
    parser.add_argument("--target", default="python",
                        choices=["python", "python-async", "python-pytest", "javascript", "java", "csharp"],
                        help="出力言語 (default: python)")
    parser.add_argument("--device", default=None, help="device 名 (例: 'iPhone 13')")
    parser.add_argument("--load-storage", default=None,
                        help="既存 storage_state.json を読み込んで認証済みで開始")
    parser.add_argument("--save-storage", default=None,
                        help="ブラウザ閉じる時に storage_state.json として保存")
    parser.add_argument("--output", type=Path, default=None,
                        help="出力 Python ファイル (省略時は stdout)")
    parser.add_argument("--viewport", default=None,
                        help="viewport (例: 1280x720)")
    parser.add_argument("--user-agent", default=None)
    args = parser.parse_args()

    # playwright CLI が見つかるか (Maj-3: 検出した絶対パスを subprocess に渡し、
    # PATH 再検索による不一致や PATH 改竄リスクを避ける)
    pw = shutil.which("playwright")
    if not pw:
        print("ERROR: playwright CLI が見つかりません。", file=sys.stderr)
        print("  uv sync && uv run playwright install chromium", file=sys.stderr)
        return 2

    cmd = [pw, "codegen", "--target", args.target]
    if args.device:
        cmd.extend(["--device", args.device])
    if args.load_storage:
        cmd.extend(["--load-storage", args.load_storage])
    if args.save_storage:
        cmd.extend(["--save-storage", args.save_storage])
    if args.viewport:
        cmd.extend(["--viewport-size", args.viewport])
    if args.user_agent:
        cmd.extend(["--user-agent", args.user_agent])
    if args.output:
        cmd.extend(["--output", str(args.output)])
    cmd.append(args.url)

    print("Running:", " ".join(cmd), file=sys.stderr)
    print("(ブラウザを開いて操作してください。閉じると Python コードが出力されます)",
          file=sys.stderr)

    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("ERROR: playwright CLI 実行に失敗", file=sys.stderr)
        return 2

    if args.output and args.output.exists():
        lang_label = {
            "python": "Python (sync)",
            "python-async": "Python (async)",
            "python-pytest": "pytest-playwright",
            "javascript": "JavaScript",
            "java": "Java",
            "csharp": "C#",
        }.get(args.target, args.target)
        print(f"\nOK: 録画コード ({lang_label}) → {args.output}", file=sys.stderr)
        print("\n次のステップ:", file=sys.stderr)
        print(f"  1. 出力された {lang_label} コードを開いて get_by_role/get_by_label が",
              file=sys.stderr)
        print("     使われているか確認", file=sys.stderr)
        print("  2. CSS セレクタ (page.locator('#foo')) は a11y 名へ書き換える", file=sys.stderr)
        print("  3. templates/test_<role>.py.template に貼り付け、page_role / role marker を",
              file=sys.stderr)
        print("     付与して `uv run pytest --pwk-config=...` で実行する", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
