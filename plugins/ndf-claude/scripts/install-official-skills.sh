#!/bin/bash
# Anthropic公式Skillsのインストーラ
#
# Usage:
#   bash install-official-skills.sh [SKILL_NAMES...]
#   bash install-official-skills.sh --all
#   bash install-official-skills.sh --list
#   bash install-official-skills.sh --scope project [SKILL_NAMES...]
#   bash install-official-skills.sh --update
#
# Examples:
#   bash install-official-skills.sh docx pptx xlsx       # 指定Skillをインストール (~/.claude/skills/)
#   bash install-official-skills.sh --scope project pdf  # プロジェクト .claude/skills/ に配置
#   bash install-official-skills.sh --all                # 全Skillインストール
#   bash install-official-skills.sh --update             # 公式リポジトリを最新化

set -euo pipefail

REPO_URL="https://github.com/anthropics/skills.git"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/anthropic-skills"
USER_SKILLS_DIR="$HOME/.claude/skills"
PROJECT_SKILLS_DIR=".claude/skills"

SCOPE="user"
DO_ALL=false
DO_LIST=false
DO_UPDATE=false
SKILLS=()

# 引数パース
while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      SCOPE="$2"
      shift 2
      ;;
    --all)
      DO_ALL=true
      shift
      ;;
    --list)
      DO_LIST=true
      shift
      ;;
    --update)
      DO_UPDATE=true
      shift
      ;;
    --help|-h)
      sed -n '2,13p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    --*)
      echo "ERROR: unknown option $1" >&2
      exit 1
      ;;
    *)
      SKILLS+=("$1")
      shift
      ;;
  esac
done

# --- クローン or 更新 ---
if [ ! -d "$CACHE_DIR/.git" ]; then
  echo "==> 公式Skillsリポジトリを取得中: $CACHE_DIR"
  mkdir -p "$(dirname "$CACHE_DIR")"
  git clone --depth 1 "$REPO_URL" "$CACHE_DIR"
elif [ "$DO_UPDATE" = true ]; then
  echo "==> 公式Skillsを最新化中"
  git -C "$CACHE_DIR" pull --ff-only
fi

# --- --list: 利用可能Skill一覧を表示 ---
if [ "$DO_LIST" = true ]; then
  echo ""
  echo "=== 利用可能なSkill ==="
  for dir in "$CACHE_DIR"/skills/*/; do
    name=$(basename "$dir")
    license_file="$dir/LICENSE.txt"
    if [ -f "$license_file" ]; then
      if grep -q "Apache License" "$license_file"; then
        license="Apache-2.0"
      elif grep -q "Proprietary\|All rights reserved" "$license_file"; then
        license="Proprietary"
      else
        license="?"
      fi
    else
      license="-"
    fi
    printf "  %-25s [%s]\n" "$name" "$license"
  done
  echo ""
  echo "Proprietary Skillは各自の環境にインストール可能ですが、再配布は禁止です。"
  exit 0
fi

# --- インストール先を決定 ---
case "$SCOPE" in
  user)
    DEST="$USER_SKILLS_DIR"
    ;;
  project)
    if [ ! -d .git ] && [ ! -f .claude-plugin/plugin.json ] && [ ! -f package.json ] && [ ! -f pyproject.toml ]; then
      echo "WARN: プロジェクトルートらしきファイルが見当たりません (.git, package.json 等)。カレントディレクトリ: $(pwd)"
    fi
    DEST="$PROJECT_SKILLS_DIR"
    ;;
  *)
    echo "ERROR: --scope は user / project のいずれか" >&2
    exit 1
    ;;
esac

mkdir -p "$DEST"
echo "==> インストール先: $DEST ($SCOPE scope)"

# --- 対象Skill決定 ---
if [ "$DO_ALL" = true ]; then
  SKILLS=()
  for dir in "$CACHE_DIR"/skills/*/; do
    SKILLS+=("$(basename "$dir")")
  done
fi

if [ "${#SKILLS[@]}" -eq 0 ]; then
  echo "ERROR: インストールするSkillが指定されていません。" >&2
  echo "利用可能な一覧は: bash $0 --list" >&2
  exit 1
fi

# --- シンボリックリンク作成 ---
INSTALLED=()
SKIPPED=()
NOT_FOUND=()
for skill in "${SKILLS[@]}"; do
  src="$CACHE_DIR/skills/$skill"
  dst="$DEST/$skill"

  if [ ! -d "$src" ]; then
    NOT_FOUND+=("$skill")
    continue
  fi

  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
      SKIPPED+=("$skill (既にリンク済み)")
      continue
    fi
    echo "WARN: $dst が既に存在します。上書きしますか? [y/N]"
    read -r ans
    if [ "$ans" != "y" ] && [ "$ans" != "Y" ]; then
      SKIPPED+=("$skill (ユーザーキャンセル)")
      continue
    fi
    rm -rf "$dst"
  fi

  ln -s "$src" "$dst"
  INSTALLED+=("$skill")
done

# --- 結果報告 ---
echo ""
echo "=== インストール結果 ==="
if [ "${#INSTALLED[@]}" -gt 0 ]; then
  echo "インストール済み (${#INSTALLED[@]}個):"
  for s in "${INSTALLED[@]}"; do echo "  ✓ $s"; done
fi
if [ "${#SKIPPED[@]}" -gt 0 ]; then
  echo "スキップ (${#SKIPPED[@]}個):"
  for s in "${SKIPPED[@]}"; do echo "  - $s"; done
fi
if [ "${#NOT_FOUND[@]}" -gt 0 ]; then
  echo "見つからない (${#NOT_FOUND[@]}個):"
  for s in "${NOT_FOUND[@]}"; do echo "  ✗ $s"; done
  echo ""
  echo "一覧確認: bash $0 --list"
fi

echo ""
echo "Claude Codeを再起動するか /plugin reload で反映してください。"
