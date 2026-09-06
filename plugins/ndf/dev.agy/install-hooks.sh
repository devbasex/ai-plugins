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
#
# **--uninstall でも定義を読む。** 消す対象を「配布する定義が持つ名前」に限るため、
# 名前の出どころが要る（#417 の 8）。
SOURCE_HOOKS="$PLUGIN_DIR/hooks.json"
[ -f "$SOURCE_HOOKS" ] || SOURCE_HOOKS="$SCRIPT_DIR/hooks.json"

if [ ! -f "$SOURCE_HOOKS" ]; then
  echo "ERROR: hook の定義が見つからない: $SOURCE_HOOKS" >&2
  exit 1
fi

command -v python3 >/dev/null 2>&1 || {
  echo "ERROR: python3 が要る（JSON の統合に使う）" >&2
  exit 1
}

python3 - "$CONFIG_FILE" "$SOURCE_HOOKS" "$PLUGIN_DIR" "$UNINSTALL" "$DRY_RUN" <<'PYEOF'
import json
import os
import pathlib
import re
import shlex
import shutil
import sys
import tempfile

config_path = pathlib.Path(sys.argv[1])
source_path = pathlib.Path(sys.argv[2])
# **相対のまま保存しない。** agy はリポジトリの外でも起動するため、実行時の現在地は
# プラグインの位置と揃わない（#417 の 3）。案内は clone からの相対パスで書いてあり、
# 書かれたとおりに実行すると踏む。
plugin_dir = pathlib.Path(sys.argv[3]).expanduser().resolve()
uninstall = sys.argv[4] == "true"
dry_run = sys.argv[5] == "true"

# 書き換えの対象にする command の形。`bash ./scripts/<名前>` だけを見る。
RELATIVE_COMMAND = re.compile(r"^bash \./scripts/([A-Za-z0-9._-]+)$")

unrewritten: list[str] = []


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


def rewrite_command(command: str) -> str:
    """`bash ./scripts/x.sh` を、導入先の絶対パスで組み立て直す。

    **置換ではなく組み立て直しである。** 置換はシェルの語としての正しさを保証しない。
    導入先に空白や特殊文字が含まれると、**正常終了するのに hook が効かない**状態に
    なる（#417 の 4）。対象の形に当たらない command は書き換えず、書き換えなかった
    ことを出力へ出す（黙って相対のまま保存しない）。
    """
    matched = RELATIVE_COMMAND.match(command)
    if not matched:
        if "./scripts/" in command:
            unrewritten.append(command)
        return command
    target = plugin_dir / "scripts" / matched.group(1)
    return f"bash {shlex.quote(str(target))}"


def absolutize(node: object) -> object:
    """`command` の値だけを、導入先の絶対パスへ直す。"""
    if isinstance(node, dict):
        return {
            key: (
                rewrite_command(value)
                if key == "command" and isinstance(value, str)
                else absolutize(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [absolutize(value) for value in node]
    return node


source = load(source_path)
config = load(config_path)
# **消すのは、配布する定義が持つ名前だけである。** 接頭辞で選ぶと、利用者が自分で
# 書いた `ndf-` 始まりの hook までまとめて消える（#417 の 8）。
names = sorted(n for n in source if n in config) if uninstall else sorted(source)

if uninstall:
    updated = {k: v for k, v in config.items() if k not in names}
else:
    updated = dict(config)
    for name in names:
        updated[name] = absolutize(source[name])

for command in unrewritten:
    print(f"NOTE: 対象の形に当たらないため書き換えられなかった command: {command}")

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

# **置き換えで書く。** 直接書くと長さを 0 へ戻してから書き直すため、中断すると利用者の
# hook が壊れた JSON のまま残る（#417 の 8）。同じディレクトリへ書いてから移す。
handle = tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=str(config_path.parent),
    prefix=config_path.name + ".", suffix=".tmp", delete=False,
)
try:
    with handle:
        handle.write(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, config_path)
except BaseException:
    pathlib.Path(handle.name).unlink(missing_ok=True)
    raise
print("完了。agy を起動し直すと反映される")
PYEOF
