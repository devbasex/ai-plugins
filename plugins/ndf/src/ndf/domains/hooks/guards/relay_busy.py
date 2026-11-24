"""中継の子の会話のバックグラウンドの作業 1 件を、待つべき作業と止めてよい監視に分ける（#4049 の決定 7）。

監視は、コマンドが待つだけの NDF のコマンド（`params.wait_commands`）だけでできている Monitor とバックグラウンドの Bash だけで、
確かめきれないものはすべて作業とする（監視と取り違えると、引継ぎで作業を失う）。
"""

from __future__ import annotations

from typing import Any, Mapping

from ndf.domains.hooks.context import ShellCommand, only_commands

# 監視になりうる種類（`background_tasks` の `type`。Monitor とバックグラウンドの Bash）
_WATCHABLE = ("monitor", "shell")
# 長いコマンドを Claude Code が切ったときに末尾へ足す文字列（`… [+N chars]`）
_CUT_MARK = "… [+"


def task_kind(kind: Any, command: Any, params: Mapping[str, Any]) -> str:
    """バックグラウンドの作業 1 件の種類（`monitor` は止めてよい監視、`work` は待つべき作業）。例外を外へ出さない。"""
    if kind not in _WATCHABLE or not isinstance(command, str) or _CUT_MARK in command:
        return "work"
    try:
        waits = [tuple(name.split()) for name in params["wait_commands"]]
        return "monitor" if only_commands(command, lambda cmd: _is_wait(cmd, waits)) else "work"
    except Exception:  # noqa: BLE001,NDF401 利用者の入力: 確かめられないコマンドは作業として待つ（監視と取り違えると作業を失う）
        return "work"


def _is_wait(cmd: ShellCommand, waits: list[tuple[str, ...]]) -> bool:
    """ラッパーを外したコマンドの先頭が、待つコマンドのどれかか（1 語目はパスを外した名前で比べる）。"""
    words = (cmd.body_name, *cmd.body_argv[1:])
    return any(wait and words[: len(wait)] == wait for wait in waits)


def work_count(tasks: list, params: Mapping[str, Any]) -> tuple[int, int]:
    """Stop の `background_tasks` の（作業の件数, 監視の件数）。辞書でない行は作業に数える。"""
    kinds = [task_kind(task.get("type"), task.get("command"), params) if isinstance(task, dict) else "work" for task in tasks]
    return kinds.count("work"), kinds.count("monitor")
