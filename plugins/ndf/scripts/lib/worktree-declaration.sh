#!/usr/bin/env bash
# NDF plugin: 宣言ファイル（`.ndf/worktree.json` と個人の宣言）の読み取り。
#
# `worktree-common.sh` が source する。呼び出し側はこのファイルを直に source せず、
# `worktree-common.sh` だけを source する。

# --- 宣言ファイル -----------------------------------------------------------

# 宣言（`wt_declaration` の出力）へ jq のフィルタを当て、結果を生の文字列で出力する。
# 終了コードは jq のもの。読めなければ何も出さない。
wt_declaration_get() {
  printf '%s' "${1:-}" | jq -r "${2:-.}" 2>/dev/null
}

# 個人の宣言をチェックし、反映する部分と反映しない項目を 1 つの JSON として出力する。
# 通常のファイルでない / JSON として読めない / 空 / オブジェクトでない / 版が未対応の
# いずれでも、何も出力せず 1 を返す。
#
# **反映するのは許可一覧の項目だけである**（#495 の決定 7）。個人の宣言は追跡されず、
# レビューを通らない。`base_branch` を変えると作業ツリーの起点が宛先のチェックとずれ、
# `guard.allow_paths` を広げるとその人にだけ編集時の案内が出ない。
#
# 型の合わない項目は、その項目だけを落とす（決定 10）。**null も反映しない。**
# 個人の宣言から共有の節を消せる形にすると、仕組みが手元でだけ黙って止まる。
# `testenv.expose` は落とす（決定 8）。追跡されないファイルから外部への公開を
# 有効にできる状態を作らない。**`expose` を落として空になった `testenv` は節ごと
# 反映しない。** 空の節を重ねると、`testenv` を宣言していないリポジトリで
# `worktree-testenv.sh` の起動判定が off から on へ変わる。
_wt_local_analysis() {
  local main_dir="${1:-}" file json
  [ -n "$main_dir" ] || return 1
  file="$main_dir/$WT_DECLARATION_LOCAL_FILE"
  [ -f "$file" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  # 最上位が配列でも `with_entries` は成功するため、型は明示的に確かめる。
  # `del(.testenv.expose)` は `testenv` が文字列だと終了コード 5 で落ちるため、
  # 節の型を先に確かめてから消す。
  json=$(jq -c --argjson ver "$WT_DECLARATION_VERSION" '
    if type != "object" then empty
    elif .version != $ver then empty
    else
      {
        overrides:
          ((if (.localenv | type) == "object" then {localenv: .localenv} else {} end)
           + (if (.testenv | type) == "object"
              then ((.testenv | del(.expose)) as $t
                    | if ($t | length) > 0 then {testenv: $t} else {} end)
              else {} end)
           + (if (.follow_branch | type) == "boolean" then {follow_branch: .follow_branch} else {} end)),
        ignored:
          ([to_entries[] |
            if (.key == "version" or .key == "$schema") then empty
            elif .key == "localenv" then (if (.value | type) == "object" then empty else .key end)
            elif .key == "testenv" then
              (if (.value | type) != "object" then .key
               elif (.value | has("expose")) then "testenv.expose"
               else empty end)
            elif .key == "follow_branch" then (if (.value | type) == "boolean" then empty else .key end)
            else .key
            end] | sort)
      }
    end' "$file" 2>/dev/null) || return 1
  [ -n "$json" ] || return 1
  printf '%s\n' "$json"
}

# 個人の宣言から反映する部分だけを 1 行の JSON として出力する。
_wt_local_overrides() {
  local analysis
  analysis=$(_wt_local_analysis "${1:-}") || return 1
  printf '%s' "$analysis" | jq -c '.overrides'
}

# 主ディレクトリの .ndf/worktree.json に .ndf/worktree.local.json を重ねて、
# 1 行の JSON として出力する。
# 共有の宣言が無い / JSON として読めない / 版が未対応のいずれでも、何も出力せず 1 を返す。
#
# **個人の宣言の状態では失敗しない**（#495 の決定 11）。個人の宣言は運用を補うもので、
# 壊れても共有の運用（宣言の有無・案内・起点）を止めない。報告は `status` / `check` の
# 「個人の宣言:」の行が持つ。
#
# **重ね合わせはここだけで行う**（決定 13）。宣言を読む入口はすべてこの関数を経由する
# ため、呼び出し側は変えない。重ね方は jq の `*` で、オブジェクトは深く併合し、配列は
# 置き換える（決定 9）。
wt_declaration() {
  local main_dir="${1:-}" file json version overrides merged
  [ -n "$main_dir" ] || return 1
  file="$main_dir/$WT_DECLARATION_FILE"
  [ -f "$file" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  json=$(jq -c '.' "$file" 2>/dev/null) || return 1
  [ -n "$json" ] || return 1
  version=$(printf '%s' "$json" | jq -r 'if (.version|type) == "number" then .version else empty end' 2>/dev/null)
  [ "$version" = "$WT_DECLARATION_VERSION" ] || return 1

  if overrides=$(_wt_local_overrides "$main_dir"); then
    merged=$(printf '%s' "$json" | jq -c --argjson ov "$overrides" '. * $ov' 2>/dev/null)
    [ -n "$merged" ] && json=$merged
  fi

  printf '%s\n' "$json"
}

# 宣言の状態を `present` / `absent` / `unreadable` の 1 語で出力する。引数が空なら 1 を返す。
# **状態を分けるのはこの関数だけである**（#527）。`check` と `status` はこれを呼び、
# 手順書と hook は基準を書き写さない。
#
# 存在は `[ -e ]` で見る。`wt_declaration` は `[ -f ]` で見るため、ディレクトリは
# `unreadable`、壊れた symlink は `absent` に分かれる。**`jq` が無いと読める宣言も
# `unreadable` になる。** 呼び出し側が先に `jq` を確かめる。
wt_declaration_state() {
  local main_dir="${1:-}"
  [ -n "$main_dir" ] || return 1
  if wt_declaration "$main_dir" >/dev/null; then
    printf 'present\n'
  elif [ -e "$main_dir/$WT_DECLARATION_FILE" ]; then
    printf 'unreadable\n'
  else
    printf 'absent\n'
  fi
}

# 個人の宣言の状態を `absent` / `present` / `unreadable` / `unused` の 1 語で出力する。
# 引数が空なら 1 を返す。
#
# `unused` は、個人の宣言はあるが共有の宣言が無い・読めない状態である（#495 の決定 12）。
# 宣言の有無はリポジトリが作業ツリー運用を使うかを表すため、個人のファイルだけで
# 仕組みを動かさない。
#
# 存在は `[ -e ]` で見る。`_wt_local_overrides` は `[ -f ]` で見るため、ディレクトリは
# `unreadable`、壊れた symlink は `absent` に分かれる（共有の宣言と同じ分け方）。
wt_declaration_local_state() {
  local main_dir="${1:-}"
  [ -n "$main_dir" ] || return 1
  if [ ! -e "$main_dir/$WT_DECLARATION_LOCAL_FILE" ]; then
    printf 'absent\n'
  elif [ "$(wt_declaration_state "$main_dir")" != present ]; then
    printf 'unused\n'
  elif ! _wt_local_overrides "$main_dir" >/dev/null; then
    printf 'unreadable\n'
  else
    printf 'present\n'
  fi
}

# 個人の宣言のうち反映しない項目名を 1 行 1 件で出力する。状態が `present` でなければ
# 何も出さず 0 を返す。
#
# 出すのは、運用を決める項目と未知の項目・型の合わない項目・`testenv.expose` である。
# **`version` と `$schema` は出さない。** 反映しないが、報告の対象でもない。
# 並びは項目名の順に揃える（出力を突き合わせる手順とテストが並び順に依存しないため）。
wt_declaration_local_ignored() {
  local main_dir="${1:-}" analysis
  [ -n "$main_dir" ] || return 1
  [ "$(wt_declaration_state "$main_dir")" = present ] || return 0
  analysis=$(_wt_local_analysis "$main_dir") || return 0
  printf '%s' "$analysis" | jq -r '.ignored[]'
  return 0
}
