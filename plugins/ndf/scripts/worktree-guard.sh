#!/usr/bin/env bash
# NDF plugin: 主ディレクトリを編集しようとしたときに、worktree で作業する旨を伝える guard の古いエントリポイント。
#
# 判定は hook の 1 本のエントリポイント（hook.py の worktree-guard。本体は hook_lib/worktree.py）が持つ
# （#1142 の決定 20）。Claude Code と Codex CLI の hook は hook.py を直に起動する。この入口は、パスを
# 変えずに結ぶ Kiro CLI（dev.kiro/install.sh）と agy（dev.agy/hooks.json）のために残す。
#
# SessionStart（worktree-session.sh）が用意した環境の python で hook.py を起動する。環境がまだ無ければ、
# 何も出力せず終了コード 0 で抜ける（判定をせずに通す）。NDF_HOOK_PYTHON があればその python を使う。
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P) || exit 0
PY=${NDF_HOOK_PYTHON:-$HOME/.cache/ndf/roots$ROOT/bin/python}
[ -x "$PY" ] || exit 0
exec "$PY" "$ROOT/scripts/hook.py" worktree-guard
