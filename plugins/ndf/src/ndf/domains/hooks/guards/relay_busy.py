"""中継の子の会話のバックグラウンドの作業を、起動ごとの記録（`busy-<起動の ID>.json`）へ書く guard（#4049 の決定 2・3・7）。

SessionStart（`startup`・`resume`）で 0 件を、作業を始める呼び出しの前に、子を止める排他の中で「1 件以上」を書く。書けない
開始は、締め切りの経路でも拒否する。記録が 0 件の子は、その後に作業を始めていない。件数は Stop の `relay-marker` が書き直す。
監視を始める呼び出しの前には、同じ排他の中で作業の件数を保ったまま監視だけを 1 件以上にする（応答を中断して Stop が来ない子を、
ラッパーが止まりで起動し直して監視を失わせない）。こちらは書けなくても拒否しない。

バックグラウンドの作業 1 件は、待つべき作業と止めてよい監視に分ける。監視は、コマンドが待つだけの NDF のコマンド
（`params.wait_commands`）だけでできている Monitor とバックグラウンドの Bash だけで、確かめきれないものはすべて作業とする
（監視と取り違えると、引継ぎで作業を失う）。

すべての PreToolUse に当たる `fail_closed` の行なので、対象外の呼び出しは、例外を上げうる処理（記録・構文解析・表の読み込み）
より先に通す。作業を始め得ない呼び出し（Read・フォアグラウンドの Bash など）は、環境変数のコントラクトを読む会話の確かめ
（`_target`）よりも先に、Tool の名前と `run_in_background` だけで通す。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ndf import identity
from ndf.domains.hooks import tables
from ndf.domains.hooks.context import ShellCommand, only_commands
from ndf.domains.hooks.guards.shell import background_call
from ndf.domains.hooks.verdict import Verdict, deny
from ndf.domains.relay import signals

GUARD_NAME = "relay-busy"
BUSY_START_FAILED = (
    "バックグラウンドの作業の開始を中継へ記録できなかったので、始めない。もう一度呼ぶ"
    "（中継のラッパーは記録の無いバックグラウンドの作業を守れない）"
)

_recorded: Optional[dict] = None
"""開始を記録し終えた事象の payload（同じ実行の `on_deadline` が受け取る payload と同じオブジェクト。`ask._recorded` と同じ扱い）。"""

# 0 件を書く SessionStart の `source`（`compact`・`clear` は前のバックグラウンドの作業が残りうる）
_FRESH_SOURCES = ("startup", "resume")

# 監視になりうる種類（`background_tasks` の `type`。Monitor とバックグラウンドの Bash）
_WATCHABLE = ("monitor", "shell")
# 長いコマンドを Claude Code が切ったときに末尾へ足す文字列（`… [+N chars]`）
_CUT_MARK = "… [+"


def check(ctx, svc) -> Optional[Verdict]:
    if ctx.event == "SessionStart":
        if ctx.payload.get("source") in _FRESH_SOURCES and _target(ctx.env, ctx.session_id):
            record_count(ctx.env, ctx.session_id, 0, "session")
        return None
    if not _may_start(ctx.tool, ctx.tool_input) or not _target(ctx.env, ctx.session_id):
        return None
    if _starts_background(ctx.tool, ctx.tool_input, svc.params):
        return _record_start(ctx)
    _record_monitor(ctx)
    return None


def on_deadline(env: Mapping[str, str], payload: dict) -> Optional[str]:
    """エントリポイントの締め切りを過ぎたときの拒否の理由。記録し終えていない子の会話の作業の開始だけを拒否し、ほかは None（通す）。"""
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input")
    if payload.get("hook_event_name") != "PreToolUse" or not _may_start(tool, tool_input):
        return None
    if not _target(env, payload.get("session_id")) or _recorded is payload:
        return None
    return BUSY_START_FAILED if _starts_background(tool, tool_input, table_params()) else None


def table_params() -> Mapping[str, Any]:
    """guard の表のこの guard の `params`。`svc.params` を受け取らない呼び手が使う。読めなければ空（すべて作業に数える）。"""
    try:
        params = next(row["params"] for row in tables.guards() if row["name"] == GUARD_NAME)
    except Exception:  # noqa: BLE001 ファイル: 読めない表では待つコマンドが分からないので、すべて作業に数える
        return {}
    return params if isinstance(params, dict) else {}


def record_count(env: Mapping[str, str], session_id: str, count: int, source: str, monitors: int = 0) -> bool:
    """子の会話の作業の件数を書けたか。保存先か起動の ID が無ければ書かずに偽（記録はラッパーが不明として読む）。"""
    directory, launch = signals.relay_dir(env), signals.relay_launch(env)
    if directory is None or launch is None:
        return False
    return signals.write_busy(directory, launch, session_id, count, source, monitors)


def _record_start(ctx) -> Optional[Verdict]:
    """作業の開始を記録する。保存先か起動の ID が無いか、書けなければ拒否。"""
    global _recorded
    directory, launch = signals.relay_dir(ctx.env), signals.relay_launch(ctx.env)
    wait = float(tables.policy_value("hook.counter-lock", "seconds"))
    if directory is None or launch is None or not signals.record_start(directory, launch, ctx.session_id, wait):
        return deny(BUSY_START_FAILED)
    _recorded = ctx.payload
    return None


def _record_monitor(ctx) -> None:
    """監視の開始を記録する。書けなくても監視は始める（止めてよく、打ち直せば同じ知らせを受け取れる）。"""
    try:
        directory, launch = signals.relay_dir(ctx.env), signals.relay_launch(ctx.env)
        if directory is not None and launch is not None:
            signals.record_monitor(directory, launch, ctx.session_id, float(tables.policy_value("hook.counter-lock", "seconds")))
    except Exception:  # noqa: BLE001,S110 ファイル: fail_closed の行なので、ここで上げると監視の開始を拒否してしまう
        pass


def _target(env: Mapping[str, str], session_id: Any) -> bool:
    """中継のラッパーが起動した子の会話か。入れ子の claude は会話の ID が違う。コントラクトの誤りも対象外とし、例外を外へ出さない。

    保存先が在るかは見ない（保存先が消えた子の会話を、中継の外と取り違えて作業を始めさせない）。
    """
    if not signals.keys_known() or not env.get(identity.env_name("relay-dir")):
        return False
    child = signals.relay_session(env)
    return child is not None and session_id == child


def _may_start(tool: Any, tool_input: Any) -> bool:
    """作業を始め得る呼び出しか（Tool の名前と Bash の `run_in_background` の値の比べだけで、ファイルと構文解析に触れない）。"""
    if tool == "Bash":
        return isinstance(tool_input, dict) and background_call(tool_input)
    return tool in ("Agent", "Monitor")


def _starts_background(tool: Any, tool_input: Any, params: Mapping[str, Any]) -> bool:
    """作業を始める呼び出しか。例外を外へ出さない。Monitor は必ずバックグラウンドで動くので、確かめられない入力は作業とする。"""
    if not _may_start(tool, tool_input) or tool == "Agent":
        return tool == "Agent"
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return task_kind("monitor" if tool == "Monitor" else "shell", command, params) == "work"


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
