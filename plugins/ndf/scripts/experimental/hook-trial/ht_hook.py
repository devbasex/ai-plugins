"""T2 の 1 本の hook のエントリポイントの試作。用意済みの環境の bin/python から直に起動する（uv run を挟まない）。

標準入力に PreToolUse の JSON を受け、worktree-guard.sh と同じ仕事（メインディレクトリへの書き込み先を見つけて
additionalContext で案内する）と、token-guard.sh の 2 つの判定（前景の sleep・プランを起こす副命令）を 1 回で行う。
判定の本体は ht_shparse.py（tree-sitter-bash）。

**環境が無い・壊れているときは判定をせずにパススルー（出力なし・終了コード 0）で終わる。** hook が止まると Tool の
呼び出しが全部止まるためである。宣言（.ndf/）の許可パスと agy・Kiro の入力の形は試作に含めない（所要を測るため）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

SHELL_TOOLS = {"Bash", "shell", "execute_bash", "local_shell", "run_command", "run_shell_command"}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}


def main_dir(cwd: str, session: str) -> tuple[str, bool] | None:
    """(メインディレクトリ, worktree の中か)。worktree-guard.sh と同じくセッションごとに TMPDIR へ控える。"""
    state = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                         "ndf-hook-trial-" + "".join(c if c.isalnum() or c in "._-" else "_" for c in session) + ".json")
    try:
        with open(state, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("resolved_from") == cwd:
            return d["main_dir"], d["in_worktree"]
    except (OSError, ValueError, KeyError):
        pass
    p = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel"],
                       cwd=cwd, capture_output=True, text=True)
    if p.returncode:
        return None
    common, top = p.stdout.split("\n")[:2]
    main = os.path.dirname(common) if common.endswith("/.git") else top
    res = (main, os.path.realpath(top) != os.path.realpath(main))
    if session:
        try:
            with open(state, "w", encoding="utf-8") as f:
                json.dump({"resolved_from": cwd, "main_dir": res[0], "in_worktree": res[1]}, f)
        except OSError:
            pass
    return res


def run(payload: dict) -> dict | None:
    import ht_shparse as sp
    import ht_shsleep as ss

    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    cwd = os.path.realpath(payload.get("cwd") or os.getcwd())
    out = []
    if tool in SHELL_TOOLS and isinstance(ti.get("command"), str):
        cmd = ti["command"]
        if not ti.get("run_in_background") and "sleep" in cmd and ss.sleep_deny(cmd, float(os.environ.get("NDF_SLEEP_MAX_SEC", 5))):
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": "前景の sleep（T2 の試作）"}}
        if ss.plan_command(cmd):
            out.append("プランを起こす副命令（T2 の試作）")
        targets = sp.write_targets(cmd, cwd)
    elif tool in EDIT_TOOLS:
        targets = [os.path.join(cwd, p) for p in (ti.get("file_path"), ti.get("notebook_path")) if isinstance(p, str) and p]
    else:
        return None
    if targets:
        found = main_dir(cwd, payload.get("session_id") or "")
        if found and not found[1]:
            main = found[0].rstrip("/") + "/"
            hits = [t for t in targets if t.startswith(main)]
            if hits:
                out.append("メインディレクトリへの書き込み（T2 の試作）: " + ", ".join(hits))
    if not out:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(out)}}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "null")
        if not isinstance(payload, dict):
            return 0
        res = run(payload)
    except Exception:  # 環境が壊れている（import できない）・入力が読めない: パススルー
        return 0
    if res:
        sys.stdout.write(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
