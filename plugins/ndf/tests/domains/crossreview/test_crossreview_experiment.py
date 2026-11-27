"""cross-review の相手役のシートを実験の単位にし、効果指標と起動の行を書く（#2944）。相手役と修正担当は偽物。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf.adapters import agents, claude, claude_choice, model_chooser
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.journal import FindingJournal, experiment_values
from ndf.domains.records import jsonl, usage

from ._repos import Repos
from .test_crossreview_fixer_sessions import SAVED, Sessions, _declare
from .test_crossreview_run import FAIL, Fake, _run, _state

USED = {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0, "output_tokens": 10}


def _major(text: str) -> dict:
    return {"text": text, "severity": "major", "impact": "利用者の経路で処理が止まる", "section": ""}


def _as_codex(spec: agents.Launch, status: agents.AgentStatus) -> agents.AgentStatus:
    """`--json` の起動は、本文を `-o` のファイルへ、使用量の行を標準出力へ書く。"""
    if "--json" in spec.argv:
        answer = Path(spec.argv[spec.argv.index("-o") + 1])
        events = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.completed", "usage": USED}]
        if status.state == agents.AgentState.DONE:
            answer.write_text(spec.paths.out.read_text(encoding="utf-8"), encoding="utf-8")
        spec.paths.out.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return status._replace(seconds=1.5)


class Seated(Fake):
    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        return _as_codex(spec, super().__call__(spec))


class SeatedSessions(Sessions):
    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        return _as_codex(spec, super().__call__(spec))


@pytest.fixture
def outcomes(monkeypatch) -> dict[str, tuple]:
    """値を選ぶ処理と使用量ログの書き込み処理を本物にし、書く効果指標を集める。"""
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    for name in ("NDF_CHARGE_TO", "NDF_LAUNCH_MODEL", "NDF_LAUNCH_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    got: dict[str, tuple] = {}
    monkeypatch.setattr(claude_choice, "launch_chooser", model_chooser)
    monkeypatch.setattr(claude, "launch_recorder", usage)
    monkeypatch.setattr(claude_choice, "report_outcome", lambda aid, quality, detail: got.__setitem__(aid, (quality, detail)))
    return got


def _rows(**wanted: Any) -> list[dict]:
    found = jsonl.read_rows(usage.usage_log_path(), kind="usage")[0]
    return [r for r in found if all(r.get(k) == v for k, v in wanted.items())]


def test_a_converged_review_records_the_share_of_the_seat_and_of_the_charged_unit(
    wt: Path, tmp_path: Path, capsys, monkeypatch, outcomes
) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "design-unit")
    fake = Seated(tmp_path / "st", {"codex": [[_major("一つ目")]], "kiro": [[_major("一つ目"), _major("二つ目")]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    rows = _rows(name="review", runtime="codex")
    assert [(r["caller"], r["layer"], r["outcome"], r["seconds"]) for r in rows] == [("codex-exec", "seat", "done", 1.5)] * 2
    run_id = _state(fake.state_dir)["experiment_units"]["id"]
    assert {r["unit"] for r in rows} == {f"{run_id}:codex"} and len({r["assign_id"] for r in rows}) == 1
    assert rows[0]["effort"] and rows[0]["session_cost_usd"] > 0 and "charge_to" not in rows[0]
    assert outcomes[rows[0]["assign_id"]][0] == 0.5
    assert outcomes["design-unit"] == (pytest.approx(1 / 3), {"major": 2})
    codex = [s for s in fake.launches if s.agent == "codex"][0]
    assert f"model_reasoning_effort={rows[0]['effort']}" in codex.argv and codex.env["NDF_CHARGE_TO"] is None


def test_the_fixer_row_is_charged_and_without_charge_to_only_seats_are_recorded(
    wt: Path, tmp_path: Path, capsys, monkeypatch, outcomes
) -> None:
    fake = Seated(tmp_path / "st", {"codex": [[_major("一つ目")]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt)
    (fix,) = _rows(name="fix")
    assert (fix["layer"], fix["caller"], "assign_id" in fix, "charge_to" in fix) == ("seat", "claude-p", False, False)
    assert len(outcomes) == 1


def test_a_review_that_stops_with_an_unread_seat_records_nothing(wt: Path, tmp_path: Path, capsys, monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    fake = Seated(tmp_path / "st", {"codex": [[_major("一つ目")]], "kiro": [FAIL, FAIL], "claude": [FAIL]})
    _run(capsys, monkeypatch, fake, wt)
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.INCOMPLETE_REVIEWERS
    assert outcomes == {}


def test_a_resumed_fixer_keeps_the_first_values_and_charge(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, outcomes) -> None:
    """続けた会話の起動は、後から変えた環境変数に影響されず、同じ値と費用を負う先を使う。"""
    _declare(repos, SAVED)
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    monkeypatch.setenv("NDF_LAUNCH_MODEL", "opus")

    class Changing(SeatedSessions):
        def _fix(self, spec):
            monkeypatch.setenv("NDF_LAUNCH_MODEL", "haiku")
            monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")
            return super()._fix(spec)

    fake = Changing(tmp_path / "st", {"codex": [["一つ目"], ["二つ目"], []], "kiro": [[], [], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    first, second = [s for s in fake.launches if s.agent == "claude"]
    assert "--resume" in second.argv
    for spec in (first, second):
        assert spec.argv[spec.argv.index("--model") + 1] == "opus"
        assert spec.env["CLAUDE_CODE_EFFORT_LEVEL"] is None
    rows = _rows(name="fix")
    assert [(r["requested_model"], r["charge_to"], r["session_seq"], r["request_try"]) for r in rows] == [
        ("opus", "impl-unit", 0, 1),
        ("opus", "impl-unit", 1, 2),
    ]


def test_a_refused_codex_arm_is_launched_once_more_with_the_base_arm(wt: Path, tmp_path: Path, capsys, monkeypatch, outcomes) -> None:
    monkeypatch.setattr(model_chooser.model_choice, "_random_value", lambda: 0.99)  # 基準でない組を引く

    class Refusing(Seated):
        def __call__(self, spec):
            status = super().__call__(spec)
            if spec.agent == "codex" and "model_reasoning_effort=high" in spec.argv:
                Path(spec.argv[spec.argv.index("-o") + 1]).unlink()
                message = "[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value"
                events = [{"type": "thread.started"}, {"type": "turn.failed", "error": {"message": message}}]
                spec.paths.out.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
                return status._replace(state=agents.AgentState.FAILED, exit_code=1)
            return status

    fake = Refusing(tmp_path / "st", {"codex": [[], []], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    first, second = _rows(name="review", runtime="codex")
    assert (first["model_refused"], first["effort"], second["effort"], second["request_try"]) == (True, "high", "low", 2)
    assert first["request_id"] == second["request_id"] and "assign_id" not in second
    assert outcomes == {} and _state(fake.state_dir)["experiment_units"]["seats"] == {}


def test_a_bad_value_stops_before_any_seat_is_launched(wt: Path, tmp_path: Path, capsys, monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_LAUNCH_CODEX_EFFORT", "bogus")
    fake = Seated(tmp_path / "st", {})
    monkeypatch.setattr("ndf.domains.crossreview.cli.LAUNCH", fake)
    from ndf.contracts import internal

    with pytest.raises(internal.InternalError) as stopped:
        _run(capsys, monkeypatch, fake, wt)
    assert stopped.value.kind == "precondition" and fake.launches == []


# --- 数え方 ------------------------------------------------------------------------------------------


def _row(fid: str, outcome: str, raisers: list[str], severity: str = "major") -> dict:
    return {"id": fid, "outcome": outcome, "severity": severity, "raisers": raisers, "by": raisers[0] if raisers else "ndf"}


ROWS = [
    _row("r1-1", fin.FIXED, ["codex"]),
    _row("r1-2", fin.FIXED, ["codex", "kiro"], "critical"),
    _row("r1-3", fin.SKIPPED, ["kiro"]),
    _row("r1-4", fin.UNRESOLVED, ["kiro"], "minor"),
    _row("r2-1", fin.FIXED, ["kiro"]),
    _row("r1-n1", fin.FIXED, []),
]
FIRST = ["r1-1", "r1-2", "r1-3", "r1-4", "r1-n1"]


def test_only_adopted_major_findings_are_counted() -> None:
    count, shares = experiment_values(ROWS, FIRST, ["codex", "kiro"])
    assert count == 2
    assert shares == {"codex": pytest.approx(2 / 3), "kiro": pytest.approx(2 / 3)}


def test_the_share_needs_two_seats_and_an_adopted_finding() -> None:
    assert experiment_values(ROWS, FIRST, ["codex"])[1] == {"codex": None}
    assert experiment_values([_row("r1-1", fin.SKIPPED, ["codex"])], ["r1-1"], ["codex", "kiro"]) == (
        0,
        {"codex": None, "kiro": None},
    )


def test_an_undecided_first_round_finding_leaves_the_count_unknown() -> None:
    rows = [*ROWS, _row("r1-5", fin.UNRESOLVED, ["codex"])]
    assert experiment_values(rows, [*FIRST, "r1-5"], ["codex", "kiro"])[0] is None
    assert experiment_values(ROWS, None, ["codex", "kiro"])[0] is None


def test_the_journal_keeps_every_seat_that_raised_a_finding() -> None:
    journal = FindingJournal()
    journal.enter(1, [{"text": "同じ指摘", "by": "codex"}, {"text": "同じ指摘", "by": "kiro"}])
    assert journal.rows["r1-1"]["raisers"] == ["codex", "kiro"] and journal.entered == ["r1-1"]
    journal.enter(2, [{"text": "同じ指摘", "by": "codex"}, {"text": "別の指摘", "by": "kiro"}])
    assert journal.rows["r1-1"]["raisers"] == ["codex", "kiro"] and len(journal.entered) == 2
