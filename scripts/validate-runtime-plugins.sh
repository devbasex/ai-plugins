#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run() {
  echo "==> $*"
  "$@"
}

run bash "$ROOT_DIR/scripts/build-runtime-plugins.sh" --check

# Skill を配る plugin family を manifests/ の有無から検出する（plugins/mcp/* は別系統）。
# 固定リストを置かないのは、family を足したときにチェック対象から漏れる経路を作らないためである。
# 後段の静的解析（claude plugin validate / Kiro installer の dry-run）もこの一覧で回す。
FAMILIES=()
for plugin_dir in "$ROOT_DIR"/plugins/*; do
  [ -d "$plugin_dir/manifests" ] || continue
  FAMILIES+=("$(basename "$plugin_dir")")
done
if [ "${#FAMILIES[@]}" -eq 0 ]; then
  echo "ERROR: plugin family が見つからない（plugins/<family>/manifests）" >&2
  exit 1
fi
echo "==> plugin families: ${FAMILIES[*]}"

run python3 -m json.tool "$ROOT_DIR/.claude-plugin/marketplace.json" >/dev/null

while IFS= read -r manifest; do
  run python3 -m json.tool "$manifest" >/dev/null
done < <(find "$ROOT_DIR/plugins" -path '*/.claude-plugin/plugin.json' -o -path '*/.codex-plugin/plugin.json' | sort)

for family in "${FAMILIES[@]}"; do
  root_manifest="$ROOT_DIR/plugins/$family/plugin.json"
  [ -f "$root_manifest" ] || continue
  run python3 -m json.tool "$root_manifest" >/dev/null
done

for family in "${FAMILIES[@]}"; do
  for agy_manifest in "$ROOT_DIR/plugins/$family/dev.agy/plugin.json" \
                      "$ROOT_DIR/plugins/$family/dev.agy/hooks.json"; do
    [ -f "$agy_manifest" ] || continue
    run python3 -m json.tool "$agy_manifest" >/dev/null
  done
done

while IFS= read -r mcp_config; do
  run python3 -m json.tool "$mcp_config" >/dev/null
done < <(find "$ROOT_DIR/plugins/mcp" -maxdepth 2 -name .mcp.json | sort)

# 定義ファイルの形と突き合わせ。形の検証に pydantic（lib/schema.py）を使うため、根の uv の環境で動く本体へ渡す（#1142 の D8）
run python3 "$ROOT_DIR/scripts/lib/validate_manifests.py" "$ROOT_DIR" "${FAMILIES[@]}"

if command -v claude >/dev/null 2>&1; then
  for family in "${FAMILIES[@]}"; do
    run claude plugin validate "$ROOT_DIR/plugins/$family"
  done
  run claude plugin validate "$ROOT_DIR/.claude-plugin/marketplace.json"
else
  echo "==> claude CLI not found; skipped claude plugin validate"
fi

# agy はクライアント拡張ディレクトリを 1 つのプラグインとして読む。CLI が無い環境では
# 読み飛ばす（claude plugin validate と同じ扱い）。
if command -v agy >/dev/null 2>&1; then
  for family in "${FAMILIES[@]}"; do
    [ -f "$ROOT_DIR/plugins/$family/dev.agy/plugin.json" ] || continue
    run agy plugin validate "$ROOT_DIR/plugins/$family/dev.agy"
  done
else
  echo "==> agy CLI not found; skipped agy plugin validate"
fi

# Kiro の installer は Agent Plugins 仕様 §8.2 のクライアント拡張ディレクトリに置く。
for family in "${FAMILIES[@]}"; do
  installer="$ROOT_DIR/plugins/$family/dev.kiro/install.sh"
  [ -f "$installer" ] || continue
  run bash "$installer" --dry-run >/dev/null
done

# --with-codex を持つのは NDF の installer だけ（Codex 向け Skill も併せて配置する経路）。
# family 共通の引数ではないため、ここだけは対象を明示してチェックする。
run bash "$ROOT_DIR/plugins/ndf/dev.kiro/install.sh" --dry-run --with-codex >/dev/null

while IFS= read -r installer; do
  run bash "$installer" --dry-run >/dev/null
done < <(find "$ROOT_DIR/plugins/mcp" -path '*/dev.kiro/install.sh' | sort)

run python3 "$ROOT_DIR/scripts/check-markdown-links.py" --root "$ROOT_DIR"

# 説明文書（README.md / plugins/ndf/README.md）に書かれた Skill 数と更新案内の版数を、
# マニフェスト・実体・plugin.json と突き合わせる。上の Python ブロックがプラグインの定義
# ファイルだけを見ているため、利用者が読む側の数は版を上げるたびに古くなっていた。
run python3 "$ROOT_DIR/scripts/check-doc-staleness.py" --root "$ROOT_DIR"

# Skill の境界をまたぐ実行の参照を数える。配る Skill を絞る配布先では、相手を
# 配らないと解決できない。例外の一覧に無い参照が増えたときに落ちる。
run python3 "$ROOT_DIR/scripts/check-cross-skill-refs.py" --root "$ROOT_DIR"

# 説明文書と配布物の Markdown が分割の基準（501 行以上）を超えていないかを見る。
# `check-skill-frontmatter.py` は SKILL.md の行数しか見ておらず、docs/ と
# references/ と README.md とリポジトリ直下の文書が対象に入っていなかった。
run python3 "$ROOT_DIR/scripts/check-doc-line-limit.py" --root "$ROOT_DIR"

echo "runtime plugin validation passed"
