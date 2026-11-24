"""承認ゲートの直前の MVV 判定（`ndf records mvv gate`。#3745・#3017）。LLM のアダプターはスタブにする。

#2213・#2704 の再現: どちらもレッドライン（#2213 は C2・C4、#2704 は C3・C4）に当たる設計で、判定の指示文の変更の後に
boundary 空の「従う」になって承認ゲートを省けた（#3017）。ここでは、判定の材料にレッドラインの一覧（共通原則の C1〜C8 と
プロジェクト MVV の P の行）が載り、判定がそれを boundary に挙げれば承認ゲートを省かないことを確かめる。
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import git_sandbox
import pytest
import sqlalchemy as sa

from ndf import contracts, identity
from ndf.adapters import git, github, llm
from ndf.contracts import causes
from ndf.contracts.internal import InternalError
from ndf.domains.records import cli, jsonfile, jsonl, mvv, mvv_gate
from ndf.progress import command, definition, invoke, model, stage_inputs, store
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress.gate.checks import note, refusals

BODY = "# MVV\n\n## 必ず人の承認が要る操作\n\n| # | 操作 | 理由 |\n| --- | --- | --- |\n| P1 | リリース対象の取得元・バージョンの規則を変える | 利用者全員に効く |\n"
SHA = hashlib.sha256(BODY.encode("utf-8")).hexdigest()
FOLLOW = {"verdict": "follow", "reasons": ["Value 1 に沿う"], "boundary": [], "basis": ["Value 1"]}
# #2213: 承認ゲートの証跡と、本番・マージへ進める認可の経路を変える設計
DESIGN_2213 = {
    "body": "承認ゲートの後と止まった後を、キューと待ちだけで流す設計",
    "files": [
        {"filename": "issues/issue-1426-design.md", "status": "added", "patch": "+承認ゲートの記録を書かずにマージへ進める経路を足す"}
    ],
    "verdict": {
        "verdict": "follow",
        "reasons": ["Value 3 に沿う"],
        "boundary": ["C2: マージへ進める認可の経路を変える", "C4: 証跡を消す"],
        "basis": ["C2", "C4"],
    },
}
# #2704: 耐久の記録を退避し 30 日で削除する設計
DESIGN_2704 = {
    "body": "耐久ワークフローや耐久の記録を失っても、計画のジャーナルから start 1 回で続ける設計",
    "files": [{"filename": "issues/issue-2647-design.md", "status": "added", "patch": "+耐久の記録を退避し、30 日で削除する"}],
    "verdict": {
        "verdict": "follow",
        "reasons": ["Value 6 に沿う"],
        "boundary": ["C3: 利用者の記録を移す", "C4: 30 日で削除する"],
        "basis": ["C3", "C4"],
    },
}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    root = tmp_path / "main"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "design/x", str(root))
    (root / ".ndf").mkdir()
    (root / ".ndf" / "mvv.md").write_text(BODY, encoding="utf-8")
    (root / ".ndf" / "mvv.json").write_text(json.dumps({"body": "mvv.md", "versions": [{"version": 1, "sha256": SHA}]}), encoding="utf-8")
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "mvv")
    return root


@pytest.fixture(autouse=True)
def notes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """PR のコメントへ書く判定の記録（GitHub へは書かず、番号と本文を残す）。"""
    posted: list[tuple[int, str]] = []
    monkeypatch.setattr(mvv_gate, "post_note", lambda hub, number, text: posted.append((number, text)))
    return posted


class Hub:
    """GitHub の偽物。設計のブランチ design/x の開いた PR と、その本文・変えたファイルを返す。"""

    repo = "o/r"

    def __init__(self, number: int, case: dict[str, Any]) -> None:
        self.number, self.case = number, case

    def open_pulls_of_head(self, branch: str, enough: int) -> list[SimpleNamespace]:
        # 本物と同じく、開いた同じリポジトリの PR を `enough` 件まで返す
        opened = [SimpleNamespace(number=self.number, state="open", cross_repository=False)]
        pulls = list(self.case.get("pulls", opened)) if branch == "design/x" else []
        return [p for p in pulls if p.state == "open" and not p.cross_repository][:enough]

    def pull(self, number: int) -> SimpleNamespace:
        assert number == self.number
        return SimpleNamespace(body=self.case["body"], head_sha=self.case.get("head", "h" * 40))

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> list[dict]:
        assert path == f"/repos/o/r/pulls/{self.number}/files"
        return self.case["files"]


class Ask:
    """LLM のアダプターのスタブ。質問・材料・スキーマを残し、渡した回答を返す。"""

    def __init__(self, answer: Any, ok: bool = True) -> None:
        self.answer, self.ok = answer, ok
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, question: str, material: str, schema: dict, timeout: float) -> llm.JudgeAnswer:
        self.calls.append((question, material, schema))
        if not self.ok:
            return llm.JudgeAnswer(False, None, causes.cause("needs_human", "形が合わない"), 2, 0.2)
        assert not llm.check_answer(self.answer, schema)
        return llm.JudgeAnswer(True, self.answer, None, 1, 0.1)


def _inputs(**extra: Any) -> dict[str, Any]:
    return {"mode": "standard", "pace": "fast", "mvv_ref": SHA, **extra}


def _hub(number: int, case: dict[str, Any]):
    return lambda root: Hub(number, case)


def _no_hub(root: Path) -> Hub:
    raise AssertionError("判定を打たないときは GitHub を読まない")


def test_normal_pace_asks_a_human_without_judging(repo: Path) -> None:
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(pace="normal"), _no_hub, ask)
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": ["進め方 normal は承認ゲートを MVV 判定で省かない"]}
    assert ask.calls == []


@pytest.mark.parametrize("mode", ["light", "operation"])
def test_a_mode_without_the_design_gate_is_not_judged(repo: Path, mode: str) -> None:
    # 設計承認ゲートを持たないモードでも判定のステップは流れ、出力をそろえるが判定は打たない
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(mode=mode), _no_hub, ask)
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": [f"モード {mode} は承認ゲート design を持たない"]}
    assert ask.calls == []
    assert mvv_gate.has_gate(mode, "release") and mvv_gate.has_gate("standard", "design")


def test_follow_without_redlines_skips_and_the_record_is_reused(repo: Path, notes: list[tuple[int, str]]) -> None:
    case = {"body": "文書の誤字を直す", "files": [{"filename": "docs/a.md", "status": "modified", "patch": "-誤字\n+正字"}]}
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, case), ask)
    assert (out["skip"], out["why"], out["verdict"]["boundary"], out["head"]) == (True, [], [], "h" * 40)
    # 承認ゲートを省いた判定も、理由を PR のコメントへ残す
    ((number, text),) = notes
    assert number == 5 and "## MVV 判定" in text and "Value 1 に沿う" in text
    # 同じ材料の「従う」は記録を再利用し、理由とレッドラインも引き継ぐ
    again = mvv_gate.gate(repo, "design", _inputs(), _hub(5, case), ask)
    assert again == out and len(ask.calls) == 1
    rows, _ = jsonl.read_rows(mvv.record_path("mvv-validation"), kind="mvv-validation")
    assert [r["boundary"] for r in rows] == [[], []] and "reused_from" in rows[1]


@pytest.mark.parametrize(("number", "gate", "case"), [(2213, "design", DESIGN_2213), (2704, "release", DESIGN_2704)])
def test_redlines_in_the_verdict_keep_the_gate_2213_2704(
    repo: Path, number: int, gate: str, case: dict[str, Any], notes: list[tuple[int, str]]
) -> None:
    ask = Ask(case["verdict"])
    inputs = _inputs(pr=number) if gate == "release" else _inputs()
    out = mvv_gate.gate(repo, gate, inputs, _hub(number, case), ask)
    assert out["skip"] is False and out["why"] == ["レッドラインに当たる: " + " / ".join(case["verdict"]["boundary"])]
    # 省かなかった判定も、レッドラインと省かない理由を PR のコメントへ残す
    ((posted, text),) = notes
    assert posted == number and case["verdict"]["boundary"][0] in text and "承認ゲートを省かない理由" in text
    ((question, material, schema),) = ask.calls
    # 判定の材料はレッドラインの一覧（共通原則の C1〜C8 とプロジェクト MVV の P1）を先に置き、PR の本文と差分を載せる
    assert question == mm.SYSTEM
    redlines = [f"- C{i}:" for i in range(1, 9)] + ["- P1: リリース対象の取得元・バージョンの規則を変える"]
    assert all(r in material for r in redlines) and material.index("- P1:") < material.index("## NDF の共通原則")
    assert case["body"] in material and case["files"][0]["patch"] in material
    assert schema == contracts.load_json(contracts.schema_path(mvv_gate.VERDICT_SCHEMA))


def test_a_follow_with_an_empty_boundary_is_judged_but_the_machine_checks_come_first(repo: Path) -> None:
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(mvv_ref="0" * 64), _no_hub, ask)
    assert out["skip"] is False and "スプリントの状態の参照" in out["why"][0] and ask.calls == []
    # レッドラインのパスを変える PR は、判定が従う・boundary 空でも省かない（検査 4）
    (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "boundary_paths": ["docs/**"]}), encoding="utf-8")
    case = {"body": "文書", "files": [{"filename": "docs/a.md", "status": "modified"}]}
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, case), ask)
    assert out["skip"] is False and out["why"] == ["レッドラインのパスに当たる: docs/a.md"]
    # PR を読んで決まる検査で止まるときも LLM を呼ばない
    assert ask.calls == []


@pytest.mark.parametrize(
    "files",
    [
        [{"filename": "src/a.py", "status": "modified", "patch": "\n".join(f"+行 {i}" for i in range(mm.PATCH_LINES + 1))}],
        [{"filename": "src/a.py", "status": "modified", "changes": 3}],
    ],
)
def test_a_diff_that_cannot_be_shown_whole_keeps_the_gate_without_judging(
    repo: Path, files: list[dict[str, Any]], notes: list[tuple[int, str]]
) -> None:
    # 差分を省いた部分（認可やデータの削除があり得る）は判定できないため、LLM を呼ばずに人へ問う
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, {"body": "変更", "files": files}), ask)
    assert out["skip"] is False and out["why"] == ["差分の全体を材料に載せられない: src/a.py"] and ask.calls == []
    assert [n for n, _ in notes] == [5]
    # 中身を変えない改名は patch を持たないが、省いた部分が無い
    assert mm.incomplete_patches([{"filename": "b.py", "status": "renamed", "changes": 0}]) == []


@pytest.mark.parametrize(
    ("inputs", "hub", "ask", "why"),
    [
        (_inputs(), _hub(5, {"body": "", "files": []}), Ask(None, ok=False), "MVV 判定の回答が無い"),
        (_inputs(), lambda root: SimpleNamespace(open_pulls_of_head=lambda b, enough: []), Ask(FOLLOW), "開いた設計の PR が無い"),
    ],
)
def test_a_verdict_that_cannot_be_had_asks_a_human(repo: Path, inputs: dict, hub: Any, ask: Ask, why: str) -> None:
    out = mvv_gate.gate(repo, "design", inputs, hub, ask)
    assert out["skip"] is False and out["verdict"] is None and why in out["why"][0]


def test_a_record_of_a_newer_version_stops_instead_of_asking_a_human(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def newer(*args: Any) -> dict:
        raise InternalError("precondition", "新しいバージョンの記録")

    monkeypatch.setattr(mvv_gate, "_judged", newer)
    with pytest.raises(InternalError):
        mvv_gate.gate(tmp_path, "design", _inputs(), _no_hub, Ask(FOLLOW))


def _raising(exc: Exception) -> Any:
    def open_hub(root: Path) -> Hub:
        raise exc

    return open_hub


@pytest.mark.parametrize(
    "exc",
    [
        mvv_gate.GateError("理由"),
        git.GitError("理由"),
        github.GitHubError("理由", 500),
        mvv.MvvError("理由"),
        jsonfile.JsonFileError("理由"),
        identity.IdentityError("理由"),
        OSError("理由"),
    ],
)
def test_a_failure_before_reading_the_pr_is_the_whole_outcome_without_the_head(
    repo: Path, exc: Exception, notes: list[tuple[int, str]]
) -> None:
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "release", _inputs(pr=5), _raising(exc), ask)
    assert out == {"gate": "release", "skip": False, "verdict": None, "why": [f"MVV 判定を打てない: {exc}"]}
    assert ask.calls == [] and notes == []


def test_an_unlisted_failure_is_not_turned_into_an_outcome(repo: Path) -> None:
    with pytest.raises(KeyError):
        mvv_gate.gate(repo, "design", _inputs(), _raising(KeyError("x")), Ask(FOLLOW))


def test_machine_checks_before_the_pr_return_every_reason_without_the_head_or_a_note(repo: Path, notes: list[tuple[int, str]]) -> None:
    out = mvv_gate.gate(repo, "design", _inputs(mvv_ref=None), _no_hub, Ask(FOLLOW))
    facts, _ = mvv_gate.base_facts(repo, _inputs(mvv_ref=None))
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": refusals(facts)} and out["why"]
    assert notes == []


@pytest.mark.parametrize(
    ("files", "why"),
    [
        ([{"filename": "docs/a.md", "status": "modified"}], "レッドラインのパスに当たる: docs/a.md"),
        ([{"filename": "src/a.py", "status": "modified", "changes": 3}], "差分の全体を材料に載せられない: src/a.py"),
    ],
)
def test_checks_after_reading_the_pr_return_the_head_and_leave_a_note(
    repo: Path, files: list[dict[str, Any]], why: str, notes: list[tuple[int, str]]
) -> None:
    (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "boundary_paths": ["docs/**"]}), encoding="utf-8")
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, {"body": "変更", "files": files, "head": "a" * 40}), Ask(FOLLOW))
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": [why], "head": "a" * 40}
    assert notes == [(5, note("design", None, [why]))]


def test_a_verdict_that_keeps_the_gate_returns_the_verdict_and_the_head(repo: Path, notes: list[tuple[int, str]]) -> None:
    case = {"body": "変更", "files": [], "head": "a" * 40}
    verdict = DESIGN_2213["verdict"]
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, case), Ask(verdict))
    why = ["レッドラインに当たる: " + " / ".join(verdict["boundary"])]
    assert out == {"gate": "design", "skip": False, "verdict": verdict, "why": why, "head": "a" * 40}
    assert notes == [(5, note("design", verdict, why))]


def test_a_note_that_cannot_be_posted_turns_a_skip_into_an_outcome_without_the_verdict_or_the_head(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(hub: Any, number: int, text: str) -> None:
        raise github.GitHubError("書けない", 403)

    monkeypatch.setattr(mvv_gate, "post_note", fail)
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, {"body": "変更", "files": []}), ask)
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": ["MVV 判定を打てない: 書けない"]} and len(ask.calls) == 1


def test_the_mode_without_the_gate_is_told_before_the_pace(tmp_path: Path) -> None:
    out = mvv_gate.gate(tmp_path, "design", {"pace": "normal"}, _no_hub, Ask(FOLLOW))
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": ["モード None は承認ゲート design を持たない"]}
    out = mvv_gate.gate(tmp_path, "design", {"mode": "standard"}, _no_hub, Ask(FOLLOW))
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": ["進め方 None は承認ゲートを MVV 判定で省かない"]}


def test_the_release_gate_needs_the_pr_in_the_context(repo: Path) -> None:
    out = mvv_gate.gate(repo, "release", _inputs(), _no_hub_release, Ask(FOLLOW))
    assert out["skip"] is False and "pr" in out["why"][0]


def _no_hub_release(root: Path) -> SimpleNamespace:
    return SimpleNamespace()


def test_cli_writes_the_outcome_as_the_step_output(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(_plan_inputs(repo, pace="normal")))
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["outputs"]["mvv_gate"]["skip"] is False
    monkeypatch.delenv("NDF_STAGE_INPUT")
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) != 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"


# --- #3959 単位 1: 今の判定の振る舞いを縛る（Task 1）・工程の入力の形（Task 2）・プランの外のエントリポイント（Task 3） ---


def _plan_inputs(repo: Path, **context: Any) -> dict[str, Any]:
    """プランが NDF_STAGE_INPUT に入れる入力（文脈から `resolve_inputs` で組んだ平らな object）。"""
    full = {"issues": [3959], "worktree": str(repo), "mode": "standard", "pace": "fast", "mvv_ref": SHA, "design_pr": 9, **context}
    return invoke.resolve_inputs(definition.load_definition(str(full["mode"])).stage("設計"), full)


def test_the_flat_plan_input_keeps_todays_outcome(repo: Path, notes: list[tuple[int, str]]) -> None:
    # Task 1: プランが組む平らな入力で、今の形の mvv_gate を返す（fast で検査を通り「従う」なら省く）
    case = {"body": "文書の誤字を直す", "files": [{"filename": "docs/a.md", "status": "modified", "patch": "-誤字\n+正字"}]}
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo), _hub(5, case), Ask(FOLLOW))
    assert (out["gate"], out["skip"], out["why"], out["verdict"]["verdict"], out["head"]) == ("design", True, [], "follow", "h" * 40)
    assert [n for n, _ in notes] == [5]


def test_a_repository_without_an_approved_mvv_keeps_a_null_verdict(repo: Path) -> None:
    # Task 1・前提 2: 承認済みの MVV が無いリポジトリでは verdict: null のまま人へ問う
    git_sandbox.sh(repo, "rm", "-q", ".ndf/mvv.json")
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo, mvv_ref=None), _no_hub, ask)
    assert out["skip"] is False and out["verdict"] is None and ask.calls == []


def test_the_gate_steps_are_steps_of_the_named_stage_documents() -> None:
    for gate_name, step_id in mvv_gate.GATE_STEPS.items():
        doc = contracts.load_json(f"workflow/stages/{mvv_gate.GATE_STAGES[gate_name]}.json")
        assert step_id in [name for task in doc["do"] for name in task]


def test_env_inputs_returns_the_plan_input_unchanged(repo: Path) -> None:
    given = _plan_inputs(repo)
    assert cli._env_inputs("design", given) == given


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"unknown": 1}, "invalid"),
        ({"design_pr": 4}, "invalid"),
        ({"pace": "slow"}, "invalid"),
        ({"mode": None}, "input_missing"),
    ],
)
def test_env_inputs_refuses_inputs_of_another_shape(repo: Path, change: dict[str, Any], kind: str) -> None:
    given = {**_plan_inputs(repo), **change}
    if change.get("mode", "") is None:
        given.pop("mode")
    with pytest.raises(cli._InputError) as caught:
        cli._env_inputs("design", given)
    assert caught.value.kind == kind


@pytest.mark.parametrize("given", [{"context": {"issues": [3959], "mode": "standard", "pace": "normal"}}, [1, 2]])
def test_env_inputs_refuses_a_nested_context_and_a_non_object_as_invalid(given: Any) -> None:
    # {"context": {...}} は最上位に mode が無く、規約の誤りと足りないキーの両方に当たるが、規約の誤りが先で invalid
    with pytest.raises(cli._InputError) as caught:
        cli._env_inputs("design", given)
    assert caught.value.kind == "invalid"


def _cli_gate(repo: Path, capsys: pytest.CaptureFixture[str], gate_name: str, *extra: str) -> dict[str, Any]:
    cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", gate_name, *extra])
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


@pytest.mark.parametrize("gate_name", ["design", "release"])
def test_the_cli_takes_the_input_from_exactly_one_source(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], gate_name: str
) -> None:
    # 受け入れ条件 8・9: 環境変数だけ・--machine だけ・両方・どちらも無しの 4 行を、区分ごとに確かめる
    seen: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        cli.mvv_gate,
        "gate",
        lambda root, g, inputs, hub, warnings: seen.append((g, dict(inputs))) or mm.gate_outcome(g, None, False, ["x"]),
    )
    from_machine = (
        {"pr": 5, "mode": "standard", "pace": "normal", "mvv_ref": SHA} if gate_name == "release" else _plan_inputs(repo, pace="normal")
    )
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", lambda root, mid, step: asked.append((mid, step)) or from_machine)
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    # どちらも無し
    none = _cli_gate(repo, capsys, gate_name)
    assert (none["status"], none["cause"]["kind"]) == ("stopped", "invalid") and "--machine" in none["summary"]
    # --machine だけ
    assert _cli_gate(repo, capsys, gate_name, "--machine", "sprint:x")["status"] == "ok"
    assert asked == [("sprint:x", mvv_gate.GATE_STEPS[gate_name])] and seen[-1] == (gate_name, from_machine)
    # 環境変数だけ
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(from_machine))
    assert _cli_gate(repo, capsys, gate_name)["status"] == "ok" and seen[-1] == (gate_name, from_machine)
    # 両方
    both = _cli_gate(repo, capsys, gate_name, "--machine", "sprint:x")
    assert (both["status"], both["cause"]["kind"]) == ("stopped", "invalid") and len(seen) == 2


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (lambda: model.Refused("ステートマシン sprint:x は無い"), "refused"),
        (lambda: invoke.StageInputMissing(["pr"]), "input_missing"),
        (lambda: invoke.StageInputInvalid(["pace: 形が違う"]), "invalid"),
    ],
)
def test_the_cli_stops_without_judging_when_the_machine_gives_no_input(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: Any, kind: str
) -> None:
    def fail(*_a: Any) -> dict[str, Any]:
        raise exc()

    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", fail)
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("入力を組めないのに判定を打った"))
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    out = _cli_gate(repo, capsys, "release", "--machine", "sprint:x")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", kind)


def test_a_db_error_while_reading_the_machine_shows_only_its_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 共有 DB への照会の失敗は、SQL 文・バインド値・接続先を結果へ載せず、例外の種類だけを出す（ndf progress と同じ）。
    # 故障は stage_inputs_of で起こすので、リポジトリは Git で作らず、CLI のリポジトリの解決も差し替える
    def fail(*_a: Any) -> dict[str, Any]:
        raise sa.exc.ProgrammingError("SELECT * FROM ndf_progress_machines WHERE id = ?", ("secret-bind",), Exception("db.internal"))

    monkeypatch.setattr(cli, "_root", lambda args: tmp_path)
    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", fail)
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("入力を組めないのに判定を打った"))
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    out = _cli_gate(tmp_path, capsys, "release", "--machine", "sprint:x")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", store.StoreUnreachable.kind)
    shown = json.dumps(out, ensure_ascii=False)
    assert "SELECT" not in shown and "secret-bind" not in shown and "db.internal" not in shown


def test_the_cli_refuses_a_nested_context_in_the_environment_without_judging(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 受け入れ条件 9 の例（11.0.22 で status: ok・判定なしを返した入力）
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("形の違う入力で判定を打った"))
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps({"context": {"pace": "normal", "mode": "standard", "issues": [3959]}}))
    out = _cli_gate(repo, capsys, "design")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "invalid")
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps({k: v for k, v in _plan_inputs(repo).items() if k != "mode"}))
    out = _cli_gate(repo, capsys, "design")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "input_missing")


def test_both_entrypoints_judge_the_same_pr_with_the_same_input(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], notes: list[tuple[int, str]]
) -> None:
    # 受け入れ条件 8: 同じステートマシンの文脈で、NDF_STAGE_INPUT と --machine が同じ入力・同じ PR で同じ mvv_gate を返す
    monkeypatch.chdir(repo)
    assert command.main(["new", "s1", "--mode", "standard", "--issues", "3959", "--pace", "fast"]) == 0
    tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
    out_file = repo.parent / "outputs.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "用意した", "outputs": {"worktree": str(repo), "branch": "design/x"}}))
    assert command.main(["finish", tid, "作業場所の用意", "--output", str(out_file), "--reason", "手で"]) == 0
    # 文脈の design_pr は判定する PR と違っても読まない（両方とも worktree のブランチの PR を判定する）
    out_file.write_text(
        json.dumps({"steps": [], "summary": "設計", "outputs": {"design_pr": 99, "mvv_gate": mm.gate_outcome("design", None, False, [])}})
    )
    assert command.main(["finish", tid, "設計", "--output", str(out_file), "--reason", "手で"]) == 0
    capsys.readouterr()
    case = {"body": "文書の誤字を直す", "files": [{"filename": "docs/a.md", "status": "modified", "patch": "-誤字\n+正字"}]}
    monkeypatch.setattr(cli, "_hub", _hub(5, case))
    seen: list[dict[str, Any]] = []
    real = mvv_gate.gate

    def judged(root: Path, gate_name: str, inputs: Any, hub: Any, warnings: Any) -> dict[str, Any]:
        seen.append(dict(inputs))
        return real(root, gate_name, inputs, hub, Ask(FOLLOW), warnings)

    monkeypatch.setattr(cli.mvv_gate, "gate", judged)
    by_machine = _cli_gate(repo, capsys, "design", "--machine", tid)
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(seen[0]))
    by_env = _cli_gate(repo, capsys, "design")
    assert seen[0] == seen[1] and "design_pr" not in seen[0]
    assert by_machine["outputs"] == by_env["outputs"] and by_env["outputs"]["mvv_gate"]["skip"] is True
    assert [n for n, _ in notes] == [5, 5]


def test_the_design_pr_is_the_open_pr_of_the_worktree_branch_in_this_repository(repo: Path, tmp_path: Path) -> None:
    # 作業場所が別のリポジトリなら、そのブランチ名で PR を探さない
    other = tmp_path / "other"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "design/x", str(other))
    git_sandbox.sh(other, "commit", "-q", "--allow-empty", "-m", "x")
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo, worktree=str(other)), _hub(5, {"body": "変更", "files": []}), ask)
    assert out["verdict"] is None and "worktree でない" in out["why"][0] and ask.calls == []
    # フォークの同名ブランチは数えず、同じリポジトリの開いた PR が 2 件なら決めない
    fork = SimpleNamespace(number=7, state="open", cross_repository=True)
    mine = SimpleNamespace(number=5, state="open", cross_repository=False)
    case = {"body": "変更", "files": [], "pulls": [fork, mine]}
    assert mvv_gate.pr_number(repo, "design", _plan_inputs(repo), Hub(5, case)) == 5
    twice = {**case, "pulls": [mine, SimpleNamespace(number=6, state="open", cross_repository=False)]}
    with pytest.raises(mvv_gate.GateError, match="複数"):
        mvv_gate.pr_number(repo, "design", _plan_inputs(repo), Hub(5, twice))


class _BranchOnlyGit:
    """ブランチ名だけを返す Git の偽物（子プロセスを起動しない）。ほかの問い合わせはテストを落とす。"""

    def __init__(self, where: Path) -> None:
        self.where = where

    def run(self, *args: str) -> str:
        assert args == ("rev-parse", "--abbrev-ref", "HEAD"), f"想定しない Git の呼び出し: {args}"
        return "design/x\n"


def test_a_worktree_at_the_same_path_is_not_checked_with_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 作業場所と起動元が同じパスなら、共通ディレクトリを Git に問わない
    def no_git(where: Path) -> str:
        raise AssertionError("同じパスで共通ディレクトリを問うた")

    monkeypatch.setattr(mvv_gate, "_common_dir", no_git)
    monkeypatch.setattr(mvv_gate, "Git", _BranchOnlyGit)
    assert mvv_gate.pr_number(tmp_path, "design", _plan_inputs(tmp_path, worktree=str(tmp_path)), Hub(5, {})) == 5


# --- #3959 Task 9: 失敗の原因を定型文と手元の診断の記録に分ける ---

HOST = "git.corp-internal.example.jp"


def _failures() -> list[dict[str, Any]]:
    rows, broken = jsonl.read_rows(mvv.record_path("mvv-gate-failures"), kind="mvv-gate-failure")
    assert not broken
    return rows


def test_failure_text_keeps_the_exception_only_in_the_local_record(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_TEST_TOKEN", "zz-fake-secret-77")
    exc = git.GitError(f"fetch https://x-access-token:zz-fake-secret-77@{HOST}/team/repo.git に失敗した")
    got = mvv_gate.failure_text("base_unreadable", exc, {"gate": "design", "pr": 5, "root": repo})
    (row,) = _failures()
    assert got == f"{mm.STOP_REASONS['base_unreadable']}（診断の記録 {row['id']}）"
    assert HOST not in got and "team/repo" not in got and "zz-fake" not in got
    assert (row["kind"], row["gate"], row["pr"], row["head"], row["repo"]) == ("base_unreadable", "design", 5, None, mvv.repo_key(repo))
    assert HOST in row["cause"] and "zz-fake-secret-77" not in row["cause"]


def test_failure_text_writes_a_new_row_each_time_and_keeps_an_empty_cause(repo: Path) -> None:
    first = mvv_gate.failure_text("llm_no_answer", "", {"gate": "release"})
    second = mvv_gate.failure_text("llm_no_answer", "", {"gate": "release"})
    rows = _failures()
    assert first != second and [r["cause"] for r in rows] == ["", ""] and len({r["id"] for r in rows}) == 2


def test_failure_text_does_not_raise_when_the_record_cannot_be_written(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("ファイルなのでディレクトリにできない")
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(blocked / "state"))
    got = mvv_gate.failure_text("diff_unreadable", OSError(f"/home/someone/{HOST}"), {})
    assert got == f"{mm.STOP_REASONS['diff_unreadable']}（診断の記録に書けない）"


def test_every_stop_reason_is_a_fixed_sentence_without_values_from_the_pr() -> None:
    assert set(mm.STOP_REASONS) == {
        "no_gate", "no_pr", "mvv_unapproved", "base_unreadable", "diff_unreadable", "pr_moved", "pr_reread_failed",
        "incomplete_patches", "llm_no_answer", "records_unreadable", "records_unwritable", "note_unwritten",
        "cite_precondition", "cite_lookup_failed",
    }  # fmt: skip
    assert all(text and "{" not in text for text in mm.STOP_REASONS.values())


# --- #3959: 診断の記録は 0600 に限ったファイル記述子へだけ追記する（record_failure） ---


class _Fd:
    """`os.open`・`os.fstat`・`os.fchmod`・`os.write` を、診断の記録のパスとその記述子だけ差し替える（錠のファイルは本物へ通す）。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fail_open: Any = None, st: Any = None, fchmod_error: Any = None) -> None:
        self.path = str(mvv.record_path("mvv-gate-failures"))
        self.opened: list[tuple[int, int]] = []
        self.written: list[int] = []
        real_open, real_fstat, real_fchmod, real_write = os.open, os.fstat, os.fchmod, os.write

        def fake_open(path: Any, flags: int, mode: int = 0o777, **kw: Any) -> int:
            if str(path) != self.path:
                return real_open(path, flags, mode, **kw)
            if fail_open is not None:
                raise fail_open
            fd = real_open(path, flags, mode, **kw)
            self.opened.append((fd, flags))
            return fd

        def fake_fstat(fd: int) -> Any:
            got = real_fstat(fd)
            if st is None or fd not in self.fds():
                return got
            return SimpleNamespace(st_mode=st.get("st_mode", got.st_mode), st_uid=st.get("st_uid", got.st_uid))

        def fake_fchmod(fd: int, mode: int) -> None:
            if fchmod_error is not None and fd in self.fds():
                raise fchmod_error
            real_fchmod(fd, mode)

        def fake_write(fd: int, data: bytes) -> int:
            if fd in self.fds():
                self.written.append(fd)
            return real_write(fd, data)

        for name, fn in (("open", fake_open), ("fstat", fake_fstat), ("fchmod", fake_fchmod), ("write", fake_write)):
            monkeypatch.setattr(os, name, fn)

    def fds(self) -> set[int]:
        return {fd for fd, _ in self.opened}


def _unwritable(got: str) -> bool:
    return got == f"{mm.STOP_REASONS['base_unreadable']}（診断の記録に書けない）"


@pytest.fixture
def umask_022() -> Any:
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_record_failure_creates_the_directory_0700_and_the_file_0600(repo: Path, umask_022: None, monkeypatch: pytest.MonkeyPatch) -> None:
    fd = _Fd(monkeypatch)
    got = mvv_gate.failure_text("base_unreadable", f"fetch {HOST}", {"gate": "design"})
    path = mvv.record_path("mvv-gate-failures")
    assert not _unwritable(got) and HOST in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700 and stat.S_IMODE(path.stat().st_mode) == 0o600
    # 権限を確かめた記述子へ書く（パスを開き直さない）。開くときは FIFO で止まらず、リンクをたどらない
    ((opened, flags),) = fd.opened
    assert fd.written and set(fd.written) == {opened} and flags & os.O_NONBLOCK and flags & os.O_NOFOLLOW


def test_record_failure_narrows_an_existing_wider_file(repo: Path, umask_022: None) -> None:
    path = mvv.record_path("mvv-gate-failures")
    path.parent.mkdir(parents=True)
    path.write_text("")
    path.chmod(0o644)
    mvv_gate.failure_text("base_unreadable", HOST, {})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and HOST in path.read_text(encoding="utf-8")


def _existing(mode: int = 0o644) -> Path:
    path = mvv.record_path("mvv-gate-failures")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("前の行\n")
    path.chmod(mode)
    return path


@pytest.mark.parametrize(
    "stubbed",
    [
        {"fchmod_error": PermissionError(1, "許されない")},
        {"st": {"st_uid": os.geteuid() + 1}},
        {"st": {"st_mode": stat.S_IFIFO | 0o600}},
    ],
)
def test_record_failure_writes_nothing_when_the_file_cannot_be_kept_private(
    repo: Path, monkeypatch: pytest.MonkeyPatch, stubbed: dict[str, Any]
) -> None:
    path = _existing()
    _Fd(monkeypatch, **stubbed)
    got = mvv_gate.failure_text("base_unreadable", HOST, {})
    assert _unwritable(got) and path.read_text(encoding="utf-8") == "前の行\n"


def test_record_failure_does_not_follow_a_symlink(repo: Path, tmp_path: Path) -> None:
    target = tmp_path / "other.txt"
    target.write_text("別のファイル\n")
    path = mvv.record_path("mvv-gate-failures")
    path.parent.mkdir(parents=True)
    path.symlink_to(target)
    assert _unwritable(mvv_gate.failure_text("base_unreadable", HOST, {}))
    assert target.read_text(encoding="utf-8") == "別のファイル\n"


def test_record_failure_does_not_wait_on_a_fifo_without_a_reader(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 本物の FIFO は作らない。読み手の無い FIFO を O_NONBLOCK で開いたときの ENXIO をスタブで返す
    _Fd(monkeypatch, fail_open=OSError(errno.ENXIO, "No such device or address"))
    assert _unwritable(mvv_gate.failure_text("base_unreadable", HOST, {}))


# --- #3959 決定 5: --expect-pr（承認する PR と、判定に使う入力の PR を判定の前に比べる） ---


def _release_machine(repo: Path, capsys: pytest.CaptureFixture[str], pr: int) -> str:
    """文脈の pr を持つステートマシンを作り、ID を返す（リリース承認ゲートの判定の入力を --machine で組める）。"""
    monkey = pytest.MonkeyPatch()
    monkey.chdir(repo)
    try:
        assert command.main(["new", "s1", "--mode", "standard", "--issues", "3959", "--pace", "fast"]) == 0
        tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
        _set_pr(repo, capsys, tid, pr)
    finally:
        monkey.undo()
    return tid


def _set_pr(repo: Path, capsys: pytest.CaptureFixture[str], tid: str, pr: int) -> None:
    monkey = pytest.MonkeyPatch()
    monkey.chdir(repo)
    try:
        out_file = repo.parent / f"pr-{pr}.json"
        out_file.write_text(json.dumps({"steps": [], "summary": "PR を作った", "outputs": {"pr": pr}}))
        assert command.main(["finish", tid, "Pull Request", "--output", str(out_file), "--reason", "手で"]) == 0
        capsys.readouterr()
    finally:
        monkey.undo()


class _UnreadHub(Hub):
    def pull(self, number: int) -> SimpleNamespace:
        raise AssertionError("判定しないのに PR を読んだ")


def _no_judging(monkeypatch: pytest.MonkeyPatch) -> None:
    # 承認する PR と比べるだけなら GitHub へつながない（認証の無い環境でも not_applicable を返す）
    monkeypatch.setattr(cli, "_hub", lambda root: pytest.fail("PR を比べる前に GitHub へつないだ"))
    monkeypatch.setattr(cli.mvv_gate, "gate", lambda *a, **k: pytest.fail("判定する PR が承認する PR と違うのに判定した"))
    monkeypatch.setattr(mvv_gate.llm, "ask", lambda *a, **k: pytest.fail("LLM を呼んだ"))


def test_expect_pr_judges_when_the_input_pr_is_the_pr_to_approve(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tid = _release_machine(repo, capsys, 12)
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    monkeypatch.setattr(cli, "_hub", lambda root: _UnreadHub(12, {}))
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cli.mvv_gate, "gate", lambda root, g, inputs, hub, warnings: seen.append(dict(inputs)) or mm.gate_outcome(g, None, False, ["x"])
    )
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "12")
    assert out["status"] == "ok" and [s["pr"] for s in seen] == [12]


def test_expect_pr_of_another_pr_stops_without_reading_judging_or_commenting(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], notes: list[tuple[int, str]]
) -> None:
    tid = _release_machine(repo, capsys, 12)
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    _no_judging(monkeypatch)
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "34")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "not_applicable") and notes == []
    shown = json.dumps([out["summary"], out.get("warnings", []), out["cause"]], ensure_ascii=False)
    assert "12" not in shown and "34" not in shown
    # 承認する PR を 12 と決めた後、判定の前に文脈の pr が 56 に変わった → 打った時点の入力と比べるので判定しない
    # （文脈の pr を書く工程は 1 回しか終えられないので、打った時点で文脈から組んだ入力をスタブで 56 にする）
    built = {**stage_inputs.stage_inputs_of(repo, tid, mvv_gate.GATE_STEPS["release"]), "pr": 56}
    monkeypatch.setattr(cli.stage_inputs, "stage_inputs_of", lambda root, mid, step: dict(built))
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "12")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "not_applicable") and notes == []
    assert "56" not in out["summary"] and "12" not in out["summary"]


def test_expect_pr_is_invalid_for_the_design_gate(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _no_judging(monkeypatch)
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(_plan_inputs(repo)))
    out = _cli_gate(repo, capsys, "design", "--expect-pr", "5")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "invalid")


def test_expect_pr_without_the_pr_in_the_context_is_input_missing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(repo)
    assert command.main(["new", "s1", "--mode", "standard", "--issues", "3959", "--pace", "fast"]) == 0
    tid = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["machine_id"]
    monkeypatch.delenv("NDF_STAGE_INPUT", raising=False)
    _no_judging(monkeypatch)
    out = _cli_gate(repo, capsys, "release", "--machine", tid, "--expect-pr", "12")
    assert (out["status"], out["cause"]["kind"]) == ("stopped", "input_missing")
