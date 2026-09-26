"""監視の既定値・一時ディレクトリ・状態の型（#1142 の C3 で `monitor.py` から分けた）。

上限の既定値は上限の表（`limits.py`）を指す別名で、ここには値を持たない。ライブラリからは `assignment` と
`limits` だけを import する。
"""
from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass
from typing import Optional

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import assignment  # noqa: E402  席の名前の規則（#727）
import limits  # noqa: E402  上限の表（#598 / #537）


def _agent_runtime(agent: str) -> str:
    """担当の名前からランタイムを引く（CLI ごとのチェックを選ぶために使う）。

    担当の単位は席の名前（`assignment.SEAT_PATTERN`。`claude-2` のように同じランタイムの
    2 つ目を表す）である。**席の形に合わない名前はそのまま返す。** cross-refactoring は
    任意の骨格（`--stem-template`）で担当名を渡せるため、形で弾くとその経路が壊れる。
    """
    try:
        return assignment.seat_runtime(agent)
    except assignment.AssignmentError:
        return agent


# **上限の既定値はこの監視に持たない。** 上限の表（`limits.py`）だけが持ち、ここの名前は
# 表を指す別名である（#598 / #537）。env (`MONITOR_TIMEOUT` / `MONITOR_STALL` /
# `MONITOR_POLL`) の解釈は **呼び出し時** に try/except 付きで行い、非数値 env でも
# import / 監視プロセスがクラッシュしないようにする。
# (codex round 5 指摘: import 時の `int(os.environ.get(...))` は
#  `MONITOR_STALL=abc` のような誤設定で落ちてしまうため)
DEFAULT_TIMEOUT = limits.PHASE_TIMEOUT[limits.DEFAULT_PHASE]
DEFAULT_STALL = limits.DEFAULT_STALL
DEFAULT_STALL_AGENT_BUILTIN = limits.AGENT_STALL
DEFAULT_POLL = 15          # 15 sec — env `MONITOR_POLL` で上書き可
# result.json が書き込まれた後もプロセスがハングするケース (実測) の
# fallback: mtime から RESULT_AGE_GRACE 秒以上経過していれば完了とみなす。
RESULT_AGE_GRACE = 30
# `MONITOR_NO_EARLY_ERROR=1` で EARLY_ERROR 検知を無効化 (escape hatch)
DEFAULT_NO_EARLY_ERROR = os.environ.get("MONITOR_NO_EARLY_ERROR", "").lower() in {
    "1", "true", "yes", "on",
}


# env を safe に int parse する。非数値時は warn を stderr に出して fallback 値を返す。
# 上限の表と同じ規則で読むため、表の側の実装を使う。
_safe_int_env = limits.safe_int_env


def _agent_stall_default(agent: str) -> int:
    """agent ごとの既定 stall timeout を解決する。

    解決順（`limits.stall_timeout`）:
      1. env `MONITOR_STALL_<AGENT>` (per-agent 明示)
      2. env `MONITOR_STALL` (全 agent 共通)
      3. 上限の表の `AGENT_STALL[agent]`
      4. `DEFAULT_STALL` (表に無い agent)

    env は **呼び出し時** に再評価し、非数値なら warn を出して表の値に戻す。

    **席の名前はランタイム名へ直してから引く**（#727）。上限の表も担当別の環境変数も
    ランタイム名で引くため、`claude-2` のまま渡すと表に無い担当として `DEFAULT_STALL`
    へ落ち、1 席目より早く無進捗と判定される。
    """
    return limits.stall_timeout(_agent_runtime(agent))


# `--tmp-dir` で明示指定された一時ディレクトリ。CLI の解析時にだけ設定する。
# 収束ループごとに一時ディレクトリが違うため（cross-review は `.cross_review/`、
# cross-refactoring は `work/.cross_refactoring/`）、呼び出し側が明示できる経路を持つ。
_TMP_DIR_OVERRIDE: Optional[pathlib.Path] = None

# 一時ディレクトリを指す環境変数。先に見つかったものを採る。
TMP_DIR_ENV_VARS = ("CROSS_REVIEW_TMP_DIR", "CROSS_REFACTORING_TMP_DIR")


def _tmp_dir() -> pathlib.Path:
    """監視対象の一時ファイルを置くディレクトリ。

    state.py の `_tmp_dir()` と同じロジック。優先:
      1. `--tmp-dir` の明示指定
      2. `CROSS_REVIEW_TMP_DIR` / `CROSS_REFACTORING_TMP_DIR` env
      3. `<worktree-root>/.cross_review/` (worktree 内。作業領域を 1 つに保つため)

    worktree root は `git rev-parse --show-toplevel` で取得する。
    サブディレクトリから起動した場合でも一貫したパスを返す。
    """
    if _TMP_DIR_OVERRIDE is not None:
        _TMP_DIR_OVERRIDE.mkdir(parents=True, exist_ok=True)
        return _TMP_DIR_OVERRIDE
    env = next((os.environ[k] for k in TMP_DIR_ENV_VARS if os.environ.get(k)), None)
    if env:
        d = pathlib.Path(env).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    # git worktree root を取得。サブディレクトリから起動しても一貫したパスにする。
    import subprocess as _sp
    r = _sp.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        root = pathlib.Path(r.stdout.strip()).resolve()
    else:
        root = pathlib.Path.cwd().resolve()
    d = root / ".cross_review"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 一時ファイル名の骨格。`{agent}` と `{id}` を埋めて `<stem>.pid` などを作る。
# 既定は cross-review の命名で、後方互換のために変えない。
DEFAULT_STEM_TEMPLATE = "{agent}-review-pr{id}"


@dataclass(frozen=True)
class MonitorConfig:
    """1 agent の監視実行設定。"""
    timeout: int
    stall_timeout: int
    poll: int
    require_result: bool
    no_early_error: bool = False
    log_prefix: str = ""
    stem_template: str = DEFAULT_STEM_TEMPLATE


@dataclass
class AgentPaths:
    agent: str
    pr: int
    pidfile: pathlib.Path
    err_log: pathlib.Path
    stdout_log: pathlib.Path
    progress_log: pathlib.Path
    result: pathlib.Path

    @classmethod
    def for_(
        cls, agent: str, pr: int, stem_template: str = DEFAULT_STEM_TEMPLATE
    ) -> "AgentPaths":
        base = _tmp_dir() / stem_template.format(agent=agent, id=pr)
        return cls(
            agent=agent, pr=pr,
            pidfile=pathlib.Path(f"{base}.pid"),
            err_log=pathlib.Path(f"{base}-err.log"),
            stdout_log=pathlib.Path(f"{base}-stdout.log"),
            progress_log=pathlib.Path(f"{base}-progress.log"),
            result=pathlib.Path(f"{base}-result.json"),
        )


@dataclass(frozen=True)
class MonitorOutcome:
    status: str
    exit_code: int
    icon: str
    detail: str
    # 状態からは決まらない理由（`usage_limit` / `cli_timeout`）。`None` なら結末を書くときに
    # `monitor_outcome.reason_for(status)` へ落ちる（#729 の決定 8）。
    reason: Optional[str] = None

    @classmethod
    def create(cls, status: str, detail: str, reason: Optional[str] = None) -> "MonitorOutcome":
        exit_code, icon = {
            "OK": (0, "✅"),
            "TIMEOUT": (2, "⏰"),
            "NO_RESULT": (3, "❌"),
            "EARLY_ERROR": (4, "💥"),
            "STALLED": (5, "🛑"),
            "PIDFILE_BAD": (6, "❓"),
        }[status]
        return cls(status, exit_code, icon, detail, reason)


@dataclass
class AgentStatus:
    agent: str
    status: str = "RUNNING"
    exit_code: int = 0
    pid: Optional[int] = None
    elapsed: float = 0.0
    detail: str = ""
    err_log_size: int = 0
    stdout_log_size: int = 0
    progress_log_size: int = 0
    progress_tail: str = ""
    idle_seconds: float = 0.0
    result_exists: bool = False
    sentinel_seen: bool = False
    # 監視の結果ファイルだけに書く欄（#662）。**標準出力の JSON には載せない**
    # （標準出力のキーは上の欄から明示で組み立てる）。
    reason: str = ""
    launched_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    outcome: Optional[MonitorOutcome] = None
