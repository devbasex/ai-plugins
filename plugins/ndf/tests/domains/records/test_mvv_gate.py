"""承認ゲートの直前の MVV 判定（`ndf records mvv gate`。#3745・#3017）。LLM のアダプターはスタブにする。

#2213・#2704 の再現: どちらもレッドライン（#2213 は C2・C4、#2704 は C3・C4）に当たる設計で、判定の指示文の変更の後に
boundary 空の「従う」になって承認ゲートを省けた（#3017）。ここでは、判定の材料にレッドラインの一覧（共通原則の C1〜C8 と
プロジェクト MVV の P の行）が載り、判定がそれを boundary に挙げれば承認ゲートを省かないことを確かめる。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import git_sandbox
import pytest

from ndf import contracts, identity
from ndf.adapters.github import FileAt
from ndf.adapters import git, github, llm
from ndf.contracts import causes
from ndf.contracts.internal import InternalError
from ndf.domains.records import cli, jsonfile, jsonl, mvv, mvv_gate
from ndf.core import merge_approval
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


# 判定した PR の承認の対象（Hub の PR 5 番・main 宛て・自動反映。#4409）
_TARGET = {"repo": "o/r", "pr": 5, "base": "main", "head": "a" * 40, "production": True}


class Hub:
    """GitHub の偽物。設計のブランチ design/x の開いた PR と、その本文・変えたファイルを返す。"""

    repo = "o/r"

    def __init__(self, number: int, case: dict[str, Any]) -> None:
        self.number, self.case = number, case

    def pulls_by_head(self, branches: list[str]) -> dict[str, list[SimpleNamespace]]:
        return {b: [SimpleNamespace(number=self.number, state="open")] if b == "design/x" else [] for b in branches}

    def pull(self, number: int) -> SimpleNamespace:
        assert number == self.number
        return SimpleNamespace(body=self.case["body"], head_sha=self.case.get("head", "h" * 40), base_ref="main", number=number)

    def branch_file(self, base: str, path: str) -> FileAt:
        """マージ先 main の設定（自動反映の本番チャネル。#4409）。"""
        section = {
            "branches": {"base": "main", "production": "main"},
            "release": {"deliveries": [{"channel": "production", "kind": "auto"}]},
        }
        return FileAt("o/r", json.dumps({"branch_strategy": section}), "c1")

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
    # 省いた判定の記録は、担い手 mvv のマージの承認の証跡の行を同じコメントに持つ（#4409 の決定 7。投稿は 1 件のまま）
    hub = SimpleNamespace(comments=lambda n: [github.Authored("octocat", "OWNER", text)])
    (row,) = merge_approval.merge_rows(hub, 5, "octocat")  # type: ignore[arg-type]
    assert (row.answer, row.by, row.target, row.production) == (
        "approved",
        "mvv",
        merge_approval.MergeTarget("o/r", 5, "main", "h" * 40),
        True,
    )
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
        (_inputs(), lambda root: SimpleNamespace(pulls_by_head=lambda b: {b[0]: []}), Ask(FOLLOW), "開いた設計の PR が無い"),
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
    assert out == {"gate": "design", "skip": False, "verdict": None, "why": [why], "head": "a" * 40, "target": _TARGET}
    assert notes == [(5, note("design", None, [why]))]


def test_a_verdict_that_keeps_the_gate_returns_the_verdict_and_the_head(repo: Path, notes: list[tuple[int, str]]) -> None:
    case = {"body": "変更", "files": [], "head": "a" * 40}
    verdict = DESIGN_2213["verdict"]
    out = mvv_gate.gate(repo, "design", _inputs(), _hub(5, case), Ask(verdict))
    why = ["レッドラインに当たる: " + " / ".join(verdict["boundary"])]
    assert out == {"gate": "design", "skip": False, "verdict": verdict, "why": why, "head": "a" * 40, "target": _TARGET}
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
    monkeypatch.setenv("NDF_STAGE_INPUT", json.dumps(_inputs(pace="normal")))
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["outputs"]["mvv_gate"]["skip"] is False
    monkeypatch.delenv("NDF_STAGE_INPUT")
    assert cli.main(["--cwd", str(repo), "mvv", "gate", "--gate", "design"]) != 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["cause"]["kind"] == "invalid"
