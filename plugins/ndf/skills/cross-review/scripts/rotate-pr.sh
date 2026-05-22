#!/usr/bin/env bash
# PR rotation — light モード (default) / squash モード
#
# Usage:
#   rotate-pr.sh prepare <STATE_PR>
#       旧 PR の title/body/isDraft + git log $BASE..HEAD + git diff --stat を
#       $TMP_DIR/rotate-pr<STATE_PR>-prepare.json に dump する。
#       light モードでは メインセッションの Agent が prepare.json を読み、
#       現状の差分・実装を反映した新 title/body を生成して
#       $TMP_DIR/rotate-pr<STATE_PR>-newtext.json に書き出すこと。
#
#   rotate-pr.sh execute <STATE_PR> [--mode light|squash]   (default: light)
#       light  : 同ブランチで旧 PR を close → 同 head/base で新 PR を作成。
#                title/body は newtext.json から流す。元 PR の isDraft をコピー。
#                PR title に内部用語 (rotated/round/cross-review) は付与しない。
#       squash : 既存挙動。squash 統合した新ブランチ (-rHHMMSS suffix) + 新 PR。
#                title 末尾に "(rotated)"、body は automated text。
#       いずれも stdout に NEW_PR= / NEW_PR_URL= / NEW_BRANCH= を出力。
#
#   rotate-pr.sh <STATE_PR>   (deprecated)
#       旧 1 引数形式。`execute --mode squash` 相当として動くが、stderr に
#       deprecation warning を出す。新規呼び出しは prepare → execute 形式へ移行。
#
# 引数 STATE_PR は state.json の key (= 最初に init した PR 番号)。
# 閉じる「現在の PR」は state.json の `current_pr` を読む。
# state.json の current_pr / pr_history 更新は `state.py set-current-pr` で別途行う。

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=_tmpdir.sh
. "$SCRIPT_DIR/_tmpdir.sh"
TMP_DIR=$(tmpdir)

usage() {
  cat >&2 <<'USAGE'
Usage:
  rotate-pr.sh prepare <STATE_PR>
  rotate-pr.sh execute <STATE_PR> [--mode light|squash]   (default: light)
  rotate-pr.sh <STATE_PR>                        (deprecated, = execute --mode squash)
USAGE
}

# state.json から共通情報を読み出して shell 変数にセットする。
# 呼び出し後: STATE_FILE / WORKTREE / OLD_PR / ROUND_IN_PR が使える。
load_state() {
  local state_pr=$1
  STATE_FILE=$TMP_DIR/cross-review-pr$state_pr-state.json
  [ -s "$STATE_FILE" ] || { echo "state.json not found: $STATE_FILE" >&2; exit 1; }
  WORKTREE=$(jq -r '.worktree_path' "$STATE_FILE")
  OLD_PR=$(jq -r '.current_pr' "$STATE_FILE")
  ROUND_IN_PR=$(jq --argjson p "$OLD_PR" '[.rounds[] | select(.pr == $p)] | length' "$STATE_FILE")
}

cmd_prepare() {
  local state_pr=${1:?STATE_PR required}
  load_state "$state_pr"

  cd "$WORKTREE"

  local pr_json
  pr_json=$(gh pr view "$OLD_PR" \
    --json number,url,title,body,headRefName,baseRefName,isDraft)
  local head_branch base_branch
  head_branch=$(jq -r '.headRefName' <<<"$pr_json")
  base_branch=$(jq -r '.baseRefName' <<<"$pr_json")

  # base が origin にあることを保証 (git log/diff のため)
  if ! git fetch --quiet origin "$base_branch"; then
    echo "⚠ git fetch origin $base_branch に失敗しました。ローカル参照のみで継続します。" >&2
  fi
  if ! git rev-parse --verify --quiet "origin/$base_branch" >/dev/null; then
    echo "⚠ origin/$base_branch が見つかりません。git_log / git_diff_stat は空になります。" >&2
  fi

  local range="origin/$base_branch..HEAD"
  local git_log git_diff_stat
  git_log=$(git log --pretty=format:'%h %s' "$range" 2>/dev/null || echo "")
  git_diff_stat=$(git diff --stat "$range" 2>/dev/null || echo "")

  local out=$TMP_DIR/rotate-pr$state_pr-prepare.json
  jq -n \
    --argjson state_pr "$state_pr" \
    --argjson pr "$pr_json" \
    --argjson round_in_pr "$ROUND_IN_PR" \
    --arg     worktree "$WORKTREE" \
    --arg     git_log "$git_log" \
    --arg     git_diff_stat "$git_diff_stat" \
    '{
      state_pr:      $state_pr,
      old_pr:        $pr.number,
      old_pr_url:    $pr.url,
      worktree_path: $worktree,
      head_branch:   $pr.headRefName,
      base_branch:   $pr.baseRefName,
      is_draft:      $pr.isDraft,
      round_in_pr:   $round_in_pr,
      old_title:     $pr.title,
      old_body:      ($pr.body // ""),
      git_log:       $git_log,
      git_diff_stat: $git_diff_stat
    }' > "$out"

  echo "✅ prepare.json 書き出し: $out" >&2
  # 呼び出し側 (SKILL.md / docs) は eval で stdout を取り込む契約。
  # PR の head/base 由来の値は shell メタ文字を含み得るため必ず printf '%q' で
  # シェルエスケープしてから出力する (例: ブランチ名に "; rm -rf / 等が来ても安全)。
  printf 'PREPARE_JSON=%q\n' "$out"
  printf 'OLD_PR=%q\n'      "$OLD_PR"
  printf 'HEAD_BRANCH=%q\n' "$head_branch"
  printf 'BASE_BRANCH=%q\n' "$base_branch"
  printf 'IS_DRAFT=%q\n'    "$(jq -r '.isDraft' <<<"$pr_json")"
}

# light モード本体: 同ブランチで旧 PR を close → 同 head/base で新 PR 作成。
execute_light() {
  local state_pr=$1
  load_state "$state_pr"

  local prep=$TMP_DIR/rotate-pr$state_pr-prepare.json
  local newtext=$TMP_DIR/rotate-pr$state_pr-newtext.json
  [ -s "$prep" ]    || { echo "prepare.json not found: $prep — 先に rotate-pr.sh prepare $state_pr を実行してください" >&2; exit 1; }
  [ -s "$newtext" ] || { echo "newtext.json not found: $newtext — Agent (general-purpose) で title/body を生成して書き出してください" >&2; exit 1; }

  local head_branch base_branch is_draft new_title new_body
  head_branch=$(jq -r '.head_branch' "$prep")
  base_branch=$(jq -r '.base_branch' "$prep")
  is_draft=$(jq -r '.is_draft' "$prep")
  new_title=$(jq -r '.title' "$newtext")
  new_body=$(jq -r '.body'  "$newtext")

  [ -n "$new_title" ] && [ "$new_title" != "null" ] || { echo "newtext.json に .title がない" >&2; exit 1; }
  # body は空文字列を許容 (GitHub は空 body を許容)。null のみ拒否。
  [ "$new_body" != "null" ] || { echo "newtext.json に .body がない (null)"  >&2; exit 1; }

  cd "$WORKTREE"

  echo "🔄 PR #$OLD_PR rotation (light): 同ブランチ $head_branch で巻き直し (base=$base_branch)" >&2

  # 1. 作業ディレクトリに未 push のコミットがある可能性に備え、close 前に push する。
  #    push しないと新 PR に最新コミットが反映されないケースがあるため必須 (gemini 指摘)。
  #    state.py init は worktree を detached HEAD で作るため、ブランチ名のみで push すると
  #    detached HEAD 上の修正コミットが push されない。HEAD:<branch> 形式で現在の HEAD を
  #    明示する (codex 指摘)。--force / --no-verify は禁止。
  echo "🔼 git push origin HEAD:$head_branch (未 push commit が無ければ no-op)" >&2
  git push origin HEAD:"$head_branch"

  # 2. 旧 PR を close (コメント残し)
  gh pr comment "$OLD_PR" --body "ℹ️ レビューコメント履歴整理のため本 PR を一度 close し、同じブランチ \`$head_branch\` で新 PR を作り直します。ブランチの内容・base は変えません。"
  gh pr close "$OLD_PR"

  # close 後に create が失敗した場合は旧 PR を reopen して rotation の途中停止を回避する
  reopen_old_pr_on_failure() {
    local exit_code=$?
    if [ "$exit_code" -ne 0 ]; then
      echo "⚠ 新 PR 作成に失敗 (exit=$exit_code) — 旧 PR #$OLD_PR を reopen します" >&2
      gh pr reopen "$OLD_PR" >&2 || echo "⚠ 旧 PR #$OLD_PR の reopen にも失敗しました。手動で確認してください。" >&2
    fi
  }
  trap reopen_old_pr_on_failure EXIT

  # 3. 新 PR を同 head/base で作成 (Draft 状態は元 PR から継承)。
  #    body は --body-file - 経由で stdin から渡し、argv 長制限を回避する (gemini 指摘)。
  local create_args=(--base "$base_branch" --head "$head_branch" --title "$new_title" --body-file -)
  if [ "$is_draft" = "true" ]; then
    create_args+=(--draft)
  fi
  local new_pr_url
  new_pr_url=$(printf '%s' "$new_body" | gh pr create "${create_args[@]}")
  # PR 番号は --json number で頑健に取得 (URL 文字列の grep は壊れやすい)
  local new_pr
  new_pr=$(gh pr view "$new_pr_url" --json number -q .number)

  # ここまで来れば create 成功なので trap は解除する
  trap - EXIT

  echo "✅ 新 PR #$new_pr: $new_pr_url" >&2
  # eval される契約。head_branch / URL に shell メタ文字が混ざっても安全なよう %q で escape
  printf 'NEW_PR=%q\n'      "$new_pr"
  printf 'NEW_PR_URL=%q\n'  "$new_pr_url"
  printf 'NEW_BRANCH=%q\n'  "$head_branch"
}

# squash モード本体: 既存挙動を完全維持。
execute_squash() {
  local state_pr=$1
  load_state "$state_pr"

  cd "$WORKTREE"

  local branch base title new_branch
  # state.py init は worktree を `git worktree add --detach origin/<head>` で作るため、
  # `git branch --show-current` は空文字を返す。空のまま new_branch を生成すると
  # `-rHHMMSS` だけのブランチ名になってしまうので、フォールバック順を以下に固定する:
  #   1. git branch --show-current (通常 worktree なら使える)
  #   2. prepare.json の head_branch (prepare 済みなら最も信頼できる)
  #   3. gh pr view --json headRefName (prepare 未実行でも復元可能)
  # (codex round 4 指摘)
  branch=$(git branch --show-current)
  if [ -z "$branch" ]; then
    local prep=$TMP_DIR/rotate-pr$state_pr-prepare.json
    if [ -s "$prep" ]; then
      branch=$(jq -r '.head_branch // empty' "$prep")
    fi
  fi
  if [ -z "$branch" ]; then
    branch=$(gh pr view "$OLD_PR" --json headRefName -q .headRefName)
  fi
  [ -n "$branch" ] || { echo "head branch を復元できませんでした (detached worktree かつ prepare.json / gh pr view から取得失敗)" >&2; exit 1; }
  base=$(gh pr view "$OLD_PR" --json baseRefName -q .baseRefName)
  title=$(gh pr view "$OLD_PR" --json title -q .title)
  new_branch="${branch}-r$(date +%H%M%S)"

  echo "🔄 PR #$OLD_PR rotation (squash): $branch → $new_branch (base=$base)" >&2

  # 1. 既存ブランチを squash して新ブランチに
  git checkout -b "$new_branch"
  git reset --soft "origin/$base"
  git commit -m "$(cat <<EOF
$title

(cross-review rotation: PR #$OLD_PR を squash 統合)
EOF
)"
  git push -u origin "$new_branch"

  # 2. 旧 PR を close (コメント残し)
  gh pr comment "$OLD_PR" --body "🔄 cross-review ループ進行中のため、本 PR を close し新規 PR に巻き直します。 round_in_pr=$ROUND_IN_PR で長尺化を回避。"
  gh pr close "$OLD_PR"

  # 3. 新 PR 作成
  local new_pr_url
  new_pr_url=$(gh pr create --base "$base" --title "$title (rotated)" --body "$(cat <<EOF
## Summary
旧 PR #$OLD_PR をベースに、cross-review クロスレビューループの継続。
旧 PR は round_in_pr=$ROUND_IN_PR で巻き直しのため close 済み。
旧 PR の resolved スレッドは既に修正済み事項。残った指摘はこの PR で再評価する。

<!-- I want to review in Japanese. -->
EOF
)")

  # PR 番号は --json number -q .number で頑健に取得
  local new_pr
  new_pr=$(gh pr view "$new_pr_url" --json number -q .number)

  echo "✅ 新 PR #$new_pr: $new_pr_url" >&2
  # eval される契約。new_branch / URL に shell メタ文字が混ざっても安全なよう %q で escape
  printf 'NEW_PR=%q\n'      "$new_pr"
  printf 'NEW_PR_URL=%q\n'  "$new_pr_url"
  printf 'NEW_BRANCH=%q\n'  "$new_branch"
}

cmd_execute() {
  local state_pr=${1:?STATE_PR required}
  shift
  # --mode 未指定時は light を default (SKILL.md / 02-fix-and-rotation.md / スクリプト
  # 冒頭コメントの「light モード (default)」表記に CLI 契約を揃える, codex round 4 指摘)
  local mode="light"
  while [ $# -gt 0 ]; do
    case $1 in
      --mode)
        mode=${2:?--mode requires light|squash}
        shift 2
        ;;
      --mode=*)
        mode=${1#--mode=}
        shift
        ;;
      *)
        echo "unknown arg: $1" >&2
        usage
        exit 2
        ;;
    esac
  done
  case $mode in
    light)  execute_light  "$state_pr" ;;
    squash) execute_squash "$state_pr" ;;
    *)      echo "invalid --mode: $mode (light|squash)" >&2; exit 2 ;;
  esac
}

# ---- entrypoint ----

if [ $# -eq 0 ]; then
  usage
  exit 2
fi

case $1 in
  prepare)
    shift
    cmd_prepare "$@"
    ;;
  execute)
    shift
    cmd_execute "$@"
    ;;
  -h|--help)
    usage
    ;;
  *)
    # 旧形式: rotate-pr.sh <STATE_PR>  → squash 相当
    if [ $# -eq 1 ] && [[ $1 =~ ^[0-9]+$ ]]; then
      echo "⚠ DEPRECATED: rotate-pr.sh <STATE_PR> 形式は廃止予定です。新形式に移行してください:" >&2
      echo "    rotate-pr.sh prepare $1" >&2
      echo "    rotate-pr.sh execute $1 --mode light|squash" >&2
      echo "  (本実行は --mode squash 相当で継続します)" >&2
      execute_squash "$1"
    else
      usage
      exit 2
    fi
    ;;
esac
