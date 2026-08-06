#!/usr/bin/env bash
# スライドを PDF と単一ファイル HTML に書き出す。
# Usage: bash docs/presentations/build.sh [SLIDE.md]
set -euo pipefail

cd "$(dirname "$0")"
SRC="${1:-2026-08-06-ai-plugins-intro.md}"
BASE="${SRC%.md}"
MARP="npx -y @marp-team/marp-cli@4"

command -v npx >/dev/null 2>&1 || { echo "ERROR: npx が必要です" >&2; exit 1; }

if [ -z "${CHROME_PATH:-}" ]; then
  for candidate in "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux/chrome \
                   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome; do
    [ -x "$candidate" ] && { export CHROME_PATH="$candidate"; break; }
  done
fi
if [ -z "${CHROME_PATH:-}" ]; then
  echo "ERROR: Chromium が見つかりません。以下で取得して CHROME_PATH を設定してください:" >&2
  echo "  npx playwright@1.49 install chromium" >&2
  exit 1
fi

echo "==> PDF: $BASE.pdf"
$MARP --pdf --pdf-outlines --allow-local-files -o "$BASE.pdf" "$SRC"

echo "==> HTML: $BASE.html"
$MARP --html --allow-local-files -o "$BASE.html" "$SRC"

# Marp の HTML 出力は画像を相対パスで参照し、絵文字を CDN の SVG に置き換える。
# 単体で配布・閲覧できるように、画像を data URI へ埋め込み、絵文字は文字に戻す。
echo "==> HTML を単一ファイル化"
python3 - "$BASE.html" <<'PY'
import base64, mimetypes, pathlib, re, sys

path = pathlib.Path(sys.argv[1])
html = path.read_text(encoding="utf-8")

def embed(match):
    src = match.group(1)
    asset = path.parent / src
    if not asset.is_file():
        sys.exit(f"ERROR: 画像が見つかりません: {asset}")
    mime = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    data = base64.b64encode(asset.read_bytes()).decode("ascii")
    return match.group(0).replace(src, f"data:{mime};base64,{data}")

html, images = re.subn(r'<img[^>]*src="((?!data:|https?:)[^"]+)"[^>]*>', embed, html)
html, emojis = re.subn(r'<img[^>]*data-marp-twemoji[^>]*alt="([^"]*)"[^>]*/?>',
                       lambda m: m.group(1), html)
html, emojis2 = re.subn(r'<img[^>]*alt="([^"]*)"[^>]*data-marp-twemoji[^>]*/?>',
                        lambda m: m.group(1), html)

# 本文中のリンク (<a href>) は残ってよい。表示に必要な外部アセットだけを検出する。
assets = re.findall(r'\bsrc="(https?://[^"]+)"', html)
assets += re.findall(r'<link[^>]*href="(https?://[^"]+)"', html)
if assets:
    sys.exit("ERROR: 外部アセット参照が残っています: " + ", ".join(sorted(set(assets))))

path.write_text(html, encoding="utf-8")
print(f"    画像 {images} 件を埋め込み、絵文字 {emojis + emojis2} 件を文字に戻しました")
PY

echo "完了: $BASE.pdf / $BASE.html"
