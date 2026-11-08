#!/usr/bin/env bash
# NDF plugin: シェルのエントリポイントを実行の核（run_core.py）の子として動かす（#2337）。
#
#   . "$(dirname "$0")/lib/run-core.sh"; rc_enter "plugins/ndf/scripts/<名前>.sh" "$@"
#
# `rc_enter` は、核の子として動いていなければ（`NDF_RUN_ENTRY` が自分の id でなければ）
# `python3 run_core.py exec <id> -- "$0" <引数>` で自分を再実行する。核の子として動いていれば印を外して戻る
# （印を外すのは、このスクリプトが起こすほかのシェルのエントリポイントが自分の核へ入れるようにするため）。
# 結果と終了コードの規則は同じディレクトリの run_core.py にある。

RC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rc_enter() {
  local id="$1"
  shift
  if [ "${NDF_RUN_ENTRY:-}" != "$id" ]; then
    exec python3 "$RC_LIB_DIR/run_core.py" exec "$id" -- "$0" "$@"
  fi
  unset NDF_RUN_ENTRY
}
