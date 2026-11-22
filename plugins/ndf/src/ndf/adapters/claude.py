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
from ndf.adapters import cli_floor, launch_slot
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
# 続けようとした会話が無いときに claude が出す文言（2026-11-21 に claude 2.1.330 で確かめた。#4013）
NO_CONVERSATION = "No conversation found with session ID"
# 自動の要約が起きたことを示す出力の行（`type: system` の `subtype`）
COMPACT_BOUNDARY = "compact_boundary"
# 結果の行の 1 回の呼び出しで文脈に入ったトークンの項目
CONTEXT_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
# 主モデルを選ぶときに足すトークンの項目（`modelUsage` のモデルごとの値）
MODEL_TOKEN_FIELDS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")


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
    # 学習に使わない設定と確かめられていないか、claude が下限より古いので起動しなかった
    REFUSED = "refused"
    # 重い起動のスロットが待ちの上限までに空かなかった
    NO_SLOT = "no_slot"


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
    """

    system: str
    permission: Permission = Permission.JUDGE
    tools: Optional[str] = None
    session: Optional[Session] = None
    json_schema: Optional[dict] = None
    model: Optional[str] = None
    stream: bool = False

    def __post_init__(self) -> None:
        if (self.permission is Permission.TOOLS) != bool(self.tools):
            raise ValueError(f"tools は権限 {Permission.TOOLS} のときだけ、空でなく渡す（権限 {self.permission}）")


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
    try:
        argv = [CLAUDE, "plugin", "list", "--json"]
        child_env = None if env is None else dict(env)
        cli_floor.require("claude", CLAUDE, child_env, cwd)
        done = run(argv, capture_output=True, text=True, timeout=PLUGIN_LIST_TIMEOUT, check=False, cwd=cwd, env=child_env)
    except (OSError, subprocess.SubprocessError, cli_floor.TooOld):  # noqa: NDF401 外部コマンド: 読めなければ外さずに起動する（#3617 の決定 8。古い claude は起動の側の比べが止める）
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
        args = minimal_args(call.system, call.stream, call.session) + permission_args(call.permission, call.tools)
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
    values = [_int(steps[-1].get(k)) for k in CONTEXT_FIELDS]
    return None if None in values else sum(v for v in values if v is not None)


def _count(value: Any) -> int:
    """整数ならその値、ほか（無い・整数でない）は 0 と数える。"""
    found = _int(value)
    return 0 if found is None else found


def _model_tokens(row: Mapping[str, Any]) -> int:
    """`modelUsage` の 1 モデルのトークンの和（整数でない値は 0 と数える）。"""
    return sum(_count(row.get(k)) for k in MODEL_TOKEN_FIELDS)


def context_window(result: Optional[Mapping[str, Any]]) -> Optional[int]:
    """結果の行の主モデル（トークンの和が最も大きく、同じなら窓の小さい方）の `contextWindow`。読めなければ None（#4013）。"""
    models = result.get("modelUsage") if isinstance(result, Mapping) else None
    rows = [r for r in models.values() if isinstance(r, Mapping)] if isinstance(models, Mapping) else []
    if not rows:
        return None
    main = max(rows, key=lambda r: (_model_tokens(r), -_count(r.get("contextWindow"))))
    window = _int(main.get("contextWindow"))
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
    `Failure.NO_SLOT` を返す。2 つを通った後、claude が下限より古ければ起動せず `Failure.REFUSED`（`cli_floor`）。専用のプロセスの組で起動し、期限を超えたら組ごと止めて回収する
    （claude が動かした Tool の子孫を残さない）。
    """
    try:
        with launch_slot.gate("claude", cwd=cwd, env=child_process_env()) as slot_env:
            try:
                cli_floor.require("claude", argv[0], child_process_env(slot_env), cwd)
            except cli_floor.TooOld as exc:
                return ClaudeReply.failed("", str(exc), failure=Failure.REFUSED)
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
