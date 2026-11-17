"""`ndf progress next` が本物の工程から作業を呼ぶ層（`invoke.invoke`）と本物のテンプレートで 1 スプリントを流すテスト（#3474）。

工程の作業の偽物（`test_run.Fake`）を使わず、各行のプランはテンプレートを差し込みの出所（課題・`.ndf/project.json`・既定）で
埋めて書く。差し替えるのは、埋めたプランを打つ子プロセス（`ndf plan next`）だけである。worker と承認ゲートの起動の
既定は P2 #3295 が入れるので、ここではテストの中で与える。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import strategy_fixture

from ndf.contracts import result
from ndf.core import durable, route
from ndf.progress import command, definition, gate, invoke, run, store

ISSUE = 3474
BRANCH = "sprint/s1"
PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"], "refactor": ["ndf-refactor"]}},
}
DEFAULTS = {"worker": ["ndf-worker"], "gate": ["ndf-gate"]}
# 手の数の上限（どのモードの行の数とゲートの数の和よりも大きい）
MAX_TURNS = 100


class Records:
    def read(self, number: int) -> str:
        return "本文\n"

    def write(self, number: int, body: str) -> None:
        return None


@pytest.fixture
def st(repo):
    root = durable.prepare_store(command.store_root_of(repo)).root
    with store.open_store(store.default_url(root), root) as opened:
        yield opened


@pytest.fixture
def sprint(repo, monkeypatch):
    """スプリントのブランチに居るリポジトリと、本物の `invoke.invoke` を呼ぶ作業。埋めたプランを `plans` に集める。"""
    subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"], cwd=repo, check=True)
    strategy_fixture.write(repo, "web-develop", PROJECT)
    held = route.params()
    patched = {n: replace(p, default=DEFAULTS[n]) if n in DEFAULTS else p for n, p in held.items()}
    monkeypatch.setattr(route, "params", lambda: patched)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None: "squash")
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    values = {"worktree": str(repo), "branch": BRANCH, "design_pr": 7, "pr": 8, "version": "11.0.0"}
    values["delivery"] = {"target": "t", "kind": "auto", "trigger": "cd", "channel": "production", "versioned": True}
    # 承認ゲートの直前の MVV 判定は人へ問う側（省かない）に倒す（#3745）
    values["mvv_gate"] = {"gate": "design", "skip": False, "verdict": None, "why": ["テストでは判定しない"]}
    plans: list[dict] = []

    def runner_for(r: invoke.StageRun):
        def runner(argv, cwd, env):
            if r.kind == invoke.SCRIPT:
                # 承認ゲートの直前の MVV 判定（ndf records mvv gate）は人へ問う側の結果を返す
                held = {**values["mvv_gate"], "gate": argv[argv.index("--gate") + 1]}
                out = result.result("records", "ok", "承認ゲートを人へ問う", outputs={"mvv_gate": held})
                return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")
            plans.append(json.loads(Path(argv[argv.index("next") + 1]).read_text(encoding="utf-8")))
            out = result.result("plan", "ok", f"{r.step} を終えた", outputs={k: values[k] for k in r.outputs} or None)
            return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")

        return runner

    def invoker(r, cwd, work_dir, answer):
        return invoke.invoke(r, cwd, work_dir, answer, runner=runner_for(r))

    env = run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=invoker,
        records=lambda cwd: Records(),
        sleep=lambda s: None,
    )
    return SimpleNamespace(env=env, plans=plans)


def _new(capsys, mode: str) -> str:
    code = command.main(["new", "s1", "--mode", mode, "--issues", str(ISSUE)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    return out["metrics"]["machine_id"]


def _turn(st, tid: str, repo: Path, env: run.Env) -> dict:
    """`next` を打ち、作業を起動したらその作業を同じプロセスで流す。承認ゲートには承認で回答する。

    承認ゲート（`ndf.listen`）への回答は、質問の id を持つ `ndf answer` だけが受ける（3364-2）。"""
    out = run.advance(st, tid, repo, env)
    if out["status"] == "ok" and "next" in out and out["next"]["argv"][2] == "wait":
        out = run.work(st, tid, repo, env)
    if out["status"] != "gate":
        return out
    v = run.read(st, tid)
    m = run.decide(v)
    if m.step is not None and invoke.is_listen(m.step):
        q = gate.question(v, m)
        gate.answer(st, tid, gate.Answer(q.id, "approved", "人が回答した", "human:test", f"test-{q.id}"))
    else:
        run.answer(st, tid, out["metrics"]["stage"], "approved", "人が回答した", None)
    return out


def _called_plans(mode: str) -> set[str]:
    return {s.args["plan"] for stage in definition.load_definition(mode).stages for s in stage.steps if s.call == "ndf.plan"}


@pytest.mark.parametrize("mode", ["light", "standard"])
def test_next_carries_a_sprint_to_the_last_row_with_the_real_templates(repo, st, sprint, capsys, mode):
    tid = _new(capsys, mode)
    for _ in range(MAX_TURNS):
        out = _turn(st, tid, repo, sprint.env)
        assert out["status"] in ("ok", "gate"), out
        if out["metrics"].get("state") == "completed":
            break
    else:
        pytest.fail(f"{mode} のスプリントが最後の行まで進まない")
    v = run.read(st, tid)
    assert {v.rows[s.name].state for s in v.snap.stages} == {"finished"}
    # 工程の定義が呼ぶどのプランもテンプレートから埋め切れ、差し込みの出所（課題・設定・既定）の値が載る
    assert {p["name"].removesuffix(f"-{BRANCH}") for p in sprint.plans} == _called_plans(mode)
    text = json.dumps(sprint.plans, ensure_ascii=False)
    assert "{{" not in text and "ndf-worker" in text and "ndf-review" in text and f"#{ISSUE} の題" in text
    if mode == "standard":
        assert "ndf-refactor" in text and "ndf-rebuild" in text


def test_next_stops_naming_the_stage_whose_plan_has_no_template(repo, st, sprint, capsys, monkeypatch):
    # テンプレートの無い名前を呼ぶ工程は、プランを打たずに工程名とテンプレートの名前を示して止まる
    held = route.templates()
    monkeypatch.setattr(route, "templates", lambda: {n: t for n, t in held.items() if n != "worktree"})
    tid = _new(capsys, "light")
    for _ in range(MAX_TURNS):
        out = _turn(st, tid, repo, sprint.env)
        if out["status"] not in ("ok", "gate"):
            break
    else:
        pytest.fail("止まらない")
    assert out["status"] == "stopped" and out["cause"]["kind"] == "precondition"
    assert out["summary"] == "工程 作業場所の用意 が失敗した: プランのテンプレート worktree が無い"
    assert out["metrics"]["stage"] == "作業場所の用意"
    assert [p["name"] for p in sprint.plans] == ["requirements"]
