"""`test-run.py whole` / `scope` — 手元の全体テストと範囲テストの上限は suite 群全体で 1 つ（#1334）。"""

from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "test-run.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("ndf_test_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _whole(mod, monkeypatch, tmp_path, seconds_per_suite):
    """3 本の suite が各 `seconds_per_suite` 秒かかる全体テストを、上限 100 秒で走らせる。渡した上限の並びを返す。"""
    strategy = types.SimpleNamespace(name="local-full", source="test", whole_on_ci=False, whole_commands=lambda: ["a", "b", "c"])
    monkeypatch.setattr(mod, "_resolve", lambda root, template: (strategy, {"whole_timeout": 100, "test_timeout": 100}, []))
    monkeypatch.setattr(mod.test_triage, "clear_junit", lambda work, s: None)
    now = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    given: list[int] = []

    def run(command, cwd, timeout, log=None):
        given.append(timeout)
        if seconds_per_suite > timeout:
            now[0] += timeout
            return None, True
        now[0] += seconds_per_suite
        return 0, False

    monkeypatch.setattr(mod.test_triage, "run_command", run)
    with pytest.raises(SystemExit) as e:  # `step_result.emit` は結果を出して終える
        mod.cmd_whole(types.SimpleNamespace(root=str(tmp_path), template=None, base=None))
    return e.value.code, given


def test_later_suites_get_only_the_seconds_left(mod, monkeypatch, tmp_path, capsys):
    code, given = _whole(mod, monkeypatch, tmp_path, 30)
    assert code == 0
    assert given == [100, 70, 40]


def test_the_limit_is_shared_so_the_suites_cannot_run_n_times_longer(mod, monkeypatch, tmp_path, capsys):
    code, given = _whole(mod, monkeypatch, tmp_path, 60)
    assert code != 0
    assert given == [100, 40], "2 本目で合計の上限に届いて止まる（suite ごとに 100 秒を渡さない）"
    assert "全体テストが 100 秒で終わらなかった" in capsys.readouterr().out


def _scope(mod, monkeypatch, tmp_path, seconds_per_suite):
    """2 本の suite の範囲テストが各 `seconds_per_suite` 秒かかるのを、上限 100 秒で走らせる。渡した上限の並びを返す。"""
    strategy = mod.ts.Strategy(
        "local-full",
        "test",
        [mod.ts.Suite("a", "run a", "run-a {paths}", paths=["a"]), mod.ts.Suite("b", "run b", "run-b {paths}", paths=["b"])],
    )
    monkeypatch.setattr(mod, "_resolve", lambda root, template: (strategy, {"whole_timeout": 100, "test_timeout": 100}, []))
    monkeypatch.setattr(mod.test_triage, "clear_junit", lambda work, s: None)
    now = [0.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    given: list[int] = []

    def run(command, cwd, timeout, log=None):
        given.append(timeout)
        if seconds_per_suite > timeout:
            now[0] += timeout
            return None, True
        now[0] += seconds_per_suite
        return 0, False

    monkeypatch.setattr(mod.test_triage, "run_command", run)
    with pytest.raises(SystemExit) as e:
        mod.cmd_scope(types.SimpleNamespace(root=str(tmp_path), template=None, base=None, paths=["a/t.py", "b/t.py"]))
    return e.value.code, given


def test_scope_suites_share_the_limit(mod, monkeypatch, tmp_path, capsys):
    code, given = _scope(mod, monkeypatch, tmp_path, 30)
    assert code == 0
    assert given == [100, 70]


def test_scope_suites_cannot_run_n_times_longer(mod, monkeypatch, tmp_path, capsys):
    code, given = _scope(mod, monkeypatch, tmp_path, 60)
    assert code != 0
    assert given == [100, 40], "2 本目は残りの 40 秒だけを受け取る"
