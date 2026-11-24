"""`ndf progress next` が本物の工程から作業を呼ぶ層（`invoke.invoke`）と本物のテンプレートで 1 スプリントを流すテスト（#3474）。

工程の作業の偽物（`test_run.Fake`）を使わず、各行のプランはテンプレートを差し込みの出所（課題・`.ndf/project.json`・既定）で
埋めて書く。差し替えるのは、埋めたプランを打つ子プロセス（`ndf plan next`）だけである。worker と承認ゲートの起動は、
プランに載ったことを見分けられるようにテストの中で偽の名前を与える（worker の既定は `params.json` が持つ。#3908）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import strategy_fixture

from ndf.adapters import git as git_adapter
from ndf.contracts import result, worker_kinds
from ndf.core import durable, loop, route, worker_outcome
from ndf.core.plan import records, runner, steps, transition_decide
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
    # 集めた時点（工程「実装」の出力 gathered）はスプリントブランチのコミット。コードレビューがつなぎ目の起点に確かめる（#3879）
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "集めた"], cwd=repo, check=True)
    gathered = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    return _sprint(repo, monkeypatch, gathered)


@pytest.fixture
def early_sprint(repo, monkeypatch):
    """工程「作業場所の用意」で止まるまでを流す `sprint`。そこまでの工程はブランチもコミットも読まないので、Git を打たない。"""
    return _sprint(repo, monkeypatch, "0" * 40)


def _sprint(repo: Path, monkeypatch, gathered: str, defaults: dict = DEFAULTS, project: dict = PROJECT) -> SimpleNamespace:
    strategy_fixture.write(repo, "web-develop", project)
    held = route.params()
    patched = {n: replace(p, default=defaults[n]) if n in defaults else p for n, p in held.items()}
    monkeypatch.setattr(route, "params", lambda: patched)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    values = {"worktree": str(repo), "branch": BRANCH, "design_pr": 7, "pr": 8, "product_version": "11.0.0", "gathered": gathered}
    values["delivery"] = {"target": "t", "kind": "auto", "trigger": "cd", "channel": "production", "versioned": True}
    # 承認ゲートの直前の MVV 判定は人へ問う側（省かない）に倒す（#3745）
    values["mvv_gate"] = {"gate": "design", "skip": False, "verdict": None, "why": ["テストでは判定しない"]}
    plans: list[dict] = []

    def runner_for(r: invoke.StageRun):
        def runner(argv, cwd, env):
            if r.kind == invoke.SCRIPT and argv[-1] == "gathered":
                # 工程「実装」の最後のステップ（ndf progress gathered）は集めた時点を返す
                out = result.result("progress", "ok", "集めた時点", outputs={"gathered": values["gathered"]})
                return subprocess.CompletedProcess(argv, 0, json.dumps(out) + "\n", "")
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


# `workflow.launch.rebuild` の無いリポジトリの設定（#4337）
PROJECT_WITHOUT_REBUILD = {**PROJECT, "workflow": {"launch": {k: v for k, v in PROJECT["workflow"]["launch"].items() if k != "rebuild"}}}


def _rebuild_step(plan: dict) -> list[str]:
    return next(step["run"] for step in plan["steps"] if step["id"] == "rebuild")


@pytest.fixture
def offline_repo(tmp_path, monkeypatch, no_wait_no_child):
    """`git init` しない作業ディレクトリ。git の起動（`git._start`）は回答の表から返す偽にし、保存先とリポジトリの識別を
    固定の値にする（test_plan.py の `no_git` と同じ形）。表に無い git と、待ち・子のプロセスの起動は、呼ばれたら落ちる。"""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    root = tmp_path / "store"
    monkeypatch.setattr(command, "store_root_of", lambda _cwd: root)
    monkeypatch.setattr(command, "repo_id", lambda _cwd, _root: "https://example.invalid/o/work")
    answers = {
        ("rev-parse", "--show-toplevel"): f"{work}\n",
        ("rev-parse", "--is-inside-work-tree"): "true\n",
        ("worktree", "list", "--porcelain", "-z"): f"worktree {work}\0HEAD {'0' * 40}\0branch refs/heads/{BRANCH}\0\0",
    }

    def start(args, cwd, text, stdin=None, env=None):
        if args[:2] == ["rev-parse", "--verify"]:
            # コミットの無いリポジトリと同じく、集めた時点（偽の SHA）はコミットへ解決できない。コードレビューはそこで止まる
            return subprocess.CompletedProcess(["git", *args], 1, "", "")
        if tuple(args) not in answers:
            raise AssertionError(f"想定していない git: {args}")
        return subprocess.CompletedProcess(["git", *args], 0, answers[tuple(args)], "")

    monkeypatch.setattr(git_adapter, "_start", start)
    yield work
    durable.release_store(root)


@pytest.fixture
def offline_st(offline_repo):
    root = durable.prepare_store(command.store_root_of(offline_repo)).root
    with store.open_store(store.default_url(root), root) as opened:
        yield opened


def test_standard_sprint_without_the_rebuild_setting_launches_the_default_command(offline_repo, offline_st, capsys, monkeypatch):
    """#4337 の条件 1・2: 設定 `workflow.launch.rebuild` が無くても、工程「設計」と「ドキュメント再構成」のプランが組め、
    組み直すステップに `ndf document-rebuild run` と `--out` が入る。

    確かめるのはプランの引数だけなので、子のプロセスを起動しない（`offline_repo`）。ブランチ名は `invoke._branch_of` の
    偽物から返し、2 つのプランを組んだ手で止める。設計の承認の後の 1 手は工程「コードレビュー」まで続けて流れるが、
    2 つのプランはその前に組み終わっている。そのため止まったかは、2 つのプランが組まれていないときだけ確かめる。
    """
    repo, st = offline_repo, offline_st
    monkeypatch.setattr(invoke, "_branch_of", lambda run: BRANCH)
    s = _sprint(repo, monkeypatch, "0" * 40, project=PROJECT_WITHOUT_REBUILD)
    tid = _new(capsys, "standard")
    wanted = {"design", "document-rebuild"}
    for _ in range(MAX_TURNS):
        out = _turn(st, tid, repo, s.env)
        if wanted <= {p["name"].removesuffix(f"-{BRANCH}") for p in s.plans}:
            break
        assert out["status"] in ("ok", "gate"), out
    else:
        pytest.fail("設計とドキュメント再構成のプランが組まれない")
    plans = {p["name"].removesuffix(f"-{BRANCH}"): p for p in s.plans}
    design, rebuild = _rebuild_step(plans["design"]), _rebuild_step(plans["document-rebuild"])
    for argv in (design, rebuild):
        assert argv[:3] == ["ndf", "document-rebuild", "run"] and argv[-2:] == ["--out", "{state_dir}/rebuild.json"]
        assert argv[argv.index("--issue") + 1] == str(ISSUE) and argv[argv.index("--state-dir") + 1] == "{state_dir}"
    assert [design[i + 1] for i, w in enumerate(design) if w == "--kind"] == ["design"]
    assert [rebuild[i + 1] for i, w in enumerate(rebuild) if w == "--kind"] == ["design", "plan"]
    assert design[design.index("--base") + 1] == "develop"
    assert design[design.index("--remote") + 1] == "origin"


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


def test_requirements_stage_starts_the_worker_by_the_default_of_params(repo, st, capsys, monkeypatch):
    # #3908 の受け入れ条件 1: 差し込み worker を与えなくても、standard の工程「要求と受け入れ条件」が params.json の既定で
    # worker を起動する。作業場所は作業の定義（kinds.json の cwd）が決めるので、既定の並びに --cwd を置かない
    s = _sprint(repo, monkeypatch, "0" * 40, {"gate": DEFAULTS["gate"]})
    tid = _new(capsys, "standard")
    out = _turn(st, tid, repo, s.env)
    assert out["status"] in ("ok", "gate"), out
    assert s.plans[0]["name"] == "requirements"
    work = next(step["run"] for step in s.plans[0]["steps"] if step["id"] == "work")
    assert work == ["ndf", "answer", "work", "--state-dir", "{state_dir}", "requirements"]


def _worktree_plan(repo: Path, plan_path: Path, report: dict, monkeypatch) -> dict:
    """プラン worktree を同じプロセスで流した結果。worker のステップは `report` を書いた結果ファイルで差し替える。

    `ndf worker run` の結果を組む関数（必須の名前は作業の定義の outputs）→ ステップの結果 → 状態の保存先の記録 →
    遷移の判断 → プランの結果の順につなぐ（子のプロセスも耐久ワークフローも起動しない）。
    """
    # HEAD は回のキーに入るだけで、出力の伝搬と停止理由に関わらない。git rev-parse を打たずに決まった値を返す
    monkeypatch.setattr(runner, "_head", lambda plan: "0" * 40)
    inv = runner.prepare(repo / ".git" / "ndf" / "state", plan_path, None, None, repo)
    inv.state_dir.mkdir(parents=True, exist_ok=True)
    checked, outputs = worker_outcome.require_outputs(report, worker_kinds.work_kinds()["worktree"]["outputs"])
    said = worker_outcome.outcome_of("worktree", repo / "result.json", checked, [], {"attempts": 1}, outputs=outputs)

    def perform(act: transition_decide.RunStep) -> transition_decide.Outcome:
        out = steps.outcome_of(inv.plan.step(act.step), result.code_for(said), result.dumps_line(said))
        if out["outputs"]:
            records._add_outputs(inv.state_dir, out["outputs"])
        records._keep_step_gaps(inv.state_dir, act.step, out["gaps"])
        return transition_decide.Outcome(out["status"], out["kind"], out["retry_after"])

    decide = lambda state, done: transition_decide.decide(inv.plan, state, done)  # noqa: E731
    final = loop.run(decide, perform, transition_decide.start_state(inv.plan, None))
    ended = {"status": final.status, "kind": final.kind, "detail": final.detail, "last": final.last, "count": final.count}
    return runner.final_result(inv, ended, "SUCCESS")


def _run_to_worktree(repo, st, sprint, capsys, monkeypatch, report: dict) -> tuple[dict, run.View]:
    """light のスプリントを、工程「作業場所の用意」が終わるか止まるまで流す。プラン worktree だけ `_worktree_plan` で流す。"""

    def invoker(r, cwd, work_dir, answer):
        if r.name != "worktree":
            return sprint.env.invoker(r, cwd, work_dir, answer)

        def plan_runner(argv, cwd_, env):
            out = _worktree_plan(repo, Path(argv[argv.index("next") + 1]), report, monkeypatch)
            return subprocess.CompletedProcess(argv, result.code_for(out), result.dumps_line(out) + "\n", "")

        return invoke.invoke(r, cwd, work_dir, answer, runner=plan_runner)

    env = replace(sprint.env, invoker=invoker)
    tid = _new(capsys, "light")
    for _ in range(MAX_TURNS):
        out = _turn(st, tid, repo, env)
        v = run.read(st, tid)
        if out["status"] not in ("ok", "gate") or v.rows["作業場所の用意"].state == "finished":
            return out, v
    pytest.fail("工程「作業場所の用意」まで進まない")


def test_worktree_plan_hands_the_outputs_of_the_result_file_to_its_stage(repo, st, sprint, capsys, monkeypatch):
    # #3908 の受け入れ条件 1: worker が結果ファイルの outputs に書いた作業場所とブランチが、工程の出力の検査を通って文脈へ渡る
    # 工程「作業場所の用意」を終えた手は工程「コードレビュー」まで進み、つなぎ目の起点に gathered を読むので `sprint` を使う
    report = {"status": "ok", "summary": "用意した", "outputs": {"worktree": str(repo), "branch": BRANCH}}
    out, v = _run_to_worktree(repo, st, sprint, capsys, monkeypatch, report)
    assert out["status"] in ("ok", "gate"), out
    assert v.rows["作業場所の用意"].state == "finished"
    assert {k: v.context()[k] for k in ("worktree", "branch")} == {"worktree": str(repo), "branch": BRANCH}


def test_worktree_plan_without_the_outputs_stops_its_stage_naming_them(repo, st, early_sprint, capsys, monkeypatch):
    # 結果ファイルが outputs を持たない ok は、bad_report でなく欠けの名前付きの incomplete で工程を止める
    out, v = _run_to_worktree(repo, st, early_sprint, capsys, monkeypatch, {"status": "ok", "summary": "用意した"})
    assert out["status"] == "stopped" and out["cause"]["kind"] == "incomplete", out
    assert out["summary"].startswith("工程 作業場所の用意 が失敗した: ") and out["summary"].endswith("（欠け: branch, worktree）")
    assert v.rows["作業場所の用意"].state == "failed"


# 工程「クリーンアップ」のプラン（merged.json）が今打つ引数（#3908 の受け入れ条件 5）
MERGED_RUN = [
    "sh",
    "-c",
    'n=$(gh pr list --head "$0" --state merged --json number --jq \'.[0].number // empty\') || exit 2; [ -n "$n" ] || exit 2; '
    'ndf worktree merged "$n"',
    BRANCH,
]


@pytest.fixture
def planned(repo, monkeypatch):
    """プランを組むだけに要る設定と差し込み。スプリントのブランチへの切り替え・コミット・HEAD の取得はしない（`sprint` と違う）。"""
    strategy_fixture.write(repo, "web-develop", PROJECT)
    held = route.params()
    patched = {n: replace(p, default=DEFAULTS[n]) if n in DEFAULTS else p for n, p in held.items()}
    monkeypatch.setattr(route, "params", lambda: patched)
    monkeypatch.setattr(route, "default_merge_method", lambda repo, base, remote=None, **_: "squash")
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    return repo


def _plan_of(name: str, repo: Path, work: Path) -> dict:
    stage = next(s for s in definition.load_definition("standard").stages if s.name == "クリーンアップ")
    held = invoke.stage_run(stage, stage.steps[0], {"worktree": str(repo), "branch": BRANCH, "issues": [ISSUE]})
    r = replace(held, name=name)
    return json.loads(invoke.plan_path(r, work, repo).read_text(encoding="utf-8"))


def test_cleanup_plan_runs_the_same_argv(planned, tmp_path):
    plan = _plan_of("merged", planned, tmp_path)
    assert [s["run"] for s in plan["steps"]] == [MERGED_RUN]


@pytest.mark.parametrize("name", ["merged", "fix"])
def test_plans_without_the_worker_keep_their_argv_whether_the_worker_has_a_default(planned, tmp_path, monkeypatch, name):
    # merged.json と fix.json は差し込み worker を使わないので、worker の既定の有無で組んだ argv が変わらない
    with_default = _plan_of(name, planned, tmp_path)
    held = route.params()
    monkeypatch.setattr(route, "params", lambda: {**held, "worker": replace(held["worker"], default=None)})
    assert _plan_of(name, planned, tmp_path) == with_default
    assert "ndf-worker" not in json.dumps(with_default)
