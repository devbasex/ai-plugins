#!/usr/bin/env bash
# NDF plugin: 設計 Pull Request のマージを承認の印に縛る判定（#266）。
#
# `workflow-common.sh` が読み込む。単独では使わない（分割・JSON・リポジトリの解決を
# そちらへ置いているため）。**この層は通信を行う唯一の場所である。**
#
# **判定できないときは拒否する。** `curl` の REST は `gh` が無くても成立するため、
# 通せば判定が抜けたままマージが動く。マージは取り消せず、設計を実装より先に確定させる
# 工程はマージが済んだ時点で意味を失う。

# コマンドの本文がマージなら 0 を返し、対象の番号を出す（番号が無ければ空を出す）。
#
# 拾う形は 2 つある。`gh pr merge`（番号あり・番号なし・Pull Request の URL）と、
# REST の経路（`…/pulls/<番号>/merge`）である。マージそのものが REST で行われることが
# あるため、両方を判定の対象にする。
#
# **`gh` と `pr` の間に置かれたグローバルオプションを読み飛ばす。**
# `gh -R devbasex/ai-plugins pr merge 268` は日常的な書き方で、飛ばさないと語の並びが
# `gh` → `pr` にならず、判定そのものへ入れない。実機（gh 2.98.0）で確かめた受け付ける形は
# 次のとおりである。
#
#   - 値を別の語で取る:   `-R <slug>` / `--repo <slug>`
#   - 値を同じ語に含む:   `-R=<slug>` / `--repo=<slug>` / `-R<slug>`
#   - 値を取らない:       `--help`
#
# `-h` と `--version` は `pr` の前に置くと `gh` が拒む。知らないオプションも拒まれるため、
# 値を別の語で取るのは `-R` / `--repo` の 2 つに限られる。**それ以外の `-` で始まる語は
# その語だけを飛ばす。** 値まで飛ばすと、続く語が `pr` であっても見落とす。
wf_merge_target() {
  local cmd="${1:-}" tok num="" state=0 found=1 rest
  while IFS= read -r -d '' tok; do
    # REST の経路。`pulls/<番号>/merge` を指す語は、方式を問わずマージの意図と見なす。
    case "$tok" in
      *pulls/*/merge|*pulls/*/merge/*)
        rest=${tok##*pulls/}
        rest=${rest%%/*}
        case "$rest" in
          ''|*[!0-9]*) ;;
          *) num="$rest"; found=0 ;;
        esac
        ;;
    esac
    if [ "$state" -ne 3 ]; then
      state=$(_wf_seek_gh_verb "$state" "$tok" "merge")
      [ "$state" = "3" ] && found=0
    fi
    case "$state" in
      3)
        [ -n "$num" ] && continue
        case "$tok" in
          *[!0-9]*)
            case "$tok" in
              */pull/*)
                rest=${tok##*/pull/}
                rest=${rest%%/*}
                case "$rest" in ''|*[!0-9]*) ;; *) num="$rest" ;; esac
                ;;
            esac
            ;;
          '') ;;
          *) num="$tok" ;;
        esac
        ;;
    esac
  done < <(wf_split "$cmd")
  [ "$found" -eq 0 ] || return 1
  printf '%s\n' "$num"
  return 0
}

# jq や awk で読み解けない入力のための、粗い見分け。**判定の対象を広く採る側へ倒す。**
# hook の matcher が `Bash` に限っているため、ここへ来る本文はコマンドである。
#
# `gh` と `pr` の間にはグローバルオプションが入りうるので、語を挟んだ形も拾う。命令の
# 区切り（`|` `;` `&`）を越えたものは別のコマンドなので、そこで打ち切る。
wf_looks_like_merge_text() {
  grep -qE 'gh[[:space:]]+([^|;&]*[[:space:]])?pr[[:space:]]+merge|pulls/[0-9]+/merge' <<<"${1:-}"
}

wf_deny_missing_label() {
  local num="${1:-<番号>}" head="${2:-}"
  printf '設計 Pull Request #%s（head: %s）は承認の印が付いていないためマージしません。\n' "$num" "$head"
  printf '人間の承認を得てから、承認の印（ラベル %s）を付けてください。付けば同じコマンドがそのまま通ります。\n' \
    "$WF_APPROVAL_LABEL"
  printf '  gh pr edit %s --add-label %s\n' "$num" "$WF_APPROVAL_LABEL"
  printf '設計 Pull Request でない場合は、head のブランチ名を %s 以外へ変えてください。\n' "$WF_DESIGN_PREFIX"
  printf '  git branch -m <新しい名前> && git push -u origin <新しい名前>\n'
}

wf_deny_undetermined() {
  local num="${1:-}" what="${2:-}"
  [ -n "$num" ] || num='<番号>'
  printf '設計 Pull Request のマージかどうかを判定できないため、このマージを止めます。\n'
  printf '確かめられなかった値: %s\n' "$what"
  printf '番号を書いて実行し直すと判定できることがあります。\n'
  printf '  gh pr merge %s --squash\n' "$num"
  printf '承認の印（ラベル %s）を付ける手順:\n' "$WF_APPROVAL_LABEL"
  printf '  gh pr edit %s --add-label %s\n' "$num" "$WF_APPROVAL_LABEL"
  printf '設計 Pull Request でない場合は、head のブランチ名を %s 以外へ変えてください。\n' "$WF_DESIGN_PREFIX"
}

_wf_require_merge_tools() {
  local num="${1:-}"
  command -v jq >/dev/null 2>&1 || {
    wf_deny_undetermined "$num" '承認の印（判定に要る jq が無い）'; return 1; }
  command -v git >/dev/null 2>&1 || {
    wf_deny_undetermined "$num" '承認の印（判定に要る git が無い）'; return 1; }
  command -v gh >/dev/null 2>&1 || {
    wf_deny_undetermined "$num" '承認の印（判定に要る gh が無い）'; return 1; }
  return 0
}

_wf_resolve_pr_info() {
  local slug="${1:-}" num="${2:-}" owner branch list json
  owner=${slug%%/*}

  # **問い合わせは REST に限る。** GraphQL は利用上限で落ちる。番号があるときは
  # `pulls/<番号>` を 1 回、無いときは `pulls?head=…` を 1 回で、どちらも番号・
  # head のブランチ名・ラベルが同じ応答で返る。
  if [ -n "$num" ]; then
    json=$(gh api "/repos/$slug/pulls/$num" 2>/dev/null) || {
      wf_deny_undetermined "$num" 'head のブランチ名と承認の印（Pull Request の問い合わせに失敗）'; return 1; }
  else
    branch=$(git branch --show-current 2>/dev/null)
    [ -n "$branch" ] || {
      wf_deny_undetermined "" 'Pull Request の番号（現在のブランチ名を取れない）'; return 1; }
    list=$(gh api "/repos/$slug/pulls?head=$owner:$branch" 2>/dev/null) || {
      wf_deny_undetermined "" "Pull Request の番号（ブランチ $branch の問い合わせに失敗）"; return 1; }
    json=$(jq -c '.[0] // empty' <<<"$list" 2>/dev/null)
    [ -n "$json" ] || {
      wf_deny_undetermined "" "Pull Request の番号（ブランチ $branch に対応する Pull Request が無い）"; return 1; }
  fi

  printf '%s\n' "$json"
  return 0
}

_wf_verify_approval_label() {
  local slug="${1:-}" num="${2:-}" head="${3:-}" json="${4:-}" out rc
  jq -e --arg l "$WF_APPROVAL_LABEL" 'any(.labels[]?; .name == $l)' <<<"$json" >/dev/null 2>&1 && return 0

  # 印が無い。**定義そのものが無いことを確かめられたときだけ通す**（決定 6）。
  # ラベルの定義が、この仕組みを有効にする宣言を兼ねる。
  out=$(gh api "/repos/$slug/labels/$WF_APPROVAL_LABEL" 2>&1)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    case "$out" in
      *404*) return 0 ;;
      *) wf_deny_undetermined "$num" "承認の印（ラベル $WF_APPROVAL_LABEL）の定義の有無"; return 1 ;;
    esac
  fi

  wf_deny_missing_label "$num" "$head"
  return 1
}

# マージを通してよければ 0 を返し、何も出さない。止めるときは理由を出して 1 を返す。
wf_check_merge() {
  local cmd="${1:-}" num slug head json
  num=$(wf_merge_target "$cmd") || return 0

  _wf_require_merge_tools "$num" || return 1

  slug=$(wf_repo_slug ".") || {
    wf_deny_undetermined "$num" 'リポジトリの名前（origin の URL を取れない）'; return 1; }

  json=$(_wf_resolve_pr_info "$slug" "$num") || {
    printf '%s\n' "$json"
    return 1
  }
  [ -n "$num" ] || num=$(jq -r '.number // empty' <<<"$json" 2>/dev/null)

  head=$(jq -r '.head.ref // empty' <<<"$json" 2>/dev/null)
  [ -n "$head" ] || {
    wf_deny_undetermined "$num" 'head のブランチ名（応答から読み取れない）'; return 1; }

  # 設計 Pull Request でなければ、この仕組みは関わらない。
  case "$head" in "$WF_DESIGN_PREFIX"*) ;; *) return 0 ;; esac

  _wf_verify_approval_label "$slug" "$num" "$head" "$json"
}
