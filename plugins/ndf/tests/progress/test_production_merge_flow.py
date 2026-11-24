"""自動反映の本番チャネルへのマージを、工程を `ndf progress next` で進めてもリリース承認ゲートの回答まで止める（#4409）。

要求仕様の「再現手順」の 3 つ（自動反映の設定・工程「Pull Request」まで進めて PR を開いたまま・`ndf progress next`）を
本物の工程・テンプレート・差し込みの組み立て（`invoke.invoke`）で流す。工程が組んだプランは遷移の判断（`transition_decide`）で
回し、ステップのうち `ndf ci merge`・`ndf ci answer` だけを本物（`ci_cli.main`）で打つ。GitHub は偽物（`merge_hub`）で、
ほかのステップは成功を返す。マージの呼び出しの数と時点で、承認の前にスプリントの PR がマージされないことを確かめる。
"""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import merge_hub
import pytest
import strategy_fixture

from ndf import ci_cli
from ndf.adapters import git as git_mod
from ndf.adapters import github
from ndf.contracts import result
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import durable, route
from ndf.core.plan import model, transition_decide
from ndf.core.plan.transition_decide import ANSWERED, AwaitAnswer, Outcome, RunStep
from ndf.progress import command, gate, invoke, run, store
from ndf.progress.gate import asking

ISSUE = 4409
BRANCH = "sprint/s1"
DESIGN_PR, SPRINT_PR = 7, 8
PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"], "refactor": ["ndf-refactor"]}},
}
DEFAULTS = {"worker": ["ndf-worker"], "gate": ["ndf-gate"]}
MAX_TURNS = 120
MAX_ACTS = 200


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


def _hub() -> merge_hub.MergeHub:
    """ai-plugins と同じ設定（ベースブランチと本番チャネルが main、自動反映の行がある）の偽の GitHub。"""
    pulls = {DESIGN_PR: merge_hub.pull(DESIGN_PR, ref=BRANCH), SPRINT_PR: merge_hub.pull(SPRINT_PR, ref=BRANCH)}
    return merge_hub.MergeHub(pulls_of=pulls)


def _ci(argv: list[str], monkeypatch) -> dict:
    """`ndf ci` を同じプロセスで打った結果 JSON。"""
    out = io.StringIO()
    with redirect_stdout(out):
        ci_cli.main(argv)
    return json.loads(out.getvalue().strip().splitlines()[-1])


def _flow(repo: Path, monkeypatch, hub: merge_hub.MergeHub) -> SimpleNamespace:
    strategy_fixture.write(repo, "web-trunk", PROJECT)
    held = route.params()
    patched = {n: replace(p, default=DEFAULTS[n]) if n in DEFAULTS else p for n, p in held.items()}
    monkeypatch.setattr(route, "params", lambda: patched)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "merge")
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))

    class FakeGit:
        def __init__(self, *_: object) -> None:
            pass

        def rev_parse(self, _rev: str) -> str:
            return merge_hub.HEAD

        def remote_repo(self) -> str:
            return merge_hub.REPO

    class FakeHub:
        def __new__(cls, _repo: str, _client: object) -> merge_hub.MergeHub:
            return hub

    # ci_cli が読む git だけを差し替える（工程の側は本物の git を打つ）
    monkeypatch.setattr(ci_cli, "git", SimpleNamespace(Git=FakeGit, GitError=git_mod.GitError, sha_matches=git_mod.sha_matches))
    monkeypatch.setattr(ci_cli, "GitHub", FakeHub)
    monkeypatch.setattr(github, "client", lambda **_: None)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "集めた"], cwd=repo, check=True)
    gathered = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    values = {"worktree": str(repo), "branch": BRANCH, "design_pr": DESIGN_PR, "pr": SPRINT_PR, "product_version": "11.0.0", "gathered": gathered}
    values["delivery"] = {"target": "t", "kind": "auto", "trigger": "cd", "channel": "production", "versioned": True}
    plans: list[str] = []
    # 工程が流したプランの名前と、そのとき既にマージされていた PR
    seen: list[tuple[str, list[int]]] = []

    def step_outcome(plan: model.Plan, step_id: str, work_dir: Path) -> Outcome:
        argv = [str(a).replace("{state_dir}", str(work_dir)) for a in plan.step(step_id).run]
        if argv[:2] == ["ndf", "ci"]:
            got = _ci(argv[2:], monkeypatch)
            kind = got["cause"]["kind"] if got["status"] == STOPPED else None
            return Outcome(got["status"], kind)
        return Outcome(OK)

    def run_plan(path: Path, work_dir: Path) -> dict:
        plan = model.parse(json.loads(path.read_text(encoding="utf-8")))
        plans.append(plan.name.removesuffix(f"-{BRANCH}"))
        seen.append((plans[-1], list(hub.merged)))
        state = transition_decide.start_state(plan, None)
        done = None
        for _ in range(MAX_ACTS):
            nxt = transition_decide.decide(plan, state, done)
            state = nxt.state
            if nxt.finished:
                break
            act = nxt.act
            if isinstance(act, RunStep):
                done = step_outcome(plan, act.step, work_dir)
            elif isinstance(act, AwaitAnswer):
                return result.result("plan", GATE, f"{plan.name} が承認を待つ", routes={"approved": None, "rejected": None})
            else:
                done = Outcome(ANSWERED if isinstance(act, AwaitAnswer) else "slept")
        if state.status == STOPPED:
            return result.stopped("plan", state.kind or "unknown", state.detail or "止まった")
        return result.result("plan", OK, f"{plan.name} を終えた")

    def runner_for(r: invoke.StageRun, work_dir: Path):
        def runner(argv, cwd, env):
            if r.kind == invoke.SCRIPT and argv[-1] == "gathered":
                out = result.result("progress", "ok", "集めた時点", outputs={"gathered": values["gathered"]})
            elif r.kind == invoke.SCRIPT:
                held = {"gate": argv[argv.index("--gate") + 1], "skip": False, "verdict": None, "why": ["テストでは判定しない"]}
                out = result.result("records", "ok", "承認ゲートを人へ問う", outputs={"mvv_gate": held})
            else:
                got = run_plan(Path(argv[argv.index("next") + 1]), work_dir)
                outputs = {k: values[k] for k in r.outputs if k in values} or None
                out = {**got, "outputs": outputs} if got["status"] == OK and outputs else got
            return subprocess.CompletedProcess(argv, 0 if out["status"] == OK else 10 if out["status"] == GATE else 1, json.dumps(out) + "\n", "")

        return runner

    def invoker(r, cwd, work_dir, answer):
        return invoke.invoke(r, cwd, work_dir, answer, runner=runner_for(r, work_dir))

    env = run.Env(
        facts=lambda words, ctx, cwd: {w: True for w in words},
        launch=lambda st_, tid, cwd: None,
        invoker=invoker,
        records=lambda cwd: Records(),
        sleep=lambda s: None,
    )
    return SimpleNamespace(env=env, plans=plans, seen=seen)


def _observe(q: gate.Question) -> gate.Target:
    """承認ゲートの今の対象（PR の今の HEAD・マージ先と、本番へ反映されるか）。"""
    return gate.Target(merge_hub.HEAD, None, merge_hub.REPO, q.pr, "main", True)


def _turn(st, tid: str, repo: Path, flow: SimpleNamespace, hub: merge_hub.MergeHub, gates: list[tuple[str, list[int]]]) -> dict:
    """`next` を打ち、作業を流す。承認ゲートでは提示した承認の対象を残し、利用者（human）の承認で回答する（証跡を書く）。"""
    out = run.advance(st, tid, repo, flow.env)
    if out["status"] == "ok" and "next" in out and out["next"]["argv"][2] == "wait":
        out = run.work(st, tid, repo, flow.env)
    if out["status"] != "gate":
        return out
    v = run.read(st, tid)
    m = run.decide(v)
    assert m.step is not None and invoke.is_listen(m.step), out
    asked = gate.question(v, m)
    q = replace(asked, target=_observe(asked))
    asking.write_material(st.root, v, q)
    gates.append((str(q.gate), list(hub.merged)))
    post = gate.github_post(repo, hub=lambda: hub)  # type: ignore[arg-type,return-value]
    a = gate.Answer(q.id, "approved", "人が回答した", "human", f"test-{q.id}", seen=gate.Target(head=merge_hub.HEAD))
    gate.answer(st, tid, a, _observe, post)
    return out


def _new(capsys, mode: str) -> str:
    code = command.main(["new", "s1", "--mode", mode, "--issues", str(ISSUE)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    return out["metrics"]["machine_id"]


@pytest.mark.parametrize("mode", ["standard", "light"])
def test_the_sprint_pr_waits_for_the_release_gate_and_merges_in_deploy(repo, st, capsys, monkeypatch, mode):
    """受け入れ条件 1・2・3・5・10・11・12: 工程「Pull Request」・ドキュメントレビュー・確定仕様化はスプリントの PR を
    マージせずに進み、デプロイのリリース承認ゲートが回答を待ち始めた時点で PR は開いたまま。承認の回答が証跡を書き、
    デプロイの最初のステップ（release-merge）が同じ PR をマージし、その後にクリーンアップが流れる。"""
    hub = _hub()
    flow = _flow(repo, monkeypatch, hub)
    tid = _new(capsys, mode)
    gates: list[tuple[str, list[int]]] = []
    for _ in range(MAX_TURNS):
        out = _turn(st, tid, repo, flow, hub, gates)
        assert out["status"] in ("ok", "gate"), (out, hub.merged)
        if out["metrics"].get("state") == "completed":
            break
    else:
        pytest.fail(f"{mode} のスプリントが最後まで進まない（流したプラン: {flow.plans}）")
    # リリース承認ゲートが回答を待ち始めた時点で、スプリントの PR はマージされていない
    release_gate = [merged for kind, merged in gates if kind == "release"]
    assert release_gate and SPRINT_PR not in release_gate[0]
    # スプリントの PR をマージしたのはデプロイのプランだけで、1 回だけ
    assert hub.merged.count(SPRINT_PR) == 1
    by_plan = dict(flow.seen)
    assert SPRINT_PR not in by_plan["release"]
    # クリーンアップ（merged）はデプロイの後に流れ、その時点で PR はマージ済み
    assert flow.plans.index("merged") > flow.plans.index("release")
    assert SPRINT_PR in by_plan["merged"]


def test_reproduction_steps_do_not_merge_the_open_sprint_pr_before_the_release_gate(repo, st, capsys, monkeypatch):
    """受け入れ条件 12: 再現手順（自動反映の設定・Pull Request まで進めて PR を開いたまま・ndf progress next）で、
    リリース承認ゲートの前にスプリントの PR へのマージの呼び出しが 0 回である。直す前は確定仕様化のプランがマージを呼ぶ。"""
    hub = _hub()
    flow = _flow(repo, monkeypatch, hub)
    tid = _new(capsys, "standard")
    gates: list[tuple[str, list[int]]] = []
    for _ in range(MAX_TURNS):
        out = _turn(st, tid, repo, flow, hub, gates)
        if any(kind == "release" for kind, _ in gates):
            break
        assert out["status"] in ("ok", "gate"), out
    else:
        pytest.fail("リリース承認ゲートまで進まない")
    assert SPRINT_PR not in hub.merged
    assert {"pr", "merge", "plan-to-spec"} <= set(flow.plans)
