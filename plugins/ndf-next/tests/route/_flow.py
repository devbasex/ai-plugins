"""雛形のテストが共有する部品: 雛形を埋める値、遷移の判断を台本の結果で回す歩み、ステップのコマンドを打つ。"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import route
from ndf.core.plan import assemble_decide, model, steps, transition_decide
from ndf.core.plan.transition_decide import ANSWERED, SLEPT, AwaitAnswer, Outcome, Sleep, StepState

# 台本が尽きるまでの手の数の上限（雛形のプランの上限より大きい）
MAX_ACTS = 500


def values(purpose: str, **given: Any) -> dict[str, Any]:
    """雛形の差し込みの見本の値（並びは 1 つの値）。`given` で上書きする。"""
    t = route.choose(purpose)
    defs = route.params()
    out: dict[str, Any] = {n: ([f"<{n}>"] if defs[n].is_list else f"<{n}>") for n in t.params}
    out.update(given)
    return out


def plan_of(purpose: str, **given: Any) -> dict[str, Any]:
    return route.render(route.choose(purpose), values(purpose, **given))


def stopped(kind: str) -> Outcome:
    return Outcome(STOPPED, kind)


def gate() -> Outcome:
    return Outcome(GATE)


def walk(plan_raw: Mapping[str, Any], script: Mapping[str, Sequence[Outcome]], answers: Sequence[str] = ()) -> tuple[list[str], StepState]:
    """プランを遷移の判断だけで回す。ステップの結果は `script` の順に返し、尽きれば ok。打ったステップの並びと終わりの状態を返す。"""
    plan = model.parse(dict(plan_raw))
    queues = {k: list(v) for k, v in script.items()}
    pending_answers = list(answers)
    state = transition_decide.start_state(plan, None)
    visited: list[str] = []
    done: Optional[Outcome] = None
    for _ in range(MAX_ACTS):
        nxt = transition_decide.decide(plan, state, done)
        state = nxt.state
        if nxt.finished:
            return visited, state
        act = nxt.act
        if isinstance(act, Sleep):
            done = Outcome(SLEPT)
        elif isinstance(act, AwaitAnswer):
            done = Outcome(ANSWERED, answer=pending_answers.pop(0))
        else:
            visited.append(act.step)
            queue = queues.get(act.step, [])
            done = queue.pop(0) if queue else Outcome(OK)
    raise AssertionError(f"{MAX_ACTS} 手で終わらない: {visited[-20:]}")


def step_of(plan_raw: Mapping[str, Any], step_id: str) -> model.Step:
    found = model.parse(dict(plan_raw)).step(step_id)
    assert found is not None, step_id
    return found


def fake_bin(tmp: Path, name: str, body: str) -> Path:
    """`tmp/bin/<name>` に偽のコマンドを置き、その置き場を返す。"""
    bindir = tmp / "bin"
    bindir.mkdir(exist_ok=True)
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return bindir


def run_step(step: model.Step, cwd: Path, bindir: Optional[Path] = None, fill: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """ステップの run を打ち、プランの実行と同じ読み方（`steps.outcome_of`）で結果を返す。"""
    env = dict(os.environ)
    if bindir is not None:
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    args = assemble_decide.fill(step.run, fill if fill is not None else {})
    done = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=60)
    return steps.outcome_of(step, done.returncode, done.stdout)


GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


def repo_with_base(tmp: Path, changed: Mapping[str, str]) -> Path:
    """`origin/develop` を指す ref と、その上に `changed` のファイルを足したコミットを持つリポジトリ。"""
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "develop")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat/x")
    for name, text in changed.items():
        (repo / name).write_text(text, encoding="utf-8")
    if changed:
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "change")
    return repo
