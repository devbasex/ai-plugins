"""MVV 判定のエントリポイント（`ndf records mvv gate`。#3959）。プランが組む入力の形・プランの外のエントリポイント・`--expect-pr`。

判定そのもの（進め方と区分で省くか・材料・記録）は `test_mvv_gate` が確かめる。ここは判定に渡る入力を誰がどの形で組むかを確かめる。
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import git_sandbox
import pytest
import sqlalchemy as sa

from ndf import contracts
from ndf.domains.records import cli, mvv_gate
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress import command, definition, invoke, model, stage_inputs, store

from . import test_mvv_gate as base
from ._mvv_gate_records import failures as _failures
from .test_mvv_gate import DOCS, FOLLOW, HEAD, SHA, Ask, Hub, _hub, _stopped

# 設計のブランチの作業場所と、PR のコメントへ書く判定の記録（`test_mvv_gate` の fixture）
repo = base.repo
notes = base.notes


# --- #3959 単位 1: 今の判定の振る舞いを縛る（Task 1）・工程の入力の形（Task 2）・プランの外のエントリポイント（Task 3） ---


def _plan_inputs(repo: Path, **context: Any) -> dict[str, Any]:
    """プランが NDF_STAGE_INPUT に入れる入力（文脈から `resolve_inputs` で組んだ平らな object）。"""
    full = {"issues": [3959], "worktree": str(repo), "mode": "standard", "pace": "fast", "mvv_ref": SHA, "design_pr": 9, **context}
    return invoke.resolve_inputs(definition.load_definition(str(full["mode"])).stage("設計"), full)


def test_the_flat_plan_input_keeps_todays_outcome(repo: Path, notes: list[tuple[int, str]]) -> None:
    # Task 1: プランが組む平らな入力で、今の形の mvv_gate を返す（fast で検査を通り「従う」で、保留が無ければ省く）
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo), _hub(5, DOCS), Ask(FOLLOW))
    assert (out["gate"], out["skip"], out["why"], out["verdict"]["verdict"], out["head"]) == ("design", True, [], "follow", HEAD)
    assert [n for n, _ in notes] == [5]


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
    # 故障は stage_inputs_of で発生させるので、リポジトリは Git で作らず、CLI のリポジトリの解決も差し替える
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
    monkeypatch.setattr(cli, "_hub", _hub(5, DOCS))
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
    # 今回のコメントの URL だけが違う（どちらの起動もコメントを 1 件書く）
    same = [{**out["outputs"]["mvv_gate"], "note_url": None} for out in (by_machine, by_env)]
    assert same[0] == same[1] and by_env["outputs"]["mvv_gate"]["skip"] is True
    assert [n for n, _ in notes] == [5, 5]


def test_the_design_pr_is_the_open_pr_of_the_worktree_branch_in_this_repository(repo: Path, tmp_path: Path) -> None:
    # 作業場所が別のリポジトリなら、そのブランチ名で PR を探さない
    other = tmp_path / "other"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "design/x", str(other))
    git_sandbox.sh(other, "commit", "-q", "--allow-empty", "-m", "x")
    ask = Ask(FOLLOW)
    out = mvv_gate.gate(repo, "design", _plan_inputs(repo, worktree=str(other)), _hub(5, {"body": "変更", "files": []}), ask)
    assert out["verdict"] is None and _stopped(out) == "no_pr" and ask.calls == []
    assert "worktree でない" in _failures()[0]["cause"] and "worktree でない" not in out["why"][0]
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
    # 作業場所とリポジトリの根（root）が同じパスなら、共通ディレクトリを Git に問わない
    def no_git(where: Path) -> str:
        raise AssertionError("同じパスで共通ディレクトリを問うた")

    monkeypatch.setattr(mvv_gate, "_common_dir", no_git)
    monkeypatch.setattr(mvv_gate, "Git", _BranchOnlyGit)
    assert mvv_gate.pr_number(tmp_path, "design", _plan_inputs(tmp_path, worktree=str(tmp_path)), Hub(5, {})) == 5


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


def test_cli_writes_the_outcome_as_the_step_output(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "_hub", _hub(5, DOCS))
    monkeypatch.setattr(mvv_gate, "gate", partial(mvv_gate.gate, ask=Ask(FOLLOW)))
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(_plan_inputs(repo, pace="normal")))
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["outputs"]["mvv_gate"]["skip"] is False
    assert out["outputs"]["mvv_gate"]["verdict"]["verdict"] == "follow"
    monkeypatch.delenv("NDF_STAGE_INPUT")
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) != 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"
