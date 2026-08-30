#!/usr/bin/env bash
# NDF plugin: 作業ツリー運用の判定を集めた共通ライブラリ。
#
# 入口のスクリプト (worktree-guard.sh / worktree-session.sh など) は入力の受け取りと
# 出力の整形だけを行い、判定はすべてこのファイルの関数が持つ。同じ判定を 3 ランタイム
# 分の入口へ書くと片方だけが古くなるため、テストもこの層に対して書く。
#
# このファイルは source して使う。単体で実行しても何も起きない。
# 依存は bash と git、宣言ファイルを読むときだけ jq。無い場合は各関数が 1 を返す。

# 主ディレクトリで編集しても案内を出さないパスの既定。
# 宣言ファイルの guard.allow_paths が指定されていればそちらが優先する。
# 末尾が `/` の項目は前方一致、それ以外は完全一致とその配下を許可する。
WT_DEFAULT_ALLOW_PATHS=(
  "issues/"
  "docs/"
  ".claude/"
  ".codex/"
  ".kiro/"
  ".agents/"
  ".gemini/"
  ".serena/"
  ".ndf/"
  ".gitignore"
)

# 読み取れる宣言ファイルの版。知らない版は読まずに終わる。
WT_DECLARATION_VERSION=1

# 開発用の作業ツリーを置くディレクトリ (主ディレクトリからの相対)。
WT_WORKTREE_DIR=".worktrees"

# 逸脱検知でパスを並べる上限。超えた分は件数へ丸める。
WT_DIRTY_LIST_MAX=20

# 誘導の対象になる tool 名。ランタイムごとに名乗りが違うため、ここで 1 箇所に
# まとめる。hook の matcher もこの一覧から作る（両方に書くと片方が古くなる）。
#   編集系 — Claude Code は Edit / Write、Kiro CLI は fs_write、Gemini CLI は replace
#   パッチ系 — Codex CLI はパッチ本文で編集先を渡す
#   シェル系 — 書き込みを伴うコマンドの形から編集先を推定する
WT_EDIT_TOOLS="Edit|MultiEdit|Write|NotebookEdit|fs_write|edit_file|write_file|str_replace_editor|replace"
WT_PATCH_TOOLS="apply_patch"
WT_SHELL_TOOLS="Bash|shell|execute_bash|local_shell|run_command|run_shell_command"

# hook の matcher に書く正規表現を出力する。
wt_tool_matcher() {
  printf '%s|%s|%s\n' "$WT_EDIT_TOOLS" "$WT_PATCH_TOOLS" "$WT_SHELL_TOOLS"
}

# --- 補助 -------------------------------------------------------------------

# 標準入力を 1 行 1 要素で配列 WT_LINES へ読み込む。
# `mapfile` / `readarray` は bash 4 以降にしかない。macOS が標準で持つ bash は
# 3.2 で、そこで呼ぶと 127 を返して読み込みが空になる。hook は失敗しても黙って
# 終わるため、案内が出ない形で壊れる。
#
# 使い方: _wt_read_lines < <(コマンド); arr=("${WT_LINES[@]+"${WT_LINES[@]}"}")
_wt_read_lines() {
  WT_LINES=()
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    WT_LINES+=("$line")
  done
}

# --- 位置の解決 -------------------------------------------------------------

# 相対パスを実体の絶対パスへ直す。存在しなければ 1 を返す。
_wt_abs() {
  local path="$1"
  [ -n "$path" ] || return 1
  (cd "$path" 2>/dev/null && pwd -P) || return 1
}

# git への問い合わせを 1 セッション 1 回に留めるための控え。
# 呼び出しは `git rev-parse --git-dir --git-common-dir` と
# `git rev-parse --show-superproject-working-tree` の 2 回まで。
_wt_resolve() {
  [ "${_WT_RESOLVED:-}" = "1" ] && return "${_WT_RESOLVE_RC:-0}"
  _WT_RESOLVED=1
  _WT_RESOLVE_RC=1
  _WT_MAIN_DIR=""
  _WT_IN_WORKTREE=1

  local dirs git_dir git_common super
  dirs=$(git rev-parse --git-dir --git-common-dir 2>/dev/null) || return 1
  git_dir=$(printf '%s\n' "$dirs" | sed -n '1p')
  git_common=$(printf '%s\n' "$dirs" | sed -n '2p')
  git_dir=$(_wt_abs "$git_dir") || return 1
  git_common=$(_wt_abs "$git_common") || return 1

  # サブモジュールの中でも 2 つの git ディレクトリは異なる。作業ツリーと
  # 取り違えないよう、上位リポジトリを持つ場合は通常のリポジトリとして扱う。
  super=$(git rev-parse --show-superproject-working-tree 2>/dev/null)
  if [ -n "$super" ]; then
    _WT_MAIN_DIR=$(git rev-parse --show-toplevel 2>/dev/null) || return 1
    _WT_MAIN_DIR=$(_wt_abs "$_WT_MAIN_DIR") || return 1
    _WT_IN_WORKTREE=1
    _WT_RESOLVE_RC=0
    return 0
  fi

  if [ "$git_dir" != "$git_common" ]; then
    _WT_IN_WORKTREE=0
  fi
  _WT_MAIN_DIR=$(dirname "$git_common")
  _WT_RESOLVE_RC=0
  return 0
}

# 主ディレクトリの絶対パスを出力する。リポジトリの外では 1 を返す。
wt_main_dir() {
  _wt_resolve || return 1
  [ -n "$_WT_MAIN_DIR" ] || return 1
  printf '%s\n' "$_WT_MAIN_DIR"
}

# 作業ツリーの中なら 0、主ディレクトリとサブモジュールの中なら 1 を返す。
wt_in_worktree() {
  _wt_resolve || return 1
  return "$_WT_IN_WORKTREE"
}

# --- 宣言ファイル -----------------------------------------------------------

# 主ディレクトリの .ndf/localenv.json を 1 行の JSON として出力する。
# ファイルが無い / JSON として読めない / 版が未対応のいずれでも、何も出力せず 1 を返す。
wt_declaration() {
  local main_dir="${1:-}" file json version
  [ -n "$main_dir" ] || return 1
  file="$main_dir/.ndf/localenv.json"
  [ -f "$file" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  json=$(jq -c '.' "$file" 2>/dev/null) || return 1
  [ -n "$json" ] || return 1
  version=$(printf '%s' "$json" | jq -r 'if (.version|type) == "number" then .version else empty end' 2>/dev/null)
  [ "$version" = "$WT_DECLARATION_VERSION" ] || return 1
  printf '%s\n' "$json"
}

# 案内を出さないパスを 1 行 1 件で出力する。
# 引数は wt_declaration の出力。空や未指定なら既定を返す。
wt_allow_paths() {
  local decl="${1:-}"
  # 空の配列は「何も許可しない」という指定である。出力が空であることと
  # 項目が無いことを区別するため、既定へ戻すかは配列の有無で決める。
  if [ -n "$decl" ] && command -v jq >/dev/null 2>&1 &&
     printf '%s' "$decl" | jq -e '(.guard.allow_paths | type) == "array"' >/dev/null 2>&1; then
    printf '%s' "$decl" | jq -r '.guard.allow_paths | .[]' 2>/dev/null
    return 0
  fi
  printf '%s\n' "${WT_DEFAULT_ALLOW_PATHS[@]}"
}

# --- パスの判定 -------------------------------------------------------------

# 主ディレクトリからの相対パスが許可一覧に該当すれば 0 を返す。
# 使い方: wt_is_allowed_path <相対パス> <許可項目>...
wt_is_allowed_path() {
  local rel="${1:-}" entry
  [ -n "$rel" ] || return 1
  shift || true
  for entry in "$@"; do
    [ -n "$entry" ] || continue
    case "$entry" in
      */)
        # ディレクトリそのものを指す形も許可する。`cp x docs/` の書き込み先は
        # 正規化の途中で末尾のスラッシュが落ち、`docs` として渡ってくる。
        [ "$rel" = "${entry%/}" ] && return 0
        case "$rel" in "$entry"*) return 0 ;; esac
        ;;
      *)
        [ "$rel" = "$entry" ] && return 0
        case "$rel" in "$entry"/*) return 0 ;; esac
        ;;
    esac
  done
  return 1
}

# 絶対パスを主ディレクトリからの相対パスへ直す。外を指すなら 1 を返す。
wt_relative_to_main() {
  local path="${1:-}" main_dir="${2:-}"
  [ -n "$path" ] && [ -n "$main_dir" ] || return 1
  case "$path" in
    "$main_dir") printf '.\n'; return 0 ;;
    "$main_dir"/*) printf '%s\n' "${path#"$main_dir"/}"; return 0 ;;
    *) return 1 ;;
  esac
}

# --- シェルコマンドからの書き込み先の推定 -----------------------------------

# シェルの語分割を、引用符を解釈しながら行う。1 行 1 語で出力する。
# `sed -i 's/a b/c/' f` のように引用符の中へ空白を含む形を 1 語として扱うため、
# 単純な空白区切りでは足りない。
_wt_tokenize() {
  local s="${1:-}" n=${#1} i c quote="" cur=""
  local -a out=()
  for ((i = 0; i < n; i++)); do
    c=${s:i:1}
    if [ -n "$quote" ]; then
      if [ "$c" = "$quote" ]; then quote=""; else cur+="$c"; fi
      continue
    fi
    case "$c" in
      "'"|'"') quote="$c" ;;
      " "|$'\t'|$'\n')
        if [ -n "$cur" ]; then out+=("$cur"); cur=""; fi
        ;;
      *) cur+="$c" ;;
    esac
  done
  [ -n "$cur" ] && out+=("$cur")
  printf '%s\n' "${out[@]+"${out[@]}"}"
}

# 書き込み先として採らない語かを判定する。
_wt_is_not_target() {
  local s="$1"
  case "$s" in
    ""|"&"*|"|"*|"&&"|";"|"/dev/null"|"/dev/stdout"|"/dev/stderr") return 0 ;;
    __WT_REDIR__|__WT_APPEND__) return 0 ;;
  esac
  return 1
}

# シェルコマンドの文字列から書き込み先を 1 行 1 件で出力する。
# 対象は直接の書き換え (sed -i)・出力の付け替え (> / >>)・標準入力からの
# 書き出し (tee)・複製と移動 (cp / mv) の 4 形式に限る。推定できなければ 1 を返す。
wt_extract_write_target() {
  local cmd="${1:-}"
  [ -n "$cmd" ] || return 1

  # `>path` のように空白の無い形を語へ分けるため、先に印を挟む。
  local spaced=${cmd//>>/ __WT_APPEND__ }
  spaced=${spaced//>/ __WT_REDIR__ }

  local -a words=()
  _wt_read_lines < <(_wt_tokenize "$spaced")
  words=("${WT_LINES[@]+"${WT_LINES[@]}"}")

  local n=${#words[@]} i j w target found=0
  _emit() {
    if ! _wt_is_not_target "$1"; then
      printf '%s\n' "$1"
      found=1
    fi
  }

  for ((i = 0; i < n; i++)); do
    w=${words[i]}
    case "$w" in
      __WT_REDIR__|__WT_APPEND__)
        _emit "${words[i + 1]:-}"
        ;;
      tee)
        # tee は並べたファイルすべてへ書き込む。1 件目で止めない。
        for ((j = i + 1; j < n; j++)); do
          case "${words[j]}" in
            __WT_*|"|"|"&&"|";") break ;;
            -*) continue ;;
            *) _emit "${words[j]}" ;;
          esac
        done
        ;;
      sed)
        # in-place の指定があるとき、操作対象のファイルをすべて拾う。
        # `-e` / `-f` が現れなければ、最初の被演算子がスクリプトで残りがファイル。
        local has_inplace=0 seen_script=0 skip_next=0
        local -a files=()
        for ((j = i + 1; j < n; j++)); do
          if [ "$skip_next" = 1 ]; then skip_next=0; continue; fi
          case "${words[j]}" in
            __WT_*|"|"|"&&"|";") break ;;
            --in-place|--in-place=*) has_inplace=1 ;;
            -e|-f|--expression|--file) seen_script=1; skip_next=1 ;;
            --expression=*|--file=*) seen_script=1 ;;
            --) ;;
            -*)
              if [[ ${words[j]} =~ ^-[a-zA-Z]*i([a-zA-Z]*|\..*)$ ]]; then
                has_inplace=1
              fi
              ;;
            *)
              if [ "$seen_script" = 0 ]; then
                seen_script=1
              else
                files+=("${words[j]}")
              fi
              ;;
          esac
        done
        if [ "$has_inplace" = 1 ]; then
          for target in "${files[@]+"${files[@]}"}"; do
            _emit "$target"
          done
        fi
        ;;
      cp|mv)
        # 既定では最後の被演算子が宛先だが、`-t <ディレクトリ>` を付けると
        # 宛先が先に来て、後ろの被演算子はすべて複製元になる。
        local dest="" target_dir="" take_next=0
        for ((j = i + 1; j < n; j++)); do
          if [ "$take_next" = 1 ]; then
            target_dir=${words[j]}
            take_next=0
            continue
          fi
          case "${words[j]}" in
            __WT_*|"|"|"&&"|";") break ;;
            -t|--target-directory) take_next=1 ;;
            --target-directory=*) target_dir=${words[j]#--target-directory=} ;;
            -*) continue ;;
            *) dest=${words[j]} ;;
          esac
        done
        [ -n "$target_dir" ] && dest=$target_dir
        _emit "$dest"
        ;;
    esac
  done

  unset -f _emit
  [ "$found" = 1 ] || return 1
}

# --- パスの正規化 -----------------------------------------------------------

# tool から渡されたパスを絶対パスへ直す。まだ存在しないパスでも、実在する
# 最も近い上位ディレクトリまでを実体解決してから残りを継ぎ足す。
# `.` と `..` を字面で畳む。実体解決は上位ディレクトリの存在を要するため、
# 存在しないパスでは `..` が残ってしまう。残ると、前方一致での「配下か」の
# 判定をすり抜ける（`<対象>/a/../../外` が `<対象>/` で始まって見える）。
_wt_lexical_normalize() {
  local path="$1" part out=""
  local IFS=/
  # shellcheck disable=SC2086
  set -- $path
  for part in "$@"; do
    case "$part" in
      ""|.) continue ;;
      ..) out=${out%/*} ;;
      *) out="$out/$part" ;;
    esac
  done
  printf '%s\n' "${out:-/}"
}

wt_normalize_path() {
  local path="${1:-}" cwd="${2:-$PWD}" suffix="" dir abs
  [ -n "$path" ] || return 1
  case "$path" in /*) ;; *) path="$cwd/$path" ;; esac
  path=$(_wt_lexical_normalize "$path")
  dir="$path"
  while [ -n "$dir" ] && [ "$dir" != "/" ]; do
    if abs=$(_wt_abs "$dir"); then
      if [ -n "$suffix" ]; then
        printf '%s/%s\n' "$abs" "$suffix"
      else
        printf '%s\n' "$abs"
      fi
      return 0
    fi
    if [ -n "$suffix" ]; then
      suffix="$(basename "$dir")/$suffix"
    else
      suffix="$(basename "$dir")"
    fi
    dir=$(dirname "$dir")
  done
  printf '%s\n' "$path"
}

# --- 作業ツリーの一覧と追従先の判定 -----------------------------------------

# 開発用の作業ツリーを `<パス><タブ><ブランチ名>` の形で 1 行 1 件で出力する。
# 対象は主ディレクトリ直下の .worktrees/ 配下に限る。レビュー用の作業ツリーは
# 非永続領域に置かれるため、この一覧には入らない。
wt_dev_worktrees() {
  local main_dir="${1:-}" prefix path branch
  [ -n "$main_dir" ] || return 1
  prefix="$main_dir/$WT_WORKTREE_DIR/"
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
        case "$path" in
          "$prefix"*) printf '%s\t%s\n' "$path" "$branch" ;;
        esac
        path=""
        branch=""
        ;;
    esac
  done < <(git -C "$main_dir" worktree list --porcelain 2>/dev/null)
  # 最後の項目は空行で終わらないことがある。
  case "$path" in
    "$prefix"*) printf '%s\t%s\n' "$path" "$branch" ;;
  esac
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

# 主ディレクトリの追跡対象の未コミット変更を `<状態> <パス>` で 1 行 1 件出力する。
# 追跡されていないファイルは含めない。
wt_dirty_paths() {
  local main_dir="${1:-}"
  [ -n "$main_dir" ] || return 1
  git -C "$main_dir" status --porcelain --untracked-files=no 2>/dev/null
}

# --- パッチ本文からの書き込み先の推定 ---------------------------------------

# `apply_patch` の本文から書き込み先を 1 行 1 件で出力する。
# Codex CLI はファイルの編集をこの形で渡し、パスは tool_input.command の中の
# `*** Update File: <パス>` などの行に入る。推定できなければ 1 を返す。
wt_extract_patch_target() {
  local patch="${1:-}" line target found=0
  [ -n "$patch" ] || return 1
  while IFS= read -r line; do
    case "$line" in
      '*** Update File: '*) target=${line#'*** Update File: '} ;;
      '*** Add File: '*) target=${line#'*** Add File: '} ;;
      '*** Delete File: '*) target=${line#'*** Delete File: '} ;;
      '*** Move to: '*) target=${line#'*** Move to: '} ;;
      *) continue ;;
    esac
    # 前後の空白を落とす。
    target=${target#"${target%%[![:space:]]*}"}
    target=${target%"${target##*[![:space:]]}"}
    if [ -n "$target" ]; then
      printf '%s\n' "$target"
      found=1
    fi
  done <<<"$patch"
  [ "$found" = 1 ] || return 1
}

# --- テスト環境の採番と台帳 --------------------------------------------------

# 台帳の位置。共通の git ディレクトリ配下へ置く。作業ツリーの中に置くと、その
# 作業ツリーを削除した時点で割り当ての記録が消える (詳細設計 06 の決定 7)。
wt_registry_path() {
  local main_dir="${1:-}"
  [ -n "$main_dir" ] || return 1
  local common
  common=$(git -C "$main_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  printf '%s/ndf/worktree-registry.json\n' "$common"
}

# 割り当てを解放しても行を消さないため、解放済みの行は増え続ける。
# 1 年を超えた解放済みの行は読み取り時に無視する。**削除はしない。**
WT_REGISTRY_KEEP_DAYS=365

# 空きスロットの上限。0 から数えるため 64 個。
WT_SLOT_MAX=63

# 環境名を作る。`<リポジトリ>-wt-<ブランチ>-<要約値 6 桁>` を小文字英数と `-` に
# 揃え、40 文字で切る。同じ作業ツリーには常に同じ値が返る。
wt_env_name() {
  local main_dir="${1:-}" branch="${2:-}" repo digest name
  [ -n "$main_dir" ] && [ -n "$branch" ] || return 1
  repo=$(basename "$main_dir")
  digest=$(printf '%s' "$branch" | (sha1sum 2>/dev/null || shasum 2>/dev/null) | cut -c1-6)
  [ -n "$digest" ] || return 1
  name=$(printf '%s-wt-%s-%s' "$repo" "$branch" "$digest" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e 's/[^a-z0-9-]/-/g' -e 's/--*/-/g' -e 's/^-//' -e 's/-$//')
  printf '%s\n' "$(printf '%s' "$name" | cut -c1-40)"
}

# ポート番号を返す。`<帯の下限> + スロット*20 + 役割番号`。
# 判定だけを行い、宣言の読み取りは呼び出し側が持つ（テストのため）。
wt_port_for() {
  local band_low="${1:-}" slot="${2:-}" role_number="${3:-}"
  case "$band_low$slot$role_number" in
    *[!0-9]*|"") return 1 ;;
  esac
  printf '%s\n' "$((band_low + slot * 20 + role_number))"
}

# 期間の表記を秒へ直す。`90` / `90s` / `45m` / `2h` / `1d` を受ける。
wt_duration_seconds() {
  local value="${1:-}" number unit
  [ -n "$value" ] || return 1
  number=${value%[smhd]}
  case "$number" in ""|*[!0-9]*) return 1 ;; esac
  unit=${value#"$number"}
  case "$unit" in
    ""|s) printf '%s\n' "$number" ;;
    m) printf '%s\n' "$((number * 60))" ;;
    h) printf '%s\n' "$((number * 3600))" ;;
    d) printf '%s\n' "$((number * 86400))" ;;
    *) return 1 ;;
  esac
}

# 台帳を読む。無ければ空の台帳を返す。
wt_registry_read() {
  local path="${1:-}"
  if [ -s "$path" ] && jq -e . "$path" >/dev/null 2>&1; then
    cat "$path"
    return 0
  fi
  printf '{"version":1,"assignments":[]}\n'
}

# 読み取り時に無視する行を落とした台帳を返す。
# 解放から WT_REGISTRY_KEEP_DAYS を超えた行は数にも一覧にも入れない。
# **ファイルからは消さない。**
wt_registry_visible() {
  local path="${1:-}"
  wt_registry_read "$path" | jq --argjson keep "$WT_REGISTRY_KEEP_DAYS" '
    .assignments |= map(
      select(.released_at == null
             or ((.released_at | fromdateiso8601) > (now - ($keep * 86400))))
    )' 2>/dev/null
}

_wt_registry_write() {
  local path="$1" content="$2" tmp
  mkdir -p "$(dirname "$path")" 2>/dev/null
  tmp=$(mktemp "${path}.XXXXXX" 2>/dev/null) || return 1
  printf '%s\n' "$content" >"$tmp" || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$path" 2>/dev/null || { rm -f "$tmp"; return 1; }
}

# --- 排他 -------------------------------------------------------------------
#
# `flock` が無い環境がある。`mkdir` は同じ名前で同時に成功するのが 1 つだけなので、
# ディレクトリの作成そのものを排他の手段として使う。

# 待ちの刻み。小数を受けない sleep がある環境では 1 秒へ落ちる。
_wt_lock_sleep() {
  sleep 0.1 2>/dev/null || sleep 1
}

# 持ち主が消えたまま残るロックを捨ててよいと見なすまでの分数。
# `mkdir` に成功した直後、印を書く前に落ちた持ち主を救うために使う。
WT_LOCK_STALE_MINUTES=5

# 判定したものと同じロックであることを確かめてから取り除く。
# 名前の付け替えは 1 つのプロセスだけが成功するため、これを関門に使う。
# 単に `rm -rf` すると、先に捨てて取り直した別のプロセスのロックを壊す。
_wt_lock_discard() {
  local dir="$1" seen="$2" token="$3" stale="$1.stale.$3"
  mv "$dir" "$stale" 2>/dev/null || return 1
  if [ "$(cat "$stale/token" 2>/dev/null)" = "$seen" ]; then
    rm -rf "$stale" 2>/dev/null
    return 0
  fi
  # 別物だった。戻せなければ、取り直した側が既に持っているので捨てる。
  mv "$stale" "$dir" 2>/dev/null || rm -rf "$stale" 2>/dev/null
  return 1
}

# ロックが捨ててよい状態かを見る。
_wt_lock_is_stale() {
  local dir="$1" owner
  owner=$(cat "$dir/pid" 2>/dev/null)
  if [ -n "$owner" ]; then
    kill -0 "$owner" 2>/dev/null && return 1
    return 0
  fi
  # 印が無いロックは、作った直後に落ちた可能性がある。古ければ捨ててよい。
  find "$dir" -maxdepth 0 -mmin "+$WT_LOCK_STALE_MINUTES" 2>/dev/null | grep -q . && return 0
  return 1
}

# ロックを取る。取れなければ 1 を返す。
#
# `flock` は使わない。使える環境と使えない環境が混じると、同じ資源に対して
# 別々の仕組みが動き、互いを見落とす。どこでも同じ `mkdir` に揃える。
wt_lock_acquire() {
  local dir="${1:-}" timeout="${2:-5}" waited=0 limit token seen
  [ -n "$dir" ] || return 1
  # ロックの位置にディレクトリ以外があれば、ロックとして成立しない。取り除く。
  if [ -e "$dir" ] && [ ! -d "$dir" ]; then
    rm -f "$dir" 2>/dev/null
  fi
  token="$$-$(date +%s 2>/dev/null)-${RANDOM:-0}"
  limit=$((timeout * 10))
  while ! mkdir "$dir" 2>/dev/null; do
    seen=$(cat "$dir/token" 2>/dev/null)
    if _wt_lock_is_stale "$dir"; then
      _wt_lock_discard "$dir" "$seen" "$token"
      continue
    fi
    waited=$((waited + 1))
    [ "$waited" -ge "$limit" ] && return 1
    _wt_lock_sleep
  done
  printf '%s\n' "$token" >"$dir/token" 2>/dev/null
  printf '%s\n' "$$" >"$dir/pid" 2>/dev/null
  return 0
}

wt_lock_release() {
  [ -n "${1:-}" ] || return 0
  rm -rf "$1" 2>/dev/null
  return 0
}

# 生きている持ち主がロックを握っていれば 0 を返す。
wt_lock_is_held() {
  local dir="${1:-}" owner
  [ -n "$dir" ] && [ -d "$dir" ] || return 1
  owner=$(cat "$dir/pid" 2>/dev/null)
  [ -n "$owner" ] || return 0
  kill -0 "$owner" 2>/dev/null
}

_wt_registry_apply() {
  local content updated
  content=$(wt_registry_read "$_WT_REGISTRY_TARGET")
  updated=$(printf '%s' "$content" | jq "${_WT_REGISTRY_ARGS[@]+"${_WT_REGISTRY_ARGS[@]}"}" \
    "$_WT_REGISTRY_PROGRAM" 2>/dev/null) || return 1
  _wt_registry_write "$_WT_REGISTRY_TARGET" "$updated"
}

# 台帳の更新を排他のもとで行う。**読み込み・変更・書き出しを 1 つの区間にまとめる。**
# 判定も jq のプログラムの中で行うこと。区間の外で読んで中で書くと、同時に走った
# 別のプロセスと同じ番号を割り当ててしまう。
#
# 使い方: wt_registry_update <台帳> <jq プログラム> [jq の引数...]
# 値は jq の引数として渡す。プログラムの文字列へ埋め込むと、引用符を含む
# ブランチ名やパスで壊れる。
wt_registry_update() {
  _WT_REGISTRY_TARGET="${1:-}"
  _WT_REGISTRY_PROGRAM="${2:-}"
  [ -n "$_WT_REGISTRY_TARGET" ] && [ -n "$_WT_REGISTRY_PROGRAM" ] || return 1
  shift 2
  _WT_REGISTRY_ARGS=("$@")
  mkdir -p "$(dirname "$_WT_REGISTRY_TARGET")" 2>/dev/null

  # 排他の手段は 1 つに揃える。`flock` の有無で使うロックが分かれると、
  # 同じ台帳を別の場所から同時に触ったときに互いを見落とす。
  local lock="${_WT_REGISTRY_TARGET}.lockdir" rc
  wt_lock_acquire "$lock" 5 || return 1
  _wt_registry_apply
  rc=$?
  wt_lock_release "$lock"
  return "$rc"
}

# 作業ツリーへ割り当てられているスロットを返す。無ければ 1 を返す。
wt_slot_of() {
  local main_dir="${1:-}" worktree="${2:-}" path slot
  path=$(wt_registry_path "$main_dir") || return 1
  slot=$(wt_registry_visible "$path" \
    | jq -r --arg wt "$worktree" \
      '[.assignments[] | select(.released_at == null and .worktree == $wt)] | last | .slot // empty' 2>/dev/null)
  [ -n "$slot" ] || return 1
  printf '%s\n' "$slot"
}

# 作業ツリーへスロットを割り当てる。既に割り当てがあれば同じ番号を返す。
# 空きが無ければ 1 を返す。
#
# **空きの判定と行の追加を 1 つの jq プログラムで行う。** 排他区間の外で空きを
# 読むと、同時に走った別のプロセスと同じ番号を掴む。
wt_slot_acquire() {
  local main_dir="${1:-}" worktree="${2:-}" branch="${3:-}" environment="${4:-}"
  local path
  path=$(wt_registry_path "$main_dir") || return 1

  wt_registry_update "$path" '
    ([.assignments[] | select(.released_at == null)]) as $active
    | if ($active | map(select(.worktree == $wt)) | length) > 0 then .
      else
        ([$active[] | .slot]) as $used
        | ([range(0; $max + 1)] - $used | first) as $slot
        | if $slot == null then .
          else
            .assignments += [{
              id: $id, worktree: $wt, branch: $branch, environment: $environment,
              slot: $slot, ports: {}, assigned_at: (now | todate),
              last_used_at: (now | todate), released_at: null, expose: null
            }]
          end
      end'     --arg wt "$worktree" --arg branch "$branch" --arg environment "$environment"     --arg id "$(date -u +%Y%m%dT%H%M%SZ)-$$" --argjson max "$WT_SLOT_MAX" || return 1

  wt_slot_of "$main_dir" "$worktree"
}

# 割り当てを解放する。**行は消さず、解放の時刻を書き込む。**
wt_slot_release() {
  local main_dir="${1:-}" worktree="${2:-}" path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" '
    .assignments |= map(
      if .worktree == $wt and .released_at == null then .released_at = (now | todate) else . end
    )' --arg wt "$worktree"
}

# 割り当てへポートを記録する。
wt_slot_set_ports() {
  local main_dir="${1:-}" worktree="${2:-}" ports_json="${3:-}" path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" '
    .assignments |= map(
      if .worktree == $wt and .released_at == null then .ports = $ports else . end
    )' --arg wt "$worktree" --argjson ports "$ports_json"
}

# 最後に使った時刻を記録する。reap の判定が読む。
wt_slot_touch() {
  local main_dir="${1:-}" worktree="${2:-}" path
  path=$(wt_registry_path "$main_dir") || return 1
  wt_registry_update "$path" '
    .assignments |= map(
      if .worktree == $wt and .released_at == null then .last_used_at = (now | todate) else . end
    )' --arg wt "$worktree"
}
