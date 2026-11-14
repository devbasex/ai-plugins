"""副命令 `launch-args` と、外部 CLI のアダプターの名前の窓口（#1142 の AD3。N10）。

中身は責務ごとのモジュールが持つ。ここは副命令と、既存の呼び出し元のための再輸出だけを持つ。

- `claude.py`: `claude -p` の起動の引数と答えの読み取り
- `run_log.py`: 1 回の実行を識別子 1 つで追う記録（#2338）
- `agents.py`: 外部 CLI の起動と監視（#1411・#2059）、kiro のworktree の控え（#2448）
"""

from __future__ import annotations

from ndf import cli, identity
from ndf.adapters.agents import (
    AgentPaths,
    AgentStatus,
    BACKUP_SUFFIX,
    EARLY_WARN,
    KIRO_AGENTS,
    Launch,
    TAIL_BYTES,
    USAGE_LIMIT_LINES,
    Warned,
    early_warnings,
    kiro_guard,
    run_agent,
    stall_seconds,
    stop,
    tail,
    usage_limit,
    watch,
)
from ndf.adapters.claude import (
    CLAUDE,
    DISABLED_PLUGINS,
    FULL_TOOLS,
    ClaudeCall,
    ClaudeReply,
    call_claude,
    claude_args,
    minimal_args,
    plugin_args,
)
from ndf.adapters.run_log import (
    ENV_RUN_ID,
    ENV_RUN_LOG,
    ENV_RUN_LOG_SINK,
    HEAD_CHARS,
    MASK,
    RUNS_DIR,
    SECRET_MIN,
    SECRET_NAME,
    SINKS,
    RunLog,
    RunLogError,
    clip,
    close,
    current,
    diag,
    open_run,
    record,
    redact,
    runs_dir,
)
from ndf.contracts import result

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)

__all__ = [
    "BACKUP_SUFFIX",
    "CLAUDE",
    "DISABLED_PLUGINS",
    "EARLY_WARN",
    "ENV_RUN_ID",
    "ENV_RUN_LOG",
    "ENV_RUN_LOG_SINK",
    "FULL_TOOLS",
    "HEAD_CHARS",
    "KIRO_AGENTS",
    "MASK",
    "RUNS_DIR",
    "SECRET_MIN",
    "SECRET_NAME",
    "SINKS",
    "TAIL_BYTES",
    "USAGE_LIMIT_LINES",
    "AgentPaths",
    "AgentStatus",
    "ClaudeCall",
    "ClaudeReply",
    "Launch",
    "RunLog",
    "RunLogError",
    "Warned",
    "call_claude",
    "claude_args",
    "clip",
    "close",
    "current",
    "diag",
    "early_warnings",
    "kiro_guard",
    "main",
    "minimal_args",
    "open_run",
    "plugin_args",
    "record",
    "redact",
    "run_agent",
    "runs_dir",
    "stall_seconds",
    "stop",
    "tail",
    "usage_limit",
    "watch",
]


def main(argv: list[str]) -> int:
    """`ndf launch-args`: full の起動で `claude` に足す引数（旧い NDF を外し、このプラグインを読む）を items で出す。"""
    if argv:
        return result.emit(result.stopped(TOOL, "invalid", f"引数を受けない: {' '.join(argv)}"))
    args = plugin_args(identity.load().root)
    return result.emit(result.result(TOOL, result.OK, "full の起動の claude に足す引数", items=[{"args": args}]))
