#!/usr/bin/env bash
# NDF plugin: 待ちの呼び出しと、文脈が上限を超えた conductor の工程の起動を止める guard の古いエントリポイント
# （#829 / #830）。
#
# 判定は hook の 1 本のエントリポイント（hook.py の token-guard。本体は hook_lib/token_guard.py）が持つ
# （#1142 の決定 20）。Claude Code の hook は hook.py を直に起動する。この入口は移行の間だけ残す。
#
# SessionStart（worktree-session.sh）が用意した環境の python で hook.py を起動する。環境がまだ無ければ、
# 何も出力せず終了コード 0 で抜ける（判定をせずに通す）。NDF_HOOK_PYTHON があればその python を使う。
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P) || exit 0
PY=${NDF_HOOK_PYTHON:-$HOME/.cache/ndf/roots$ROOT/bin/python}
[ -x "$PY" ] || exit 0
exec "$PY" "$ROOT/scripts/hook.py" token-guard
