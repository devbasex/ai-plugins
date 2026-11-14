"""ステップのコマンドを打ち、結果 JSON を読む。

- 作業ディレクトリは打つたびに決める。マージで作業場所が消えた・登録が外れた後はリポジトリの根で打つ
- 子のプロセスは自分のプロセスの組で起こし、pid とホスト名を状態の置き場の `steps/<ステップ>.pid.json` に書く。
  打ち直しの前に、前の回の子が残っていれば止めてから打つ。別のホストの子が残っていれば打たずに止まる（B10）
- 結果は標準出力の最後の JSON の行から読む。JSON を出さないコマンドは終了コードと `exit_cause` で種類を決める
- 結果に載せる文は、秘密の環境変数の値とトークンの形を伏せてから返す（耐久の記録と結果 JSON へ秘密を入れない）
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import git, run_log
from ndf.contracts import limits
from ndf.contracts.exit_codes import GATE, OK, STATUSES, STOPPED
from ndf.core.plan import assemble_decide
from ndf.core.plan.model import Plan, Step

STEPS_DIR = "steps"
PID_SUFFIX = ".pid.json"
LOG_SUFFIX = ".log"
RUN_STEP_LIMIT = "supervise.run-step"
STOP_TREE = "procs.stop-tree"


class StepRefused(Exception):
    """前の回の子のプロセスが別のホストに残っていて、止めたかを確かめられない。"""


@dataclass(frozen=True)
class StepContext:
    """ステップを打つ文脈。`values` は差し込みの値（`state_dir` など）。"""

    plan: Plan
    state_dir: Path
    values: dict[str, str]


def usable_worktree(path: Optional[str]) -> bool:
    """作業場所として使えるか（ディレクトリがあり、git の worktree の中か）。"""
    if path is None or not Path(path).is_dir():
        return False
    try:
        return git.Git(Path(path)).run("rev-parse", "--is-inside-work-tree").strip() == "true"
    except git.GitError:  # noqa: NDF401 外部コマンド: 登録の外れた作業場所は git が読めない
        return False


def workdir(plan: Plan, step: Step) -> Optional[str]:
    """ステップを打つ作業ディレクトリ（`assemble_decide.workdir_for` の判断に作業場所の今の状態を渡す）。"""
    return assemble_decide.workdir_for(step.place, plan.worktree, plan.repo, usable_worktree(plan.worktree))


def _pid_path(state_dir: Path, step_id: str) -> Path:
    return state_dir / STEPS_DIR / f"{step_id}{PID_SUFFIX}"


def _alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:  # noqa: NDF401 外部コマンド: 組の成員が残っていない
        return False
    except PermissionError:  # noqa: NDF401 外部コマンド: 別の利用者の組は生きている
        return True
    return True


def stop_group(pid: int) -> None:
    """プロセスの組を止める。猶予の間に終わらなければ SIGKILL を送る。"""
    grace = limits.seconds(STOP_TREE)
    interval = limits.value(STOP_TREE, "interval")
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:  # noqa: NDF401 外部コマンド: 組が既に終わっている
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(interval)
    if _alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:  # noqa: S110 外部コマンド: 組が猶予の間に終わった
            pass


def ensure_previous_stopped(state_dir: Path, step_id: str) -> Optional[int]:
    """前の回の子のプロセスが残っていれば止める。止めた pid を返す。別のホストの記録なら `StepRefused`。"""
    path = _pid_path(state_dir, step_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # noqa: NDF401 ファイル: 記録が無い・壊れている（前の回は子を起こす前に終わった）
        return None
    if record["host"] != socket.gethostname():
        raise StepRefused(f"ステップ {step_id} の前の子（pid {record['pid']}）が別のホスト {record['host']} に残っている")
    if not _alive(record["pid"]):
        path.unlink(missing_ok=True)
        return None
    stop_group(record["pid"])
    path.unlink(missing_ok=True)
    return record["pid"]


def _write_pid(state_dir: Path, step_id: str, pid: int) -> Path:
    path = _pid_path(state_dir, step_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": pid, "host": socket.gethostname(), "started": time.time()}), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _last_json(stdout: str) -> Optional[dict[str, Any]]:
    """標準出力の最後の空でない行が結果 JSON（`status` を持つ辞書）なら返す。"""
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        found = json.loads(lines[-1])
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 結果 JSON を出さないコマンド
        return None
    return found if isinstance(found, dict) and found.get("status") in STATUSES else None


def outcome_of(step: Step, code: int, stdout: str) -> dict[str, Any]:
    """終了コードと標準出力からステップの結果を作る（`status`・`kind`・`retry_after`・`routes`・`summary`・`code`）。"""
    found = _last_json(stdout)
    if found is None:
        if code == 0:
            return {"status": OK, "kind": None, "retry_after": None, "routes": None, "summary": "", "code": code}
        kind = step.exit_cause[code] if code in step.exit_cause else "unknown"
        return {"status": STOPPED, "kind": kind, "retry_after": None, "routes": None, "summary": f"終了コード {code}", "code": code}
    cause = found.get("cause") if isinstance(found.get("cause"), dict) else None
    return {
        "status": found["status"],
        "kind": cause.get("kind") if cause else None,
        "retry_after": cause.get("retry_after") if cause else None,
        "routes": found.get("routes") if found["status"] == GATE else None,
        "summary": str(found.get("summary")),
        "code": code,
    }


def _launch(ctx: StepContext, step: Step) -> subprocess.Popen:
    args = assemble_decide.fill(step.run, ctx.values)
    log_path = ctx.state_dir / STEPS_DIR / f"{step.id}{LOG_SUFFIX}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as err:
        return subprocess.Popen(
            args, cwd=workdir(ctx.plan, step), stdout=subprocess.PIPE, stderr=err, text=True, start_new_session=True
        )


def run(ctx: StepContext, step: Step) -> dict[str, Any]:
    """ステップを 1 回打ち、結果を返す。上限を超えたら組を止めて interrupted、別のホストの子が残れば refused。"""
    try:
        ensure_previous_stopped(ctx.state_dir, step.id)
    except StepRefused as exc:
        return {"status": STOPPED, "kind": "refused", "retry_after": None, "routes": None, "summary": str(exc), "code": None}
    timeout = step.timeout if step.timeout is not None else limits.seconds(RUN_STEP_LIMIT)
    try:
        proc = _launch(ctx, step)
    except OSError as exc:
        summary = f"ステップ {step.id} を起動できない: {exc}"
        return {"status": STOPPED, "kind": "precondition", "retry_after": None, "routes": None, "summary": summary, "code": None}
    pid_path = _write_pid(ctx.state_dir, step.id, proc.pid)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_group(proc.pid)
        proc.communicate()
        out = {"status": STOPPED, "kind": "interrupted", "retry_after": None, "routes": None, "summary": f"{timeout:.0f} 秒で打ち切った", "code": None}
    else:
        out = outcome_of(step, proc.returncode, stdout)
    pid_path.unlink(missing_ok=True)
    return run_log.redact(out)
