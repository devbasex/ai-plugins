"""`claude -p` の起動の引数と答えの読み取り（#1142 の AD3）。

起動の権限は `Permission` の 3 つだけで、権限ごとの引数は `permission_args` の 1 か所が持つ（#3364）。
判断の呼び出しは Tool を使わせず、Tool を許す起動も許可のモードは acceptEdits までで、許可を飛ばす
フラグ（`--dangerously-skip-permissions`・`bypassPermissions`）を使わない。

Skill を回す `full` の起動は、入っている旧い NDF を `--settings` の `enabledPlugins` で外し、`--plugin-dir` で
このプラグインを読む（`plugin_args`）。外す設定は `DISABLED_PLUGINS` の 1 か所だけが持ち、起動のスクリプトは
副命令 `launch-args` で同じ値を読む。利用者の設定ファイルは書き換えない。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, Optional, Sequence

from ndf import identity
from ndf.adapters.agents import stop, usage_limit
from ndf.adapters.run_log import child_process_env
from ndf.contracts import facts

CLAUDE = facts.value("runtimes", "claude", "executable")
# Skill を回す起動（full）で外す、入っている旧い NDF（マーケットプレイスの登録の名前）。
# 起動のスクリプトは `ndf launch-args` の items で同じ値を読む。値を写さない
DISABLED_PLUGINS: dict[str, bool] = {"ndf@ai-plugins": False}
# full の起動で許す Tool
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch,WebFetch,WebSearch"
# 起動の頭（問いを 1 回渡し、答えを JSON で受ける）
PRINT_JSON = ["-p", "--output-format", "json"]
# Tool を許す起動の許可のモード
ACCEPT_EDITS = ["--permission-mode", "acceptEdits"]


class Permission(StrEnum):
    """`claude -p` の起動の権限。

    - `judge`: 判断の呼び出し。最小構成で Tool を使わせない
    - `tools`: 最小構成で、渡した Tool だけを許して編集を受ける（acceptEdits）
    - `full`: Skill を回す起動。設定・プラグイン・hook をそのまま読み、`FULL_TOOLS` を許して編集を受ける
    """

    JUDGE = "judge"
    TOOLS = "tools"
    FULL = "full"


class Failure(StrEnum):
    """`claude -p` の 1 回が答えを返せなかった理由のうち、打ち直しても直らないもの（#3394）。"""

    LAUNCH = "launch"
    TIMEOUT = "timeout"
    USAGE_LIMIT = "usage_limit"


@dataclass(frozen=True)
class ClaudeCall:
    """`claude -p` の 1 回の起動の中身。

    `permission` は起動の権限（`Permission`）。`tools` は `tools` の権限で許す Tool で、ほかの権限では渡さない。
    `resume` は `full` の起動だけが受ける。`json_schema` は答えを縛るスキーマ。
    """

    system: str
    permission: Permission = Permission.JUDGE
    tools: Optional[str] = None
    resume: Optional[str] = None
    json_schema: Optional[dict] = None
    model: Optional[str] = None

    def __post_init__(self) -> None:
        if (self.permission is Permission.TOOLS) != bool(self.tools):
            raise ValueError(f"tools は権限 {Permission.TOOLS} のときだけ、空でなく渡す（権限 {self.permission}）")
        if self.resume and self.permission is not Permission.FULL:
            raise ValueError(f"resume は権限 {Permission.FULL} の起動だけが受ける（権限 {self.permission}）")


def plugin_args(root: Path) -> list[str]:
    """入っている旧い NDF を外し、`root` のプラグインを読む `claude` の引数。"""
    settings = json.dumps({"enabledPlugins": DISABLED_PLUGINS}, separators=(",", ":"))
    return ["--settings", settings, "--plugin-dir", str(root)]


def minimal_args(system: str) -> list[str]:
    """最小構成（設定・MCP・スラッシュコマンドを読まず、会話を残さない）の `claude -p` の引数。"""
    head = [*PRINT_JSON, "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    return head + ["--disable-slash-commands", "--system-prompt", system]


def permission_args(permission: Permission, tools: Optional[str] = None) -> list[str]:
    """権限ごとの、使える Tool と許可のモードの引数。権限の引数はここだけが持つ。"""
    if permission is Permission.FULL:
        return [*ACCEPT_EDITS, "--allowed-tools", FULL_TOOLS]
    if permission is Permission.TOOLS:
        if not tools:
            raise ValueError(f"権限 {Permission.TOOLS} は許す Tool を要る")
        return ["--tools", tools, "--allowed-tools", tools, *ACCEPT_EDITS]
    return ["--tools", ""]


def _full_args(call: ClaudeCall, root: Path) -> list[str]:
    args = [*PRINT_JSON, *permission_args(Permission.FULL)]
    args += plugin_args(root) + ["--append-system-prompt", call.system]
    return args + (["--resume", call.resume] if call.resume else [])


def claude_args(call: ClaudeCall, root: Optional[Path] = None, base: Sequence[str] = (CLAUDE,)) -> list[str]:
    """起動の引数。`root` は full で読むプラグインの根（省けば自分の根）。`base` は起動のコマンド。"""
    if call.permission is Permission.FULL:
        args = _full_args(call, root if root is not None else identity.load().root)
    else:
        args = minimal_args(call.system) + permission_args(call.permission, call.tools)
    if call.json_schema is not None:
        args += ["--json-schema", json.dumps(call.json_schema, ensure_ascii=False)]
    if call.model:
        args += ["--model", call.model]
    return [*base, *args]


class ClaudeReply(NamedTuple):
    """`claude -p --output-format json` の 1 回の答え。`answer` は縛った答え（`structured_output`）か本文の JSON。

    `failure` は打ち直しても直らない失敗の理由（`Failure`）。None の失敗（誤りで終えた・出力が JSON でない）は
    打ち直せば直りうる。
    """

    ok: bool
    answer: Any
    raw: str
    cost: float
    error: Optional[str] = None
    failure: Optional[Failure] = None

    @classmethod
    def failed(cls, raw: str, error: str, cost: float = 0.0, failure: Optional[Failure] = None) -> "ClaudeReply":
        """`ok` が偽で答えを持たない答え。"""
        return cls(False, None, raw, cost, error, failure)


def _limit_hit(payload: dict, text: str) -> bool:
    """claude の結果の JSON が利用上限（`api_error_status` 429 か、上限の行）を告げているか。"""
    return payload.get("api_error_status") == 429 or usage_limit(text) is not None


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
        if _limit_hit(payload, text):
            return ClaudeReply.failed(text, f"claude の利用上限: {text[:200]}", cost, Failure.USAGE_LIMIT)
        return ClaudeReply.failed(text, f"claude が誤りで終えた: {text[:200]}", cost)
    if "structured_output" in payload:
        return ClaudeReply(True, payload["structured_output"], text, cost)
    try:
        return ClaudeReply(True, json.loads(text), text, cost)
    except json.JSONDecodeError:
        # 答えの本文が JSON でなければ、形に合わない答えとして返す
        return ClaudeReply(True, None, text, cost)


def _close_streams(proc: subprocess.Popen) -> None:
    """止めたプロセスの標準入出力を閉じる。閉じるときの失敗は受け流す。"""
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except OSError:  # noqa: S110 外部コマンド: 止めた claude との管が既に壊れていても閉じる試みで足りる
            pass


def call_claude(argv: Sequence[str], prompt: str, timeout: float, cwd: Optional[Path] = None) -> ClaudeReply:
    """`claude` を起動し、問いを標準入力で渡して答えを読む。起動の失敗・期限切れは `ok` が偽の答えにする。

    専用のプロセスの組で起動し、期限を超えたら組ごと止めて回収する（claude が動かした Tool の子孫を残さない）。
    """
    pipe = subprocess.PIPE
    try:
        proc = subprocess.Popen(
            list(argv), stdin=pipe, stdout=pipe, stderr=pipe, text=True, cwd=cwd, env=child_process_env(), start_new_session=True
        )
    except OSError as exc:
        return ClaudeReply.failed("", f"claude を起動できない: {exc}", failure=Failure.LAUNCH)
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stop(proc)
        _close_streams(proc)
        return ClaudeReply.failed("", f"claude が期限を超えた: {exc}", failure=Failure.TIMEOUT)
    if proc.returncode != 0 and not stdout.strip():
        limit = Failure.USAGE_LIMIT if usage_limit(stderr) else None
        return ClaudeReply.failed(stderr, f"claude が終了コード {proc.returncode} で終えた: {stderr[:200]}", failure=limit)
    return _parse_reply(stdout)
