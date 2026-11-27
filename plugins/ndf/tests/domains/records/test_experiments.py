"""#2944: 効果指標の記録・単位の値・出現率の計算し直し（experiments）。

記録の行は実験の記録のスキーマ（experiment.schema.json）と使用量ログのスキーマの形の見本で作り、書く前に形を確かめる。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ndf.adapters import launch_records
from ndf.adapters import model_choice as mc
from ndf.contracts import internal
from ndf.domains.records import experiments as ex
from ndf.domains.records import jsonl

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
EVEN = [1 / 3, 1 / 3, 1 / 3]
ARMS = [{}, {"model": "sonnet", "effort": "medium"}, {"model": "sonnet", "effort": "high"}]
KEY = mc.compatibility_key(ARMS, mc.metric_of("impl"))
WHERE = mc.Where("/r", "impl", "claude", KEY)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    monkeypatch.setattr(mc.launch_approval, "repo_key", lambda root: os.path.realpath(root))


@pytest.fixture
def store(monkeypatch) -> Path:
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    return mc.records_dir()


@pytest.fixture
def clock(monkeypatch):
    now = {"at": NOW}
    monkeypatch.setattr(mc, "utc_now", lambda: now["at"])
    return now


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """出現率を計算するリポジトリ（作業ディレクトリ）。"""
    found = tmp_path / "repo"
    (found / ".git").mkdir(parents=True)
    (found / ".ndf").mkdir()
    (found / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": {}}), encoding="utf-8")
    monkeypatch.chdir(found)
    return found


def _at(when: datetime) -> str:
    return when.isoformat(timespec="microseconds")


def _assign(aid: str, chosen: int, when: datetime = NOW, *, probs=None, repo="/r", key=KEY) -> dict:
    row = {
        "kind": "assign",
        "at": _at(when),
        "assign_id": aid,
        "unit": f"run:{aid}",
        "stage": "impl",
        "runtime": "claude",
        "repo": repo,
        "arms": ARMS,
        "probs": probs or EVEN,
        "chosen": chosen,
        "key": key,
        "phase": "burn_in",
        "window_days": 60,
    }
    return internal.write_record("experiment", row)


def _outcome(aid: str, quality: float | None, when: datetime = NOW, *, version: int = 1) -> dict:
    row = {
        "kind": "outcome",
        "at": _at(when),
        "assign_id": aid,
        "metric": "pr_review_major_findings",
        "metric_version": version,
        "quality": quality,
        "detail": {"major": 0},
    }
    return internal.write_record("experiment", row)


def _launch(lid: str, cost: float | None, when: datetime = NOW, **fields) -> dict:
    row = {"at": _at(when), "caller": "claude-p", "usage": "unknown", "launch_id": lid, "session_id": f"s-{lid}", "session_seq": 0}
    if cost is not None:
        row["session_cost_usd"] = cost
    return internal.write_record("usage", {**row, **fields})


def _unit(aid: str, chosen: int, quality: float | None, cost: float | None, when: datetime = NOW, model: str | None = None) -> tuple:
    """単位 1 つの割り付け・効果指標・起動 1 回の行。"""
    usage = _launch(f"l-{aid}", cost, when, assign_id=aid, **({"model": model} if model else {}))
    return [_assign(aid, chosen, when), _outcome(aid, quality, when)], [usage]


def _values(*units: tuple, extra=(), extra_usage=()) -> list[ex.UnitValue]:
    rows = [r for u in units for r in u[0]] + list(extra)
    usage = [r for u in units for r in u[1]] + list(extra_usage)
    return ex.unit_values(rows, usage, where=WHERE, now=NOW, window_days=60)


# --- 単位の値 ---


def test_unit_values_carry_the_effect_the_probability_and_the_cost_with_rework():
    a = _unit("a", 1, 0.5, 2.0)
    rework = _launch("fix", 1.5, charge_to="a", assign_id="other")  # 手戻りの起動の費用も a に入る
    (found,) = _values(a, extra_usage=[rework])
    assert (found.assign_id, found.arm, found.quality, found.cost) == ("a", 1, 0.5, 3.5)
    assert found.probs == pytest.approx(tuple(EVEN)) and found.at == NOW


@pytest.mark.parametrize("costs", [[None], [0.0]])
def test_unknown_or_zero_cost_keeps_the_effect_without_a_cost(costs):
    (found,) = _values(_unit("a", 0, 1.0, costs[0]))
    assert found.quality == 1.0 and found.cost is None


def test_a_unit_without_any_launch_has_no_cost():
    (found,) = _values(([_assign("a", 0), _outcome("a", 0.0)], []))
    assert found.cost is None and found.quality == 0.0


def test_units_that_are_out_of_scope_or_left_the_experiment_are_not_used():
    kept = _unit("kept", 0, 1.0, 1.0)
    other_repo = ([_assign("repo", 0, repo="/other"), _outcome("repo", 1.0)], [])
    other_key = ([_assign("key", 0, key="old"), _outcome("key", 1.0)], [])
    old = ([_assign("old", 0, NOW - timedelta(days=61)), _outcome("old", 1.0)], [])
    no_effect = _unit("none", 0, None, 1.0)
    halted = _unit("halted", 1, 1.0, 1.0)
    switched = _unit("switched", 2, 1.0, 1.0)
    stale_metric = ([_assign("metric", 0), _outcome("metric", 1.0, version=2)], [])
    marks = [
        internal.write_record(
            "experiment",
            {"kind": "halted", "at": _at(NOW), "unit": "run:halted", **WHERE.fields(), "assign_id": "halted", "reason": "manual"},
        ),
        internal.write_record(
            "experiment",
            {"kind": "launch_error", "at": _at(NOW), "assign_id": "switched", "stage": "impl", "runtime": "claude", "arm": ARMS[2]}
            | {"reason": "model_refused", "switched": True},
        ),
    ]
    found = _values(kept, other_repo, other_key, old, no_effect, halted, switched, stale_metric, extra=marks)
    assert [u.assign_id for u in found] == ["kept"]


def test_only_the_first_effect_of_an_assignment_counts():
    rows, usage = _unit("a", 0, 1.0, 1.0)
    (found,) = _values((rows + [_outcome("a", 0.0)], usage))
    assert found.quality == 1.0


def test_a_reassignment_after_the_window_is_a_separate_unit():
    first = _unit("first", 1, 0.0, 1.0, NOW - timedelta(days=30))
    second = _unit("second", 2, 1.0, 2.0, NOW - timedelta(days=1))
    assert [(u.assign_id, u.arm) for u in _values(first, second)] == [("first", 1), ("second", 2)]


# --- 実際のモデル ---


def _day(n: int) -> datetime:
    return NOW - timedelta(days=30 - n)


def test_units_of_an_older_default_model_are_dropped():
    old = _unit("old", 0, 1.0, 1.0, _day(1), "claude-old")
    new = _unit("new", 0, 0.0, 1.0, _day(2), "claude-new")
    named = _unit("named", 1, 1.0, 1.0, _day(0), "claude-sonnet")  # 指定する組は別のまとまり
    assert [u.assign_id for u in _values(old, new, named)] == ["named", "new"]


def test_the_model_of_a_unit_is_from_its_first_launch_that_was_not_raised():
    rows, usage = _unit("a", 0, 1.0, 1.0, _day(1), "claude-new")
    raised = _launch("up", 1.0, _day(0), assign_id="a", model="claude-old", raised=True)
    later = _unit("b", 0, 1.0, 1.0, _day(2), "claude-new")
    assert [u.model for u in _values((rows, usage + [raised]), later)] == ["claude-new", "claude-new"]


def test_a_unit_whose_first_launch_returned_no_model_keeps_an_unknown_model():
    # 最初の起動が期限切れで回答が無く、再開後の起動だけがモデルを返した単位は、再開後のモデルを最初の起動のモデルにしない
    rows, _ = _unit("a", 0, 1.0, 1.0, _day(1))
    lost = _launch("l-a", None, _day(1), assign_id="a")
    resumed = _launch("l-a2", 1.0, _day(1) + timedelta(hours=1), assign_id="a", model="claude-new")
    (found,) = _values((rows, [lost, resumed]))
    assert found.model is None


@pytest.mark.parametrize(
    ("models", "kept"),
    [
        # 変化を観測していなければ、モデルの分からない単位はすべて数える
        (["A", None, "A", None], ["u0", "u1", "u2", "u3"]),
        # A → B → A: 最後に B を観測した単位より後で、A を最初に観測した単位（u4）以降の分からない単位だけを数える
        (["A", None, "B", None, "A", None, "A"], ["u0", "u4", "u5", "u6"]),
    ],
)
def test_units_without_a_known_model_count_only_after_the_last_change(models, kept):
    units = []
    for i, model in enumerate(models):
        rows, usage = _unit(f"u{i}", 0, 1.0, 1.0, _day(i), model)
        if model is None:
            usage = [_launch(f"l-u{i}", None, _day(i), assign_id=f"u{i}")]  # 期限切れで回答が無い
        rows[1] = _outcome(f"u{i}", 0.0 if model is None else 1.0, _day(i) + timedelta(hours=1))
        units.append((rows, usage))
    assert [u.assign_id for u in _values(*units)] == kept


def test_units_without_a_known_model_do_not_count_when_the_current_model_was_not_seen_after_the_change():
    # 今のモデル（最も新しく評価した u0 の A）を、B を観測した u1 より後に割り付けた単位で観測していない
    first = _unit("u0", 0, 1.0, 1.0, _day(0), "A")
    first[0][1] = _outcome("u0", 1.0, _day(9))
    changed = _unit("u1", 0, 1.0, 1.0, _day(1), "B")
    lost = ([_assign("u2", 0, _day(2)), _outcome("u2", 0.0, _day(2))], [_launch("l-u2", None, _day(2), assign_id="u2")])
    assert [u.assign_id for u in _values(first, changed, lost)] == ["u0"]


# --- 効果指標の記録 ---


def _ledger(store: Path, *rows: dict) -> None:
    with mc.ledger(NOW - timedelta(days=60), store) as led:
        for row in rows:
            led.append(row)


def _rows(store: Path, kind: str) -> list[dict]:
    return [r for r in mc.read_experiments(NOW - timedelta(days=60), store) if r["kind"] == kind]


@pytest.fixture
def refreshed(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(ex, "_refresh_with", lambda assign, *rest: calls.append(assign["assign_id"]) or True)
    return calls


def test_write_outcome_adds_one_row_with_the_stage_metric_and_refreshes(store, clock, refreshed):
    _ledger(store, _assign("a", 1, NOW - timedelta(days=1)))
    assert ex.write_outcome("a", 0.75, {"major": 1})
    (row,) = _rows(store, "outcome")
    assert (row["metric"], row["metric_version"], row["quality"], row["detail"]) == ("pr_review_major_findings", 1, 0.75, {"major": 1})
    assert refreshed == ["a"]


def test_write_outcome_does_not_add_a_second_row_but_still_refreshes(store, clock, refreshed):
    # 前の呼び出しが指標を書いた直後に止まっても、再開した呼び出しが出現率の更新を判定する
    _ledger(store, _assign("a", 1), _outcome("a", 1.0))
    assert ex.write_outcome("a", 0.0, {})
    assert [r["quality"] for r in _rows(store, "outcome")] == [1.0] and refreshed == ["a"]


def test_write_outcome_sees_the_row_written_while_it_waited_for_the_lock(store, clock, refreshed, monkeypatch):
    # ロックの外で読んだ後、先にロックを取った呼び出しが指標を書いた。ロックを取ってから読み足した行を見て足さない
    _ledger(store, _assign("a", 1))

    def rival(path: Path) -> contextlib.AbstractContextManager:
        launch_records.write_row(mc._month_file(store, "2026-11"), _outcome("a", 1.0))
        return contextlib.nullcontext()

    monkeypatch.setattr(mc, "_lock", rival)
    assert ex.write_outcome("a", 0.0, {})
    assert [r["quality"] for r in _rows(store, "outcome")] == [1.0]


def test_write_outcome_without_the_assignment_warns_and_returns_false(store, clock, refreshed, caplog):
    with caplog.at_level(logging.WARNING):
        assert not ex.write_outcome("missing", 1.0, {})
    assert "missing" in caplog.text and _rows(store, "outcome") == [] and refreshed == []


# --- 出現率の計算し直し ---


def _fill(store: Path, root: Path, per_arm: int, *, costs=(4.0, 1.0, 4.0)) -> dict:
    """組ごとに `per_arm` 件の評価した単位を書き、最後の割り付けの行を返す。"""
    repo, rows, usage = mc.launch_approval.repo_key(root), [], []
    for arm in range(3):
        for i in range(per_arm):
            aid, when = f"{arm}-{i}", NOW - timedelta(hours=arm * per_arm + i + 1)
            rows += [_assign(aid, arm, when, repo=repo), _outcome(aid, 0.8 if i % 5 else 0.0, when)]
            model = {"model": "claude-x"} if arm == 0 else {}  # 指定する組の単位はモデルを観測していない
            usage.append(_launch(f"l-{aid}", costs[arm] * (1 + i % 3 / 10), when, assign_id=aid, **model))
    _ledger(store, *rows)
    for row in usage:
        jsonl.append(store / "usage.jsonl", row, kind="usage")
    return rows[-2]


def _entries(store: Path) -> list[dict]:
    return mc.read_weights(store)


def test_refresh_writes_a_burn_in_entry_while_units_are_few(store, clock, root):
    last = _fill(store, root, 3)
    assert ex.refresh(last)
    (entry,) = _entries(store)
    assert entry["phase"] == "burn_in" and "probs" not in entry and entry["counts"] == [3, 3, 3]
    assert entry["candidates"] == [0, 1, 2] and entry["allocation"] == dict(mc.table(root).allocation)
    assert entry["at"] == _at(NOW) and entry["models"] == {"default": "claude-x"}


def test_refresh_adapts_and_the_next_draw_uses_the_written_probabilities(store, clock, root):
    last = _fill(store, root, 12)
    assert ex.refresh(last)
    (entry,) = _entries(store)
    assert entry["phase"] == "adaptive" and sum(entry["probs"]) == pytest.approx(1.0) and entry["probs"][1] > 0.5
    choice = mc.launch_choice("worker", "impl", "claude", cwd=root, environ={}, unit="next")
    (row,) = [r for r in _rows(store, "assign") if r["unit"] == "next"]
    assert choice.assign_id == row["assign_id"] and row["phase"] == "adaptive" and row["probs"] == entry["probs"]


def test_refresh_waits_an_hour_unless_the_actual_model_changed(store, clock, root):
    last = _fill(store, root, 3)
    assert ex.refresh(last)
    clock["at"] = NOW + timedelta(minutes=30)
    assert not ex.refresh(last)
    _ledger(
        store,
        internal.write_record(
            "experiment", {"kind": "observed", "at": _at(NOW), "assign_id": last["assign_id"]} | {"arm": {}, "model": "claude-y"}
        ),
    )
    assert ex.refresh(last)
    # 新しいモデルの評価がまだ無いので、古いモデルの単位を数えずに件数を集める段階を、観測のモデルで保存する
    (entry,) = _entries(store)
    assert entry["models"] == {"default": "claude-y"} and entry["phase"] == "burn_in" and entry["counts"] == [0, 3, 3]
    clock["at"] = NOW + timedelta(minutes=45)
    assert not ex.refresh(last)  # 保存した項目が最新の観測に合うので、1 時間以内は計算し直さない
    clock["at"] = NOW + timedelta(minutes=90)
    assert ex.refresh(last)


def test_write_outcome_of_an_old_assignment_ignores_observations_tied_to_it(store, clock, root):
    # 期間の外の割り付けは古い月を読み足して見つけるが、計算は割り付けが読むのと同じ月の範囲の行だけを使う
    last = _fill(store, root, 3)
    old = _assign("old", 0, NOW - timedelta(days=90), repo=last["repo"])
    seen = {"kind": "observed", "at": _at(NOW), "assign_id": "old", "arm": {}, "model": "claude-z"}
    _ledger(store, old, internal.write_record("experiment", seen))
    assert ex.write_outcome("old", 1.0, {})
    (entry,) = _entries(store)
    assert entry["models"] == {"default": "claude-x"} and entry["counts"] == [3, 3, 3]


def test_write_outcome_uses_the_rows_it_read_and_skips_git_while_the_entry_is_fresh(store, clock, root, monkeypatch):
    last = _fill(store, root, 3)
    assert ex.refresh(last)
    _ledger(store, _assign("z", 1, repo=last["repo"]))
    clock["at"] = NOW + timedelta(minutes=10)

    def unexpected(*args):
        raise AssertionError("呼ばれない")

    monkeypatch.setattr(mc, "read_experiments", unexpected)
    monkeypatch.setattr(mc.launch_approval, "repo_key", unexpected)
    assert ex.write_outcome("z", 1.0, {})
    assert _entries(store)[0]["at"] == _at(NOW)


def test_refresh_drops_entries_past_their_window_and_rebuilds_a_broken_file(store, clock, root):
    last = _fill(store, root, 3)
    (store / "launch-weights.json").write_text("{broken", encoding="utf-8")
    assert ex.refresh(last)
    old = {**_entries(store)[0], "stage": "fix", "at": _at(NOW - timedelta(days=61))}
    mc.update_weights(store, NOW - timedelta(days=61), old)
    clock["at"] = NOW + timedelta(hours=2)
    assert ex.refresh(last)
    assert [e["stage"] for e in _entries(store)] == ["impl"]


def test_refresh_of_an_assignment_made_under_other_settings_does_nothing(store, clock, root):
    last = _fill(store, root, 3)
    assert not ex.refresh({**last, "key": "old"})
    assert not (store / "launch-weights.json").exists()


def test_refresh_keeps_the_previous_entry_when_it_cannot_compute(store, clock, root, caplog):
    last = _fill(store, root, 3)
    (root / ".ndf" / "launch.json").write_text("{broken", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert not ex.refresh(last)
    assert "出現率" in caplog.text
