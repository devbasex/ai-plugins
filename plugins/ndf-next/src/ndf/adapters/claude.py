"""`claude -p` の起動の引数と答えの読み取り（#1142 の AD3）。

Skill を回す `full` の起動は、入っている旧い NDF を `--settings` の `enabledPlugins` で外し、`--plugin-dir` で
このプラグインを読む（`plugin_args`）。外す設定は `DISABLED_PLUGINS` の 1 か所だけが持ち、起動のスクリプトは
副命令 `launch-args` で同じ値を読む。利用者の設定ファイルは書き換えない。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional, Sequence

from ndf import identity
from ndf.adapters.run_log import current

CLAUDE = "claude"
# Skill を回す起動（full）で外す、入っている旧い NDF（マーケットプレイスの登録の名前）。
# 起動のスクリプトは `ndf launch-args` の items で同じ値を読む。値を写さない
DISABLED_PLUGINS: dict[str, bool] = {"ndf@ai-plugins": False}
# full の起動で許す Tool
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch,WebFetch,WebSearch"
# 起動の頭（問いを 1 回渡し、答えを JSON で受ける）
PRINT_JSON = ["-p", "--output-format", "json"]
# Tool を許す起動の許可のモード
ACCEPT_EDITS = ["--permission-mode", "acceptEdits"]


@dataclass(frozen=True)
class ClaudeCall:
    """`claude -p` の 1 回の起動の中身。

    `full` は Skill を回す起動（設定・プラグイン・hook をそのまま読む）。偽なら最小構成（利用者の設定を読まない）。
    `tools` は最小構成で許す Tool（None なら Tool を使わせない）。`json_schema` は答えを縛るスキーマ。
    """

    system: str
    full: bool = False
    tools: Optional[str] = None
    resume: Optional[str] = None
    json_schema: Optional[dict] = None
    model: Optional[str] = None


def plugin_args(root: Path) -> list[str]:
    """入っている旧い NDF を外し、`root` のプラグインを読む `claude` の引数。"""
    settings = json.dumps({"enabledPlugins": DISABLED_PLUGINS}, separators=(",", ":"))
    return ["--settings", settings, "--plugin-dir", str(root)]


def minimal_args(system: str) -> list[str]:
    """最小構成（設定・MCP・スラッシュコマンドを読まず、会話を残さない）の `claude -p` の引数。"""
    head = [*PRINT_JSON, "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    return head + ["--disable-slash-commands", "--system-prompt", system]


def _full_args(call: ClaudeCall, root: Path) -> list[str]:
    args = [*PRINT_JSON, *ACCEPT_EDITS, "--allowed-tools", FULL_TOOLS]
    args += plugin_args(root) + ["--append-system-prompt", call.system]
    return args + (["--resume", call.resume] if call.resume else [])


def claude_args(call: ClaudeCall, root: Optional[Path] = None, base: Sequence[str] = (CLAUDE,)) -> list[str]:
    """起動の引数。`root` は full で読むプラグインの根（省けば自分の根）。`base` は起動のコマンド。"""
    if call.full:
        args = _full_args(call, root if root is not None else identity.load().root)
    elif call.tools:
        args = minimal_args(call.system) + ["--tools", call.tools, "--allowed-tools", call.tools, *ACCEPT_EDITS]
    else:
        args = minimal_args(call.system) + ["--tools", ""]
    if call.json_schema is not None:
        args += ["--json-schema", json.dumps(call.json_schema, ensure_ascii=False)]
    if call.model:
        args += ["--model", call.model]
    return [*base, *args]


class ClaudeReply(NamedTuple):
    """`claude -p --output-format json` の 1 回の答え。`answer` は縛った答え（`structured_output`）か本文の JSON。"""

    ok: bool
    answer: Any
    raw: str
    cost: float
    error: Optional[str] = None

    @classmethod
    def failed(cls, raw: str, error: str, cost: float = 0.0) -> "ClaudeReply":
        """`ok` が偽で答えを持たない答え。"""
        return cls(False, None, raw, cost, error)


def _parse_reply(stdout: str) -> ClaudeReply:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return ClaudeReply.failed(stdout, f"claude の出力が JSON でない: {exc}")
    if not isinstance(payload, dict):
        return ClaudeReply.failed(stdout, "claude の出力が JSON のオブジェクトでない")
    spent = payload.get("total_cost_usd")
    cost = float(spent) if isinstance(spent, (int, float)) else 0.0
    text = payload.get("result") if isinstance(payload.get("result"), str) else stdout
    if payload.get("is_error"):
        return ClaudeReply.failed(text, f"claude が誤りで終えた: {text[:200]}", cost)
    if "structured_output" in payload:
        return ClaudeReply(True, payload["structured_output"], text, cost)
    try:
        return ClaudeReply(True, json.loads(text), text, cost)
    except json.JSONDecodeError:
        # 答えの本文が JSON でなければ、形に合わない答えとして返す
        return ClaudeReply(True, None, text, cost)


def call_claude(argv: Sequence[str], prompt: str, timeout: float, cwd: Optional[Path] = None) -> ClaudeReply:
    """`claude` を起動し、問いを標準入力で渡して答えを読む。起動の失敗・期限切れは `ok` が偽の答えにする。"""
    log = current()
    env = {**os.environ, **(log.child_env() if log else {})}
    try:
        done = subprocess.run(list(argv), input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ClaudeReply.failed("", f"claude を起動できない・期限を超えた: {exc}")
    if done.returncode != 0 and not done.stdout.strip():
        return ClaudeReply.failed(done.stderr, f"claude が終了コード {done.returncode} で終えた: {done.stderr[:200]}")
    return _parse_reply(done.stdout)
