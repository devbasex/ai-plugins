"""外部 CLI（claude・codex・kiro ほか）の起動と監視（#1142 の AD3。N10）。

- 監視: 利用上限の語は CLI が出す行の形（行頭）だけを読み、コメントの写しの中の語を拾わない（#1411）。
  早期の警告は種類ごとに 1 回ずつ出す（#2059）
- kiro: 起動の前後でworktree の `.amazonq/cli-agents` を写して戻し、起動の間に増えた `.bak` を消す（#2448）
- 起動の前: 学習に使わない設定のスナップショットを読み（`training.refusal`。#3391）、重い起動のスロットを取る（`launch_slot.held`。#3368）。2 つは `launch_slot.gate` の 1 か所が持つ
- 起動の直前: CLI のバージョンを下限と比べる（`cli_floor.require`。#3691）
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Optional

from ndf import identity, process_group
from ndf.adapters import cli_floor, launch_slot
from ndf.adapters.run_log import child_process_env, diag, record, record_cost_file
from ndf.contracts import facts, limits

# ---------------------------------------------------------------- サンドボックス（#3893）

# サンドボックス（bubblewrap）が名前空間を作れずにコマンドを打てなかったときに標準エラーの行頭に出る文字列
SANDBOX_FAILED = "bwrap:"
# その行。行頭に限り、相手役が標準エラーへ写した差分・文書の中の語を拾わない（PR 3944 の差分の文書で誤って読めなかったとした）
_SANDBOX_LINE = re.compile(r"^" + re.escape(SANDBOX_FAILED), re.MULTILINE)


@functools.lru_cache(maxsize=1)
def user_namespaces() -> bool:
    """このプロセスがユーザー名前空間を作れるか（`unshare -Ur true` が通るか）。codex のサンドボックスが要る。

    Linux 以外（サンドボックスに名前空間を使わない）と `unshare` の無い環境は作れるとみなす。作れないのに作れると
    みなしても、相手役の標準エラーの `SANDBOX_FAILED` で読めなかったと分かる。"""
    if not sys.platform.startswith("linux") or shutil.which("unshare") is None:
        return True
    try:
        done = subprocess.run(["unshare", "-Ur", "true"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):  # noqa: NDF401 外部コマンド: unshare を打てない環境は名前空間を作れないとみなす
        return False
    return done.returncode == 0


def sandbox_failed(paths: "AgentPaths") -> Optional[str]:
    """起動の標準エラーか、`codex exec --json` のコマンドの出力にサンドボックスの失敗（`SANDBOX_FAILED`）があればその行。無ければ None。

    失敗は最初のコマンドで出て、その後の回答のログに埋もれるので、末尾でなく全体を読む。`--json` の起動は失敗を標準エラーに
    出さず、失敗で終わったコマンドの出力が `bwrap:` で始まる。"""
    err = _whole_text(paths.err)
    found = _SANDBOX_LINE.search(err)
    if found:
        return _line_at(err, found.start())
    for item in (e.get("item") for e in codex_events(paths.out) if e.get("type") == "item.completed"):
        if not isinstance(item, dict) or item.get("type") != "command_execution" or item.get("exit_code") == 0:
            continue
        output = str(item.get("aggregated_output"))
        if output.startswith(SANDBOX_FAILED):
            return _line_at(output, 0)
    return None


# ---------------------------------------------------------------- codex exec --json の出力（#2944）

# `--json` の出力の最初のイベント（ほかの CLI の本文と、`--json` でない codex の回答を見分ける）
_CODEX_STARTED = "thread.started"
# API がモデルか effort の指定を理由に断った `turn.failed` の文（2026-11-27 に codex-cli 0.172.0 の実際の出力で確かめた）
_CODEX_REFUSAL = re.compile(r"model is not supported|reasoning\.effort")


def codex_answer_path(out: Path) -> Path:
    """`codex exec -o` で回答を書かせるファイル（出力の名前に `.answer` を足す）。"""
    return out.with_name(out.name + ".answer")


def _whole_text(path: Path) -> str:
    """ファイル全体を文字で読む。無ければ空。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:  # noqa: NDF401 ファイル: 書く前に落ちた起動は中身を持たない
        return ""


def _codex_event(line: str) -> Optional[dict[str, Any]]:
    if not line.startswith("{"):
        return None
    try:
        found = json.loads(line)
    except ValueError:  # noqa: NDF401 外部コマンド: 書きかけの行はイベントとして数えない
        return None
    return found if isinstance(found, dict) else None


def codex_events(out: Path) -> list[dict[str, Any]]:
    """`codex exec --json` の出力のイベント。最初の行が `thread.started` でなければ（`--json` の出力でなければ）空。"""
    lines = _whole_text(out).split("\n")
    first = _codex_event(lines[0])
    if first is None or first.get("type") != _CODEX_STARTED:
        return []
    return [e for e in map(_codex_event, lines) if e is not None]


class CodexUsage(NamedTuple):
    """`turn.completed` の行の `usage` のうち費用に要る欄。`cached_input_tokens` と `cache_write_input_tokens` は `input_tokens` の内数、
    推論のトークン（`reasoning_output_tokens`）は `output_tokens` の内数なので読まない。"""

    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int


def codex_usage(out: Path) -> Optional[CodexUsage]:
    """`turn.completed` の行の `usage` の合計。行が無ければ（失敗した起動）None。"""
    found = [e["usage"] for e in codex_events(out) if e.get("type") == "turn.completed" and isinstance(e.get("usage"), Mapping)]
    if not found:
        return None
    return CodexUsage(*(sum(u[k] for u in found if isinstance(u.get(k), int)) for k in CodexUsage._fields))


def _failure_message(event: Mapping[str, Any]) -> str:
    """`error` か `turn.failed` のイベントの文。ほかは空。"""
    if event.get("type") == "error":
        return str(event.get("message"))
    error = event.get("error")
    return str(error.get("message")) if event.get("type") == "turn.failed" and isinstance(error, Mapping) else ""


def codex_refused(out: Path) -> bool:
    """API がモデルか effort の指定を理由に、実行を始める前に断ったか（回答も使用量も無く、`turn.failed` の文が当たる）。"""
    events = codex_events(out)
    items = [e.get("item") for e in events]
    ran = any(e.get("type") == "turn.completed" for e in events)
    ran = ran or any(isinstance(i, Mapping) and i.get("type") == "agent_message" for i in items)
    return not ran and any(e.get("type") == "turn.failed" and _CODEX_REFUSAL.search(_failure_message(e)) for e in events)


# ---------------------------------------------------------------- 監視（#1411・#2059）

# claude が標準出力へ出す結果の JSON の利用上限（`api_error_status: 429`）
CLAUDE_USAGE_LIMIT_JSON = re.compile(r'^\{.*?"api_error_status"\s*:\s*429', re.MULTILINE)
# 利用上限の行。CLI が出す行の形（行頭。codex の `ERROR:` の前置きだけを許す）に限り、コメントの写し
# （`path:line [user] 本文`・`[PR-COMMENT] [user] 本文`）や差分・文書の中の語を拾わない
USAGE_LIMIT_LINES = (
    re.compile(r"^(?:ERROR:\s*)?(?:quota exceeded|rate limit exceeded)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:ERROR:\s*)?You['’]ve hit your (?:[\w'’ -]+ )?(?:limit|budget)\b", re.MULTILINE),  # 「5-hour limit」のハイフンも通す
    re.compile(r"^(?:ERROR:\s*)?exceeded retry limit, last status: 429\b", re.MULTILINE),
    re.compile(r"^(?:ERROR:\s*)?Monthly request limit reached", re.MULTILINE),
    CLAUDE_USAGE_LIMIT_JSON,
    re.compile(r"^HTTP/\d\S* 429 ", re.MULTILINE),
)
# 早期の警告（致命ではない）の行
EARLY_WARN = re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE)
# 監視が読むファイルの末尾のバイト数
TAIL_BYTES = 200 * 1024


@dataclass(frozen=True)
class AgentPaths:
    """1 回の起動のファイル。`out` は標準出力、`err` は標準エラー、`launch` は起動の記録（JSON）。

    `progress` は起動した担当が作業の途中で追記する進捗のファイル。完了まで標準出力へ何も出さない担当
    （claude の `--output-format json`）の無進捗も、このファイルの増え方で測る。担当へは質問の中でこのパスを渡す。
    """

    out: Path
    err: Path
    launch: Path
    progress: Optional[Path] = None

    @classmethod
    def at(cls, stem: Path) -> "AgentPaths":
        def named(suffix: str) -> Path:
            return stem.with_name(stem.name + suffix)

        return cls(named(".out"), named(".err.log"), named("-launch.json"), named("-progress.log"))

    def watched(self) -> tuple[Path, ...]:
        """無進捗の判定で大きさを足すファイル。"""
        return (self.out, self.err) + ((self.progress,) if self.progress is not None else ())


def tail(path: Path, size: int = TAIL_BYTES) -> str:
    """ファイルの末尾を文字で読む。無ければ空。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, path.stat().st_size - size))
            return fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:  # noqa: NDF401 ファイル: 起動の前はまだ無い
        return ""


def _line_at(text: str, pos: int) -> str:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start : end if end >= 0 else len(text)].strip()


def usage_limit(text: str) -> Optional[str]:
    """利用上限の行があればその行を返す。"""
    for pat in USAGE_LIMIT_LINES:
        m = pat.search(text)
        if m:
            return _line_at(text, m.start())
    return None


def _denials_warning(stdout: str) -> Optional[str]:
    """claude が成功で終わり拒否が残ったときの警告の文（拒否は致命にしない）。

    結果の JSON は最後の行にある（`--output-format json` は 1 行だけ、`stream-json` は 1 歩ごとの行の後に結果の行。#3945）。
    行の区切りは LF だけ。`splitlines()` は本文の U+2028・U+2029 でも切り、結果の JSON を途中で割る。"""
    lines = stdout.strip().split("\n")
    try:
        payload = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 終わる前の出力は JSON でない
        return None
    if not isinstance(payload, dict) or payload.get("subtype") != "success" or payload.get("is_error"):
        return None
    denials = payload.get("permission_denials")
    if not isinstance(denials, list) or not denials:
        return None
    return f"permission_denials {len(denials)} 件（subtype: success で終わったため致命にしない）"


def early_warnings(paths: AgentPaths) -> dict[str, str]:
    """早期の警告を種類（stderr・denials）ごとに返す。警告の無い種類はキーを持たない。"""
    found: dict[str, str] = {}
    err = tail(paths.err)
    m = EARLY_WARN.search(err)
    if m:
        found["stderr"] = _line_at(err, m.start())
    denials = _denials_warning(tail(paths.out))
    if denials:
        found["denials"] = denials
    return found


@dataclass
class Warned:
    """出した警告の種類。同じ種類はもう出さない。"""

    kinds: set[str] = field(default_factory=set)

    def new(self, warnings: dict[str, str]) -> list[tuple[str, str]]:
        fresh = [(k, v) for k, v in sorted(warnings.items()) if k not in self.kinds]
        self.kinds.update(k for k, _ in fresh)
        return fresh


@dataclass(frozen=True)
class Launch:
    """外部 CLI の 1 回の起動。`timeout` は監視の上限の秒。`env` は足す環境変数（値が None の名前は子の環境から外す）。`verify` は起動の直前に打つ照らし直しで、
    理由の 1 行を返せば起動しない（安全装置を外す起動の承認の読み直し。#1959）。"""

    agent: str
    argv: tuple[str, ...]
    cwd: Path
    paths: AgentPaths
    timeout: float
    env: Optional[Mapping[str, Optional[str]]] = None
    verify: Optional[Callable[[], Optional[str]]] = None


def adding_env(spec: Launch, changes: Mapping[str, Optional[str]]) -> Launch:
    """`spec` の子の環境へ `changes` を足した起動（値が None の名前は外す）。"""
    env: dict[str, Optional[str]] = {}
    if spec.env is not None:
        env.update(spec.env)
    env.update(changes)
    return replace(spec, env=env)


class AgentState(StrEnum):
    """監視の結果の終わり方。値は文字列のまま比べ・書き出せる。"""

    DONE = "done"
    FAILED = "failed"
    USAGE_LIMIT = "usage_limit"
    STALLED = "stalled"
    TIMEOUT = "timeout"
    # 起動しなかった: 学習に使わない設定と確かめられていない（`ndf training check` で直る）か、CLI が下限より古いか、起動の直前の照らし直しが理由を返した
    REFUSED = "refused"
    # 起動しなかった: 重い起動のスロットが待ちの上限までに空かなかった
    NO_SLOT = "no_slot"


class AgentStatus(NamedTuple):
    """監視の結果。`state` は `AgentState`、`restore` は kiro のバックアップの結果（`RestoreState`。kiro 以外は None）。
    `public` は `detail` が手元のパスを含むときの公開用の文面（無ければ `detail` をそのまま公開してよい。PR 4461 の r1-1）。
    `seconds` は起動した子の起動から終了までの秒（子を起動しなかったら None。#2944）。"""

    agent: str
    state: str
    exit_code: Optional[int]
    detail: str
    warnings: tuple[str, ...]
    restore: Optional[str] = None
    public: Optional[str] = None
    seconds: Optional[float] = None

    @property
    def shown(self) -> str:
        """PR のコメントなど公開される場所へ載せる文面。"""
        return self.detail if self.public is None else self.public


def stall_seconds(agent: str) -> float:
    """担当の無進捗の許容（表に無い担当は既定）。"""
    try:
        return limits.seconds(f"monitor.stall.{agent}")
    except limits.LimitsError:
        # 表に無い担当は既定の許容で見る
        return limits.seconds("monitor.stall-default")


@dataclass
class _Watch:
    spec: Launch
    started: float
    last_change: float
    size: int = -1
    warned: Warned = field(default_factory=Warned)

    def progressed(self, now: float) -> None:
        size = sum(p.stat().st_size for p in self.spec.paths.watched() if p.exists())
        if size != self.size:
            self.size, self.last_change = size, now


def _report_warnings(w: _Watch) -> None:
    for kind, text in w.warned.new(early_warnings(w.spec.paths)):
        diag(f"[{w.spec.agent}] 早期の警告（{kind}）: {text}", kind=kind, agent=w.spec.agent)


def _out_usage_limit(spec: Launch) -> Optional[str]:
    """claude の標準出力の結果の JSON か、`codex exec --json` の誤りのイベントに利用上限があれば返す（他の CLI の標準出力は読まない）。"""
    if spec.agent == "codex":
        return next((hit for e in codex_events(spec.paths.out) if (hit := usage_limit(_failure_message(e)))), None)
    if spec.agent != "claude" or not CLAUDE_USAGE_LIMIT_JSON.search(tail(spec.paths.out)):
        return None
    return "標準出力の結果の JSON に api_error_status 429"


def _verdict(w: _Watch, code: Optional[int], now: float) -> Optional[tuple[str, str]]:
    """終わり方を決める。まだ続くなら None。利用上限は終了コードより先に見る。"""
    hit = usage_limit(tail(w.spec.paths.err)) or _out_usage_limit(w.spec)
    if hit:
        return AgentState.USAGE_LIMIT, hit
    if code is not None:
        return (AgentState.DONE if code == 0 else AgentState.FAILED), f"終了コード {code}"
    w.progressed(now)
    if now - w.last_change > stall_seconds(w.spec.agent):
        return AgentState.STALLED, f"{now - w.last_change:.0f} 秒出力が増えない"
    if now - w.started > w.spec.timeout:
        return AgentState.TIMEOUT, f"監視の上限 {w.spec.timeout:.0f} 秒を超えた"
    return None


def watch(proc: subprocess.Popen, spec: Launch, poll: Optional[float] = None) -> AgentStatus:
    """起動したプロセスを終わるまで見る。利用上限・無進捗・上限の秒で止め、早期の警告を種類ごとに 1 回出す。"""
    interval = poll if poll is not None else limits.value("monitor-types.poll", "interval")
    now = time.monotonic()
    w = _Watch(spec, started=now, last_change=now)
    while True:
        code = proc.poll()
        _report_warnings(w)
        verdict = _verdict(w, code, time.monotonic())
        if verdict is not None:
            break
        time.sleep(interval)
    # CLI が自力で終えても、組に残った子孫（SIGTERM を無視する Tool・常駐の子）は後続の工程と並ばないよう止める
    if code is None or process_group.alive(proc, proc.pid):
        process_group.stop_group(proc)
    state, detail = verdict
    return AgentStatus(spec.agent, state, proc.poll(), detail, tuple(sorted(w.warned.kinds)))


def ai_child_env() -> dict[str, str]:
    """NDF が非対話で起動した AI の子のマーカー（`NDF_AI_CHILD=1`）。子の環境に足す（#1959）。"""
    return {identity.env_name("ai-child"): "1"}


def executable(runtime: str) -> str:
    """ランタイムの CLI の実行ファイルの名前（表 runtimes）。"""
    return facts.value("runtimes", runtime, "executable")


def _start(spec: Launch) -> subprocess.Popen:
    """相手役と修正担当の子を起動する。子の環境には NDF が起動した AI のマーカー（`NDF_AI_CHILD`）を足す（#1959）。"""
    child = ai_child_env()
    env = child_process_env(child if spec.env is None else {**spec.env, **child})
    spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
    codex_answer_path(spec.paths.out).unlink(missing_ok=True)  # codex は失敗すると `-o` の前の中身を残す
    with open(spec.paths.out, "wb") as out, open(spec.paths.err, "wb") as err:
        return subprocess.Popen(
            list(spec.argv), cwd=spec.cwd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=env, start_new_session=True
        )


def _start_and_watch(spec: Launch, poll: Optional[float]) -> AgentStatus:
    """起動して見る。起動できない（実行ファイルが無い・実行権が無い）ときは `AgentState.FAILED` を返す。"""
    started = time.monotonic()
    try:
        proc = _start(spec)
    except OSError as exc:
        # 文面はログ・実行ファイルの絶対パスを含み得るので、公開用はファイル名と理由だけにする（PR 4461 の r2-1）
        return AgentStatus(spec.agent, AgentState.FAILED, None, f"起動できない: {exc}", (), public=f"起動できない: {_os_error_public(exc)}")
    status = watch(proc, spec, poll)
    return status._replace(seconds=round(time.monotonic() - started, 3))


def _os_error_public(exc: OSError) -> str:
    """`OSError` の、パスをファイル名だけに縮めた文面。"""
    why = exc.strerror or type(exc).__name__
    return why if exc.filename is None else f"{why}: {Path(os.fsdecode(exc.filename)).name}"


# 照らし直しで起動を止めたときの公開用の文面（理由は設定のパスと承認のコマンドを含むので、PR などへはこちらを載せる。#1959）
VERIFY_REFUSED_PUBLIC = "起動の直前に読み直した起動の設定が、承認を確かめた後に変わったか取り消されていたので起動しなかった。launch-config で承認してから打ち直す"


def _not_started(spec: Launch, state: AgentState, detail: str, public: Optional[str] = None) -> AgentStatus:
    record("agent", agent=spec.agent, state=state, exit=None, warnings=[])
    return AgentStatus(spec.agent, state, None, detail, (), public=public)


def run_agent(spec: Launch, poll: Optional[float] = None) -> AgentStatus:
    """外部 CLI を起動して見る。kiro はworktree の `.amazonq/cli-agents` を起動の前後で守る。

    学習に使わない設定と確かめられていなければ起動せず `AgentState.REFUSED`、重い起動のスロットが空かなければ
    `AgentState.NO_SLOT` を返す。2 つを通った後、CLI が下限より古いか、起動の直前の照らし直し（`Launch.verify`）が理由を返せば起動せず `AgentState.REFUSED`。
    """
    try:
        with launch_slot.gate(spec.agent, cwd=spec.cwd, env=child_process_env(spec.env)) as slot_env:
            return _guarded_run(replace(spec, env=dict(slot_env) if spec.env is None else {**spec.env, **slot_env}), poll)
    except launch_slot.NotLaunched as exc:
        return _not_started(spec, AgentState(exc.kind), exc.detail)


def _guarded_run(spec: Launch, poll: Optional[float]) -> AgentStatus:
    try:
        cli_floor.require(spec.agent, spec.argv[0], child_process_env(spec.env), spec.cwd, args=spec.argv[1:])
    except cli_floor.TooOld as exc:
        return _not_started(spec, AgentState.REFUSED, str(exc), exc.public)
    # スロットと CLI の確かめで待った間の取り消しも見るため、子を起動する直前に照らし直す（#1959）
    why = spec.verify() if spec.verify is not None else None
    if why is not None:
        return _not_started(spec, AgentState.REFUSED, why, VERIFY_REFUSED_PUBLIC)
    guard = kiro_guard(spec.cwd, spec.paths) if spec.agent == "kiro" else contextlib.nullcontext({})
    with guard as kept:
        status = _start_and_watch(spec, poll)
    if spec.agent == "claude":
        record_cost_file(spec.paths.out)
    record("agent", agent=spec.agent, state=status.state, exit=status.exit_code, warnings=list(status.warnings))
    return status._replace(restore=kept.get("restore"))


# ---------------------------------------------------------------- kiro のworktree（#2448）

KIRO_AGENTS = Path(".amazonq") / "cli-agents"
BACKUP_SUFFIX = "-amazonq"


class RestoreState(StrEnum):
    """kiro のバックアップの結果。起動の記録（`paths.launch`）の `restore` にも同じ値を書く。"""

    RESTORED = "restored"
    SKIPPED = "restore_skipped"
    FAILED = "restore_failed"


def _in_worktree(workdir: Path) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
        )
    except OSError:  # noqa: NDF401 外部コマンド: git が無ければworktree として扱わない
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


@dataclass(frozen=True)
class _AgentsBackup:
    """1 回のバックアップ。`agents` は戻す先、`backup` はバックアップの保存先、`before_bak` は起動の前からある `.bak` の名前。"""

    agents: Path
    backup: Path
    before_bak: frozenset[str]


def _remove(path: Path) -> None:
    """リンクは辿らずにリンクだけを消し、ディレクトリは中身ごと消す。"""
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def _kind_changed(src: Path, dst: Path) -> bool:
    """バックアップ `src` と戻す先 `dst` で種類が違うか。リンクはバックアップの側でも戻す先でも作り直すので、違うとみなす
    （写しはリンクのある場所へ書けない）。"""
    return dst.is_symlink() or src.is_symlink() or dst.is_dir() != src.is_dir()


def _clear_replaced(backup: Path, agents: Path) -> None:
    """戻す先で、起動の間に種類が変わったもの（リンクへの置き換え・ファイルとディレクトリの入れ替え）を消す。

    消さずに写すと、写しがリンクを辿ってリンク先（worktree の外の利用者のファイル）をバックアップの中身で上書きする。
    """
    pairs = [(backup, agents)]
    while pairs:
        src, dst = pairs.pop()
        if not (dst.is_symlink() or dst.exists()):
            continue
        if _kind_changed(src, dst):
            _remove(dst)
            continue
        if src.is_dir():
            pairs.extend((child, dst / child.name) for child in src.iterdir())


def _restore(backed: _AgentsBackup) -> None:
    if backed.backup.is_dir():
        _clear_replaced(backed.backup, backed.agents)
        shutil.copytree(backed.backup, backed.agents, symlinks=True, dirs_exist_ok=True)
    if backed.agents.is_dir():
        for p in backed.agents.glob("*.bak"):
            if p.name not in backed.before_bak:
                p.unlink()


def _write_launch(paths: AgentPaths, kept: dict[str, Any]) -> None:
    paths.launch.parent.mkdir(parents=True, exist_ok=True)
    paths.launch.write_text(json.dumps(kept, ensure_ascii=False) + "\n", encoding="utf-8")


def _guard_restore(paths: AgentPaths, backed: _AgentsBackup, kept: dict[str, Any]) -> None:
    try:
        _restore(backed)
    except OSError as exc:
        kept.update(restore=RestoreState.FAILED, detail=str(exc), backup=str(backed.backup))
        with open(paths.err, "a", encoding="utf-8") as err:
            err.write(f"kiro が書き換えた {backed.agents} を戻せない（バックアップは {backed.backup}）: {exc}\n")
        return
    shutil.rmtree(backed.backup, ignore_errors=True)
    kept["restore"] = RestoreState.RESTORED


def _keep_unrestored(backup: Path) -> None:
    """前の起動で戻せずに残ったバックアップを、消さずに別名へ移す（唯一の元の設定のため）。"""
    if not backup.exists():
        return
    kept = backup.with_name(f"{backup.name}-unrestored-{time.time_ns()}")
    backup.rename(kept)
    diag(
        f"前の kiro の起動で戻せなかったバックアップを {kept} へ移して残す（起動の前の {KIRO_AGENTS} の中身。戻すのは人の手で）",
        agent="kiro",
    )


def _backup_agents(workdir: Path, paths: AgentPaths) -> _AgentsBackup:
    """worktree の `.amazonq/cli-agents` をバックアップする。戻せずに残った前のバックアップは別名で残す。"""
    agents = workdir / KIRO_AGENTS
    backup = paths.launch.with_name(paths.launch.stem + BACKUP_SUFFIX)
    _keep_unrestored(backup)
    before_bak = frozenset(p.name for p in agents.glob("*.bak")) if agents.is_dir() else frozenset()
    if agents.is_dir():
        shutil.copytree(agents, backup, symlinks=True)
    return _AgentsBackup(agents, backup, before_bak)


@contextlib.contextmanager
def kiro_guard(workdir: Path, paths: AgentPaths) -> Iterator[dict[str, Any]]:
    """kiro の起動の前にworktree の `.amazonq/cli-agents` をバックアップ、終わった後に戻して増えた `.bak` を消す。

    worktree でなければバックアップずに起動し、`RestoreState.SKIPPED` を残す。戻せなければバックアップを残し `RestoreState.FAILED` を残す。
    結果（`restore`）は起動の記録（`paths.launch`）にも書く。
    """
    kept: dict[str, Any] = {"agent": "kiro", "restore": None}
    backed = _backup_agents(workdir, paths) if _in_worktree(workdir) else None
    if backed is None:
        kept["restore"] = RestoreState.SKIPPED
    try:
        yield kept
    finally:
        if backed is not None:
            _guard_restore(paths, backed, kept)
        _write_launch(paths, kept)
