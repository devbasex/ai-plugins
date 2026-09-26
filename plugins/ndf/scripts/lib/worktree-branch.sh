#!/usr/bin/env bash
# NDF plugin: worktree の一覧・追従先・ブランチの判定。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。

# --- 作業ツリーの一覧と追従先の判定 -----------------------------------------

# ディレクトリで今チェックアウトしているブランチ名を出力する。detached HEAD や
# リポジトリの外では何も出さず 1 を返す。
wt_current_branch() {
  git -C "${1:-}" symbolic-ref --short -q HEAD 2>/dev/null
}

# 開発用の作業ツリーを `<パス><タブ><ブランチ名>` の形で 1 行 1 件で出力する。
# 対象は主ディレクトリ直下の .worktrees/ 配下に限る。レビュー用の作業ツリーは
# 非永続領域に置かれるため、この一覧には入らない。
wt_dev_worktrees() {
  local main_dir="${1:-}" prefix path branch
  [ -n "$main_dir" ] || return 1
  prefix="$main_dir/$WT_WORKTREE_DIR/"
  # prefix 配下の作業ツリーだけを 1 行出力する。出力の条件と書式を 1 か所へ寄せ、
  # ループ内（空行の枝）とループ後（最後の項目）で食い違わないようにする。
  _wt_emit_worktree() {
    case "$1" in
      "$prefix"*) printf '%s\t%s\n' "$1" "$2" ;;
    esac
  }
  path=""
  branch=""
  while IFS= read -r line; do
    case "$line" in
      "worktree "*)
        path=${line#worktree }
        branch=""
        ;;
      "branch "*)
        branch=${line#branch }
        branch=${branch#refs/heads/}
        ;;
      "")
        _wt_emit_worktree "$path" "$branch"
        path=""
        branch=""
        ;;
    esac
  done < <(git -C "$main_dir" worktree list --porcelain 2>/dev/null)
  # 最後の項目は空行で終わらないことがある。
  _wt_emit_worktree "$path" "$branch"
  unset -f _wt_emit_worktree
}

# 主ディレクトリの追従先を決める。git は呼ばず、引数だけで判定する。
# 使い方: wt_follow_target "<一覧>" "<未コミット変更があれば 1>"
# 出力: `detach <ブランチ名>` / `default` / `skip`
wt_follow_target() {
  local listing="${1:-}" dirty="${2:-0}" line branch count=0 single=""
  if [ "$dirty" = "1" ]; then
    printf 'skip\n'
    return 0
  fi
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    branch=${line#*$'\t'}
    # ブランチを持たない作業ツリー (detached) は追従先にしない。
    [ -n "$branch" ] || continue
    count=$((count + 1))
    single=$branch
  done <<<"$listing"

  if [ "$count" = 1 ]; then
    printf 'detach %s\n' "$single"
  else
    printf 'default\n'
  fi
}

# 宣言が主ディレクトリの追従を有効にしているかを終了コードで返す。git は呼ばず、
# 引数の JSON だけで判定する。
#
# **追従は既定で行わない**（#610 の決定 2）。並列に動くエージェントのどれが開始しても
# 主ディレクトリの HEAD が動かないようにするためで、`follow_branch` が真偽値の `true`
# のときだけ 0 を返す。`"true"` / `1` / `null` / 項目なし / 空の入力はすべて 1 になる。
# 使い方: wt_follow_enabled "<宣言の JSON>"
wt_follow_enabled() {
  local decl="${1:-}"
  [ -n "$decl" ] || return 1
  printf '%s' "$decl" | jq -e '.follow_branch == true' >/dev/null 2>&1 || return 1
}

# 主ディレクトリの既定ブランチ名を出力する。origin の HEAD が指す先を優先し、
# 取れなければ main / master の順で存在するものを返す。
wt_default_branch() {
  local main_dir="${1:-}" ref candidate
  [ -n "$main_dir" ] || return 1
  ref=$(git -C "$main_dir" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null)
  if [ -n "$ref" ]; then
    printf '%s\n' "${ref#origin/}"
    return 0
  fi
  for candidate in main master; do
    if git -C "$main_dir" show-ref --verify --quiet "refs/heads/$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# 指定された名前のブランチが origin かローカルに実在するかを判定する。
#
# 取得済みの参照 (`refs/remotes/origin/<名前>` と `refs/heads/<名前>`) を先に見て、
# どちらにも無いときだけ origin へ問い合わせる。取得済みの参照だけで判定すると、
# **origin には既にあるがまだ取得していないブランチを「無い」と読む**。起点を
# `develop` へ移した直後の作業ディレクトリがこの状態になり、`git fetch` を挟むまで
# 起点の解決が失敗し続ける。
#
# 問い合わせを後ろへ置くのは、実在する場合に通信を挟まないためである。この関数は
# セッション開始時の hook からも呼ばれるため、通常の経路で待たせない。認証の入力待ちで
# 止まらないよう、端末への問い合わせは禁じる。
#
# 問い合わせの照合先を `refs/heads/<名前>` と完全な参照名で書くのは、`git ls-remote` の
# パターンが参照名の末尾に一致するためである。`develop` とだけ渡すと
# `refs/heads/feature/develop` にも一致する（実測）。
wt_branch_exists() {
  local main_dir="${1:-}" name="${2:-}" listing line
  [ -n "$main_dir" ] && [ -n "$name" ] || return 1
  git -C "$main_dir" show-ref --verify --quiet "refs/remotes/origin/$name" && return 0
  git -C "$main_dir" show-ref --verify --quiet "refs/heads/$name" && return 0
  listing=$(GIT_TERMINAL_PROMPT=0 git -C "$main_dir" ls-remote --heads origin \
    "refs/heads/$name" 2>/dev/null) || return 1
  while IFS= read -r line; do
    case "$line" in *$'\t'"refs/heads/$name") return 0 ;; esac
  done <<<"$listing"
  return 1
}

# 宣言 JSON から指定されたキーの string 型の値だけを出力する。値が string でない、
# キーが無い、JSON が空のいずれでも何も出力しない。実在確認・NOTE 出力・既定ブランチ
# への落としは含まない。それらは呼び出し側が担う。
_wt_declaration_string() {
  local decl="${1:-}" key="${2:-}"
  [ -n "$decl" ] || return 0
  printf '%s' "$decl" |
    jq -r --arg key "$key" 'if (.[$key]|type) == "string" then .[$key] else empty end' 2>/dev/null
}

# 宣言の指定されたキーからブランチ名を読み、実在を確認して出力する。指定が無ければ
# 既定ブランチへ落とす。
wt_declaration_branch() {
  local main_dir="${1:-}" key="${2:-}" decl name=
  [ -n "$main_dir" ] && [ -n "$key" ] || return 1
  if decl=$(wt_declaration "$main_dir"); then
    name=$(_wt_declaration_string "$decl" "$key")
  fi
  if [ -n "$name" ]; then
    if wt_branch_exists "$main_dir" "$name"; then
      printf '%s\n' "$name"
      return 0
    fi
    printf 'NOTE: .ndf/worktree.json の %s が指す %s は origin にもローカルにもありません\n' \
      "$key" "$name" >&2
    return 1
  fi
  wt_default_branch "$main_dir"
}

# 開発の起点ブランチ名を出力する。宣言の base_branch を優先し、指定が無ければ
# 既定ブランチへ落とす。
#
# **指定された名前が実在しないときは既定ブランチへ落とさない。** 落とすと、開発の
# 変更が正式版から分岐したまま進む。origin かローカルのどちらかに同名のブランチが
# あることを確かめ、無ければ標準エラーへ案内を出して 1 を返す。
wt_base_branch() {
  wt_declaration_branch "${1:-}" base_branch
}

# 本番のチャネルのブランチ名を出力する。宣言の production_branch を優先し、指定が
# 無ければ既定ブランチへ落とす。
#
# **`base_branch` は読まない**（#424 の決定 10）。`base_branch` は開発の起点であり、
# 作業ツリーの分岐元を決める（`follow_branch: true` のときは主ディレクトリの追従先にも
# なる）。このリポジトリの値は `develop` で、本番のチャネルと定める `main` とは別の
# ブランチである。流用すると、開発版のチャネルへのマージが本番の扱いになる。
#
# **指定された名前が実在しないときは既定ブランチへ落とさない。** 本番のチャネルを
# 取り違えると、承認を求める対象そのものが変わる。
wt_production_branch() {
  wt_declaration_branch "${1:-}" production_branch
}

# 主ディレクトリの追跡対象の未コミット変更を `<状態> <パス>` で 1 行 1 件出力する。
# 追跡されていないファイルは含めない。
wt_dirty_paths() {
  local main_dir="${1:-}"
  [ -n "$main_dir" ] || return 1
  git -C "$main_dir" status --porcelain --untracked-files=no 2>/dev/null
}
