"""lib/slow_step.py: 想定時間・設定の重ね方・所要の履歴・組み込みの一次の調査。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import slow_step as ss  # noqa: E402


def test_config_layers_args_over_plan_over_decl_over_default():
    cfg = ss.resolve_config({"factor": "2"}, {"factor": 4, "floor": 100}, {"floor": 50, "window": 5})
    assert (cfg.factor, cfg.floor, cfg.window, cfg.default) == (2.0, 100.0, 5, 900.0)
    assert ss.resolve_config().max_retry == 1


@pytest.mark.parametrize("layer", [{"nope": 1}, {"window": "x"}, {"enabled": "maybe"}, {"window": 1.5},
                                   {"factor": -1}])
def test_config_rejects_unknown_key_and_bad_value(layer):
    with pytest.raises(ss.SlowConfigError):
        ss.resolve_config(None, layer, None)


def test_parse_overrides():
    assert ss.parse_overrides(["factor=2", "enabled=false"]) == {"factor": "2", "enabled": "false"}
    assert ss.resolve_config(ss.parse_overrides(["enabled=false"])).enabled is False
    with pytest.raises(ss.SlowConfigError):
        ss.parse_overrides(["factor"])


def test_expected_uses_default_floor_history_and_step():
    cfg = ss.resolve_config()
    assert ss.expected_for([100, 100], cfg)[0] == 900.0
    assert ss.expected_for([100, 100], cfg)[1]["source"] == "default"
    assert ss.expected_for([10, 10, 10], cfg) == (300.0, {"source": "floor", "samples": 3, "factor": 3.0,
                                                          "floor": 300.0, "median": 10.0})
    value, basis = ss.expected_for([200, 200, 200], cfg)
    assert (value, basis["source"]) == (600.0, "history")
    assert ss.expected_for([1, 2, 3], cfg, step_expected=5) == (5.0, {"source": "step"})


def test_expected_changes_with_each_key():
    hist = [100.0] * 3 + [400.0] * 3
    base = ss.resolve_config()
    assert ss.expected_for(hist, base)[0] == 750.0
    assert ss.expected_for(hist, ss.resolve_config({"factor": 2}))[0] == 500.0
    assert ss.expected_for(hist, ss.resolve_config({"floor": 1000}))[0] == 1000.0
    assert ss.expected_for(hist, ss.resolve_config({"window": 3}))[0] == 1200.0
    assert ss.expected_for(hist, ss.resolve_config({"min_samples": 7, "default": 42}))[0] == 42.0


def test_past_outliers_are_caught_and_usual_steps_are_not():
    release = [48.7, 60.0, 65.0, 70.0, 72.3, 73.0, 80.0, 90.0, 120.0, 168.7]
    value, basis = ss.expected_for(release, ss.resolve_config())
    assert basis["median"] == 72.65 and basis["source"] == "floor" and value == 300.0
    assert 1148.8 > value
    impl = [300.0, 350.0, 380.0, 400.0, 410.0, 415.8, 420.0, 450.0, 500.0, 600.0]
    value, basis = ss.expected_for(impl, ss.resolve_config())
    assert basis["median"] == 412.9 and value == 1238.7
    assert 886.3 < value


def test_history_append_and_read(tmp_path):
    h = tmp_path / "h" / "step-history.jsonl"
    for sec in (1, 2, 3):
        assert ss.append_history(h, ss.history_record("実装", "impl", "work", sec, 0, "p.json", f"t{sec}")) is None
    ss.append_history(h, ss.history_record("配布", "impl", "work", 99, 0, "p.json", "t9"))
    assert ss.read_history(h, "実装", "impl") == [1.0, 2.0, 3.0]
    assert ss.read_history(h, "実装", "impl", 2) == [2.0, 3.0]
    assert ss.read_history(tmp_path / "none.jsonl", "実装", "impl") == []


def test_history_path_defaults(tmp_path):
    assert ss.history_path(tmp_path, tmp_path / "st") == tmp_path / "st" / "step-history.jsonl"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert ss.history_path(repo, tmp_path / "st") == (repo / ".git" / "ndf" / "step-history.jsonl").resolve()
    assert ss.history_path(repo, tmp_path / "st", "x/h.jsonl") == repo.resolve() / "x" / "h.jsonl"


def write_progress(d: Path, name: str, rows):
    st = d / f"{name}-state"
    st.mkdir(parents=True)
    (d / f"{name}.json").write_text(json.dumps({"フェーズ": "実装", "steps": []}))
    p = st / "progress.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_import_keeps_success_and_gate_and_skips_duplicates(tmp_path):
    rows = [{"kind": "step", "at": "a1", "step": "impl", "type": "work", "exit": 0, "seconds": 10},
            {"kind": "step", "at": "a2", "step": "merge", "type": "run", "exit": 1, "seconds": 20},
            {"kind": "step", "at": "a3", "step": "gate", "type": "run", "exit": 10, "seconds": 5},
            {"kind": "step", "at": "a4", "step": "judge", "type": "judge", "exit": 0, "seconds": 3},
            {"kind": "alive", "at": "a5", "step": "impl", "elapsed": 600}]
    p = write_progress(tmp_path, "one", rows)
    h = tmp_path / "h.jsonl"
    assert ss.import_progress([p], h)["added"] == 2
    again = ss.import_progress([p], h)
    assert (again["added"], again["skipped"]) == (0, 2)
    assert ss.read_history(h, "実装", "impl") == [10.0]


def test_import_keeps_same_row_of_different_plans(tmp_path):
    row = [{"kind": "step", "at": "a1", "step": "impl", "type": "work", "exit": 0, "seconds": 10}]
    h = tmp_path / "h.jsonl"
    res = ss.import_progress([write_progress(tmp_path, "one", row), write_progress(tmp_path, "two", row)], h)
    assert res["added"] == 2


def test_probe_output_sees_growth(tmp_path):
    f = tmp_path / "err.log"
    f.write_text("a\n")
    first, size = ss.probe_output(f, 0)
    assert first["action"] == "wait"
    second, _ = ss.probe_output(f, size)
    assert second["action"] == "judge" and "a" in second["summary"]


def test_probe_worker():
    assert ss.probe_worker(1, 0, "", None)["action"] == "wait"
    assert ss.probe_worker(0, 1, "", None)["action"] == "wait"
    silent = ss.probe_worker(0, 0, "テストを足した", 42)
    assert silent["action"] == "judge" and "テストを足した" in silent["summary"]


def test_probe_cmd_does_not_go_through_shell(tmp_path):
    script = tmp_path / "p.py"
    script.write_text("import json, sys\nprint(json.dumps({'status': 'ok', 'summary': sys.argv[1], "
                      "'metrics': {'class': 'c', 'action': 'wait'}}))\n")
    out = ss.probe_cmd(f"{sys.executable} {script} {{branch}}", {"branch": "a;touch x"}, tmp_path, 10)
    assert out["summary"] == "a;touch x" and out["action"] == "wait"
    assert not (tmp_path / "x").exists()


def test_probe_cmd_unreadable_is_judge(tmp_path):
    out = ss.probe_cmd("sh -c 'echo nope; exit 3'", {}, tmp_path, 10)
    assert (out["class"], out["action"]) == ("unknown", "judge") and "exit=3" in out["summary"]
    slow = ss.probe_cmd("sleep 5", {}, tmp_path, 0.2)
    assert slow["action"] == "judge"
