#!/usr/bin/env bash
# playwright-kit Plugin Installer for Kiro CLI
# Usage: bash plugins/playwright-kit-kiro/install.sh [--project PATH] [--scope workspace|global] [--dry-run]
#
# Kiro CLI にはプラグイン機構がないため、配布物の skills/ を導入先の skills/ へ symlink する。
# NDF の installer と違い、エージェント定義・常時指示・プロンプト・フックは扱わない
# （この plugin は Skill だけを配る）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$SCRIPT_DIR"
PLUGIN_SKILLS_DIR="$PLUGIN_DIR/skills"
VERSION_FILE="$PLUGIN_DIR/VERSION"

PROJECT_ROOT="$(pwd)"
PROJECT_GIVEN=false
SCOPE="workspace"
DRY_RUN=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --project)
      [ "$#" -ge 2 ] || { echo "ERROR: --project requires a path" >&2; exit 2; }
      PROJECT_ROOT="$(cd "$2" && pwd)"
      PROJECT_GIVEN=true
      shift
      ;;
    --scope)
      [ "$#" -ge 2 ] || { echo "ERROR: --scope requires workspace or global" >&2; exit 2; }
      SCOPE="$2"
      shift
      ;;
    --dry-run) DRY_RUN=true ;;
    --help|-h)
      echo "Usage: bash plugins/playwright-kit-kiro/install.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --project PATH   install into PATH instead of current directory (--scope workspace のみ)"
      echo "  --scope SCOPE    workspace（既定, プロジェクトの .kiro/）または global（~/.kiro/）"
      echo "  --dry-run        書き込みを行わず実行内容を表示"
      echo "  -h, --help       このヘルプを表示"
      exit 0
      ;;
    *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

case "$SCOPE" in
  workspace)
    KIRO_DIR="$PROJECT_ROOT/.kiro"
    ;;
  global)
    [ -n "${HOME:-}" ] || { echo "ERROR: --scope global には HOME が必要です" >&2; exit 2; }
    KIRO_DIR="$HOME/.kiro"
    if [ "$PROJECT_GIVEN" = true ]; then
      echo "WARN: --scope global では --project は使用されません" >&2
    fi
    ;;
  *)
    echo "ERROR: invalid --scope: $SCOPE (workspace|global)" >&2
    exit 2
    ;;
esac

SKILLS_DIR="$KIRO_DIR/skills"

echo "=== playwright-kit Plugin Installer for Kiro CLI ==="
echo "  スコープ: $SCOPE ($KIRO_DIR)"

if [ ! -d "$PLUGIN_SKILLS_DIR" ]; then
  echo "ERROR: $PLUGIN_SKILLS_DIR が見つかりません。先に scripts/build-runtime-plugins.sh を実行してください。" >&2
  exit 1
fi
if [ ! -s "$VERSION_FILE" ]; then
  echo "ERROR: $VERSION_FILE が見つからないか空です" >&2
  echo "       bash scripts/build-runtime-plugins.sh を実行して配布物を生成してください" >&2
  exit 1
fi
PLUGIN_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"

# 既存リンクの掃除は、このプラグイン配下を指すものだけに限る。
# 利用者が置いた実体ディレクトリや他プラグインのリンクには触れない。
if [ "$DRY_RUN" = false ]; then
  mkdir -p "$SKILLS_DIR"
  plugin_skills_abs="$(realpath -m "$PLUGIN_SKILLS_DIR")"
  while IFS= read -r skill_link; do
    target="$(readlink "$skill_link")"
    case "$target" in
      /*) target_abs="$target" ;;
      *) target_abs="$(realpath -m "$(dirname "$skill_link")/$target")" ;;
    esac
    case "$target_abs" in
      "$plugin_skills_abs"/*) rm -f "$skill_link" ;;
    esac
  done < <(find "$SKILLS_DIR" -mindepth 1 -maxdepth 1 -type l | sort)
fi

SKILL_COUNT=0
while IFS= read -r src_dir; do
  skill_name="$(basename "$src_dir")"
  if [ ! -f "$src_dir/SKILL.md" ]; then
    echo "  SKIP: $skill_name (SKILL.mdなし)"
    continue
  fi
  if [ -e "$SKILLS_DIR/$skill_name" ] && [ ! -L "$SKILLS_DIR/$skill_name" ]; then
    echo "  WARN: $SKILLS_DIR/$skill_name はシンボリックリンクではありません。" >&2
    echo "        内容を確認のうえ手動で退避または削除してください。" >&2
    continue
  fi
  if [ "$DRY_RUN" = false ]; then
    ln -sfn "$PLUGIN_SKILLS_DIR/$skill_name" "$SKILLS_DIR/$skill_name"
  fi
  echo "  linked: $skill_name"
  SKILL_COUNT=$((SKILL_COUNT + 1))
done < <(find "$PLUGIN_SKILLS_DIR" -mindepth 1 -maxdepth 1 -type d | sort)

if [ "$DRY_RUN" = true ]; then
  echo ""
  echo "DRY RUN: 書き込みは行いませんでした"
  echo "  playwright-kit バージョン: $PLUGIN_VERSION"
  echo "  Skills数: $SKILL_COUNT (配置先: $SKILLS_DIR)"
  exit 0
fi

echo ""
echo "=== インストール完了 ==="
echo "  playwright-kit バージョン: $PLUGIN_VERSION"
echo "  Skills数: $SKILL_COUNT (シンボリックリンク: $SKILLS_DIR)"
echo ""
echo "playwright_kit の実行環境は playwright-kit-ops skill の手順で用意してください。"
