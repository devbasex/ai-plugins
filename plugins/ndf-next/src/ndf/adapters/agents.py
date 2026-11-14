"""外部 CLI（claude・codex・kiro ほか）の起動と監視（#1142 の AD3。N10）。

- 監視: 利用上限の語は CLI が出す行の形（行頭）だけを読み、コメントの写しの中の語を拾わない（#1411）。
  早期の警告は種類ごとに 1 回ずつ出す（#2059）
- kiro: 起動の前後でworktree の `.amazonq/cli-agents` を写して戻し、起動の間に増えた `.bak` を消す（#2448）
- 起動の前: 学習に使わない設定の控えを読み（`training.refusal`。#3391）、重い起動の枠を取る（`launch_slot.held`。#3368）。2 つは `launch_slot.gate` の 1 か所が持つ
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional

from ndf.adapters import launch_slot
from ndf.adapters.run_log import child_process_env, diag, record
from ndf.contracts import limits

# ---------------------------------------------------------------- 監視（#1411・#2059）

# claude が標準出力へ出す結果の JSON の利用上限（`api_error_status: 429`）
CLAUDE_USAGE_LIMIT_JSON = re.compile(r'^\{.*?"api_error_status"\s*:\s*429', re.MULTILINE)
# 利用上限の行。CLI が出す行の形（行頭。codex の `ERROR:` の前置きだけを許す）に限り、コメントの写し
# （`path:line [user] 本文`・`[PR-COMMENT] [user] 本文`）や差分・文書の中の語を拾わない
USAGE_LIMIT_LINES = (
    re.compile(r"^(?:ERROR:\s*)?(?:quota exceeded|rate limit exceeded)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:ERROR:\s*)?You['’]ve hit your (?:[\w'’ ]+ )?(?:limit|budget)\b", re.MULTILINE),
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
    （claude の `--output-format json`）の無進捗も、このファイルの増え方で測る。担当へは問いの中でこのパスを渡す。
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
    """claude が成功で終わり拒否が残ったときの警告の文（拒否は致命にしない）。"""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 終わる前の出力は JSON でない
        return None
    if not isinstance(payload, dict) or payload.get("subtype") != "success" or payload.get("is_error"):
        return None
    denials = payload.get("permission_denials")
    if not isinstance(denials, list) or not denials:
        return None
    return f"permission_denials {len(denials)} 件（subtype: success で終わったため致命にしない）"


def early_warnings(paths: AgentPaths) -> dict[str, str]:
    """早期の警告を種類（stderr・denials）ごとに返す。警告の無い種類は鍵を持たない。"""
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
    """外部 CLI の 1 回の起動。`timeout` は監視の上限の秒。`env` は足す環境変数。"""

    agent: str
    argv: tuple[str, ...]
    cwd: Path
    paths: AgentPaths
    timeout: float
    env: Optional[dict[str, str]] = None


class AgentState(StrEnum):
    """監視の結果の終わり方。値は文字列のまま比べ・書き出せる。"""

    DONE = "done"
    FAILED = "failed"
    USAGE_LIMIT = "usage_limit"
    STALLED = "stalled"
    TIMEOUT = "timeout"
    # 起動しなかった: 学習に使わない設定と確かめられていない（`ndf training check` で直る）
    REFUSED = "refused"
    # 起動しなかった: 重い起動の枠が待ちの上限までに空かなかった
    NO_SLOT = "no_slot"


class AgentStatus(NamedTuple):
    """監視の結果。`state` は `AgentState`、`restore` は kiro の控えの結果（`RestoreState`。kiro 以外は None）。"""

    agent: str
    state: str
    exit_code: Optional[int]
    detail: str
    warnings: tuple[str, ...]
    restore: Optional[str] = None


def stall_seconds(agent: str) -> float:
    """担当の無進捗の許容（表に無い担当は既定）。"""
    try:
        return limits.seconds(f"monitor.stall.{agent}")
    except limits.LimitsError:
        # 表に無い担当は既定の許容で見る
        return limits.seconds("monitor.stall-default")


def _group_alive(proc: subprocess.Popen) -> bool:
    """プロセスの組に生きた成員が残るか。直接の子は先に回収し、終わった子だけで残っているとみなさない。"""
    proc.poll()
    try:
        os.killpg(proc.pid, 0)
    except ProcessLookupError:  # noqa: NDF401 外部コマンド: 組の成員が残っていない
        return False
    return True


def stop(proc: subprocess.Popen) -> None:
    """プロセスの組を止める。直接の子が終わっても組に子孫が残れば、猶予の後に組へ SIGKILL を送る。"""
    grace = limits.seconds("procs.stop-tree")
    interval = limits.value("procs.stop-tree", "interval")
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        proc.poll()
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _group_alive(proc):
            return
        time.sleep(interval)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:  # noqa: S110 外部コマンド: 組が猶予の間に終わった
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:  # noqa: S110 外部コマンド: SIGKILL の後も回収できなければ待たずに戻る
        pass


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
    """claude の標準出力の結果の JSON に利用上限があれば、その旨の文を返す（他の CLI の標準出力は読まない）。"""
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
    if code is None or _group_alive(proc):
        stop(proc)
    state, detail = verdict
    return AgentStatus(spec.agent, state, proc.poll(), detail, tuple(sorted(w.warned.kinds)))


def _start(spec: Launch) -> subprocess.Popen:
    env = child_process_env(spec.env)
    spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
    with open(spec.paths.out, "wb") as out, open(spec.paths.err, "wb") as err:
        return subprocess.Popen(
            list(spec.argv), cwd=spec.cwd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=env, start_new_session=True
        )


def _start_and_watch(spec: Launch, poll: Optional[float]) -> AgentStatus:
    """起動して見る。起動できない（実行ファイルが無い・実行権が無い）ときは `AgentState.FAILED` を返す。"""
    try:
        proc = _start(spec)
    except OSError as exc:
        return AgentStatus(spec.agent, AgentState.FAILED, None, f"起動できない: {exc}", ())
    return watch(proc, spec, poll)


def _not_started(spec: Launch, state: AgentState, detail: str) -> AgentStatus:
    record("agent", agent=spec.agent, state=state, exit=None, warnings=[])
    return AgentStatus(spec.agent, state, None, detail, ())


def run_agent(spec: Launch, poll: Optional[float] = None) -> AgentStatus:
    """外部 CLI を起動して見る。kiro はworktree の `.amazonq/cli-agents` を起動の前後で守る。

    学習に使わない設定と確かめられていなければ起動せず `AgentState.REFUSED`、重い起動の枠が空かなければ
    `AgentState.NO_SLOT` を返す。
    """
    try:
        with launch_slot.gate(spec.agent, cwd=spec.cwd):
            return _guarded_run(spec, poll)
    except launch_slot.NotLaunched as exc:
        return _not_started(spec, AgentState(exc.kind), exc.detail)


def _guarded_run(spec: Launch, poll: Optional[float]) -> AgentStatus:
    guard = kiro_guard(spec.cwd, spec.paths) if spec.agent == "kiro" else contextlib.nullcontext({})
    with guard as kept:
        status = _start_and_watch(spec, poll)
    record("agent", agent=spec.agent, state=status.state, exit=status.exit_code, warnings=list(status.warnings))
    return status._replace(restore=kept.get("restore"))


# ---------------------------------------------------------------- kiro のworktree（#2448）

KIRO_AGENTS = Path(".amazonq") / "cli-agents"
BACKUP_SUFFIX = "-amazonq"


class RestoreState(StrEnum):
    """kiro の控えの結果。起動の記録（`paths.launch`）の `restore` にも同じ値を書く。"""

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
    """1 回の控え。`agents` は戻す先、`backup` は控えの置き場、`before_bak` は起動の前からある `.bak` の名前。"""

    agents: Path
    backup: Path
    before_bak: frozenset[str]


def _remove(path: Path) -> None:
    """リンクは辿らずにリンクだけを消し、ディレクトリは中身ごと消す。"""
    if path.is_symlink() or not path.is_dir():
        path.unlink()
    else:
        shutil.rmtree(path)


def _clear_replaced(backup: Path, agents: Path) -> None:
    """戻す先で、起動の間に種類が変わったもの（リンクへの置き換え・ファイルとディレクトリの入れ替え）を消す。

    消さずに写すと、写しがリンクを辿ってリンク先（worktree の外の利用者のファイル）を控えの中身で上書きする。
    """
    pairs = [(backup, agents)]
    while pairs:
        src, dst = pairs.pop()
        if not (dst.is_symlink() or dst.exists()):
            continue
        # リンクは控えの側でも戻す先でも作り直す（写しはリンクのある場所へ書けない）
        if dst.is_symlink() or src.is_symlink() or dst.is_dir() != src.is_dir():
            _remove(dst)
        elif src.is_dir():
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
            err.write(f"kiro が書き換えた {backed.agents} を戻せない（控えは {backed.backup}）: {exc}\n")
        return
    shutil.rmtree(backed.backup, ignore_errors=True)
    kept["restore"] = RestoreState.RESTORED


def _keep_unrestored(backup: Path) -> None:
    """前の起動で戻せずに残った控えを、消さずに別名へ移す（唯一の元の設定のため）。"""
    if not backup.exists():
        return
    kept = backup.with_name(f"{backup.name}-unrestored-{time.time_ns()}")
    backup.rename(kept)
    diag(f"前の kiro の起動で戻せなかった控えを {kept} へ移して残す（起動の前の {KIRO_AGENTS} の中身。戻すのは人の手で）", agent="kiro")


def _backup_agents(workdir: Path, paths: AgentPaths) -> _AgentsBackup:
    """worktree の `.amazonq/cli-agents` を控える。戻せずに残った前の控えは別名で残す。"""
    agents = workdir / KIRO_AGENTS
    backup = paths.launch.with_name(paths.launch.stem + BACKUP_SUFFIX)
    _keep_unrestored(backup)
    before_bak = frozenset(p.name for p in agents.glob("*.bak")) if agents.is_dir() else frozenset()
    if agents.is_dir():
        shutil.copytree(agents, backup, symlinks=True)
    return _AgentsBackup(agents, backup, before_bak)


@contextlib.contextmanager
def kiro_guard(workdir: Path, paths: AgentPaths) -> Iterator[dict[str, Any]]:
    """kiro の起動の前にworktree の `.amazonq/cli-agents` を控え、終わった後に戻して増えた `.bak` を消す。

    worktree でなければ控えずに起動し、`RestoreState.SKIPPED` を残す。戻せなければ控えを残し `RestoreState.FAILED` を残す。
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
