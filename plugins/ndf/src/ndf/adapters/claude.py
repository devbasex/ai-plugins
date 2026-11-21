"""`claude -p` の起動の引数と回答の読み取り（#1142 の AD3）。

起動の権限は `Permission` の 3 つだけで、権限ごとの引数は `permission_args` の 1 か所が持つ（#3364）。
判断の呼び出しは Tool を使わせず、Tool を許す起動も許可のモードは acceptEdits までで、許可を飛ばす
フラグ（`--dangerously-skip-permissions`・`bypassPermissions`）を使わない。

Skill を回す `full` の起動は、入っている旧い NDF を `--settings` の `enabledPlugins` で外し、`--plugin-dir` で
このプラグインを読む（`plugin_args`）。外す登録は登録名を決め打たず、`claude plugin list --json` の導入済みの一覧から
決める（#3617）。起動のスクリプトはサブコマンド `launch-args` で同じ値を読む。利用者の設定ファイルは書き換えない。

起動の前に、学習に使わない設定のスナップショット（`training.refusal`）を読み、重い起動のスロット（`launch_slot.held`）を取る（#3391・#3368。前置きは `launch_slot.gate` が持つ）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence

from ndf import identity, process_group
from ndf.adapters import launch_slot
from ndf.adapters.agents import usage_limit
from ndf.adapters.run_log import child_process_env
from ndf.contracts import facts

CLAUDE = facts.value("runtimes", "claude", "executable")
# 導入済みのプラグインの一覧を読む時間の上限（秒）
PLUGIN_LIST_TIMEOUT = 30
# 一覧を読めなかったときの知らせ（導入先のパスを含む CLI の出力は載せない）
LIST_UNREADABLE = "導入済みのプラグインの一覧を読めず、旧い NDF を外さずに起動する"
# full の起動で許す Tool
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch,WebFetch,WebSearch"
# 起動の頭（質問を 1 回渡し、回答を JSON で受ける）
PRINT_JSON = ["-p", "--output-format", "json"]
# 逐次の起動の頭（1 歩ごとに 1 行の JSON を出す。stream-json は --verbose が無いと起動できない。#3945）
PRINT_STREAM = ["-p", "--output-format", "stream-json", "--verbose"]
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
    """`claude -p` の 1 回が回答を返せなかった理由のうち、打ち直しても直らないもの（#3394）。"""

    LAUNCH = "launch"
    TIMEOUT = "timeout"
    USAGE_LIMIT = "usage_limit"
    # 学習に使わない設定と確かめられていないので起動しなかった（`ndf training check` で直る）
    REFUSED = "refused"
    # 重い起動のスロットが待ちの上限までに空かなかった
    NO_SLOT = "no_slot"


@dataclass(frozen=True)
class ClaudeCall:
    """`claude -p` の 1 回の起動の中身。

    `permission` は起動の権限（`Permission`）。`tools` は `tools` の権限で許す Tool で、ほかの権限では渡さない。
    `resume` は `full` の起動だけが受ける。`json_schema` は回答を縛るスキーマ。`stream` は標準出力を 1 歩ごとの
    行にする（結果をファイルで受け、標準出力を読まない担当だけが使う。無進捗の監視が作業中の担当を止まりと誤らない。#3945）。
    """

    system: str
    permission: Permission = Permission.JUDGE
    tools: Optional[str] = None
    resume: Optional[str] = None
    json_schema: Optional[dict] = None
    model: Optional[str] = None
    stream: bool = False

    def __post_init__(self) -> None:
        if (self.permission is Permission.TOOLS) != bool(self.tools):
            raise ValueError(f"tools は権限 {Permission.TOOLS} のときだけ、空でなく渡す（権限 {self.permission}）")
        if self.resume and self.permission is not Permission.FULL:
            raise ValueError(f"resume は権限 {Permission.FULL} の起動だけが受ける（権限 {self.permission}）")


class PluginArgs(NamedTuple):
    """`claude` の引数と、導入済みの一覧を読めなかったときの知らせ（読めれば `None`）。"""

    args: list[str]
    notice: Optional[str]


class Ran(NamedTuple):
    """`claude plugin` を 1 回打った結果。起動できない・時間切れは `code` が None で、`error` が理由を持つ。"""

    code: Optional[int]
    stdout: str
    error: str


def run_plugin(
    args: Sequence[str],
    timeout: float,
    env: Mapping[str, str],
    cwd: Path,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Ran:
    """`claude plugin <args>` を標準入力なしで `cwd`・`env` で 1 回打つ（project・local のスコープは作業ディレクトリで決まる）。

    起動できない・時間切れは例外を上げずに `Ran(None, "", <理由>)` を返す。理由に導入先のパスを含めない。
    """
    argv = [CLAUDE, "plugin", *args]
    try:
        done = run(argv, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:  # 時間切れは呼び出し元が失敗として扱う
        return Ran(None, "", f"{timeout:g} 秒で終わらない")
    except (OSError, subprocess.SubprocessError) as exc:  # 起動できなければ呼び出し元が失敗として扱う
        return Ran(None, "", f"起動できない: {type(exc).__name__}")
    return Ran(done.returncode, done.stdout, "")


def list_plugins(
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[list[dict[str, Any]]]:
    """`cwd`（省けばこのプロセスの場所）での `claude plugin list --json` の要素の並び。起動できない・失敗・時間切れ・形の違いは `None`。

    一覧はプロジェクトの設定で変わるため、これから起動する `claude` と同じ `cwd` で読む。`env` を省けばこのプロセスの環境で打つ。
    """
    try:
        argv = [CLAUDE, "plugin", "list", "--json"]
        child_env = None if env is None else dict(env)
        done = run(argv, capture_output=True, text=True, timeout=PLUGIN_LIST_TIMEOUT, check=False, cwd=cwd, env=child_env)
    except (OSError, subprocess.SubprocessError):  # noqa: NDF401 外部コマンド: 読めなければ外さずに起動する（#3617 の決定 8）
        return None
    if done.returncode != 0:
        return None
    try:
        listed = json.loads(done.stdout)
    except ValueError:  # noqa: NDF401 外部コマンド: JSON でなければ読めなかったとして扱う
        return None
    if not isinstance(listed, list) or not all(isinstance(e, dict) and isinstance(e.get("id"), str) for e in listed):
        return None
    return listed


def disabled_plugins(listed: Sequence[Mapping[str, Any]], own: Optional[str]) -> dict[str, bool]:
    """外す登録（`{<id>: False}`）。有効で、`@` の前が旧い NDF の名前で、自分の名前と違う登録だけ（I10）。"""
    out: dict[str, bool] = {}
    for entry in listed:
        plugin_id = entry.get("id")
        if not isinstance(plugin_id, str) or entry.get("enabled") is not True:
            continue
        name = plugin_id.split("@", 1)[0]
        if name == identity.FINAL_NAME and name != own:
            out[plugin_id] = False
    return out


def is_ndf(entry: Mapping[str, Any]) -> bool:
    """有効な `ndf@<マーケットプレイス>` の導入の行か（`scope` と `installPath` が文字列）。"""
    plugin_id = entry.get("id")
    return (
        entry.get("enabled") is True
        and isinstance(plugin_id, str)
        and plugin_id.startswith(f"{identity.FINAL_NAME}@")
        and isinstance(entry.get("scope"), str)
        and isinstance(entry.get("installPath"), str)
    )


def marketplace_of(plugin_id: str) -> str:
    """登録の ID（`<名前>@<マーケットプレイス>`）のマーケットプレイス名（最初の `@` の後ろ）。"""
    return plugin_id.split("@", 1)[1]


def same_cache_rows(
    listed: Sequence[Mapping[str, Any]], root: Path, version: Optional[str], environ: Mapping[str, str]
) -> list[Mapping[str, Any]]:
    """有効な NDF の行のうち、根 `root` と同じキャッシュ・同じマーケットプレイスのバージョンのディレクトリを指す行（一覧の順）。

    根が起動時のバージョン `version` の名前のディレクトリでなければ空（`--plugin-dir` の開発のチェックアウトを外す。#3909）。
    キャッシュの根は一覧を読んだ環境 `environ` の `CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）の `plugins/cache`。
    ファイルを読まないので、根が消えた後も起動時の所属で選べる。
    """
    mine = root.resolve()
    if version is None or mine.name != version:
        return []
    cache = (identity.claude_config_root(environ) / "plugins" / "cache").resolve()
    hits = []
    for entry in listed:
        if not is_ndf(entry):
            continue
        install_parent = Path(entry["installPath"]).resolve().parent
        market = marketplace_of(entry["id"])
        if install_parent == mine.parent and install_parent.name == identity.FINAL_NAME and install_parent.parent == cache / market:
            hits.append(entry)
    return hits


def plugin_args(
    root: Path, listed: Optional[Callable[[], Optional[list[dict[str, Any]]]]] = None, cwd: Optional[Path] = None
) -> PluginArgs:
    """入っている旧い NDF を外し、`root` のプラグインを読む `claude` の引数。

    外す登録は導入済みの一覧（`listed`。省けば起動先の `cwd` での `list_plugins`）から決め、`root` のプラグインの名前（`identity.name_of`）と同じ名前の
    登録は外さない（入れ替えの後に自分を外さない）。一覧を読めなければ外さずに起動し、`notice` に理由を入れる（I10a）。
    外す登録が無ければ `--settings` を付けない。自分の名前が旧い NDF の名前（`identity.FINAL_NAME`）と同じなら
    外す登録は必ず無いため、一覧を読まない（`claude plugin list --json` の起動を省く）。
    """
    load = ["--plugin-dir", str(root)]
    own = identity.name_of(root)
    if own == identity.FINAL_NAME:
        return PluginArgs(load, None)
    found = listed() if listed is not None else list_plugins(cwd=cwd)
    if found is None:
        return PluginArgs(load, LIST_UNREADABLE)
    disabled = disabled_plugins(found, own)
    if not disabled:
        return PluginArgs(load, None)
    return PluginArgs(["--settings", json.dumps({"enabledPlugins": disabled}, separators=(",", ":")), *load], None)


def minimal_args(system: str, stream: bool = False) -> list[str]:
    """最小構成（設定・MCP・スラッシュコマンドを読まず、会話を残さない）の `claude -p` の引数。`stream` なら逐次の出力。"""
    head = [*(PRINT_STREAM if stream else PRINT_JSON), "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
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


def _full_args(call: ClaudeCall, root: Path, cwd: Optional[Path]) -> PluginArgs:
    plugins = plugin_args(root, cwd=cwd)
    args = [*(PRINT_STREAM if call.stream else PRINT_JSON), *permission_args(Permission.FULL)]
    args += plugins.args + ["--append-system-prompt", call.system]
    return PluginArgs(args + (["--resume", call.resume] if call.resume else []), plugins.notice)


def claude_launch(call: ClaudeCall, root: Optional[Path] = None, base: Sequence[str] = (CLAUDE,), cwd: Optional[Path] = None) -> PluginArgs:
    """起動の引数と、full の起動で導入済みの一覧を読めなかった知らせ（I10a。読めたか full でなければ `None`）。

    `root` は full で読むプラグインの根（省けば自分の根）。`base` は起動のコマンド。`cwd` は起動する場所で、
    full の起動の導入済みの一覧をそこで読む（省けばこのプロセスの場所）。知らせは呼び手が結果の warnings へ載せる。
    """
    notice: Optional[str] = None
    if call.permission is Permission.FULL:
        args, notice = _full_args(call, root if root is not None else identity.load().root, cwd)
    else:
        args = minimal_args(call.system, call.stream) + permission_args(call.permission, call.tools)
    if call.json_schema is not None:
        args += ["--json-schema", json.dumps(call.json_schema, ensure_ascii=False)]
    if call.model:
        args += ["--model", call.model]
    return PluginArgs([*base, *args], notice)


def claude_args(call: ClaudeCall, root: Optional[Path] = None, base: Sequence[str] = (CLAUDE,), cwd: Optional[Path] = None) -> list[str]:
    """起動の引数だけ（`claude_launch` の `args`）。知らせを結果に残す full の起動は `claude_launch` を使う。"""
    return claude_launch(call, root, base, cwd).args


class ClaudeReply(NamedTuple):
    """`claude -p --output-format json` の 1 回の回答。`answer` は縛った回答（`structured_output`）か本文の JSON。

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
        """`ok` が偽で回答を持たない回答。"""
        return cls(False, None, raw, cost, error, failure)


def _limit_hit(payload: dict, text: str) -> bool:
    """claude の結果の JSON が利用上限（`api_error_status` 429 か、上限の行）を告げているか。"""
    return payload.get("api_error_status") == 429 or usage_limit(text) is not None


def _parse_reply(stdout: str) -> ClaudeReply:
    payload = _payload(stdout)
    if isinstance(payload, ClaudeReply):
        return payload
    spent = payload.get("total_cost_usd")
    cost = float(spent) if isinstance(spent, (int, float)) else 0.0
    text = payload.get("result") if isinstance(payload.get("result"), str) else stdout
    if payload.get("is_error"):
        return _error_reply(payload, text, cost)
    return _answer_reply(payload, text, cost)


def _payload(stdout: str) -> dict | ClaudeReply:
    """claude の出力の JSON のオブジェクト。JSON のオブジェクトでなければ、その失敗の回答。"""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return ClaudeReply.failed(stdout, f"claude の出力が JSON でない: {exc}")
    if not isinstance(payload, dict):
        return ClaudeReply.failed(stdout, "claude の出力が JSON のオブジェクトでない")
    return payload


def _error_reply(payload: dict, text: str, cost: float) -> ClaudeReply:
    """誤りで終えた回答。利用上限なら打ち直しても直らない失敗（`Failure.USAGE_LIMIT`）にする。"""
    if _limit_hit(payload, text):
        return ClaudeReply.failed(text, f"claude の利用上限: {text[:200]}", cost, Failure.USAGE_LIMIT)
    return ClaudeReply.failed(text, f"claude が誤りで終えた: {text[:200]}", cost)


def _answer_reply(payload: dict, text: str, cost: float) -> ClaudeReply:
    """誤りで終えなかった回答。縛った回答（`structured_output`）があればそれ、無ければ本文の JSON。"""
    if "structured_output" in payload:
        return ClaudeReply(True, payload["structured_output"], text, cost)
    try:
        return ClaudeReply(True, json.loads(text), text, cost)
    except json.JSONDecodeError:
        # 回答の本文が JSON でなければ、形に合わない回答として返す
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
    """`claude` を起動し、質問を標準入力で渡して回答を読む。起動の失敗・期限切れは `ok` が偽の回答にする。

    学習に使わない設定と確かめられていなければ起動せず `Failure.REFUSED`、重い起動のスロットが空かなければ
    `Failure.NO_SLOT` を返す。専用のプロセスの組で起動し、期限を超えたら組ごと止めて回収する
    （claude が動かした Tool の子孫を残さない）。
    """
    try:
        with launch_slot.gate("claude", cwd=cwd, env=child_process_env()) as slot_env:
            return _run_claude(argv, prompt, timeout, cwd, slot_env)
    except launch_slot.NotLaunched as exc:
        return ClaudeReply.failed("", exc.detail, failure=Failure(exc.kind))


def _run_claude(argv: Sequence[str], prompt: str, timeout: float, cwd: Optional[Path], slot_env: dict[str, str]) -> ClaudeReply:
    pipe = subprocess.PIPE
    try:
        proc = subprocess.Popen(
            list(argv), stdin=pipe, stdout=pipe, stderr=pipe, text=True, cwd=cwd, env=child_process_env(slot_env), start_new_session=True
        )
    except OSError as exc:
        return ClaudeReply.failed("", f"claude を起動できない: {exc}", failure=Failure.LAUNCH)
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process_group.stop_group(proc)
        _close_streams(proc)
        return ClaudeReply.failed("", f"claude が期限を超えた: {exc}", failure=Failure.TIMEOUT)
    if proc.returncode != 0 and not stdout.strip():
        limit = Failure.USAGE_LIMIT if usage_limit(stderr) else None
        return ClaudeReply.failed(stderr, f"claude が終了コード {proc.returncode} で終えた: {stderr[:200]}", failure=limit)
    return _parse_reply(stdout)
