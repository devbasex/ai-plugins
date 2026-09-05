#!/usr/bin/env bash
# NDF hook installer for agy (Antigravity CLI)
# Usage: bash plugins/ndf/dev.agy/install-hooks.sh [--uninstall] [--dry-run]
#                                                  [--config PATH] [--plugin-dir PATH]
#
# `agy plugin install` は hooks.json を複製するが、**agy はそれを読み込まない**。読む先は
# 利用者の `~/.gemini/config/hooks.json` の 1 か所だけである（agy 1.1.26 で実測。プラグイン
# 配下とプロジェクト直下のどちらに置いても `loaded 1 named hooks from 1 hooks.json file(s)`
# のまま変わらない）。このスクリプトが、導入したプラグインの名前付き hook をその 1 か所へ
# 差し込む。
#
# 冪等である。同じ名前の項目は置き換えるだけで、他の項目には触れない。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

CONFIG_FILE="${HOME}/.gemini/config/hooks.json"
PLUGIN_DIR="${HOME}/.gemini/config/plugins/ndf"
UNINSTALL=false
DRY_RUN=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --uninstall) UNINSTALL=true ;;
    --dry-run) DRY_RUN=true ;;
    --config)
      [ "$#" -ge 2 ] || { echo "ERROR: --config requires a path" >&2; exit 2; }
      CONFIG_FILE="$2"; shift ;;
    --plugin-dir)
      [ "$#" -ge 2 ] || { echo "ERROR: --plugin-dir requires a path" >&2; exit 2; }
      PLUGIN_DIR="$2"; shift ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# 差し込む定義の出どころは、導入した実体の hooks.json である。実体が無いときだけ、この
# リポジトリの dev.agy/hooks.json へ落ちる（導入前に中身を確かめられるようにするため）。
SOURCE_HOOKS="$PLUGIN_DIR/hooks.json"
[ -f "$SOURCE_HOOKS" ] || SOURCE_HOOKS="$SCRIPT_DIR/hooks.json"

if [ "$UNINSTALL" = false ] && [ ! -f "$SOURCE_HOOKS" ]; then
  echo "ERROR: hook の定義が見つからない: $SOURCE_HOOKS" >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: python3 が要る（JSON の統合に使う）" >&2
  exit 1
}

python3 - "$CONFIG_FILE" "$SOURCE_HOOKS" "$PLUGIN_DIR" "$UNINSTALL" "$DRY_RUN" <<'PY'
import json
import pathlib
import shutil
import sys

config_path = pathlib.Path(sys.argv[1])
source_path = pathlib.Path(sys.argv[2])
plugin_dir = pathlib.Path(sys.argv[3])
uninstall = sys.argv[4] == "true"
dry_run = sys.argv[5] == "true"


def load(path: pathlib.Path) -> dict:
    """JSON を読む。無ければ空、壊れていれば止まる（黙って上書きしない）。"""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: {path} を JSON として読めない: {exc}")
    if not isinstance(value, dict):
        sys.exit(f"ERROR: {path} の中身が名前付き hook の辞書ではない")
    return value


def absolutize(node: object) -> object:
    """`bash ./scripts/x.sh` のような相対の指定を、導入先の絶対パスへ直す。

    利用者の設定へ差し込むと、実行時の現在地はプラグインの位置と揃わない。
    """
    if isinstance(node, dict):
        return {k: absolutize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [absolutize(v) for v in node]
    if isinstance(node, str):
        return node.replace("./scripts/", f"{plugin_dir}/scripts/")
    return node


source = {} if uninstall else load(source_path)
config = load(config_path)
names = sorted(source) if not uninstall else sorted(
    n for n in config if n.startswith("ndf-")
)

if uninstall:
    updated = {k: v for k, v in config.items() if k not in names}
else:
    updated = dict(config)
    for name in names:
        updated[name] = absolutize(source[name])

if updated == config:
    print(f"変更なし: {config_path}")
    print(f"対象の hook: {', '.join(names) if names else '（なし）'}")
    sys.exit(0)

action = "削除" if uninstall else "差し込み"
print(f"{action}: {', '.join(names) if names else '（なし）'}")
print(f"書き込み先: {config_path}")

if dry_run:
    print("--dry-run のため書き込まない。書き込む内容:")
    print(json.dumps(updated, ensure_ascii=False, indent=2))
    sys.exit(0)

config_path.parent.mkdir(parents=True, exist_ok=True)
if config_path.exists():
    backup = config_path.with_suffix(config_path.suffix + ".bak")
    shutil.copy2(config_path, backup)
    print(f"退避: {backup}")

config_path.write_text(
    json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print("完了。agy を起動し直すと反映される")
PY
