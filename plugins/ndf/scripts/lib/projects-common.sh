#!/usr/bin/env bash
# NDF plugin: 工程の進行を GitHub Projects へ記録するための判定。
#
# **リポジトリに .ndf/projects.json があるときだけ動く。** 無ければ何もしない。
# 進行管理は開発の前提条件ではないため、盤面が無い環境でも工程はそのまま通る。
#
# 判定はすべてこのファイルが持ち、入口のスクリプトは入出力の整形だけを行う
# （worktree の詳細設計 06 の決定 8 と同じ構造）。

PJ_DECLARATION_VERSION=1
PJ_DECLARATION_REL=".ndf/projects.json"

# 工程の値。**development-workflow の工程表の行名と一致させる。**
# 綴りの違う値を書き込むと、盤面の側に工程表に無い値が増える。
PJ_STAGES=$'作業場所の用意\n要求と受け入れ条件\n設計\n計画\n実装\n構造改善\nレビュー\n完了判定\nPull Request\n確定仕様化\n後片付け\n配布\nリリース後テスト\n振り返り'
PJ_MODES=$'light\nstandard\narchitecture\nlegacy-refactor'
# 盤面の既定のフィールド。GitHub が最初から持つもので、値も既定のまま使う。
PJ_STATUSES=$'Todo\nIn Progress\nDone'

# キーと、盤面のフィールド名の既定。宣言の `fields` で差し替えられる。
_pj_default_field() {
  case "$1" in
    stage) printf '進行\n' ;;
    mode) printf 'モード\n' ;;
    worktree) printf '作業ツリー\n' ;;
    plan) printf '計画ファイル\n' ;;
    status) printf 'Status\n' ;;
    *) return 1 ;;
  esac
}

# 宣言を 1 行の JSON で返す。読めなければ 1 を返す。
#
# 盤面を特定できない宣言（owner か number が欠けている）は無効として扱う。
# 部分的に読めた値で書き込み先を推測すると、別の盤面を更新しかねない。
pj_declaration() {
  local dir="${1:-}" file json
  [ -n "$dir" ] || return 1
  file="$dir/$PJ_DECLARATION_REL"
  [ -f "$file" ] || return 1
  command -v jq >/dev/null 2>&1 || return 1
  json=$(jq -c '.' "$file" 2>/dev/null) || return 1
  [ -n "$json" ] || return 1
  printf '%s' "$json" | jq -e \
    --argjson v "$PJ_DECLARATION_VERSION" \
    '(.version == $v) and ((.owner | type) == "string") and ((.owner | length) > 0)
     and ((.number | type) == "number")' >/dev/null 2>&1 || return 1
  printf '%s\n' "$json"
}

pj_owner() { printf '%s' "${1:-}" | jq -r '.owner' 2>/dev/null; }
pj_number() { printf '%s' "${1:-}" | jq -r '.number' 2>/dev/null; }

# キーに対応する盤面のフィールド名を返す。知らないキーは 1 を返す。
pj_field_name() {
  local json="${1:-}" key="${2:-}" name
  _pj_default_field "$key" >/dev/null || return 1
  name=$(printf '%s' "$json" | jq -r --arg k "$key" \
    'if (.fields[$k] | type) == "string" and (.fields[$k] | length) > 0 then .fields[$k] else empty end' 2>/dev/null)
  if [ -n "$name" ]; then
    printf '%s\n' "$name"
  else
    _pj_default_field "$key"
  fi
}

# 値がその一覧に含まれるか。含まれない値は書き込まない。
_pj_in_list() { printf '%s\n' "$1" | grep -Fxq -- "$2"; }
pj_is_stage() { _pj_in_list "$PJ_STAGES" "${1:-}"; }
pj_is_mode() { _pj_in_list "$PJ_MODES" "${1:-}"; }
pj_is_status() { _pj_in_list "$PJ_STATUSES" "${1:-}"; }

# キーが取る値の種類。single-select は一覧で照合し、text は照合しない。
pj_key_kind() {
  case "${1:-}" in
    stage|mode|status) printf 'select\n' ;;
    worktree|plan) printf 'text\n' ;;
    *) return 1 ;;
  esac
}

# キーの値が妥当かを判定する。text は任意の文字列を受ける。
pj_is_valid_value() {
  local key="${1:-}" value="${2:-}"
  case "$key" in
    stage) pj_is_stage "$value" ;;
    mode) pj_is_mode "$value" ;;
    status) pj_is_status "$value" ;;
    worktree|plan) [ -n "$value" ] ;;
    *) return 1 ;;
  esac
}
