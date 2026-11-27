"""#2944: 出現率のファイル（launch-weights.json）の形を、読む側（割り付け）と書く側（update_weights）の両方で確かめる。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ndf.adapters import model_choice as mc
from ndf.contracts.internal import InternalError

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_git(monkeypatch):
    monkeypatch.setattr(mc.launch_approval, "repo_key", lambda root: os.path.realpath(root))


@pytest.fixture
def store(monkeypatch) -> Path:
    monkeypatch.delenv("NDF_USAGE_DIR", raising=False)
    return mc.records_dir()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(mc, "utc_now", lambda: NOW)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "experiments": {}}), encoding="utf-8")
    return root


def _at(when: datetime) -> str:
    return when.isoformat(timespec="microseconds")


def _entry(root: Path, **change) -> dict:
    arms = list(mc.table(root).arms["impl"]["claude"])
    base = {
        "repo": mc.launch_approval.repo_key(root),
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
    return {k: v for k, v in {**base, **change}.items() if v is not None}


def _put(store: Path, *entries: dict) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / "launch-weights.json"
    path.write_text(json.dumps({"version": 1, "entries": list(entries)}), encoding="utf-8")
    return path


def _drawn(root: Path, store: Path) -> dict:
    mc.launch_choice("worker", "impl", "claude", cwd=root, environ={}, unit="u")
    path = store / "launch-experiments-2026-11.jsonl"
    (row,) = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if '"assign"' in line]
    return row


@pytest.mark.parametrize(
    "change",
    [
        {"probs": [0.2, 0.8]},  # 確率の数が組の数と違う
        {"probs": [0.0, 0.2, 0.8]},  # 候補の確率が 0
        {"probs": [0.1, 0.1, 0.7]},  # 合計が 1 でない
        {"probs": None},  # adaptive なのに確率が無い
        {"candidates": [0, 1, 5], "probs": [0.1, 0.1, 0.8]},  # 範囲の外の添字
    ],
)
def test_adaptive_weights_that_do_not_fit_the_arms_fall_back_to_an_even_draw(tmp_path, store, change):
    root = _repo(tmp_path)
    _put(store, _entry(root, **change))
    row = _drawn(root, store)
    assert row["phase"] == "burn_in" and row["probs"] == pytest.approx([1 / 3] * 3)


def test_a_cutoff_with_a_malformed_deadline_makes_the_whole_file_unused(tmp_path, store):
    root = _repo(tmp_path)
    _put(store, _entry(root, cutoffs=[{"arm": 2, "until": "2027-01-01"}], probs=[0.5, 0.5, 0.0], candidates=[0, 1]))
    row = _drawn(root, store)
    assert row["phase"] == "burn_in" and row["probs"] == pytest.approx([1 / 3] * 3)  # 打ち切りも読まない


def test_a_fitting_entry_is_still_used(tmp_path, store):
    root = _repo(tmp_path)
    _put(store, _entry(root, cutoffs=[{"arm": 2, "until": _at(NOW + timedelta(days=3))}], probs=[0.5, 0.5, 0.0], candidates=[0, 1]))
    row = _drawn(root, store)
    assert row["phase"] == "adaptive" and row["probs"] == [0.5, 0.5, 0.0]


def test_update_weights_replaces_the_entry_of_the_same_key_and_drops_past_ones(tmp_path, store):
    root = _repo(tmp_path)
    old = _entry(root, at=_at(NOW - timedelta(days=61)), stage="fix")
    same = _entry(root, probs=[0.2, 0.2, 0.6])
    path = _put(store, old, same)
    new = _entry(root, at=_at(NOW))
    mc.update_weights(store, NOW, new)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["entries"] == [new]
    assert path.stat().st_mode & 0o777 == 0o600


def test_update_weights_rebuilds_a_broken_file_but_prune_leaves_it(tmp_path, store):
    root = _repo(tmp_path)
    path = _put(store)
    path.write_text("{broken", encoding="utf-8")
    mc.update_weights(store, NOW)
    assert path.read_text(encoding="utf-8") == "{broken"
    mc.update_weights(store, NOW, _entry(root))
    assert [e["stage"] for e in mc.read_weights(store)] == ["impl"]


@pytest.mark.parametrize("change", [{"phase": "uniform"}, {"probs": None}, {"cutoffs": [{"arm": 1, "until": "tomorrow"}]}, {"extra": 1}])
def test_update_weights_refuses_an_entry_of_the_wrong_shape(tmp_path, store, change):
    root = _repo(tmp_path)
    with pytest.raises(InternalError):
        mc.update_weights(store, NOW, _entry(root, **change))
    assert not (store / "launch-weights.json").exists()


def test_probs_fit_checks_the_indexes_even_while_burning_in(tmp_path):
    root = _repo(tmp_path)
    assert mc.probs_fit(_entry(root, phase="burn_in", probs=None), 3)
    assert not mc.probs_fit(_entry(root, phase="burn_in", probs=None, cutoffs=[{"arm": 3, "until": _at(NOW)}]), 3)
