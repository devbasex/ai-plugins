"""ステップのコマンドを打ち、結果 JSON を読む。

- 作業ディレクトリは打つたびに決める。マージで作業場所が消えた・登録が外れた後はリポジトリの根で打つ
- 子のプロセスは自分のプロセスの組で起こし、pid とホスト名を状態の置き場の `steps/<ステップ>.pid.json` に書く。
  打ち直しの前に、前の回の子が残っていれば止めてから打つ。別のホストの子が残っていれば打たずに止まる（B10）
- 結果は標準出力の最後の JSON の行から読む。JSON を出さないコマンドは終了コードと `exit_cause` で種類を決める
- 結果に載せる文は、秘密の環境変数の値とトークンの形を伏せてから返す（耐久の記録と結果 JSON へ秘密を入れない）
- 結果 JSON の items の `{"kind": "pr", "number": <番号>}` を拾い、プランの結果とキューの `{queue_prs}` へ渡す

外部への操作（push・PR の作成・マージ・Issue を閉じる・ラベル・コメント・リリース）のステップは、`probe` に相手の状態を
読むコマンドを持つ（B9）。`run` の前に毎回打ち、済んでいれば `run` を打たずに ok を返す。操作の直後にプロセスが落ちて
耐久ステップの記録が残らなくても、回復で打ち直したステップは済んだ操作を繰り返さない。`probe` の答え:

- 終了コード 0（結果 JSON なら `ok`）: 済んでいる。`run` を打たない
- 終了コード 1（結果 JSON なら `not_applicable` で止まった）: まだ。`run` を打つ
- ほか: 済んだかを確かめられない。`run` を打たずに、その種類（結果 JSON の種類か `exit_cause`、無ければ unknown）で止まる

見本（ブランチの push。リモートの head が手元の head と同じなら済んでいる）:

    {"id": "push", "run": ["git", "push", "origin", "HEAD:refs/heads/feat/x"],
     "probe": ["sh", "-c", "test \"$(git ls-remote origin refs/heads/feat/x | cut -f1)\" = \"$(git rev-parse HEAD)\""]}
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


def _prs(found: dict[str, Any]) -> list[int]:
    """結果 JSON の items の PR の番号（`{"kind": "pr", "number": <整数>}`）。"""
    items = found.get("items") if isinstance(found.get("items"), list) else []
    return [i["number"] for i in items if isinstance(i, dict) and i.get("kind") == "pr" and type(i.get("number")) is int]


def outcome_of(step: Step, code: int, stdout: str) -> dict[str, Any]:
    """終了コードと標準出力からステップの結果を作る（`status`・`kind`・`retry_after`・`routes`・`summary`・`code`・`prs`）。"""
    found = _last_json(stdout)
    if found is None:
        if code == 0:
            return {"status": OK, "kind": None, "retry_after": None, "routes": None, "summary": "", "code": code, "prs": []}
        kind = step.exit_cause[code] if code in step.exit_cause else "unknown"
        return {"status": STOPPED, "kind": kind, "retry_after": None, "routes": None, "summary": f"終了コード {code}", "code": code, "prs": []}
    cause = found.get("cause") if isinstance(found.get("cause"), dict) else None
    return {
        "status": found["status"],
        "kind": cause.get("kind") if cause else None,
        "retry_after": cause.get("retry_after") if cause else None,
        "routes": found.get("routes") if found["status"] == GATE else None,
        "summary": str(found.get("summary")),
        "code": code,
        "prs": _prs(found),
    }


def _probe(ctx: StepContext, step: Step, timeout: float) -> Optional[dict[str, Any]]:
    """`probe` を打ち、済んでいれば ok の結果、確かめられなければ止まった結果、まだなら None を返す（B9）。"""
    args = assemble_decide.fill(step.probe if step.probe is not None else (), ctx.values)
    try:
        done = subprocess.run(args, cwd=workdir(ctx.plan, step), capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _stopped("precondition", f"ステップ {step.id} の probe を打てない: {exc}")
    found = outcome_of(step, done.returncode, done.stdout)
    if found["status"] == OK:
        return {**found, "summary": f"済んでいた（{step.id} の probe）"}
    if found["kind"] == "not_applicable" or (found["code"] == 1 and _last_json(done.stdout) is None):
        return None
    kind = found["kind"] if found["kind"] is not None else "unknown"
    return _stopped(kind, f"ステップ {step.id} の probe で済んだかを確かめられない: {found['summary']}")


def _stopped(kind: str, summary: str) -> dict[str, Any]:
    return {"status": STOPPED, "kind": kind, "retry_after": None, "routes": None, "summary": summary, "code": None, "prs": []}


def _launch(ctx: StepContext, step: Step) -> subprocess.Popen:
    args = assemble_decide.fill(step.run, ctx.values)
    log_path = ctx.state_dir / STEPS_DIR / f"{step.id}{LOG_SUFFIX}"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as err:
        return subprocess.Popen(args, cwd=workdir(ctx.plan, step), stdout=subprocess.PIPE, stderr=err, text=True, start_new_session=True)


def run(ctx: StepContext, step: Step) -> dict[str, Any]:
    """ステップを 1 回打ち、結果を返す。上限を超えたら組を止めて interrupted、別のホストの子が残れば refused。"""
    try:
        ensure_previous_stopped(ctx.state_dir, step.id)
    except StepRefused as exc:
        return _stopped("refused", str(exc))
    timeout = step.timeout if step.timeout is not None else limits.seconds(RUN_STEP_LIMIT)
    if step.probe is not None:
        checked = _probe(ctx, step, timeout)
        if checked is not None:
            return run_log.redact(checked)
    try:
        proc = _launch(ctx, step)
    except OSError as exc:
        return _stopped("precondition", f"ステップ {step.id} を起動できない: {exc}")
    pid_path = _write_pid(ctx.state_dir, step.id, proc.pid)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_group(proc.pid)
        proc.communicate()
        out = _stopped("interrupted", f"{timeout:.0f} 秒で打ち切った")
    else:
        out = outcome_of(step, proc.returncode, stdout)
    pid_path.unlink(missing_ok=True)
    return run_log.redact(out)
