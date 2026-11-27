"""cross-review の相手役のシートを実験の単位にし、効果指標と起動の行を書く（#2944）。

相手役と修正担当は偽物で、git は起動しない（起動するとテストが落ちる）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ndf.adapters import agents, claude, claude_choice, git, model_choice
from ndf.contracts import internal
from ndf.core.converge import participants
from ndf.domains.crossreview import drive, experiment, fixer, seats, settings
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.journal import FindingJournal, experiment_values
from ndf.domains.records import jsonl, usage

USED = {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0, "output_tokens": 10}
NO_FINDINGS = '{"findings": []}'


@pytest.fixture
def outcomes(monkeypatch, launch_chooser) -> dict[str, tuple]:
    """値を選ぶ処理と使用量ログの書き込み処理を本物にし、書く効果指標を集める。git の起動は失敗にする。"""
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    for name in ("NDF_CHARGE_TO", "NDF_LAUNCH_MODEL", "NDF_LAUNCH_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL"):
        monkeypatch.delenv(name, raising=False)
    got: dict[str, tuple] = {}
    monkeypatch.setattr(claude, "launch_recorder", usage)
    monkeypatch.setattr(claude_choice, "report_outcome", lambda aid, quality, detail: got.__setitem__(aid, (quality, detail)))

    def no_git(args, *rest, **named):
        raise AssertionError(f"git を起動した: {args}")

    monkeypatch.setattr(git, "_start", no_git)
    return got


def _held(tmp_path: Path, saved: bool = False) -> drive._Run:
    """1 回の駆動の値。git と状態の読み書きを使わない部分だけを持つ。"""
    work, state = tmp_path / "wt", tmp_path / "st"
    work.mkdir()
    state.mkdir()
    cfg = SimpleNamespace(worktree=work, state_dir=state, fixer="claude", settings=settings.ReviewSettings(fixer_sessions=saved))
    return drive._Run(cfg=cfg, g=None, ref=None, data={}, journal=FindingJournal(), warnings=[], process=[], history=[])


def _done(spec: agents.Launch) -> agents.AgentStatus:
    return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())._replace(seconds=1.5)


class Seats:
    """偽の相手役。codex の `--json` の起動は、本文を `-o` のファイルへ、使用量の行を標準出力へ書く。"""

    def __init__(self) -> None:
        self.launches: list[agents.Launch] = []

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        self.launches.append(spec)
        spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
        if "--json" in spec.argv:
            Path(spec.argv[spec.argv.index("-o") + 1]).write_text(NO_FINDINGS, encoding="utf-8")
            events = [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.completed", "usage": USED}]
            spec.paths.out.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        else:
            spec.paths.out.write_text(NO_FINDINGS, encoding="utf-8")
        return _done(spec)


def _rows(**wanted: Any) -> list[dict]:
    found = jsonl.read_rows(usage.usage_log_path(), kind="usage")[0]
    return [r for r in found if all(r.get(k) == v for k, v in wanted.items())]


def _review(run: drive._Run, round_no: int, launch, *names: str) -> None:
    seats.launch_reviewers(run, round_no, {s: "読む" for s in names}, [], launch)


def test_each_seat_launch_writes_a_row_and_the_repository_key_is_resolved_once(tmp_path: Path, monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "design-unit")
    resolved: list[Path] = []
    monkeypatch.setattr(model_choice.launch_approval, "repo_key", lambda root: resolved.append(root) or str(root))
    run, fake = _held(tmp_path), Seats()
    for round_no in (1, 2):
        _review(run, round_no, fake, "codex", "kiro")
    assert len(resolved) == 1
    rows = _rows(name="review", runtime="codex")
    assert [(r["caller"], r["layer"], r["outcome"], r["seconds"]) for r in rows] == [("codex-exec", "seat", "done", 1.5)] * 2
    held = run.data[experiment.KEY]
    assert {r["unit"] for r in rows} == {f"{held['id']}:codex"} and held["seats"]["codex"] == rows[0]["assign_id"]
    assert rows[0]["assign_id"] == rows[1]["assign_id"]
    assert rows[0]["effort"] and rows[0]["session_cost_usd"] > 0 and "charge_to" not in rows[0]
    codex = [s for s in fake.launches if s.agent == "codex"][0]
    assert f"model_reasoning_effort={rows[0]['effort']}" in codex.argv and codex.env["NDF_CHARGE_TO"] is None


def test_a_large_claude_seat_answer_is_parsed_once_for_the_cost_and_the_denials(tmp_path: Path, monkeypatch, outcomes) -> None:
    diff = tmp_path / "round1.diff"

    def large(spec: agents.Launch) -> agents.AgentStatus:
        body = "読んだ" * agents.TAIL_BYTES + "\n" + NO_FINDINGS
        denied = [{"tool_name": "Read", "tool_input": {"file_path": str(diff)}}]
        spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
        answer = {"type": "result", "result": body, "session_id": "s", "total_cost_usd": 0.5, "permission_denials": denied}
        spec.paths.out.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
        return _done(spec)

    def read_again(out: Path):
        raise AssertionError(f"claude の出力を読み直した: {out}")

    monkeypatch.setattr(participants, "claude_answer", read_again)
    _, unread = seats.launch_reviewers(_held(tmp_path), 1, {"claude": "読む"}, [diff], large)
    (row,) = _rows(name="review")
    assert row["session_cost_usd"] == 0.5 and list(unread) == ["claude"]


def test_a_refused_codex_arm_is_launched_once_more_with_the_base_arm(tmp_path: Path, monkeypatch, outcomes) -> None:
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)  # 基準でない組を引く

    class Refusing(Seats):
        def __call__(self, spec):
            status = super().__call__(spec)
            if "model_reasoning_effort=high" in spec.argv:
                Path(spec.argv[spec.argv.index("-o") + 1]).unlink()
                message = "[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value"
                events = [{"type": "thread.started"}, {"type": "turn.failed", "error": {"message": message}}]
                spec.paths.out.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
                return status._replace(state=agents.AgentState.FAILED, exit_code=1)
            return status

    run = _held(tmp_path)
    _review(run, 1, Refusing(), "codex")
    first, second = _rows(name="review", runtime="codex")
    assert (first["model_refused"], first["effort"], second["effort"], second["request_try"]) == (True, "high", "low", 2)
    assert first["request_id"] == second["request_id"] and "assign_id" not in second
    assert run.data[experiment.KEY]["seats"] == {}


class Slow(Seats):
    """起動のたびに時計を `took` 秒進める codex の席。`refuse` が真を返す起動は、モデルを理由に拒まれる。"""

    def __init__(self, monkeypatch, took: float, refuse) -> None:
        super().__init__()
        self.now, self.took, self.refuse = 1000.0, took, refuse
        monkeypatch.setattr(claude_choice, "_monotonic", lambda: self.now)

    def __call__(self, spec):
        status = super().__call__(spec)
        self.now += self.took
        if not self.refuse(len(self.launches)):
            return status
        if "-o" in spec.argv:
            Path(spec.argv[spec.argv.index("-o") + 1]).unlink()
        message = "[ReasoningEffortParam] [reasoning.effort] [invalid_enum_value] Invalid value"
        events = [{"type": "thread.started"}, {"type": "turn.failed", "error": {"message": message}}]
        spec.paths.out.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        return status._replace(state=agents.AgentState.FAILED, exit_code=1)


@pytest.mark.parametrize("refusals, unread_seats", [(1, []), (2, ["codex"])])
def test_the_relaunch_gets_the_remaining_time_and_its_result_is_the_seats_result(
    tmp_path: Path, monkeypatch, outcomes, refusals: int, unread_seats: list[str]
) -> None:
    """起動し直しは 1 回だけで、監視の上限は残りの秒。席の結果と行の結果は最後の起動のもの。"""
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)
    fake, run = Slow(monkeypatch, 100.0, lambda n: n <= refusals), _held(tmp_path)
    raised, unread = seats.launch_reviewers(run, 1, {"codex": "読む"}, [], fake)
    first, second = fake.launches
    assert second.timeout == first.timeout - 100.0
    assert (raised, list(unread)) == ([], unread_seats)
    assert [r["outcome"] for r in _rows(name="review")] == ["failed", "failed" if unread_seats else "done"]


def test_a_refused_seat_is_not_relaunched_after_the_deadline(tmp_path: Path, monkeypatch, outcomes) -> None:
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)
    fake = Slow(monkeypatch, 10**9, lambda n: True)
    _, unread = seats.launch_reviewers(_held(tmp_path), 1, {"codex": "読む"}, [], fake)
    assert len(fake.launches) == 1 and list(unread) == ["codex"]
    (row,) = _rows(name="review")
    assert (row["model_refused"], row["outcome"]) == (True, "failed")


def test_a_seat_without_chosen_values_is_launched_once_and_writes_no_row(tmp_path: Path, monkeypatch, outcomes) -> None:
    monkeypatch.setattr(claude_choice, "launch_chooser", None)
    fake = Slow(monkeypatch, 1.0, lambda n: True)
    _, unread = seats.launch_reviewers(_held(tmp_path), 1, {"codex": "読む"}, [], fake)
    assert len(fake.launches) == 1 and list(unread) == ["codex"] and _rows(name="review") == []


def test_a_bad_value_stops_before_any_seat_is_launched(tmp_path: Path, monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_LAUNCH_CODEX_EFFORT", "bogus")
    fake = Seats()
    with pytest.raises(internal.InternalError) as stopped:
        _review(_held(tmp_path), 1, fake, "kiro", "codex")
    assert stopped.value.kind == "precondition" and fake.launches == []


# --- 修正担当 ----------------------------------------------------------------------------------------


class Fixer:
    """偽の修正担当。会話を続けられる大きさの文脈を持つ結果の行を書く。`after` は起動のたびに呼ぶ。"""

    def __init__(self, after=lambda: None) -> None:
        self.after = after
        self.launches: list[agents.Launch] = []

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        self.launches.append(spec)
        spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
        iteration = {"input_tokens": 2, "cache_read_input_tokens": 998, "cache_creation_input_tokens": 0}
        models = {"claude-opus-5-5": {"inputTokens": 1000, "outputTokens": 1, "contextWindow": 1000000}}
        result = {"type": "result", "usage": {"iterations": [iteration]}, "modelUsage": models}
        spec.paths.out.write_text(json.dumps(result) + "\n", encoding="utf-8")
        self.after()
        return _done(spec)


def _fix(run: drive._Run, round_no: int, launch: Fixer) -> None:
    fixer._launch_fixer(run, round_no, "直す", run.cfg.state_dir / "fix-result.json", launch)


def test_a_fixer_row_without_charge_to_has_neither_assignment_nor_charge(tmp_path: Path, outcomes) -> None:
    _fix(_held(tmp_path), 1, Fixer())
    (row,) = _rows(name="fix")
    assert (row["layer"], row["caller"], "assign_id" in row, "charge_to" in row) == ("seat", "claude-p", False, False)


def test_a_resumed_fixer_keeps_the_first_values_and_charge(tmp_path: Path, monkeypatch, outcomes) -> None:
    """続けた会話の起動は、後から変えた環境変数に影響されず、同じ値と費用を負う先を使う。"""
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    monkeypatch.setenv("NDF_LAUNCH_MODEL", "opus")

    def change() -> None:
        monkeypatch.setenv("NDF_LAUNCH_MODEL", "haiku")
        monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")

    run, fake = _held(tmp_path, saved=True), Fixer(change)
    _fix(run, 1, fake)
    run.fixer_last = run.fixer_last._replace(taken=True)  # 駆動が結果をマージできた後と同じ
    _fix(run, 2, fake)
    first, second = fake.launches
    assert "--resume" in second.argv
    for spec in (first, second):
        assert spec.argv[spec.argv.index("--model") + 1] == "opus"
        assert spec.env["CLAUDE_CODE_EFFORT_LEVEL"] is None
    rows = _rows(name="fix")
    assert [(r["requested_model"], r["charge_to"], r["session_seq"], r["request_try"]) for r in rows] == [
        ("opus", "impl-unit", 0, 1),
        ("opus", "impl-unit", 1, 2),
    ]


# --- 効果指標 ----------------------------------------------------------------------------------------


def _major(text: str, by: str, severity: str = "major") -> dict:
    return {"text": text, "by": by, "severity": severity, "impact": "利用者の経路で処理が止まる", "section": ""}


def _data() -> dict:
    return {experiment.KEY: {"id": "run", "seats": {"codex": "a-codex", "kiro": "a-kiro"}}}


def _entered(data: dict, journal: FindingJournal, round_no: int, *items: dict, complete: bool = True) -> None:
    journal.enter(round_no, list(items))
    experiment.keep_first(data, round_no, journal.entered_serious, complete)


def _decide(journal: FindingJournal, outcome: str) -> None:
    for row in journal.rows.values():
        row["outcome"] = outcome


def _record(data: dict, journal: FindingJournal, final: str) -> None:
    experiment.record(data, list(journal.rows.values()), ["codex", "kiro"], final)


def test_a_converged_review_records_the_share_of_the_seat_and_of_the_charged_unit(monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "design-unit")
    data, journal = _data(), FindingJournal()
    _entered(data, journal, 1, _major("一つ目", "codex"), _major("一つ目", "kiro"), _major("二つ目", "kiro"))
    _decide(journal, fin.FIXED)
    _record(data, journal, fin.CONVERGED)
    assert (outcomes["a-codex"][0], outcomes["a-kiro"][0]) == (0.5, 1.0)
    assert outcomes["design-unit"] == (pytest.approx(1 / 3), {"major": 2})


def test_a_review_that_ends_with_an_unread_seat_records_nothing(monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    data, journal = _data(), FindingJournal()
    _entered(data, journal, 1, _major("一つ目", "codex"))
    _record(data, journal, fin.INCOMPLETE_REVIEWERS)
    assert outcomes == {}


def test_a_finding_that_becomes_serious_later_is_not_counted_for_the_unit(monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    data, journal = _data(), FindingJournal()
    _entered(data, journal, 1, _major("一つ目", "codex", "minor"))
    _entered(data, journal, 2, _major("一つ目", "codex"))
    _decide(journal, fin.FIXED)
    _record(data, journal, fin.CONVERGED)
    assert outcomes["impl-unit"] == (1.0, {"major": 0})


def test_the_first_round_is_taken_again_only_when_the_charged_unit_changes(monkeypatch, outcomes) -> None:
    data = _data()
    seen = []
    for charged, round_no, serious in (("impl-a", 1, "r1-1"), ("impl-a", 2, "r2-1"), ("impl-b", 3, "r3-1"), (None, 4, "r4-1")):
        if charged is None:
            monkeypatch.delenv("NDF_CHARGE_TO")
        else:
            monkeypatch.setenv("NDF_CHARGE_TO", charged)
        experiment.keep_first(data, round_no, [serious], True)
        seen.append((data[experiment.KEY]["charged"], data[experiment.KEY]["first"]))
    assert seen == [("impl-a", ["r1-1"]), ("impl-a", ["r1-1"]), ("impl-b", ["r3-1"]), ("impl-b", ["r3-1"])]


def test_the_first_round_stays_open_until_every_seat_has_read(monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    data, journal = _data(), FindingJournal()
    _entered(data, journal, 1, _major("一つ目", "codex"), complete=False)
    _entered(data, journal, 2, _major("二つ目", "kiro"))
    _entered(data, journal, 3, _major("三つ目", "kiro"))
    assert data[experiment.KEY]["first"] == ["r1-1", "r2-1"] and "partial" not in data[experiment.KEY]


def test_a_new_charged_unit_counts_the_severity_of_its_own_round(monkeypatch, outcomes) -> None:
    data, journal = _data(), FindingJournal()
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-a")
    _entered(data, journal, 1, _major("一つ目", "codex"))
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-b")
    _entered(data, journal, 2, _major("一つ目", "codex", "minor"))
    assert data[experiment.KEY]["first"] == []


@pytest.mark.parametrize("order", [("Value 1", "P1"), ("P1", "Value 1")])
def test_reports_of_one_round_are_merged_with_the_approval_item_first(order: tuple[str, str]) -> None:
    """承認が必要な項目が付いた指摘は重さを固定しないので、軽い報告だけなら重大に数えない。"""
    reports = [_major("同じ指摘", by, "minor") | {"mvv": item} for by, item in zip(("codex", "kiro"), order)]
    journal = FindingJournal()
    journal.enter(1, reports, ("Value 1", "P1"), "v")
    assert journal.entered_serious == []
    alone = FindingJournal()
    alone.enter(1, [r for r in reports if r["mvv"] == "Value 1"], ("Value 1", "P1"), "v")
    assert alone.entered_serious == ["r1-1"]


def test_the_round_limit_holds_the_seat_shares_while_a_serious_finding_is_undecided(outcomes) -> None:
    data, journal = _data(), FindingJournal()
    _entered(data, journal, 1, _major("一つ目", "codex"), _major("二つ目", "kiro"))
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    _record(data, journal, fin.MAX_ROUNDS)
    assert outcomes == {}
    _decide(journal, fin.FIXED)
    _record(data, journal, fin.CONVERGED)
    assert (outcomes["a-codex"][0], outcomes["a-kiro"][0]) == (0.5, 0.5)


def test_a_review_stopped_at_the_round_limit_leaves_unknown_values_for_the_resumed_run(monkeypatch, outcomes) -> None:
    monkeypatch.setenv("NDF_CHARGE_TO", "impl-unit")
    data, journal = _data(), FindingJournal()
    _entered(data, journal, 1, _major("一つ目", "codex"))
    _record(data, journal, fin.MAX_ROUNDS)
    assert outcomes == {}
    _decide(journal, fin.FIXED)
    _record(data, journal, fin.CONVERGED)
    assert outcomes["impl-unit"] == (0.5, {"major": 1}) and outcomes["a-codex"][0] == 1.0


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
FIRST = ["r1-1", "r1-2", "r1-3", "r1-n1"]


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
    assert journal.rows["r1-1"]["raisers"] == ["codex", "kiro"]
    journal.enter(2, [{"text": "同じ指摘", "by": "codex"}, {"text": "別の指摘", "by": "kiro"}])
    assert journal.rows["r1-1"]["raisers"] == ["codex", "kiro"] and len(journal.rows) == 2
