"""#2944: 実験の記録と割り付け（単位ごとに組を決め、記録に残す）。"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import launch_settings as ls
from ndf.adapters import model_choice as mc

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
MODEL_ENV = identity.env_name("launch-model")
# 停止の行の範囲（記録の読み書きだけを確かめるテストの値）
SCOPE = {"repo": "/r", "stage": "impl", "runtime": "claude"}


@pytest.fixture
def store(monkeypatch, tmp_path) -> Path:
    """`_repo` が作るリポジトリの、実験の記録のディレクトリ。"""
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    return mc.repo_store(mc.records_dir(), ls.repo_key(tmp_path / "repo"))


@pytest.fixture
def clock(monkeypatch):
    now = {"at": NOW}
    monkeypatch.setattr(mc, "_utc_now", lambda: now["at"])
    return now


@pytest.fixture
def draw(monkeypatch):
    """抽選の乱数（0 以上 1 未満）を決める。既定は 0（候補の先頭）。"""
    value = {"x": 0.0}
    monkeypatch.setattr(mc, "_random_value", lambda: value["x"])
    return value


def _repo(tmp_path: Path, experiments: dict | None = None) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": experiments or {}}), encoding="utf-8")
    return root


def _pick(root: Path, name: str = "impl", **kw) -> mc.Choice:
    kw.setdefault("environ", {})
    return mc.launch_choice("worker", name, "claude", cwd=root, **kw)


def _rows(store: Path, kind: str | None = None) -> list[dict]:
    out = []
    for path in sorted(store.glob("launch-experiments-*.jsonl")):
        out += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]
    return [r for r in out if kind is None or r["kind"] == kind]


def _at(when: datetime) -> str:
    return when.isoformat(timespec="microseconds")


def _write(store: Path, row: dict) -> None:
    """記録へ 1 行足す（ほかの起動が書いた行の代わり）。"""
    with mc.ledger(datetime.fromisoformat(row["at"]), store) as led:
        led.append(row)


def _assign(
    store: Path, root: Path, unit: str, chosen: int, when: datetime = NOW, *, arms=None, stage="impl", aid=None, repo=None, window=60
) -> dict:
    arms = arms if arms is not None else list(mc.table(root).arms[stage]["claude"])
    row = {
        "kind": "assign",
        "at": _at(when),
        "assign_id": aid or f"a-{unit}-{when:%m%d}",
        "unit": unit,
        "stage": stage,
        "runtime": "claude",
        "repo": repo or ls.repo_key(root),
        "arms": arms,
        "probs": [1 / len(arms)] * len(arms),
        "chosen": chosen,
        "key": mc.compatibility_key(arms, mc.metric_of(stage)),
        "phase": "burn_in",
        "window_days": window,
    }
    _write(store, row)
    return row


# --- 割り付け ---


def test_the_first_launch_of_a_unit_draws_and_the_next_takes_the_same_arm(tmp_path, store, clock, draw):
    root = _repo(tmp_path)
    draw["x"] = 0.5  # 3 組の均等な抽選で 2 つ目
    first = _pick(root, unit="r1:impl", mode="standard")
    again = _pick(root, unit="r1:impl")
    (row,) = _rows(store, "assign")
    assert first.assign_id == again.assign_id == row["assign_id"]
    assert (first.model, first.effort, first.model_source) == ("sonnet", "medium", mc.Source.EXPERIMENT)
    assert first.arm == again.arm == {"model": "sonnet", "effort": "medium"}
    assert row["chosen"] == 1 and row["phase"] == "burn_in" and row["probs"] == pytest.approx([1 / 3] * 3)
    assert row["repo"] == ls.repo_key(root) and row["mode"] == "standard" and row["unit"] == "r1:impl"
    assert row["key"] == mc.compatibility_key(row["arms"], mc.metric_of("impl"))
    assert (store / "launch-experiments-2026-11.jsonl").is_file()


def test_parallel_launches_of_one_unit_write_one_assignment(tmp_path, store, clock, draw):
    root = _repo(tmp_path)
    mc.table(root)  # 表の読み込みのキャッシュを先に作る
    got: list[mc.Choice] = []
    threads = [threading.Thread(target=lambda: got.append(_pick(root, unit="r1:impl"))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_rows(store, "assign")) == 1 and len({c.assign_id for c in got}) == 1


def test_a_launch_without_a_unit_reads_and_writes_no_records(tmp_path, store, clock):
    choice = _pick(_repo(tmp_path))
    assert choice.assign_id is None and choice.model_source is mc.Source.DEFAULT
    assert not list(store.glob("launch-experiments-*"))
    assert (store / "launch-pruned").read_text(encoding="utf-8") == "2026-11"


def test_an_assignment_from_the_previous_month_is_used(tmp_path, store, clock):
    root = _repo(tmp_path)
    row = _assign(store, root, "r1:impl", 2, NOW - timedelta(days=30))
    choice = _pick(root, unit="r1:impl")
    assert choice.assign_id == row["assign_id"] and choice.effort == "high"
    assert len(_rows(store, "assign")) == 1


def test_an_assignment_past_the_window_is_drawn_again(tmp_path, store, clock):
    root = _repo(tmp_path, {"allocation": {"window_days": 10}})
    old = _assign(store, root, "r1:impl", 2, NOW - timedelta(days=11))
    choice = _pick(root, unit="r1:impl")
    assert choice.assign_id not in (None, old["assign_id"]) and len(_rows(store, "assign")) == 2


def test_a_stopped_experiment_halts_an_assigned_unit_once(tmp_path, store, clock):
    root = _repo(tmp_path)
    row = _assign(store, root, "r1:impl", 2)
    root = _repo(tmp_path, {"enabled": False})
    for _ in range(2):
        choice = _pick(root, unit="r1:impl")
        assert choice.assign_id is None and choice.model_source is mc.Source.DEFAULT
    (halted,) = _rows(store, "halted")
    assert (halted["unit"], halted["assign_id"], halted["reason"]) == ("r1:impl", row["assign_id"], "disabled")
    assert len(_rows(store, "assign")) == 1


def test_a_stopped_experiment_without_an_assignment_writes_nothing(tmp_path, store, clock):
    _pick(_repo(tmp_path, {"enabled": False}), unit="r1:impl")
    assert _rows(store) == []


def test_a_fixed_choice_halts_an_assigned_unit(tmp_path, store, clock):
    root = _repo(tmp_path)
    _assign(store, root, "r1:impl", 1)
    choice = _pick(_repo(tmp_path, {"stages": {"impl": {"arms": {"claude": [{"model": "opus"}]}}}}), unit="r1:impl")
    assert choice.model == "opus" and choice.model_source is mc.Source.BASE
    assert [r["reason"] for r in _rows(store, "halted")] == ["fixed"]


def test_changed_choices_halt_the_unit_and_launch_the_base(tmp_path, store, clock):
    root = _repo(tmp_path)
    _assign(store, root, "r1:impl", 1)
    root = _repo(tmp_path, {"stages": {"impl": {"arms": {"claude": [{}, {"model": "opus"}]}}}})
    choice = _pick(root, unit="r1:impl")
    assert choice.assign_id is None and choice.model is None
    assert [r["reason"] for r in _rows(store, "halted")] == ["changed"]
    assert _pick(root, unit="r1:impl").assign_id is None  # 停止の行がある単位は割り付け直さない
    assert len(_rows(store, "assign")) == 1 and len(_rows(store, "halted")) == 1


def test_a_manual_launch_halts_its_unit_and_the_charged_unit(tmp_path, store, clock):
    root = _repo(tmp_path)
    impl = _assign(store, root, "r1:impl", 1)
    choice = _pick(root, unit="r1:fix", charge_to=impl["assign_id"], environ={MODEL_ENV: "opus"})
    assert choice.model == "opus" and choice.assign_id is None
    halted = {(r["unit"], r["assign_id"], r["reason"]) for r in _rows(store, "halted")}
    assert halted == {("r1:fix", None, "manual"), ("r1:impl", impl["assign_id"], "manual")}
    assert _pick(root, unit="r1:impl").assign_id is None  # 手動指定をやめても基準の組のまま


def test_records_that_cannot_be_written_launch_without_values(tmp_path, store, clock, caplog):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("", encoding="utf-8")  # 保存先がファイルで、記録を書けない
    with caplog.at_level(logging.WARNING):
        choice = _pick(_repo(tmp_path), unit="r1:impl")
    assert choice.assign_id is None and choice.model is None and choice.effort is None
    assert "記録" in caplog.text


def test_a_retry_raises_the_assigned_arm(tmp_path, store, clock):
    root = _repo(tmp_path)
    _assign(store, root, "r1:fix", 1, stage="fix")
    choice = _pick(root, "fix", unit="r1:fix", retry="test_failed")
    assert (choice.model, choice.effort, choice.raised) == ("sonnet", "high", True)


# --- 候補 ---


def _refusal(store: Path, assign: dict, when: datetime) -> None:
    arm = assign["arms"][assign["chosen"]]
    row = {"kind": "launch_error", "at": _at(when), "assign_id": assign["assign_id"], "stage": "impl", "runtime": "claude"}
    _write(store, {**row, "arm": arm, "reason": "model_refused", "switched": True})


def _candidates(root: Path, store: Path, entry=None, now: datetime = NOW, repo=None) -> list[int]:
    arms = list(mc.table(root).arms["impl"]["claude"])
    rows = mc.read_experiments(now - timedelta(days=60), store)
    key = mc.compatibility_key(arms, mc.metric_of("impl"))
    return mc.candidates(arms, rows, entry, now, mc.Where(repo or ls.repo_key(root), "impl", "claude", key))


def test_two_refusals_in_seven_days_drop_the_arm_until_they_age(tmp_path, store, clock):
    root = _repo(tmp_path)
    for i in range(2):
        _refusal(store, _assign(store, root, f"u{i}", 2, NOW - timedelta(days=6), aid=f"a{i}"), NOW - timedelta(days=6))
    assert _candidates(root, store) == [0, 1]
    assert _candidates(root, store, now=NOW + timedelta(days=2)) == [0, 1, 2]


def test_refusals_of_another_repo_or_the_base_are_not_counted(tmp_path, store, clock):
    root = _repo(tmp_path)
    for i in range(2):
        _refusal(store, _assign(store, root, f"u{i}", 2, aid=f"a{i}", repo="/other"), NOW)
        _refusal(store, _assign(store, root, f"b{i}", 0, aid=f"b{i}"), NOW)
    assert _candidates(root, store) == [0, 1, 2]


def test_cut_arms_return_after_their_deadline_and_the_base_stays(tmp_path, store, clock):
    root = _repo(tmp_path)
    entry = {"cutoffs": [{"arm": 1, "until": _at(NOW + timedelta(days=1))}, {"arm": 2, "until": _at(NOW + timedelta(days=1))}]}
    assert _candidates(root, store, entry) == [0]
    assert _candidates(root, store, entry, now=NOW + timedelta(days=2)) == [0, 1, 2]


def test_a_refused_arm_is_not_drawn(tmp_path, store, clock, draw):
    root = _repo(tmp_path)
    for i in range(2):
        _refusal(store, _assign(store, root, f"u{i}", 2, aid=f"a{i}"), NOW)
    draw["x"] = 0.99
    _pick(root, unit="new")
    row = [r for r in _rows(store, "assign") if r["unit"] == "new"][0]
    assert row["chosen"] == 1 and row["probs"] == [0.5, 0.5, 0]


# --- 出現率 ---


def _weights(store: Path, root: Path, **entry) -> dict:
    arms = list(mc.table(root).arms["impl"]["claude"])
    base = {
        "repo": ls.repo_key(root),
        "stage": "impl",
        "runtime": "claude",
        "key": mc.compatibility_key(arms, mc.metric_of("impl")),
        "at": _at(NOW - timedelta(hours=1)),
        "phase": "adaptive",
        "candidates": [0, 1, 2],
        "allocation": dict(mc.table(root).allocation),
        "probs": [0.1, 0.1, 0.8],
        "models": {},
        "cutoffs": [],
    }
    store.mkdir(parents=True, exist_ok=True)
    (store / "launch-weights.json").write_text(json.dumps({"version": 1, "entries": [{**base, **entry}]}), encoding="utf-8")
    return {**base, **entry}


def test_adapted_weights_are_drawn_with_their_probabilities(tmp_path, store, clock, draw):
    root = _repo(tmp_path)
    _weights(store, root)
    draw["x"] = 0.15
    _pick(root, unit="u")
    (row,) = _rows(store, "assign")
    assert row["phase"] == "adaptive" and row["probs"] == [0.1, 0.1, 0.8] and row["chosen"] == 1


@pytest.mark.parametrize(
    "entry",
    [{"allocation": {"floor": 0.05, "min_units": 10, "margin": 0.1, "window_days": 30}}, {"candidates": [0, 1]}, {"phase": "burn_in"}],
)
def test_weights_that_do_not_match_are_not_used(tmp_path, store, clock, entry):
    root = _repo(tmp_path)
    _weights(store, root, **entry)
    _pick(root, unit="u")
    (row,) = _rows(store, "assign")
    assert row["phase"] == "burn_in" and row["probs"] == pytest.approx([1 / 3] * 3)


def test_a_newer_actual_model_stops_using_the_weights(tmp_path, store, clock):
    root = _repo(tmp_path)
    _weights(store, root, models={"default": "claude-old"})
    old = _assign(store, root, "prev", 0, NOW - timedelta(days=1), aid="prev")
    _write(store, {"kind": "observed", "at": _at(NOW - timedelta(days=1)), "assign_id": old["assign_id"], "arm": {}, "model": "claude-new"})
    _pick(root, unit="u")
    assert [r["phase"] for r in _rows(store, "assign") if r["unit"] == "u"] == ["burn_in"]


def test_an_unpriced_model_in_the_window_makes_the_draw_uniform(tmp_path, store, clock):
    root = _repo(tmp_path)
    _weights(store, root)
    old = _assign(store, root, "prev", 1, NOW - timedelta(days=1), aid="prev")
    _write(store, {"kind": "unpriced", "at": _at(NOW - timedelta(days=1)), "assign_id": old["assign_id"], "model": "m"})
    _pick(root, unit="u")
    row = [r for r in _rows(store, "assign") if r["unit"] == "u"][0]
    assert row["phase"] == "uniform" and row["probs"] == pytest.approx([1 / 3] * 3)


# --- 基準の組への切り替え ---


def test_fallback_records_the_refusal_and_the_unit_stays_on_the_base(tmp_path, store, clock, draw, caplog):
    root = _repo(tmp_path)
    draw["x"] = 0.9
    choice = _pick(root, unit="u")
    with caplog.at_level(logging.WARNING):
        back = mc.base_fallback(choice, "model_refused")
    assert back is not None and back.model is None and back.model_source is mc.Source.DEFAULT and back.assign_id is None
    (err,) = _rows(store, "launch_error")
    assert err["switched"] and err["assign_id"] == choice.assign_id and err["arm"] == {"model": "sonnet", "effort": "high"}
    assert "sonnet" in caplog.text
    again = _pick(root, unit="u", retry=None)
    assert again.assign_id is None and again.model is None


def test_fallback_raises_again_from_the_base(tmp_path, store, clock):
    root = _repo(tmp_path, {"stages": {"fix": {"arms": {"claude": [{"effort": "medium"}, {"model": "sonnet", "effort": "high"}]}}}})
    _assign(store, root, "r1:fix", 1, stage="fix")
    picked = _pick(root, "fix", unit="r1:fix", retry="test_failed")
    assert (picked.model, picked.effort) == ("opus", "high")
    back = mc.base_fallback(picked, "model_refused")
    assert back is not None and (back.model, back.effort, back.raised) == (None, "high", True)


def test_fallback_of_the_base_or_an_unassigned_launch_is_none(tmp_path, store, clock, draw):
    root = _repo(tmp_path)
    assert mc.base_fallback(_pick(root), "model_refused") is None
    assert mc.base_fallback(_pick(root, unit="u"), "model_refused") is None  # 乱数の既定 0 で基準の組
    assert _rows(store, "launch_error") == []


# --- 記録の読み書き ---


def test_read_rows_skips_broken_lines_and_append_closes_a_torn_end(tmp_path, store, clock):
    path = store / "launch-experiments-2026-11.jsonl"
    store.mkdir(parents=True)
    path.write_bytes(b'not json\n{"kind": "halted"')
    path.chmod(0o600)
    _write(store, {"kind": "halted", "at": _at(NOW), "unit": "u", **SCOPE, "assign_id": None, "reason": "manual"})
    rows = mc.read_experiments(NOW, store)
    assert [r["unit"] for r in rows] == ["u"]


def test_append_refuses_a_row_of_the_wrong_shape(store, clock):
    with pytest.raises(Exception, match="experiment"):
        _write(store, {"kind": "halted", "at": _at(NOW), "unit": "u", **SCOPE, "reason": "manual", "extra": 1})


def test_append_row_returns_false_when_it_cannot_write(store, clock, caplog):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("", encoding="utf-8")  # 保存先がファイルで、記録を書けない
    assert not mc.append_row({"kind": "halted", "at": _at(NOW), "unit": "u", **SCOPE, "assign_id": None, "reason": "manual"}, store)


# --- 月の掃除 ---


def _month_file(store: Path, month: str) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"launch-experiments-{month}.jsonl"
    path.write_text("", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_prune_removes_months_older_than_the_window_and_some_days(store):
    # 2027-01-10 から 60 + 30 日前は 2026-10-12。2026-09 は丸ごと前で消え、2026-10 は境なので残る
    files = {m: _month_file(store, m) for m in ("2026-09", "2026-10", "2027-01")}
    mc.prune(datetime(2027, 1, 10, tzinfo=UTC), 60, store)
    assert [m for m, p in files.items() if p.exists()] == ["2026-10", "2027-01"]
    assert (store / "launch-pruned").read_text(encoding="utf-8") == "2027-01"


def test_prune_does_nothing_once_the_month_is_done(store):
    old = _month_file(store, "2020-01")
    (store / "launch-pruned").write_text("2026-11", encoding="utf-8")
    mc.prune(NOW, 60, store)
    assert old.exists()


def test_prune_drops_weights_past_their_window(tmp_path, store):
    root = _repo(tmp_path)
    kept = _weights(store, root)
    data = json.loads((store / "launch-weights.json").read_text(encoding="utf-8"))
    data["entries"].append({**kept, "stage": "fix", "at": _at(NOW - timedelta(days=61))})
    (store / "launch-weights.json").write_text(json.dumps(data), encoding="utf-8")
    mc.prune(NOW, 60, store)
    left = json.loads((store / "launch-weights.json").read_text(encoding="utf-8"))["entries"]
    assert [e["stage"] for e in left] == ["impl"]


def test_a_failed_prune_is_retried_and_the_launch_goes_on(tmp_path, store, clock, monkeypatch, caplog):
    old = _month_file(store, "2020-01")
    real = Path.unlink

    def refuse(self, missing_ok=False):
        raise PermissionError("拒否")

    monkeypatch.setattr(Path, "unlink", refuse)
    with caplog.at_level(logging.WARNING):
        choice = _pick(_repo(tmp_path, {"enabled": False}), unit="u")
    assert choice.model_source is mc.Source.DEFAULT and not (store / "launch-pruned").exists()
    monkeypatch.setattr(Path, "unlink", real)
    _pick(_repo(tmp_path, {"enabled": False}))
    assert not old.exists() and (store / "launch-pruned").read_text(encoding="utf-8") == "2026-11"


def test_prune_keeps_months_a_longer_window_of_another_worktree_uses(tmp_path, store):
    # 呼んだ側は 60 日でも、同じリポジトリの記録に 200 日の割り付けがあれば 200 + 30 日より前に終わった月だけを消す
    root = _repo(tmp_path)
    files = {m: _month_file(store, m) for m in ("2026-01", "2026-05")}
    _assign(store, root, "long", 1, datetime(2026, 5, 2, tzinfo=UTC), window=200)
    mc.prune(NOW, 60, store)
    assert [m for m, p in files.items() if p.exists()] == ["2026-05"]


def test_prune_writes_the_weights_only_for_the_owner(tmp_path, store, monkeypatch):
    root = _repo(tmp_path)
    kept = _weights(store, root)
    path = store / "launch-weights.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"].append({**kept, "stage": "fix", "at": _at(NOW - timedelta(days=61))})
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    old = os.umask(0o022)
    try:
        mc.prune(NOW, 60, store)
    finally:
        os.umask(old)
    assert path.stat().st_mode & 0o777 == 0o600


# --- 範囲と読めない記録 ---


def test_the_same_unit_name_in_another_repo_or_runtime_is_a_new_unit(tmp_path, store, clock):
    root = _repo(tmp_path)
    other = _assign(store, root, "r1:impl", 2, repo="/other", aid="other")
    _write(store, {"kind": "halted", "at": _at(NOW), "unit": "r1:impl", **SCOPE, "assign_id": "other", "reason": "manual"})
    choice = _pick(root, unit="r1:impl")
    assert choice.assign_id not in (None, other["assign_id"])
    assert len(_rows(store, "assign")) == 2


def test_a_month_that_cannot_be_read_does_not_draw_again(tmp_path, store, clock, caplog):
    root = _repo(tmp_path)
    _assign(store, root, "r1:impl", 2, NOW - timedelta(days=30))
    (store / "launch-experiments-2026-10.jsonl").chmod(0o644)  # 所有者だけの形でない前月は読めない
    with caplog.at_level(logging.WARNING):
        choice = _pick(root, unit="r1:impl")
    assert choice.assign_id is None and choice.model is None and "記録" in caplog.text
    assert not (store / "launch-experiments-2026-11.jsonl").exists()


def test_the_newest_observation_by_time_decides_the_model(tmp_path, store, clock):
    root = _repo(tmp_path)
    _weights(store, root, models={"default": "claude-new"})
    old = _assign(store, root, "prev", 0, NOW - timedelta(days=2), aid="prev")
    for when, model in ((NOW - timedelta(days=1), "claude-new"), (NOW - timedelta(days=2), "claude-old")):  # 古い観測が後から届く
        _write(store, {"kind": "observed", "at": _at(when), "assign_id": old["assign_id"], "arm": {}, "model": model})
    _pick(root, unit="u")
    assert [r["phase"] for r in _rows(store, "assign") if r["unit"] == "u"] == ["adaptive"]


def test_weights_past_their_window_are_not_used_before_the_next_prune(tmp_path, store, clock):
    root = _repo(tmp_path)
    _weights(store, root, at=_at(NOW - timedelta(days=61)))
    (store / "launch-pruned").write_text("2026-11", encoding="utf-8")  # 今月の掃除は済んでいる
    _pick(root, unit="u")
    (row,) = _rows(store, "assign")
    assert row["phase"] == "burn_in"


def test_fallback_writes_to_the_store_the_choice_used(tmp_path, monkeypatch, clock, draw):
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    other = tmp_path / "usage"
    root = _repo(tmp_path)
    draw["x"] = 0.9
    choice = _pick(root, unit="u", environ={"NDF_USAGE_DIR": str(other)})
    assert mc.base_fallback(choice, "model_refused") is not None
    assert [r["switched"] for r in _rows(mc.repo_store(other, ls.repo_key(root)), "launch_error")] == [True]
    assert _pick(root, unit="u", environ={"NDF_USAGE_DIR": str(other)}).assign_id is None


def test_fallback_raises_from_the_base_when_the_arm_was_at_its_top(tmp_path, store, clock):
    root = _repo(tmp_path, {"stages": {"fix": {"arms": {"claude": [{"effort": "medium"}, {"model": "opus", "effort": "max"}]}}}})
    _assign(store, root, "r1:fix", 1, stage="fix")
    picked = _pick(root, "fix", unit="r1:fix", retry="test_failed")
    assert (picked.model, picked.effort, picked.raised) == ("opus", "max", False)
    back = mc.base_fallback(picked, "model_refused")
    assert back is not None and (back.effort, back.raised) == ("high", True)


def test_the_repo_key_of_a_linked_worktree_is_the_main_git_dir_without_git(tmp_path, monkeypatch):
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "w").mkdir(parents=True)
    (main / ".git" / "worktrees" / "w" / "commondir").write_text("../..\n", encoding="utf-8")
    tree = tmp_path / "w"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'w'}\n", encoding="utf-8")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("git を起動した"))
    assert ls.repo_key(tree) == ls.repo_key(main) == os.path.realpath(main / ".git")
    assert ls.repo_key(tmp_path) == os.path.realpath(tmp_path)


def test_a_prune_another_launch_is_running_is_skipped_without_waiting(store):
    from filelock import FileLock

    old = _month_file(store, "2020-01")
    with FileLock(str(store / "launch-pruned.lock")):
        mc.prune(NOW, 60, store)
    assert old.exists() and not (store / "launch-pruned").exists()


def test_a_month_that_cannot_be_read_stops_the_prune_until_the_next_launch(store):
    old = _month_file(store, "2020-01")
    _month_file(store, "2026-10").chmod(0o644)  # 所有者だけの形でない月は、長い window_days の行を持つかもしれない
    with pytest.raises(OSError):
        mc.prune(NOW, 60, store)
    assert old.exists() and not (store / "launch-pruned").exists()


def test_each_repo_keeps_its_own_window_whichever_prunes_first(tmp_path, monkeypatch):
    # 60 日と 300 日のリポジトリ。どちらが先に掃除しても、各リポジトリは自分の設定で消し（300 + 30 日前は 2026-02-14）、
    # 相手のディレクトリは設定によらない 365 + 30 日の境でしか消さないので、同じ月が残る
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    base = mc.records_dir()
    now = datetime(2027, 1, 10, tzinfo=UTC)
    kept = []
    for order in (("short", "long"), ("long", "short")):
        dirs = {name: mc.repo_store(base / f"{order[0]}-first", name) for name in order}
        for d in dirs.values():
            for m in ("2026-01", "2026-09"):
                _month_file(d, m)
        for name in order:
            mc.prune(now, {"short": 60, "long": 300}[name], dirs[name])
        kept.append({name: sorted(mc._month_files(d)) for name, d in dirs.items()})
    assert kept[0] == kept[1] == {"short": [], "long": ["2026-09"]}


def test_prune_sweeps_months_no_setting_uses_from_other_repos(store):
    # 2027-03-10 から 365 + 30 日前は 2026-02-08。ほかのリポジトリの 2026-01 は消え、2026-02 は境なので残る
    files = {m: _month_file(store.parent / ("a" * 16), m) for m in ("2026-01", "2026-02")}
    mc.prune(datetime(2027, 3, 10, tzinfo=UTC), 60, store)
    assert [m for m, p in files.items() if p.exists()] == ["2026-02"]
    assert (store.parent / ("a" * 16)).is_dir()  # 月が残るディレクトリは残す
    assert (store.parent / "launch-swept").read_text(encoding="utf-8") == "2027-03"


def test_the_sweep_runs_once_a_month_for_the_whole_store(tmp_path, store):
    # 2 つ目のリポジトリの掃除は、同じ月の片付けをもう一度しない（ほかのディレクトリを読み直さない）
    mc.prune(datetime(2027, 3, 10, tzinfo=UTC), 60, store)
    late = _month_file(store.parent / ("a" * 16), "2026-01")
    mc.prune(datetime(2027, 3, 11, tzinfo=UTC), 60, mc.repo_store(store.parent.parent, "/other"))
    assert late.exists()


def test_the_sweep_skips_links_and_names_it_did_not_make(tmp_path, store):
    outside = tmp_path / "outside"
    kept = [_month_file(outside, "2020-01"), _month_file(store.parent / "notes", "2020-01")]
    (store.parent / ("b" * 16)).symlink_to(outside, target_is_directory=True)
    mc.prune(datetime(2027, 3, 10, tzinfo=UTC), 60, store)
    assert all(p.exists() for p in kept)


def test_prune_reads_and_drops_months_inside_the_record_lock(store, monkeypatch):
    # 同じリポジトリの別の作業ディレクトリが、読み取りと削除の間に長い window_days の割り付けを書けない
    from filelock import FileLock, Timeout

    _month_file(store, "2020-01")
    real, held = mc._drop_months, []

    def check(root, edge):
        if root == store and not held:  # 自分の月の削除（片付けは自分のディレクトリを通らない）
            with pytest.raises(Timeout):
                FileLock(str(store / "launch-experiments.lock"), timeout=0).acquire()
            held.append(root)
        return real(root, edge)

    monkeypatch.setattr(mc, "_drop_months", check)
    mc.prune(NOW, 60, store)
    assert held == [store]


SWEEP_AT = datetime(2027, 3, 10, tzinfo=UTC)


def _weights_file(root: Path, *days_ago: int) -> Path:
    """`days_ago` 日前に計算した、期間 60 日の出現率の項目を持つファイル。"""
    entries = [{"at": _at(SWEEP_AT - timedelta(days=d)), "allocation": {"window_days": 60}} for d in days_ago]
    path = root / "launch-weights.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def _left(path: Path) -> int:
    return len(json.loads(path.read_text(encoding="utf-8"))["entries"])


def test_a_busy_lock_of_another_repo_does_not_hold_up_the_own_prune(store):
    # ほかのリポジトリの出現率のロックが使用中でも待たずに飛ばし、自分の掃除と片付けの済みを今月にする
    from filelock import FileLock

    other = store.parent / ("a" * 16)
    old = _month_file(other, "2026-01")
    weights = _weights_file(other, 90)
    with FileLock(str(other / "launch-weights.json.lock")):
        mc.prune(SWEEP_AT, 60, store)
    assert (store / "launch-pruned").read_text(encoding="utf-8") == "2027-03"
    assert (store.parent / "launch-swept").read_text(encoding="utf-8") == "2027-03"
    assert not old.exists() and _left(weights) == 1  # 古い月はロックなしで消え、使用中の出現率は次に回す


def test_the_sweep_is_retried_after_the_own_prune_is_done(store):
    # 自分の掃除が今月済みでも、片付けが済んでいなければ次の起動が片付ける
    old = _month_file(store.parent / ("a" * 16), "2026-01")
    _month_file(store, "2027-03")
    (store / "launch-pruned").write_text("2027-03", encoding="utf-8")
    mc.prune(SWEEP_AT, 60, store)
    assert not old.exists() and (store.parent / "launch-swept").read_text(encoding="utf-8") == "2027-03"


def test_the_sweep_drops_weights_that_expired_after_last_months_prune(store):
    other = store.parent / ("a" * 16)
    _month_file(other, "2027-02")
    (other / "launch-pruned").write_text("2027-02", encoding="utf-8")
    weights = _weights_file(other, 61, 5)
    mc.prune(SWEEP_AT, 60, store)
    assert _left(weights) == 1


def _idle_repo(root: Path, pruned: str | None = "2027-01") -> Path:
    _month_file(root, "2026-01")
    _weights_file(root)
    for name in ("launch-experiments.lock", "launch-pruned.lock", "launch-weights.json.lock"):
        (root / name).write_text("", encoding="utf-8")
    if pruned is not None:
        (root / "launch-pruned").write_text(pruned, encoding="utf-8")
    return root


def test_the_sweep_removes_a_repo_ndf_pruned_with_nothing_left(store):
    other = _idle_repo(store.parent / ("a" * 16))
    mc.prune(SWEEP_AT, 60, store)
    assert not other.exists()


def test_the_sweep_keeps_repos_ndf_did_not_mark_or_used_last_month(store):
    unmarked = _idle_repo(store.parent / ("a" * 16), None)
    recent = _idle_repo(store.parent / ("b" * 16), "2027-02")
    mc.prune(SWEEP_AT, 60, store)
    assert (unmarked / "launch-experiments.lock").exists() and (recent / "launch-experiments.lock").exists()


def test_the_sweep_keeps_a_repo_with_weights_or_unknown_files(store):
    kept, unknown = _idle_repo(store.parent / ("a" * 16)), _idle_repo(store.parent / ("b" * 16))
    _weights_file(kept, 9)
    (unknown / "notes.txt").write_text("", encoding="utf-8")
    mc.prune(SWEEP_AT, 60, store)
    assert (kept / "launch-weights.json").exists() and (unknown / "notes.txt").exists()
    assert (unknown / "launch-experiments.lock").exists()  # 利用者が置いたものがあるディレクトリは、何も消さない


def test_the_sweep_keeps_a_repo_whose_files_are_links(tmp_path, store):
    target = tmp_path / "users-file"
    target.write_text("2027-01", encoding="utf-8")
    other = _idle_repo(store.parent / ("a" * 16), None)
    (other / "launch-pruned").symlink_to(target)
    mc.prune(SWEEP_AT, 60, store)
    assert target.exists() and (other / "launch-pruned").is_symlink()


def test_the_sweep_removes_a_half_written_file_ndf_left(store):
    other = _idle_repo(store.parent / ("a" * 16))
    (other / f".launch-weights.json.{'0' * 32}.tmp").write_text("{", encoding="utf-8")
    mc.prune(SWEEP_AT, 60, store)
    assert not other.exists()


def test_a_failed_removal_keeps_its_mark_and_the_next_repo_goes_on(store, monkeypatch):
    broken, fine = _idle_repo(store.parent / ("a" * 16)), _idle_repo(store.parent / ("b" * 16))
    real = Path.unlink

    def refuse(self, missing_ok=False):
        if self.parent == broken and self.name == "launch-weights.json":
            raise PermissionError("拒否")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    mc.prune(SWEEP_AT, 60, store)
    assert not fine.exists() and (broken / "launch-pruned").exists()  # launch-pruned は最後に消すので、翌月も候補になる


def test_a_broken_store_lock_launches_without_records(tmp_path, store, clock, monkeypatch, caplog):
    import sqlite3

    from ndf.adapters import launch_store

    def broken(self, *args, **kwargs):
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(launch_store.ReadWriteLock, "acquire_read", broken)
    with caplog.at_level(logging.WARNING):
        choice = _pick(_repo(tmp_path), unit="u")
    assert choice.assign_id is None and "共有ロック" in caplog.text


def test_a_launch_using_the_store_holds_off_the_removal(store):
    # 起動がリポジトリのロックを取っている間（保存先の共有ロックを持つ間）は回収しない
    from ndf.adapters import launch_store

    other = _idle_repo(store.parent / ("a" * 16))
    with launch_store.using(store.parent, 0):
        mc.prune(SWEEP_AT, 60, store)
    assert (other / "launch-pruned").exists()


def test_records_of_two_repos_are_kept_apart(tmp_path, store, clock):
    root = _repo(tmp_path)
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    (other / ".ndf").mkdir()
    (other / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": {}}), encoding="utf-8")
    _pick(root, unit="u")
    _pick(other, unit="u")
    assert [r["repo"] for r in _rows(store, "assign")] == [ls.repo_key(root)]
    assert [r["repo"] for r in _rows(mc.repo_store(store.parent.parent, ls.repo_key(other)), "assign")] == [ls.repo_key(other)]
