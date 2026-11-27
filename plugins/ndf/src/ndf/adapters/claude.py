"""`claude -p` の起動の引数と回答の読み取り（#1142 の AD3）。

起動の権限は `Permission` の 3 つだけで、権限ごとの引数は `permission_args` の 1 か所が持つ（#3364）。
判断の呼び出しは Tool を使わせず、Tool を許す起動も許可のモードは acceptEdits までで、許可を飛ばす
フラグ（`--dangerously-skip-permissions`・`bypassPermissions`）を使わない。

Skill を回す `full` の起動は、入っている旧い NDF を `--settings` の `enabledPlugins` で外し、`--plugin-dir` で
このプラグインを読む（`plugin_args`）。外す登録は登録名を決め打たず、`claude plugin list --json` の導入済みの一覧から
決める（#3617）。起動のスクリプトはサブコマンド `launch-args` で同じ値を読む。利用者の設定ファイルは書き換えない。

起動の前に、学習に使わない設定のスナップショット（`training.refusal`）を読み、重い起動のスロット（`launch_slot.held`）を取る（#3391・#3368。前置きは `launch_slot.gate` が持つ）。

中継の子から継いだ起動先（中継のマーカー `NDF_RELAY_DIR` のある環境の `NDF_CLAUDE_ACCOUNT`）があれば、起動の前に中継の確かめ（`begin`）を当て、上限に当たったら
替え先で 1 回だけ打ち直す（`retry`。#3604 #3864 の U12）。確かめの本体は `ndf.domains.relay.plan_claude` にあり、下の層の
このモジュールは読めないので、一番上のエントリポイントが `set_relay_checks` で差し込む。差し込まれていなければ確かめを当てない。
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional, Protocol, Sequence, Union

from ndf import identity, process_group
from ndf.adapters import cli_floor, launch_slot, transcript
from ndf.adapters.agents import usage_limit
from ndf.adapters.run_log import child_process_env, record_cost
from ndf.contracts import facts, limits

CLAUDE = facts.value("runtimes", "claude", "executable")
# 導入済みのプラグインの一覧を読む時間の上限の方針（contracts/limits/）
PLUGIN_LIST = "claude-plugins.list"
# 一覧を読めなかったときの知らせ（導入先のパスを含む CLI の出力は載せない）
LIST_UNREADABLE = "導入済みのプラグインの一覧を読めず、旧い NDF を外さずに起動する"
# full の起動で許す Tool
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch,WebFetch,WebSearch"
# full の起動で読み込む組み込みの Tool。許す 13 個に、工程の中で使う 3 個を足す（#1959 の決定 5）。起動の設定の `load` を後ろに足す
LOAD_TOOLS = FULL_TOOLS + ",TaskStop,NotebookEdit,ListAgents"
# MCP を起動の設定で指定したものだけ読む引数（指定の無い工程は環境の MCP を読まない。#1959 の決定 1）
STRICT_MCP = "--strict-mcp-config"
# 起動の頭（質問を 1 回渡し、回答を JSON で受ける）
PRINT_JSON = ["-p", "--output-format", "json"]
# 逐次の起動の頭（1 歩ごとに 1 行の JSON を出す。stream-json は --verbose が無いと起動できない。#3945）
PRINT_STREAM = ["-p", "--output-format", "stream-json", "--verbose"]
# Tool を許す起動の許可のモード
ACCEPT_EDITS = ["--permission-mode", "acceptEdits"]
# 読める場所を限った起動の許可のモード（事前に許していない操作を尋ねずに断る。#3955）と、作業ディレクトリへ閉じ込める引数
DONT_ASK = ["--permission-mode", "dontAsk"]
RESTRICTED = "--restricted"
# 続けようとした会話が無いときに claude が出す文言（2026-11-21 に claude 2.1.330 で確かめた。#4013）
NO_CONVERSATION = "No conversation found with session ID"
# 自動の要約が起きたことを示す出力の行（`type: system` の `subtype`）
COMPACT_BOUNDARY = transcript.COMPACT_SUBTYPE
# 結果の行の 1 回の呼び出しで文脈に入ったトークンの項目
CONTEXT_FIELDS = transcript.USAGE_KEYS
# 主モデルを選ぶときに足すトークンの項目（`modelUsage` のモデルごとの値）
MODEL_TOKEN_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
# API キーの子（`key:<名前>` と API キーの設定の従量の接続）にキーを渡せる起動の形（`launch_form` の値）。別冊
# 中継の従量の接続と API キーの確定仕様の「最初の払い出しまで送らない」: 標準入力の終わりを待たずに NDF の helper を呼ぶ形だけを置く。
# U11a の実機の確かめ（PR #4086。Claude Code 2.1.330）で json・stream・min-json・min-stream の 4 形はどれも、標準入力を読み終えるか
# 3 秒待ち切るまで helper を呼ばなかったので、置く形は無い（キーの子の claude -p は `key_gate` で起動しない）
KEY_FORMS: frozenset[str] = frozenset()


class Permission(StrEnum):
    """`claude -p` の起動の権限。

    - `judge`: 判断の呼び出し。最小構成で Tool を使わせない
    - `tools`: 最小構成で、渡した Tool だけを許して編集を受ける（acceptEdits）
    - `full`: Skill を回す起動。設定・プラグイン・hook をそのまま読み、`LOAD_TOOLS` の組み込みの Tool だけを読み込み、`FULL_TOOLS` を
      許して編集を受ける。MCP は起動の設定が指定した工程だけが読む（#1959）
    """

    JUDGE = "judge"
    TOOLS = "tools"
    FULL = "full"


# `claude -p` の 1 回が回答を返せなかった理由のうち、打ち直しても直らないもの（#3394）。語彙・意味・呼び手ごとの停止理由は表が持つ
# （members の名前は id の大文字）
FAILURES = "claude-failures"
Failure = StrEnum("Failure", [(i.upper(), i) for i in facts.ids(FAILURES)])


class LaunchGrant(Protocol):
    """full の起動 1 回の指定（`launch_settings.Grant` が満たす）。`load` は読み込みに足す Tool、`allow` は尋ねずに使ってよい指定、
    `inherit` は環境の MCP をすべて読むか、`mcp_files` は読む MCP の定義（確かめた中身のコピー）。"""

    @property
    def load(self) -> tuple[str, ...]: ...

    @property
    def allow(self) -> tuple[str, ...]: ...

    @property
    def inherit(self) -> bool: ...

    @property
    def mcp_files(self) -> tuple[Path, ...]: ...


class Session(NamedTuple):
    """1 回の起動が使う会話の ID と、その会話を続けるか（偽なら新しく始める。#4013）。"""

    id: str
    resume: bool


@dataclass(frozen=True)
class ClaudeCall:
    """`claude -p` の 1 回の起動の中身。

    `permission` は起動の権限（`Permission`）。`tools` は `tools` の権限で許す Tool で、ほかの権限では渡さない。
    `session` は会話の指定（None なら最小構成は会話を残さない。#4013）。`json_schema` は回答を縛るスキーマ。`stream` は標準出力を 1 歩ごとの
    行にする（結果をファイルで受け、標準出力を読まない担当だけが使う。無進捗の監視が作業中の担当を止まりと誤らない。#3945）。
    `readable` は作業ディレクトリのほかに読める場所で、`tools` の権限でだけ渡す。渡すと読める場所を限った起動になる（#3955）。
    `grant` は起動の設定から解いた指定（`LaunchGrant`）で、`full` の権限でだけ渡す。None なら指定なしと同じ（#1959）。
    """

    system: str
    permission: Permission = Permission.JUDGE
    tools: Optional[str] = None
    session: Optional[Session] = None
    json_schema: Optional[dict] = None
    model: Optional[str] = None
    stream: bool = False
    readable: tuple[Path, ...] = ()
    grant: Optional[LaunchGrant] = None

    def __post_init__(self) -> None:
        if (self.permission is Permission.TOOLS) != bool(self.tools):
            raise ValueError(f"tools は権限 {Permission.TOOLS} のときだけ、空でなく渡す（権限 {self.permission}）")
        if self.readable and self.permission is not Permission.TOOLS:
            raise ValueError(f"readable は権限 {Permission.TOOLS} のときだけ渡す（権限 {self.permission}）")
        if self.grant is not None and self.permission is not Permission.FULL:
            raise ValueError(f"grant は権限 {Permission.FULL} のときだけ渡す（権限 {self.permission}）")


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

    起動できない・時間切れ・下限より古い claude は例外を上げずに `Ran(None, "", <理由>)` を返す。理由に導入先のパスを含めない（下限の理由は claude の実行ファイルのパスだけを持つ）。
    """
    argv = [CLAUDE, "plugin", *args]
    try:
        cli_floor.require("claude", CLAUDE, env, cwd)
    except cli_floor.TooOld as exc:
        return Ran(None, "", str(exc))
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
    argv = [CLAUDE, "plugin", "list", "--json"]
    child_env = None if env is None else dict(env)
    try:
        cli_floor.require("claude", CLAUDE, child_env, cwd)
        done = run(argv, capture_output=True, text=True, timeout=limits.seconds(PLUGIN_LIST), check=False, cwd=cwd, env=child_env)
    except (OSError, subprocess.SubprocessError, cli_floor.TooOld):  # noqa: NDF401 外部コマンド: 読めなければ外さずに起動する（#3617 の決定 8。古い claude は起動の側の比べが止める）
        return None
    return _parse_listed(done.stdout) if done.returncode == 0 else None


def _parse_listed(stdout: str) -> Optional[list[dict[str, Any]]]:
    """`claude plugin list --json` の標準出力の要素の並び。JSON でない・`id` が文字列の辞書の並びでないものは `None`。"""
    try:
        listed = json.loads(stdout)
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


def session_args(session: Optional[Session]) -> list[str]:
    """会話の指定の引数。None は空、続けるなら `--resume <ID>`、新しく始めるなら `--session-id <ID>`（#4013）。"""
    if session is None:
        return []
    return ["--resume" if session.resume else "--session-id", session.id]


def minimal_args(system: str, stream: bool = False, session: Optional[Session] = None) -> list[str]:
    """最小構成（設定・MCP・スラッシュコマンドを読まない）の `claude -p` の引数。`stream` なら逐次の出力。

    `session` が None なら会話を残さない（`--no-session-persistence`）。指定があれば付けずに会話の引数を足す（#4013）。
    """
    keep = [] if session is not None else ["--no-session-persistence"]
    head = [*(PRINT_STREAM if stream else PRINT_JSON), *keep, "--setting-sources", "", "--strict-mcp-config"]
    return head + ["--disable-slash-commands", "--system-prompt", system, *session_args(session)]


def permission_args(
    permission: Permission, tools: Optional[str] = None, readable: Sequence[Path] = (), grant: Optional[LaunchGrant] = None
) -> list[str]:
    """権限ごとの、使える Tool と許可のモードの引数。権限の引数はここだけが持つ。

    `readable` が空でなければ、Tool をその場所と作業ディレクトリへ閉じ込め（`--restricted`・`--add-dir`）、許していない操作を
    断る（`dontAsk`）。値を 1 つだけ取る `--permission-mode` を最後に置き、後ろのリクエストを `--add-dir` の値と読ませない（#3955）。
    `full` の引数は `_full_permission`（`grant` は full でだけ使う）。"""
    if permission is Permission.FULL:
        return _full_permission(grant)
    if permission is Permission.TOOLS:
        if not tools:
            raise ValueError(f"権限 {Permission.TOOLS} は許す Tool を要る")
        if readable:
            places = [a for p in readable for a in ("--add-dir", str(p))]
            return ["--tools", tools, "--allowed-tools", tools, RESTRICTED, *places, *DONT_ASK]
        return ["--tools", tools, "--allowed-tools", tools, *ACCEPT_EDITS]
    return ["--tools", ""]


def _full_permission(grant: Optional[LaunchGrant]) -> list[str]:
    """full の起動の Tool と MCP の引数（#1959）。

    読み込む Tool（`--tools`）は `LOAD_TOOLS` に `grant.load` を足し、尋ねずに使ってよい指定（`--allowed-tools`）は `FULL_TOOLS` に
    `grant.allow` を足す。`load` の名前は許可に入れない。許可の指定は空白を含みうるので 1 件を 1 つの引数にする（値を複数取る引数）。
    `inherit` でなければ `--strict-mcp-config` で環境の MCP を読まず、定義のコピーがあれば `--mcp-config` で渡す。後ろには必ず
    `--` で始まる引数（プラグインの引数か `--append-system-prompt`）が続き、値を複数取る引数の終わりになる。
    """
    load = (*LOAD_TOOLS.split(","), *(grant.load if grant is not None else ()))
    allow = (*FULL_TOOLS.split(","), *(grant.allow if grant is not None else ()))
    args = [*ACCEPT_EDITS, "--tools", ",".join(dict.fromkeys(load)), "--allowed-tools", *dict.fromkeys(allow)]
    if grant is not None and grant.inherit:
        return args
    files = [str(p) for p in grant.mcp_files] if grant is not None else []
    return [*args, STRICT_MCP, *(["--mcp-config", *files] if files else [])]


def _print_head(stream: bool) -> list[str]:
    """`-p` の出力の形の引数。`stream` なら逐次（stream-json）、でなければ 1 回の JSON。"""
    return list(PRINT_STREAM if stream else PRINT_JSON)


def _full_args(call: ClaudeCall, root: Path, cwd: Optional[Path]) -> PluginArgs:
    plugins = plugin_args(root, cwd=cwd)
    args = [*_print_head(call.stream), *permission_args(Permission.FULL, grant=call.grant)]
    args += plugins.args + ["--append-system-prompt", call.system]
    return PluginArgs(args + session_args(call.session), plugins.notice)


def claude_launch(call: ClaudeCall, root: Optional[Path] = None, base: Sequence[str] = (CLAUDE,), cwd: Optional[Path] = None) -> PluginArgs:
    """起動の引数と、full の起動で導入済みの一覧を読めなかった知らせ（I10a。読めたか full でなければ `None`）。

    `root` は full で読むプラグインの根（省けば自分の根）。`base` は起動のコマンド。`cwd` は起動する場所で、
    full の起動の導入済みの一覧をそこで読む（省けばこのプロセスの場所）。知らせは呼び手が結果の warnings へ載せる。
    """
    notice: Optional[str] = None
    if call.permission is Permission.FULL:
        args, notice = _full_args(call, root if root is not None else identity.load().root, cwd)
    else:
        args = minimal_args(call.system, call.stream, call.session) + permission_args(call.permission, call.tools, call.readable)
    if call.json_schema is not None:
        args += ["--json-schema", json.dumps(call.json_schema, ensure_ascii=False)]
    if call.model:
        args += ["--model", call.model]
    return PluginArgs([*base, *args], notice)


def claude_args(call: ClaudeCall, root: Optional[Path] = None, base: Sequence[str] = (CLAUDE,), cwd: Optional[Path] = None) -> list[str]:
    """起動の引数だけ（`claude_launch` の `args`）。知らせを結果に残す full の起動は `claude_launch` を使う。"""
    return claude_launch(call, root, base, cwd).args


def launch_form(argv: Sequence[str]) -> Optional[str]:
    """`call_claude` の起動の形（`json`・`stream`、最小構成なら `min-json`・`min-stream`）。どれでもなければ None。"""
    words = list(argv[1:])
    head = "stream" if words[: len(PRINT_STREAM)] == PRINT_STREAM else "json" if words[: len(PRINT_JSON)] == PRINT_JSON else None
    if head is None:
        return None
    minimal = any(w == "--setting-sources" and words[i + 1 : i + 2] == [""] for i, w in enumerate(words))
    return f"min-{head}" if minimal else head


def _nothing(_launched: bool) -> None:
    return None


class AccountRun(NamedTuple):
    """中継の確かめに通った起動（`ndf.domains.relay.plan_claude` が作る）。`env`・`argv` は最終の環境と起動引数。

    `gate` があれば関所（`gate(argv, 開ける読み口, 知らせの書き口)` の起動引数）で起動し、開ける前に `attach(子の pid)` を打つ
    （理由の符号が返れば子を止めて起動しない）。`end(起動したか)` は終わった後（失敗・例外・期限切れでも）に 1 回だけ打つ。
    `recheck` があればスロットを取った後の起動の直前に打ち、1 行が返れば起動せずに `Failure.REFUSED` で返す（認証の上書きと従量の実効値の確かめ直し）。
    """

    env: dict[str, str]
    argv: list[str]
    gate: Optional[Callable[[Sequence[str], int, int], list[str]]] = None
    attach: Optional[Callable[[int], Optional[str]]] = None
    end: Callable[[bool], None] = _nothing
    recheck: Optional[Callable[[], Optional[str]]] = None


class AccountRefused(NamedTuple):
    """中継の確かめで起動しない理由（`failure` と、値を含まない 1 行）。"""

    failure: Failure
    detail: str


class RelayChecks(Protocol):
    """中継の起動先を継いだ `claude -p` の確かめ（`ndf.domains.relay.plan_claude` が満たす）。`keys` は起動の形がキーを渡せるか。"""

    def begin(self, argv: list[str], env: Mapping[str, str], cwd: Path, keys: bool) -> Union[AccountRun, AccountRefused]: ...

    def retry(self, argv: list[str], env: Mapping[str, str], cwd: Path, keys: bool) -> Optional[AccountRun]: ...


# 差し込まれた中継の確かめ。None なら確かめを当てない（エントリポイントを通らない呼び出しとテスト）
relay_checks: Optional[RelayChecks] = None


def set_relay_checks(checks: Optional[RelayChecks]) -> None:
    """中継の確かめを差し込む（一番上のエントリポイントが起動時に 1 回呼ぶ）。"""
    global relay_checks
    relay_checks = checks


class LaunchRecorder(Protocol):
    """起動した子 1 つの使用量の行を書く処理（`ndf.domains.records.usage` が満たす。#1959 の決定 6）。"""

    def record_cli(self, usage: Any, meta: Mapping[str, Any]) -> Any: ...


# 差し込まれた使用量ログの書き込み処理。None なら書かない（エントリポイントを通らない呼び出しとテスト）
launch_recorder: Optional[LaunchRecorder] = None


def set_launch_recorder(recorder: Optional[LaunchRecorder]) -> None:
    """使用量ログの書き込み処理を差し込む（一番上のエントリポイントが起動時に 1 回呼ぶ）。"""
    global launch_recorder
    launch_recorder = recorder


class ClaudeReply(NamedTuple):
    """`claude -p --output-format json` の 1 回の回答。`answer` は縛った回答（`structured_output`）か本文の JSON。

    `failure` は打ち直しても直らない失敗の理由（`Failure`）。None の失敗（誤りで終えた・出力が JSON でない）は
    打ち直せば直りうる。

    使用量ログの行の元（#1959 の決定 6）: `session_id`・`usage`・`model_usage`（結果の `modelUsage`）・`session_cost_usd`
    （結果の `total_cost_usd`）は結果の JSON の値で、読めなければ None。`usage` は起動 1 回分、`model_usage` と
    `session_cost_usd` は会話の累計である（2026-11-27 に Claude Code 2.1.332 で同じ会話を 2 回再開して測った）。
    `launched` は子プロセスを起動できたか（スロット・下限・照らし直しで止まった起動と、起動に失敗した起動は偽）、
    `launched_at`・`seconds` は起動した時刻（UTC）とかかった秒。`earlier` は、アカウントを替えて打ち直す前に起動した子の回答である。
    """

    ok: bool
    answer: Any
    raw: str
    cost: float
    error: Optional[str] = None
    failure: Optional[Failure] = None
    session_id: Optional[str] = None
    usage: Optional[Mapping[str, Any]] = None
    model_usage: Optional[Mapping[str, Any]] = None
    session_cost_usd: Optional[float] = None
    launched: bool = False
    launched_at: Optional[str] = None
    seconds: Optional[float] = None
    earlier: tuple["ClaudeReply", ...] = ()

    @classmethod
    def failed(cls, raw: str, error: str, cost: float = 0.0, failure: Optional[Failure] = None) -> "ClaudeReply":
        """`ok` が偽で回答を持たない回答。"""
        return cls(False, None, raw, cost, error, failure)


def result_line(text: str) -> Optional[dict[str, Any]]:
    """出力の最後の行が `type: result` の JSON のオブジェクトならそれ、ほかは None（#4013）。

    行は LF だけで割る（`splitlines()` は本文の U+2028・U+2029 でも割る）。末尾の空白を落としてから最後の行を読む。
    """
    last = text.rstrip().rsplit("\n", 1)[-1]
    try:
        found = json.loads(last)
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 途中で止まった修正担当の出力・空の出力は結果の行を持たない
        return None
    return found if isinstance(found, dict) and found.get("type") == "result" else None


def _int(value: Any) -> Optional[int]:
    """真偽値でない整数ならその値、ほかは None。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def context_tokens(result: Optional[Mapping[str, Any]]) -> Optional[int]:
    """結果の行の `usage.iterations` の最後の要素の、文脈に入ったトークンの和。読めなければ None（#4013）。"""
    usage = result.get("usage") if isinstance(result, Mapping) else None
    steps = usage.get("iterations") if isinstance(usage, Mapping) else None
    if not isinstance(steps, list) or not steps or not isinstance(steps[-1], Mapping):
        return None
    found = [_int(steps[-1].get(k)) for k in CONTEXT_FIELDS]
    values = [v for v in found if v is not None]
    return sum(values) if len(values) == len(found) else None


def _count(value: Any) -> int:
    """整数ならその値、ほか（無い・整数でない）は 0 と数える。"""
    found = _int(value)
    return 0 if found is None else found


def _model_tokens(row: Mapping[str, Any]) -> int:
    """`modelUsage` の 1 モデルのトークンの和（整数でない値は 0 と数える）。"""
    return sum(_count(row.get(k)) for k in MODEL_TOKEN_FIELDS)


def main_model(models: Mapping[str, Any]) -> Optional[tuple[str, Mapping[str, Any]]]:
    """`modelUsage` の主モデル（トークンの和が最も大きく、同じなら窓の小さい方）の名前と値。無いか空なら None（#1959・#4013）。"""
    rows = ((name, row) for name, row in models.items() if isinstance(row, Mapping))
    return max(rows, key=lambda nr: (_model_tokens(nr[1]), -_count(nr[1].get("contextWindow"))), default=None)


def context_window(result: Optional[Mapping[str, Any]]) -> Optional[int]:
    """結果の行の主モデル（`main_model`）の `contextWindow`。読めなければ None（#4013）。"""
    models = result.get("modelUsage") if isinstance(result, Mapping) else None
    found = main_model(models) if isinstance(models, Mapping) else None
    if found is None:
        return None
    window = _int(found[1].get("contextWindow"))
    return window if window is not None and window > 0 else None


def compacted(path: Path) -> bool:
    """修正担当の出力のファイルの全体に、自動の要約の行（`type: system`・`subtype: compact_boundary`）があるか（#4013）。

    末尾だけを読むと途中の行を見落とすので 1 行ずつ全体を読む。ファイルが無ければ偽、JSON でない行は飛ばす。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return any(_is_compact(line) for line in fh)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 起動の前に止まった修正担当は出力を持たない
        return False


def _is_compact(line: str) -> bool:
    if COMPACT_BOUNDARY not in line:  # 要約の行でない大半の行（読んだファイルの中身など）は JSON として解析しない（PR 4186 の r1-2）
        return False
    try:
        row = json.loads(line)
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 1 歩の行でない出力（書きかけの行など）は自動の要約の行でない
        return False
    return isinstance(row, dict) and row.get("type") == "system" and row.get("subtype") == COMPACT_BOUNDARY


def conversation_missing(out_text: str, err_text: str) -> bool:
    """続けようとした会話が無いと claude が告げたか（結果の行の `errors` の文字列か標準エラーに `NO_CONVERSATION`。#4013）。"""
    result = result_line(out_text)
    errors = result.get("errors") if result is not None else None
    said = [e for e in errors if isinstance(e, str)] if isinstance(errors, list) else []
    return NO_CONVERSATION in err_text or any(NO_CONVERSATION in e for e in said)


def _limit_hit(payload: dict, text: str) -> bool:
    """claude の結果の JSON が利用上限（`api_error_status` 429 か、上限の行）を告げているか。"""
    return payload.get("api_error_status") == 429 or usage_limit(text) is not None


def _parse_reply(stdout: str) -> ClaudeReply:
    payload = _payload(stdout)
    if isinstance(payload, ClaudeReply):
        return payload
    spent = payload.get("total_cost_usd")
    cost = float(spent) if isinstance(spent, (int, float)) and not isinstance(spent, bool) else 0.0
    text = payload.get("result") if isinstance(payload.get("result"), str) else stdout
    reply = _error_reply(payload, text, cost) if payload.get("is_error") else _answer_reply(payload, text, cost)
    return _with_usage(reply, payload)


def _with_usage(reply: ClaudeReply, payload: Mapping[str, Any]) -> ClaudeReply:
    """回答に、使用量ログの行の元になる結果の値を持たせる。形の違う値は None にする（#1959）。"""
    sid, usage, models, spent = (payload.get(k) for k in ("session_id", "usage", "modelUsage", "total_cost_usd"))
    return reply._replace(
        session_id=sid if isinstance(sid, str) and sid else None,
        usage=usage if isinstance(usage, Mapping) else None,
        model_usage=models if isinstance(models, Mapping) else None,
        session_cost_usd=float(spent) if isinstance(spent, (int, float)) and not isinstance(spent, bool) else None,
    )


def launch_meta(name: str, repo: Optional[str]) -> dict[str, Any]:
    """使用量ログの行の工程の欄。`name` は工程の名前（作業種別か Skill の名前）、`repo` は `<所有者>/<リポジトリ>`（読めなければ None）。

    プランの実行の子なら、プランの実行の id・ステップの id・何回目かを環境変数から足す（#1959 の「子へ渡す環境変数」）。
    無い値と、整数でない回数は欄を省く。
    """
    meta: dict[str, Any] = {"name": name}
    if repo:
        meta["repo"] = repo
    for field_name, env in (("run_id", identity.env_name("plan-run-id")), ("step", identity.env_name("step-id"))):
        value = os.environ.get(env)
        if value:
            meta[field_name] = value
    raw = os.environ.get(identity.env_name("step-attempt"), "")
    if raw.isdigit() and int(raw) > 0:
        meta["attempt"] = int(raw)
    return meta


def report_launch(reply: ClaudeReply, meta: Mapping[str, Any], seq: int) -> int:
    """起動した子ごとに使用量ログの行を 1 つ書き込み処理へ渡し、起動した子の数を返す（#1959 の決定 6）。

    子は `reply.earlier` の順に続けて `reply`。起動しなかった子（`launched` が偽）は行を渡さない。`seq` は最初の子の
    `session_seq` で、子ごとに 1 進める。`meta` の `session_id` が無ければ結果の会話の id を書く。書き込み処理が
    差し込まれていなければ渡さない。行に質問と回答の本文を入れない。
    """
    children = [child for child in (*reply.earlier, reply) if child.launched]
    for offset, child in enumerate(children):
        if launch_recorder is not None:
            launch_recorder.record_cli(child.usage, _launch_row(child, meta, seq + offset))
    return len(children)


def _launch_row(child: ClaudeReply, meta: Mapping[str, Any], seq: int) -> dict[str, Any]:
    """子 1 つの行の欄（使用量を除く）。値の無い欄は None のまま渡し、書き込み処理が省く。"""
    models = child.model_usage if child.model_usage is not None else {}
    main = main_model(models)
    tokens = {n: {k: _count(r.get(k)) for k in MODEL_TOKEN_FIELDS} for n, r in models.items() if isinstance(r, Mapping)}
    row = {
        **meta,
        "launch_id": uuid.uuid4().hex,
        "session_id": meta["session_id"] if "session_id" in meta else child.session_id,
        "session_seq": seq,
        "session_cost_usd": child.session_cost_usd,
        "model": main[0] if main is not None else None,
        "launched_at": child.launched_at,
        "seconds": child.seconds,
    }
    if tokens:  # モデルの無い結果は欄を置かない
        row["model_usage"] = tokens
    return row


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


@dataclass(frozen=True)
class LaunchOptions:
    """起動の前置きの選び方（#4000 の決定 3・決定 8）。省けば今までどおりの振る舞い。

    - `slot_wait`: 重い起動のスロットを待つ上限の秒。None なら設定の上限（`launch.wait.llm-cli`）、0 なら待たずに `Failure.NO_SLOT`
    - `new_group`: claude を専用のプロセスの組で起動するか。偽なら呼び手の組に入り、呼び手の 1 回の組への送信で claude も止まる
    - `cli_floor`: CLI の下限（`claude --version`）を確かめるか。偽なら確かめずに起動する（今動いている claude を打つ子など、下限を満たすと分かっている起動）
    - `ensure_training`: 学習に使わない設定の記録が無いか期限切れのとき、その場で確かめて記録するか（`training.ensure`）。
      偽なら確かめ直さずに `Failure.REFUSED`（アカウントの設定の API を呼ばず、設定も変えない）
    - `switch_account`: 中継の起動先を継いだ起動が利用上限に当たったとき、替え先のアカウントで 1 回だけ打ち直すか。
      偽なら打ち直さずに `Failure.USAGE_LIMIT` を返す（1 回の判定で 1 回だけ起動すると決めた呼び手）
    - `verify`: 子を起動する直前（スロット・アカウントの確かめ直しの後、CLI の下限の前）に打つ照らし直し。理由の 1 行を返せば起動せず `Failure.LAUNCH`。
      承認を確かめた起動の設定が、確かめた後に変わったか取り消されたかを見る（`launch_settings.Grant.verify`。#1959）。
      子プロセスを起動しない確かめなので、`claude --version` を打つ CLI の下限より前に打つ（止まる起動に `--version` を打たない）
    - `ready`: 起動の前の確かめをすべて通った後、子を起動する直前に 1 回打つ準備（worker が前の結果ファイルを消すなど）。
      どの確かめで止まった起動でも打たない
    """

    slot_wait: Optional[float] = None
    new_group: bool = True
    cli_floor: bool = True
    ensure_training: bool = True
    switch_account: bool = True
    verify: Optional[Callable[[], Optional[str]]] = None
    ready: Optional[Callable[[], None]] = None


# 選び方を省いた起動（今までどおり）
DEFAULT_LAUNCH = LaunchOptions()


def call_claude(
    argv: Sequence[str], prompt: str, timeout: float, cwd: Optional[Path] = None, options: LaunchOptions = DEFAULT_LAUNCH
) -> ClaudeReply:
    """`claude` を起動し、質問を標準入力で渡して回答を読む。起動の失敗・期限切れは `ok` が偽の回答にする。

    学習に使わない設定と確かめられていなければ起動せず `Failure.REFUSED`、重い起動のスロットが空かなければ
    `Failure.NO_SLOT` を返す。2 つを通った後、claude が下限より古ければ起動せず `Failure.REFUSED`（`cli_floor`）。期限を超えたら組ごと止めて回収する
    （claude が動かした Tool の子孫を残さない）。中継の起動先を継いだ起動は、確かめ（`begin`・`retry`）で確定した起動ごとの
    環境と起動引数でこの前置きを打つ。NDF が起動した AI のマーカー（`NDF_AI_CHILD`）は前置き（`_prefaced`）が子の最終の環境へ足す
    （#1959。子は起動の承認を書けない）。中継の確かめは `child: drop` の指定でマーカーを除くため、確かめより前には足さない。

    `options`（`LaunchOptions`）は前置きの選び方で、スロットを待つ上限・プロセスの組・下限の確かめの有無を決める。
    """
    env = child_process_env()
    base = AccountRun(env, list(argv))
    if relay_checks is None or not _inherited(env):
        return _prefaced(base, prompt, timeout, cwd, options)
    return _accounted(relay_checks, base, prompt, timeout, cwd, options)


def _inherited(env: Mapping[str, str]) -> bool:
    """中継が確定して子へ渡した起動先を継いだ環境か。中継のマーカー（`NDF_RELAY_DIR`）の無い `NDF_CLAUDE_ACCOUNT` は利用者の指定として扱う（#4136）。"""
    return bool(env.get("NDF_CLAUDE_ACCOUNT")) and bool(env.get(identity.env_name("relay-dir")))


def _accounted(
    checks: RelayChecks, base: AccountRun, prompt: str, timeout: float, cwd: Optional[Path], options: LaunchOptions
) -> ClaudeReply:
    """起動先を継いだ起動。中継の確かめに通れば起動し、登録済みアカウントの上限なら替え先で 1 回だけ打ち直す（決定 9）。

    `base` は確かめへ渡す起動（このプロセスの環境と起動引数）。確かめ（キーを渡せない形の `key_gate` を含む）は前置きより
    先に打ち、前置きは確かめが確定した環境で打つ。
    """
    where = _workdir(cwd).absolute()  # 作業ディレクトリの基準を Popen(cwd=…) と同じにする
    keys = launch_form(base.argv) in KEY_FORMS
    got = checks.begin(base.argv, base.env, where, keys)
    if isinstance(got, AccountRefused):
        return ClaudeReply.failed("", got.detail, failure=got.failure)
    reply = _prefaced(got, prompt, timeout, cwd, options)
    if reply.failure is not Failure.USAGE_LIMIT or not options.switch_account:
        return reply
    again = checks.retry(base.argv, base.env, where, keys)
    if again is None:
        return reply
    retried = _prefaced(again, prompt, timeout, cwd, options)
    return retried._replace(earlier=(*reply.earlier, reply, *retried.earlier))  # 前の子の使用量の行を残す（#1959）


def require_claude(run: AccountRun, cwd: Optional[Path], check: bool) -> Optional[ClaudeReply]:
    """CLI の下限を確かめる。古ければ起動しない回答、下限を満たすか `check` が偽（確かめない）なら None。"""
    try:
        if check:
            cli_floor.require("claude", run.argv[0], run.env, cwd)
    except cli_floor.TooOld as exc:
        return ClaudeReply.failed("", str(exc), failure=Failure.REFUSED)
    return None


def _precheck(run: AccountRun, cwd: Optional[Path], options: LaunchOptions) -> Optional[ClaudeReply]:
    """起動の直前の確かめ。`run.recheck` が 1 行を返せば `Failure.REFUSED`、次に `options.verify` が 1 行を返せば
    `Failure.LAUNCH`、最後に CLI の下限（`require_claude`）。すべて通れば `options.ready` を打って None。

    照らし直しは子プロセスを起動しないので、`claude --version` を打つ下限より前に打つ（取り消しで止まる起動に `--version` を打たない）。
    起動の準備（`ready`）は確かめをすべて通った後に打つので、どの確かめで止まった起動でも準備をしない。
    """
    refused = run.recheck() if run.recheck is not None else None
    if refused is not None:
        return ClaudeReply.failed("", refused, failure=Failure.REFUSED)
    changed = options.verify() if options.verify is not None else None
    if changed is not None:
        return ClaudeReply.failed("", changed, failure=Failure.LAUNCH)
    too_old = require_claude(run, cwd, options.cli_floor)
    if too_old is not None:
        return too_old
    if options.ready is not None:
        options.ready()
    return None


def _prefaced(run: AccountRun, prompt: str, timeout: float, cwd: Optional[Path], options: LaunchOptions) -> ClaudeReply:
    """起動の前置き（学習の設定・スロット・下限）を `run` の環境と実行ファイルで打って起動し、どの終わり方でも `run.end` を 1 回だけ打つ。

    前置きの選び方は `options`（スロットを待つ上限・claude を置くプロセスの組・下限の確かめ）。関所の起動は今までどおり新しい組で起動する。
    子の最終の環境には NDF が起動した AI のマーカー（`NDF_AI_CHILD`）を足す。中継の確かめが確定した環境（初回と替え先の打ち直し）にも足すためである（#1959）。
    """
    launched = False
    try:
        with launch_slot.gate(
            "claude", cwd=cwd, env=_absolute_config(run.env, cwd), wait=options.slot_wait, ensure=options.ensure_training
        ) as slot_env:
            run = run._replace(env={**run.env, **slot_env, identity.env_name("ai-child"): "1"})
            stopped = _precheck(run, cwd, options)
            if stopped is not None:
                return stopped
            at, started = datetime.now(UTC).isoformat(timespec="seconds"), time.monotonic()
            if run.gate is None:
                launched = True
                return _timed(_run_claude(run.argv, prompt, timeout, cwd, run.env, options.new_group), at, started)
            proc = _gated(run, cwd)
            if isinstance(proc, ClaudeReply):
                return proc
            launched = True
            return _timed(_talk(proc, prompt, timeout), at, started)
    except launch_slot.NotLaunched as exc:
        return ClaudeReply.failed("", exc.detail, failure=Failure(exc.kind))
    finally:
        run.end(launched)


def _timed(reply: ClaudeReply, at: str, started: float) -> ClaudeReply:
    """起動した子の回答に、起動した時刻とかかった秒を持たせる。起動できなかった回答はそのまま返す。"""
    if not reply.launched:
        return reply
    return reply._replace(launched_at=at, seconds=round(time.monotonic() - started, 3))


def _absolute_config(env: dict[str, str], cwd: Optional[Path]) -> dict[str, str]:
    """学習の設定の確かめへ渡す環境。相対の `CLAUDE_CONFIG_DIR` を子と同じ作業ディレクトリから解く（子へ渡す値は変えない）。"""
    conf = env.get("CLAUDE_CONFIG_DIR")
    if not conf or Path(conf).is_absolute():
        return env
    return {**env, "CLAUDE_CONFIG_DIR": str(_workdir(cwd) / conf)}


def _workdir(cwd: Optional[Path]) -> Path:
    """子の作業ディレクトリ。`cwd` を省けばこのプロセスの場所（`Popen(cwd=None)` と同じ）。"""
    return cwd if cwd is not None else Path.cwd()


def _gated(run: AccountRun, cwd: Optional[Path]) -> "subprocess.Popen | ClaudeReply":
    """関所で起動し、`attach` に通ったら開けて claude になったかを確かめる。なれなければ組を止めて `Failure.LAUNCH`。

    子を作った後の失敗（`attach` の例外・関所の口の `OSError`）も組を止めて回収してから返す（届けは呼び出し元が回収の後に消す）。
    """
    opened: list[int] = []
    try:
        for _ in range(2):
            opened.extend(os.pipe())
    except OSError as exc:
        _close_fds(opened)
        return ClaudeReply.failed("", f"claude を起動できない: {exc}", failure=Failure.LAUNCH)
    r, w, sr, sw = opened
    proc: Optional[subprocess.Popen] = None
    try:
        try:
            proc = _popen(run.gate(run.argv, r, sw), cwd, run.env, (r, sw))  # type: ignore[misc]
        finally:
            os.close(r)
            os.close(sw)
        why = _open_gate(run, proc, w, sr)
    except OSError as exc:
        if proc is None:
            return ClaudeReply.failed("", f"claude を起動できない: {exc}", failure=Failure.LAUNCH)
        why = f"gate_error {type(exc).__name__}"
    except BaseException:
        if proc is not None:
            _stop(proc)
        raise
    finally:
        _close_fds((w, sr))
    if why is None:
        return proc
    _stop(proc)
    return ClaudeReply.failed("", f"claude を関所で起動できない: {why}", failure=Failure.LAUNCH)


def _close_fds(fds: Sequence[int]) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:  # noqa: S110 ファイル: 閉じ済みの関所の口は閉じ直さない
            pass


def _open_gate(run: AccountRun, proc: subprocess.Popen, w: int, sr: int) -> Optional[str]:
    """`attach` に通ったら関所を開け、起動の知らせを読む。claude になれば None、なれなければ理由の符号。"""
    why = run.attach(proc.pid) if run.attach is not None else None
    if why is not None:
        return why
    os.write(w, b"1")
    got = process_group.read_status(sr, limits.seconds("relay.gate-exec"))
    return "gate_timeout" if got is None else f"launch_failed errno {_errno_name(got)}" if got else None


def _errno_name(got: bytes) -> str:
    """関所が知らせた errno の数値を名前にする（`2` は `ENOENT`）。数値でなければ受けた文字列のまま。"""
    text = got.decode(errors="replace").strip()
    code = int(text) if text.isdigit() else None
    return errno.errorcode[code] if code in errno.errorcode else text


def _stop(proc: subprocess.Popen) -> None:
    process_group.stop_group(proc)
    _close_streams(proc)


def _popen(
    argv: Sequence[str], cwd: Optional[Path], env: dict[str, str], fds: tuple[int, ...] = (), new_group: bool = True
) -> subprocess.Popen:
    """子を起動する。`new_group` が偽なら新しいプロセスの組を作らず、呼び手の組のままにする（#4000 の決定 3）。"""
    pipe = subprocess.PIPE
    extra = {"pass_fds": fds} if fds else {}
    return subprocess.Popen(
        list(argv), stdin=pipe, stdout=pipe, stderr=pipe, text=True, cwd=cwd, env=env, start_new_session=new_group, **extra
    )


def _run_claude(
    argv: Sequence[str], prompt: str, timeout: float, cwd: Optional[Path], env: dict[str, str], new_group: bool = True
) -> ClaudeReply:
    try:
        proc = _popen(argv, cwd, env, new_group=new_group)
    except OSError as exc:
        return _launch_failed(exc)
    return _talk(proc, prompt, timeout)


def _launch_failed(exc: OSError) -> ClaudeReply:
    """子を起動できなかったときの回答（`Failure.LAUNCH`）。"""
    return ClaudeReply.failed("", f"claude を起動できない: {exc}", failure=Failure.LAUNCH)


def _talk(proc: subprocess.Popen, prompt: str, timeout: float) -> ClaudeReply:
    """起動した子に質問を渡して回答を読み、起動した子の回答（`launched` が真）にする。"""
    return _read_answer(proc, prompt, timeout)._replace(launched=True)


def _read_answer(proc: subprocess.Popen, prompt: str, timeout: float) -> ClaudeReply:
    """質問を標準入力で渡して回答を読む。期限を超えたら組ごと止めて回収する。"""
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop(proc)
        return ClaudeReply.failed("", f"claude が期限を超えた: {exc}", failure=Failure.TIMEOUT)
    except BaseException:  # 中断・入出力の例外でも組を止めて回収してから送り直す（届けは呼び出し元が回収の後に消す）
        _stop(proc)
        raise
    if proc.returncode != 0 and not stdout.strip():
        limit = Failure.USAGE_LIMIT if usage_limit(stderr) else None
        return ClaudeReply.failed(stderr, f"claude が終了コード {proc.returncode} で終えた: {stderr[:200]}", failure=limit)
    record_cost(stdout)
    return _parse_reply(stdout)
