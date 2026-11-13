#!/usr/bin/env python3
"""NDF の hook の 1 本のエントリポイント（#1142 の決定 20）。PreToolUse・Stop・Notification ほかを標準入力の JSON で受ける。

    <hook の環境の python> hook.py [worktree-guard | token-guard | wait-notify] [--runtime claude|codex|kiro]
    <hook の環境の python> hook.py goal-skill  < UserPromptSubmit の JSON   # `/goal` の条件の Skill の案内（hook_lib/goal_skill.py）
    python3 hook.py unattended-start|unattended-stop|unattended-end  < hook の JSON
        # 人のいない実行（NDF_FACTORY_RUN）の印と区間の報告の差し戻し（hook_lib/unattended.py。#3242）
    <hook の環境の python> hook.py words     < コマンドの本文   # 語の分割（workflow-guard.sh の wf_split）
    <hook の環境の python> hook.py merge-target --base <宛先> [--root <dir>]
        # マージの宛先の判定（workflow-merge.sh の wf_check_merge。lib/delivery.py。1 行目が判定、2 行目が理由）

副命令を省くと、事象と Tool の名前で振り分ける。

| 事象 | 判定 |
| --- | --- |
| PreToolUse（編集・パッチ・シェルの Tool） | worktree の guard（`hook_lib/worktree.py`） |
| PreToolUse（Bash・Read・Skill・Agent・Task。Claude Code だけ） | token の guard（`hook_lib/token_guard.py`） |
| PreToolUse（Agent・Task。Claude Code だけ） | 読む Skill の guard（`hook_lib/worker_skills.py`。supervisor が「読む Skill」の欄の無い・対応表を引かない `ndf:worker` を起動するのを止める。#2399） |
| PreToolUse（Read・Bash。Claude Code だけ） | 規約の文書の guard（`hook_lib/supervisor_docs.py`。supervisor が読み込まれたプラグインの外の規約の文書を読むのを止め、中の同じ文書の絶対パスを返す。#2813） |
| PreToolUse（シェルの Tool。Claude Code と Codex） | 手元のテストの guard（`hook_lib/ci_test_guard.py`。CI テストの宣言のあるリポジトリで、テストの suite の起動の形に当たるコマンドを止める。#3056） |
| PreToolUse（Bash。Claude Code だけ） | 起票の guard（`hook_lib/issue_guard.py`。AI が直接打つ `gh issue create` の本文が記載項目の定義を満たさなければ止める。#2737） |
| PreToolUse（AskUserQuestion。Claude Code だけ） | 問い合わせの guard（`hook_lib/ask_guard.py`。区分の無い・許されない問いを止める。#2206） |
| PreToolUse（AskUserQuestion。止めなかったとき）・Stop・Notification・PermissionRequest | 待ちの Slack 通知（`hook_lib/wait_notify.py`） |
| userPromptSubmit（Kiro CLI） | worktree の guard の、パスを見ない案内 |
| UserPromptSubmit（Claude Code。副命令 `goal-skill`） | `/goal /<プラグイン>:<Skill>` の条件の Skill を読み込む案内（`hook_lib/goal_skill.py`） |
| UserPromptSubmit・Stop・SessionEnd（Claude Code。副命令 `unattended-*`。`NDF_FACTORY_RUN` があるときだけ） | 人のいない実行の印と、区間の報告の形に合わない最後の応答の差し戻し（`hook_lib/unattended.py`） |

起動は `uv run` を挟まず、SessionStart が用意した環境の python を直に使う（`lib/hook_python.py`）。**どの失敗も
終了コード 0 で、判定をせずに通す**（hook が止まると Tool の呼び出しが全部止まる）。PreToolUse と userPromptSubmit の
guard は締め切り（`hook_lib/deadline.py`）の中で打ち、過ぎたら残りを飛ばして標準エラーへ 1 行残す。拒否を返すのは token の guard と
読む Skill の guard と規約の文書の guard と手元のテストの guard と起票の guard と問い合わせの guard だけで、拒否があればそれだけを出す（問い合わせを止めたときは待ちの通知も送らない）。案内は `additionalContext` に並べる。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))

COMMANDS = (
    "worktree-guard",
    "token-guard",
    "wait-notify",
    "goal-skill",
    "words",
    "merge-target",
    "unattended-start",
    "unattended-stop",
    "unattended-end",
)
UNATTENDED = ("unattended-start", "unattended-stop", "unattended-end")
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
    deny = next((o for o in outs if isinstance(o, dict) and o.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"), None)
    if deny is not None:
        return deny
    if len(outs) <= 1:
        return outs[0] if outs else None
    merged = _merge_top_level(outs)
    if contexts := _collect_contexts(outs):
        merged["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "additionalContext": "\n\n".join(contexts)}
    return merged or None


def _collect_contexts(outs: list) -> list:
    """各出力の hookSpecificOutput.additionalContext を順に集める。"""
    dicts = (o for o in outs if isinstance(o, dict))
    return [spec["additionalContext"] for spec in ((o.get("hookSpecificOutput") or {}) for o in dicts) if spec.get("additionalContext")]


def _merge_top_level(outs: list) -> dict:
    """hookSpecificOutput 以外のキーを、後の出力が勝つ順で 1 つの辞書へ畳む。"""
    merged: dict = {}
    for o in outs:
        if isinstance(o, dict):
            merged.update({k: v for k, v in o.items() if k != "hookSpecificOutput"})
    return merged


def dispatch(command: str, runtime: str, raw: dict | None) -> dict | str | None:
    if command in UNATTENDED:  # guard と待ち通知を通さず、部品の出力 1 つを返す（標準ライブラリだけの経路。#3242）
        return _unattended(command, raw)
    from hook_lib import payload as pl

    if command == "wait-notify":
        from hook_lib import wait_notify

        wait_notify.hook(runtime, raw)
        return None
    if command == "goal-skill":  # _merge を通さず、自分の出力 1 つを返す（hookEventName を UserPromptSubmit に保つ）
        from hook_lib import goal_skill

        text = goal_skill.context(raw)
        return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}} if text else None
    if raw is None:
        return None
    ev = pl.event_of(raw)
    outs: list = []
    _guards(command, runtime, ev, outs)
    if not command and (ev.event in NOTIFY_EVENTS or (ev.tool == "AskUserQuestion" and not _denied(outs))):
        from hook_lib import wait_notify

        wait_notify.hook(runtime, raw)
    return _merge(outs)


def _unattended(command: str, raw: dict | None) -> dict | None:
    """人のいない実行の hook（`hook_lib/unattended.py`）。失敗は標準エラーへ 1 行残して通す（#3242 の I9）。"""
    if raw is None:
        return None
    import time

    from hook_lib import unattended

    try:
        if command == "unattended-start":
            text = unattended.mark_session(raw, time.time())
            return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}} if text else None
        if command == "unattended-stop":
            return unattended.review_stop(raw)
        unattended.clear_mark(raw)
    except Exception as e:  # noqa: BLE001 — 印の読み書きの失敗で Stop と UserPromptSubmit を止めない
        print(f"ndf: {command}: {type(e).__name__}", file=sys.stderr)
    return None


def _denied(outs: list) -> bool:
    return any(isinstance(o, dict) and (o.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny" for o in outs)


def _guards(command: str, runtime: str, ev, outs: list) -> None:
    """worktree の guard・token の guard・読む Skill の guard・規約の文書の guard・手元のテストの guard・起票の guard・問い合わせの guard を締め切りの中で打ち、結果を `outs` へ足す（#1340 の I11・I12）。

    guard が走るのは PreToolUse と userPromptSubmit（agy の入力は事象の名前を持たない）だけで、事象の名前を問わずに掛ける。
    締め切りを過ぎたら残りの判定を飛ばし、標準エラーへ 1 行残す。それまでに終わった判定の結果（拒否を含む）は
    `outs` に残り、今の `_merge` の規則で出る。
    """
    from hook_lib import deadline

    dl = deadline.Deadline()
    dl.start()
    is_claude_pretool = not command and runtime == "claude" and ev.event == "PreToolUse"
    try:
        if command in ("", "worktree-guard") and (ev.tool_kind or ev.event in ("userPromptSubmit", "UserPromptSubmit")):
            _run_guard(dl, "worktree-guard", _worktree_guard, ev, outs)
        if command == "token-guard" or is_claude_pretool:
            _run_guard(dl, "token-guard", _token_guard, ev, outs)
        if is_claude_pretool and ev.tool in ("Agent", "Task"):
            _run_guard(dl, "worker-skills", _worker_skills_guard, ev, outs)
        if is_claude_pretool and ev.tool in ("Read", "Bash"):
            _run_guard(dl, "supervisor-docs", _supervisor_docs_guard, ev, outs)
        if not command and runtime in ("claude", "codex") and ev.event == "PreToolUse" and ev.tool_kind == "shell":
            _run_guard(dl, "ci-test-guard", _ci_test_guard, ev, outs)
        if is_claude_pretool and ev.tool == "Bash":
            _run_guard(dl, "issue-guard", _issue_guard, ev, outs)
        if is_claude_pretool and ev.tool == "AskUserQuestion":
            _run_guard(dl, "ask-guard", _ask_guard, ev, outs)
    except deadline.HookDeadlineExceeded as e:
        print(dl.notice(e.name), file=sys.stderr)
    finally:
        dl.cancel()


def _run_guard(dl, name: str, guard, ev, outs: list) -> None:
    """締め切りの今の判定を `name` にして guard を 1 本打ち、返った出力を `outs` へ足す。"""
    dl.current = name
    outs.extend(guard(ev))


def _worktree_guard(ev) -> list:
    from hook_lib import worktree

    return [worktree.notice(ev)]


def _token_guard(ev) -> list:
    from hook_lib import token_guard

    return [token_guard.decision(ev)] if ev.tool in token_guard.TOOLS else []


def _worker_skills_guard(ev) -> list:
    from hook_lib import worker_skills

    try:
        return [worker_skills.skills_decision(ev)]
    except Exception:  # noqa: BLE001 — 判定の途中の失敗は通す（ほかの guard の結果は残す）
        return []


def _supervisor_docs_guard(ev) -> list:
    from hook_lib import supervisor_docs

    try:
        return [supervisor_docs.docs_decision(ev)]
    except Exception:  # noqa: BLE001 — 判定の途中の失敗は通す（ほかの guard の結果は残す）
        return []


def _ci_test_guard(ev) -> list:
    from hook_lib import ci_test_guard

    try:
        found = ci_test_guard.ci_test_decision(ev)
    except Exception:  # noqa: BLE001 — 判定の途中の失敗は通す（ほかの guard の結果は残す）
        return []
    return [found] if found else []


def _issue_guard(ev) -> list:
    from hook_lib import issue_guard

    try:
        return [issue_guard.issue_decision(ev)]
    except Exception:  # noqa: BLE001 — 判定の途中の失敗は通す（ほかの guard の結果は残す）
        return []


def _ask_guard(ev) -> list:
    from hook_lib import ask_guard

    return [ask_guard.ask_decision(ev)]


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
