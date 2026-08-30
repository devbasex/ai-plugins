#!/usr/bin/env bash
# cross-refactoring: 作業ディレクトリの準備と、手順書となる Skill の配置。
#
# Usage:
#   prepare-worktrees.sh <ID>            # 作成 + Skill 配置（冪等）
#   prepare-worktrees.sh <ID> sync <sha> # 読み取り用を指定 SHA へ同期
#
# 構成:
#   <worktree-root>/
#   ├── work/        書き込み用。Pull Request の head ブランチ（唯一の非 detach）
#   ├── <参加1>/     読み取り用。--detach
#   ├── <参加2>/     読み取り用。--detach
#   ├── <参加3>/     読み取り用。--detach
#   └── work/.cross_refactoring/   状態ファイル / プロンプト / 結果 / ログ
#
# **同一ブランチを 2 つの作業ディレクトリへ checkout できない**という git の制約が
# あるため、提案・レビュー用は必ず `--detach` にする。
#
# 読み取り専用でも作業ディレクトリを分ける理由は 3 つ。
#   1. レビュー担当がテストを実行して振る舞い不変を確認するため、書き込める領域が要る
#   2. テスト実行が生む生成物（キャッシュ / 依存 / ビルド出力）が競合しない
#   3. gemini の作業領域制約を、各自のディレクトリ内で完結させて回避できる

set -euo pipefail

ID=${1:?ID required}
MODE=${2:-prepare}
SYNC_SHA=${3:-}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# `<...>/skills/cross-refactoring/scripts` の 2 つ上が Skill の置き場所。
# 共通編集元でも各ランタイムの配布物でも同じ形なので、環境変数に頼らずに解決できる。
SKILLS_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)

command -v jq >/dev/null 2>&1 || { echo "jq が必要です" >&2; exit 1; }

TMP_DIR=${CROSS_REFACTORING_TMP_DIR:?CROSS_REFACTORING_TMP_DIR を export してください}
STATE=$TMP_DIR/cross-refactoring-rf$ID-state.json
[ -s "$STATE" ] || { echo "状態ファイルがありません: $STATE" >&2; exit 1; }

ROOT=$(jq -r '.worktree_root' "$STATE")
WORK=$(jq -r '.worktrees.work' "$STATE")
HEAD_BRANCH=$(jq -r '.head_branch' "$STATE")
# `mapfile` は bash 4 以降にしかない。macOS が標準で持つ bash は 3.2 で、そこでは
# 127 を返して読み込みが空になる。読み込みは while ループで行う。
read_lines() {
  LINES=()
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    LINES+=("$line")
  done
}

read_lines < <(jq -r '.runtimes[]' "$STATE")
RUNTIMES=("${LINES[@]+"${LINES[@]}"}")
read_lines < <(jq -r '.skills.required[]' "$STATE")
REQUIRED_SKILLS=("${LINES[@]+"${LINES[@]}"}")

# ランタイム標準の配置先。**利用者のホームと対象リポジトリ本体には一切書き込まない。**
#
# gemini だけは NDF の配布先ではないため「標準の配置先」が無い。それでも
# **スメルと手法の語彙を読ませないと提案が語彙外になり全件降格する**ので、
# 同じ形の場所へ置き、プロンプトの明示パスで読ませる（kiro と同じ扱い）。
skill_dir_for() {
  case "$1" in
    claude) echo ".claude/skills" ;;
    codex)  echo ".agents/skills" ;;
    kiro)   echo ".kiro/skills" ;;
    gemini) echo ".gemini/skills" ;;
    *)      echo ""; return 1 ;;
  esac
}

# 現リポジトリに登録済みの作業ディレクトリか。存在しても別リポジトリの残骸なら
# 流用すると git 操作が壊れるため、必ず確認してから使う。
is_registered_worktree() {
  local target
  target=$(cd -- "$1" 2>/dev/null && pwd) || return 1
  git worktree list --porcelain | grep -qx "worktree $target"
}

ensure_readonly_worktree() {
  local dir=$1 sha=$2
  if [ -d "$dir" ]; then
    if is_registered_worktree "$dir"; then
      git -C "$dir" checkout --detach "$sha" >/dev/null 2>&1 \
        || { git -C "$dir" fetch origin >/dev/null 2>&1
             git -C "$dir" checkout --detach "$sha" >/dev/null; }
      return
    fi
    local stale="$dir.stale-$(date +%Y%m%d%H%M%S)"
    mv "$dir" "$stale"
    echo "⚠ 現リポジトリの作業ディレクトリでないため退避しました: $stale" >&2
  fi
  git worktree prune
  git worktree add --detach "$dir" "$sha" >/dev/null
  echo "✅ 読み取り用の作業ディレクトリを作成しました: $dir" >&2
}

# 対象リポジトリが元から Skill を持っている場合は**それを使い、上書きしない**。
# 利用者の設定を壊さないため。
provision_skill() {
  local base=$1 runtime=$2 name=$3
  local rel dest src
  if ! rel=$(skill_dir_for "$runtime"); then
    echo "unsupported"
    return
  fi
  dest="$base/$rel/$name"
  src="$SKILLS_ROOT/$name"

  if [ -f "$dest/SKILL.md" ]; then
    echo "preexisting"
    return
  fi
  # **SKILL.md が無いだけで既存の中身を消さない。** 利用者が作りかけている Skill や
  # 補助ファイルを失う。空でないディレクトリがあれば触らず、衝突として報告する。
  if [ -e "$dest" ] && [ -n "$(ls -A "$dest" 2>/dev/null)" ]; then
    echo "conflict"
    return
  fi
  if [ ! -f "$src/SKILL.md" ]; then
    echo "missing"
    return
  fi
  mkdir -p "$(dirname "$dest")"
  # シンボリックリンクにはしない。作業ディレクトリを消せば残らない状態にする。
  # ここで消えるのは空ディレクトリか、存在しないパスだけである。
  rm -rf "$dest"
  cp -R "$src" "$dest"
  ignore_dir "$dest"
  echo "provisioned"
}

# 生成物が Pull Request の差分に混入しないよう、**配置したディレクトリの中だけで**
# 完結する形で除外する。
#
# `.git/worktrees/<name>/info/exclude` は使えない。現行の git は作業ディレクトリごとの
# `info/exclude` を読まず、共通の `.git/info/exclude` を見る（実測で確認）。そちらへ
# 書くと**対象リポジトリ本体を書き換える**ことになり、「配置は作業ディレクトリの中だけで
# 完結させる」という前提を破る。
#
# 代わりに、配置したディレクトリ自身へ全件無視の `.gitignore` を置く。自分自身も
# 無視されるため差分に現れず、他の未追跡ファイルには影響しない。
ignore_dir() {
  mkdir -p "$1"
  printf '*\n' > "$1/.gitignore"
}

# gemini は**除外設定を読み取りにも適用する**。上の全件無視をそのまま効かせると、
# 配置した手順書を `read_file` で一切開けない。
#
#   Error executing tool read_file: File path '.../.gemini/skills/refactoring/SKILL.md'
#   is ignored by configured ignore patterns.
#
# 手順書自身が「兆候と手法の語彙を読ませないと提案が語彙外になって全件降格する」と
# 書いている前提が崩れるため、**作業ディレクトリ限定で**読み取り側の除外を無効にする。
# 設定の項目名は gemini の版で変わるので、新旧どちらの形式でも書く（0.55.1 で確認）。
configure_gemini_reading() {
  local base=$1
  local dir="$base/.gemini"
  mkdir -p "$dir"
  cat > "$dir/settings.json" <<'GEMINI_SETTINGS_EOF'
{
  "context": {
    "fileFiltering": {
      "respectGitIgnore": false,
      "respectGeminiIgnore": false
    }
  },
  "fileFiltering": {
    "respectGitIgnore": false,
    "respectGeminiIgnore": false
  }
}
GEMINI_SETTINGS_EOF
  # 設定そのものも差分に出さない。`.gemini/` ごと無視する。
  printf '*\n' > "$dir/.gitignore"
}


HEAD_SHA=$(git rev-parse "origin/$HEAD_BRANCH" 2>/dev/null || git -C "$WORK" rev-parse HEAD)

if [ "$MODE" = "sync" ]; then
  [ -n "$SYNC_SHA" ] || { echo "sync には SHA が必要です" >&2; exit 1; }
  git fetch origin >/dev/null 2>&1 || true
  for rt in "${RUNTIMES[@]}"; do
    git -C "$ROOT/$rt" checkout --detach "$SYNC_SHA" >/dev/null
    echo "↻ $rt を $SYNC_SHA へ同期しました" >&2
  done
  exit 0
fi

mkdir -p "$ROOT"
[ -d "$WORK" ] || { echo "書き込み用の作業ディレクトリがありません: $WORK（init を先に実行してください）" >&2; exit 1; }

for rt in "${RUNTIMES[@]}"; do
  ensure_readonly_worktree "$ROOT/$rt" "$HEAD_SHA"
done

# 配置結果を状態ファイルへ記録する。黙って劣化した状態で走らせない。
RECORD='{}'
MISSING=()
CONFLICT=()

for rt in "${RUNTIMES[@]}"; do
  # 配置より先に置く。gemini は起動時に 1 度だけ設定を読むため、
  # 手順書を配ってから設定を書いても間に合う保証がない。
  [ "$rt" = "gemini" ] && configure_gemini_reading "$ROOT/$rt"
  entry='{}'
  for name in "${REQUIRED_SKILLS[@]}"; do
    status=$(provision_skill "$ROOT/$rt" "$rt" "$name")
    [ "$status" = "missing" ] && MISSING+=("$rt/$name")
    [ "$status" = "conflict" ] && CONFLICT+=("$rt/$name")
    entry=$(jq --arg n "$name" --arg s "$status" '. + {($n): $s}' <<<"$entry")
  done
  RECORD=$(jq --arg rt "$rt" --argjson e "$entry" '. + {($rt): $e}' <<<"$RECORD")
done

# **実装担当はラウンドごとに変わる**ため、work/ には 3 ランタイム分すべての配置先を作る。
for rt in claude codex kiro; do
  entry='{}'
  for name in "${REQUIRED_SKILLS[@]}"; do
    status=$(provision_skill "$WORK" "$rt" "$name")
    [ "$status" = "missing" ] && MISSING+=("work/$rt/$name")
    [ "$status" = "conflict" ] && CONFLICT+=("work/$rt/$name")
    entry=$(jq --arg n "$name" --arg s "$status" '. + {($n): $s}' <<<"$entry")
  done
  RECORD=$(jq --arg rt "work.$rt" --argjson e "$entry" '. + {($rt): $e}' <<<"$RECORD")
done

# 状態ファイル・プロンプト・結果・ログも差分に出さない。
ignore_dir "$TMP_DIR"

# 2 回目以降は自分が置いたものも「既存」に見えるため、一度 `provisioned` と
# 記録したものはそのまま残す。何を配置したのかが再開で消えないようにする。
TMP_STATE=$STATE.tmp
jq --argjson r "$RECORD" '
  .skills = reduce ($r | to_entries[]) as $rt (.skills;
    .[$rt.key] = reduce ($rt.value | to_entries[]) as $s ((.[$rt.key] // {});
      .[$s.key] = (
        if (.[$s.key] == "provisioned" and $s.value == "preexisting")
        then "provisioned" else $s.value end)))
' "$STATE" > "$TMP_STATE"
mv "$TMP_STATE" "$STATE"

if [ ${#CONFLICT[@]} -gt 0 ]; then
  echo "❌ 配置先に SKILL.md の無い既存の中身があります: ${CONFLICT[*]}" >&2
  echo "   利用者の作りかけを消さないため、触らずに中断します。" >&2
  echo "   内容を確認のうえ、退避または削除してから再実行してください" >&2
  exit 1
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "❌ ホスト側にも見つからない Skill があります: ${MISSING[*]}" >&2
  echo "   黙って劣化した状態では走らせません" >&2
  exit 1
fi

echo "✅ 作業ディレクトリと Skill の配置が完了しました" >&2
