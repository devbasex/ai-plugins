"""外部 CLI（claude・codex・kiro ほか）の起動と監視（#1142 の AD3。N10）。

- 監視: 利用上限の語は CLI が出す行の形（行頭）だけを読み、コメントの写しの中の語を拾わない（#1411）。
  早期の警告は種類ごとに 1 回ずつ出す（#2059）
- kiro: 起動の前後でworktree の `.amazonq/cli-agents` を写して戻し、起動の間に増えた `.bak` を消す（#2448）
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
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional

from ndf.adapters.run_log import current, diag, record
from ndf.contracts import limits

# ---------------------------------------------------------------- 監視（#1411・#2059）

# 利用上限の行。CLI が出す行の形（行頭。codex の `ERROR:` の前置きだけを許す）に限り、コメントの写し
# （`path:line [user] 本文`・`[PR-COMMENT] [user] 本文`）や差分・文書の中の語を拾わない
USAGE_LIMIT_LINES = (
    re.compile(r"^(?:ERROR:\s*)?(?:quota exceeded|rate limit exceeded)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^(?:ERROR:\s*)?You['’]ve hit your (?:[\w'’ ]+ )?(?:limit|budget)\b", re.MULTILINE),
    re.compile(r"^(?:ERROR:\s*)?exceeded retry limit, last status: 429\b", re.MULTILINE),
    re.compile(r"^(?:ERROR:\s*)?Monthly request limit reached", re.MULTILINE),
    re.compile(r'^\{.*?"api_error_status"\s*:\s*429', re.MULTILINE),
    re.compile(r"^HTTP/\d\S* 429 ", re.MULTILINE),
)
# 早期の警告（致命ではない）の行
EARLY_WARN = re.compile(r"^(?:Error|FATAL|fatal|panic|PANIC|Traceback)[: ]", re.MULTILINE)
# 監視が読むファイルの末尾のバイト数
TAIL_BYTES = 200 * 1024


@dataclass(frozen=True)
class AgentPaths:
    """1 回の起動のファイル。`out` は標準出力、`err` は標準エラー、`launch` は起動の記録（JSON）。"""

    out: Path
    err: Path
    launch: Path

    @classmethod
    def at(cls, stem: Path) -> "AgentPaths":
        return cls(stem.with_name(stem.name + ".out"), stem.with_name(stem.name + ".err.log"), stem.with_name(stem.name + "-launch.json"))


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


class AgentStatus(NamedTuple):
    """監視の結果。`state` は done・failed・usage_limit・stalled・timeout。"""

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


def stop(proc: subprocess.Popen) -> None:
    """プロセスの組を止める。猶予の後も残れば SIGKILL を送る。"""
    grace = limits.seconds("procs.stop-tree")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:  # noqa: NDF401 外部コマンド: 既に終わっている
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:  # noqa: S112 外部コマンド: 猶予を超えたら次の信号を送る
            continue


@dataclass
class _Watch:
    spec: Launch
    started: float
    last_change: float
    size: int = -1
    warned: Warned = field(default_factory=Warned)

    def progressed(self, now: float) -> None:
        size = sum(p.stat().st_size for p in (self.spec.paths.out, self.spec.paths.err) if p.exists())
        if size != self.size:
            self.size, self.last_change = size, now


def _report_warnings(w: _Watch) -> None:
    for kind, text in w.warned.new(early_warnings(w.spec.paths)):
        diag(f"[{w.spec.agent}] 早期の警告（{kind}）: {text}", kind=kind, agent=w.spec.agent)


def _verdict(w: _Watch, code: Optional[int], now: float) -> Optional[tuple[str, str]]:
    """終わり方を決める。まだ続くなら None。"""
    hit = usage_limit(tail(w.spec.paths.err))
    if hit:
        return "usage_limit", hit
    if code is not None:
        return ("done" if code == 0 else "failed"), f"終了コード {code}"
    w.progressed(now)
    if now - w.last_change > stall_seconds(w.spec.agent):
        return "stalled", f"{now - w.last_change:.0f} 秒出力が増えない"
    if now - w.started > w.spec.timeout:
        return "timeout", f"監視の上限 {w.spec.timeout:.0f} 秒を超えた"
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
    if code is None:
        stop(proc)
    state, detail = verdict
    return AgentStatus(spec.agent, state, proc.poll(), detail, tuple(sorted(w.warned.kinds)))


def _start(spec: Launch) -> subprocess.Popen:
    log = current()
    env = {**os.environ, **(spec.env if spec.env else {}), **(log.child_env() if log else {})}
    spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
    with open(spec.paths.out, "wb") as out, open(spec.paths.err, "wb") as err:
        return subprocess.Popen(
            list(spec.argv), cwd=spec.cwd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, env=env, start_new_session=True
        )


def run_agent(spec: Launch, poll: Optional[float] = None) -> AgentStatus:
    """外部 CLI を起動して見る。kiro はworktree の `.amazonq/cli-agents` を起動の前後で守る。"""
    guard = kiro_guard(spec.cwd, spec.paths) if spec.agent == "kiro" else contextlib.nullcontext({})
    with guard as kept:
        status = watch(_start(spec), spec, poll)
    record("agent", agent=spec.agent, state=status.state, exit=status.exit_code, warnings=list(status.warnings))
    return status._replace(restore=kept.get("restore"))


# ---------------------------------------------------------------- kiro のworktree（#2448）

KIRO_AGENTS = Path(".amazonq") / "cli-agents"
BACKUP_SUFFIX = "-amazonq"


def _in_worktree(workdir: Path) -> bool:
    try:
        done = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
        )
    except OSError:  # noqa: NDF401 外部コマンド: git が無ければworktree として扱わない
        return False
    return done.returncode == 0 and done.stdout.strip() == "true"


def _restore(agents: Path, backup: Path, before_bak: set[str]) -> None:
    if backup.is_dir():
        shutil.copytree(backup, agents, dirs_exist_ok=True)
    if agents.is_dir():
        for p in agents.glob("*.bak"):
            if p.name not in before_bak:
                p.unlink()


def _write_launch(paths: AgentPaths, kept: dict[str, Any]) -> None:
    paths.launch.parent.mkdir(parents=True, exist_ok=True)
    paths.launch.write_text(json.dumps(kept, ensure_ascii=False) + "\n", encoding="utf-8")


def _guard_restore(workdir: Path, paths: AgentPaths, backup: Path, before_bak: set[str], kept: dict[str, Any]) -> None:
    agents = workdir / KIRO_AGENTS
    try:
        _restore(agents, backup, before_bak)
    except OSError as exc:
        kept.update(restore="restore_failed", detail=str(exc), backup=str(backup))
        with open(paths.err, "a", encoding="utf-8") as err:
            err.write(f"kiro が書き換えた {agents} を戻せない（控えは {backup}）: {exc}\n")
        return
    shutil.rmtree(backup, ignore_errors=True)
    kept["restore"] = "restored"


def _backup_agents(workdir: Path, paths: AgentPaths) -> tuple[Path, set[str]]:
    """worktree の `.amazonq/cli-agents` を控え、控えのパスと起動の前からある `.bak` の名前を返す。"""
    agents = workdir / KIRO_AGENTS
    backup = paths.launch.with_name(paths.launch.stem + BACKUP_SUFFIX)
    shutil.rmtree(backup, ignore_errors=True)
    before_bak = {p.name for p in agents.glob("*.bak")} if agents.is_dir() else set()
    if agents.is_dir():
        shutil.copytree(agents, backup)
    return backup, before_bak


@contextlib.contextmanager
def kiro_guard(workdir: Path, paths: AgentPaths) -> Iterator[dict[str, Any]]:
    """kiro の起動の前にworktree の `.amazonq/cli-agents` を控え、終わった後に戻して増えた `.bak` を消す。

    worktree でなければ控えずに起動し、`restore_skipped` を残す。戻せなければ控えを残し `restore_failed` を残す。
    結果（`restore`）は起動の記録（`paths.launch`）にも書く。
    """
    kept: dict[str, Any] = {"agent": "kiro", "restore": None}
    backed = _backup_agents(workdir, paths) if _in_worktree(workdir) else None
    if backed is None:
        kept["restore"] = "restore_skipped"
    try:
        yield kept
    finally:
        if backed is not None:
            _guard_restore(workdir, paths, *backed, kept)
        _write_launch(paths, kept)
