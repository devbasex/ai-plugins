#!/usr/bin/env python3
"""NDF の hook の 1 本のエントリポイント（#1142 の決定 20）。PreToolUse・Stop・Notification ほかを標準入力の JSON で受ける。

    <hook の環境の python> hook.py [worktree-guard | token-guard | wait-notify] [--runtime claude|codex|kiro]
    <hook の環境の python> hook.py words     < コマンドの本文   # 語の分割（workflow-guard.sh の wf_split）
    <hook の環境の python> hook.py merge-target --base <宛先> [--root <dir>]
        # マージの宛先の判定（workflow-merge.sh の wf_check_merge。lib/delivery.py。1 行目が判定、2 行目が理由）

副命令を省くと、事象と Tool の名前で振り分ける。

| 事象 | 判定 |
| --- | --- |
| PreToolUse（編集・パッチ・シェルの Tool） | worktree の guard（`hook_lib/worktree.py`） |
| PreToolUse（Bash・Read・Skill・Agent・Task。Claude Code だけ） | token の guard（`hook_lib/token_guard.py`） |
| PreToolUse（AskUserQuestion）・Stop・Notification・PermissionRequest | 待ちの Slack 通知（`hook_lib/wait_notify.py`） |
| userPromptSubmit（Kiro CLI） | worktree の guard の、パスを見ない案内 |

起動は `uv run` を挟まず、SessionStart が用意した環境の python を直に使う（`lib/hook_python.py`）。**どの失敗も
終了コード 0 で、判定をせずに通す**（hook が止まると Tool の呼び出しが全部止まる）。PreToolUse と userPromptSubmit の
guard は締め切り（`hook_lib/deadline.py`）の中で打ち、過ぎたら残りを飛ばして標準エラーへ 1 行残す。拒否を返すのは token の guard だけで、
拒否があればそれだけを出す。案内は `additionalContext` に並べる。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))

COMMANDS = ("worktree-guard", "token-guard", "wait-notify", "words", "merge-target")
RUNTIMES = ("claude", "codex", "kiro")
NOTIFY_EVENTS = ("Stop", "stop", "Notification", "PermissionRequest")


def _args(argv: list[str]) -> tuple[str, str]:
    command, runtime = "", "claude"
    it = iter(argv)
    for a in it:
        if a in COMMANDS:
            command = a
        elif a == "--runtime":
            runtime = next(it, runtime)
    return command, runtime if runtime in RUNTIMES else "claude"


def _merge(outs: list) -> dict | str | None:
    outs = [o for o in outs if o]
    for o in outs:
        if isinstance(o, dict) and o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            return o
    if len(outs) <= 1:
        return outs[0] if outs else None
    merged: dict = {}
    contexts = []
    for o in outs:
        if not isinstance(o, dict):
            continue
        spec = o.get("hookSpecificOutput") or {}
        if spec.get("additionalContext"):
            contexts.append(spec["additionalContext"])
        merged.update({k: v for k, v in o.items() if k != "hookSpecificOutput"})
    if contexts:
        merged["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "additionalContext": "\n\n".join(contexts)}
    return merged or None


def dispatch(command: str, runtime: str, raw: dict | None) -> dict | str | None:
    from hook_lib import payload as pl

    if command == "wait-notify":
        from hook_lib import wait_notify

        wait_notify.hook(runtime, raw)
        return None
    if raw is None:
        return None
    ev = pl.event_of(raw)
    outs: list = []
    _guards(command, runtime, ev, outs)
    if not command and (ev.event in NOTIFY_EVENTS or ev.tool == "AskUserQuestion"):
        from hook_lib import wait_notify

        wait_notify.hook(runtime, raw)
    return _merge(outs)


def _guards(command: str, runtime: str, ev, outs: list) -> None:
    """worktree の guard と token の guard を締め切りの中で打ち、結果を `outs` へ足す（#1340 の I11・I12）。

    guard が走るのは PreToolUse と userPromptSubmit（agy の入力は事象の名前を持たない）だけで、事象の名前を問わずに掛ける。
    締め切りを過ぎたら残りの判定を飛ばし、標準エラーへ 1 行残す。それまでに終わった判定の結果（拒否を含む）は
    `outs` に残り、今の `_merge` の規則で出る。
    """
    from hook_lib import deadline

    dl = deadline.Deadline()
    dl.start()
    try:
        if command in ("", "worktree-guard") and (ev.tool_kind or ev.event in ("userPromptSubmit", "UserPromptSubmit")):
            dl.current = "worktree-guard"
            from hook_lib import worktree

            outs.append(worktree.notice(ev))
        if command == "token-guard" or (not command and runtime == "claude" and ev.event == "PreToolUse"):
            dl.current = "token-guard"
            from hook_lib import token_guard

            if ev.tool in token_guard.TOOLS:
                outs.append(token_guard.decision(ev))
    except deadline.HookDeadlineExceeded as e:
        print(dl.notice(e.name), file=sys.stderr)
    finally:
        dl.cancel()


def _opt(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv[:-1] else None


def merge_target(argv: list[str]) -> str:
    """`--base` の宛先へのマージの判定（`production` / `not-production` / `undetermined`）と理由の 2 行。"""
    import delivery

    base = _opt(argv, "--base")
    if base is None:
        return ""
    verdict = delivery.judge_target(delivery.load_delivery(_opt(argv, "--root") or "."), base)
    return f"{verdict.value}\n{verdict.reason}\n"


def main(argv: list[str]) -> int:
    try:
        command, runtime = _args(argv)
        if command == "words":  # 本文は JSON でない。語を NUL で区切って出す（hook_lib/words.py）
            from hook_lib import words

            got = words.command_stream(sys.stdin.read())
            sys.stdout.write("".join(w + "\0" for w in got))
            return 0
        if command == "merge-target":  # 判定できなければ何も出さない（呼び出し側が止める側へ倒す）
            sys.stdout.write(merge_target(argv))
            return 0
        try:
            raw = json.loads(sys.stdin.read() or "null")
        except (OSError, ValueError):
            raw = None
        out = dispatch(command, runtime, raw if isinstance(raw, dict) else None)
        if isinstance(out, str):
            sys.stdout.write(out)
        elif out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 環境が壊れている（import できない）・入力が読めない: 判定をせずに通す
        pass
    except BaseException as e:  # 締め切りの割り込みが guard の外で届いた: 通す（hook_lib/deadline.py）
        if type(e).__name__ != "HookDeadlineExceeded":
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
