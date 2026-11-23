from __future__ import annotations

import contextlib
import json
import signal
from contextlib import contextmanager
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import causes, result
from ndf.testing import command, records, slots


@pytest.fixture
def project(tmp_path):
    """設定の runner が `fakerun` のプロジェクトのディレクトリ。"""
    root = tmp_path / "project"
    (root / ".ndf").mkdir(parents=True)
    suites = [{"name": "fake", "runner": "fakerun", "command": "fakerun ."}]
    (root / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}))
    (root / ".ndf" / "resources.json").write_text(
        json.dumps({"test_slots": {"max_runs": 1, "memory_floor_mib": 1, "wait_limit_seconds": 120}})
    )
    return root


def _result(out: str, code: int) -> dict:
    """標準出力の 1 行の結果 JSON。コントラクト（形と終了コードの対応）に合うことも確かめる。"""
    obj = json.loads(out)
    assert result.errors(obj, code) == []
    return obj


def test_report_lists_the_old_state_locations_it_retired(monkeypatch, tmp_path, capsys):
    # スロットの保存先のために状態の保存先を用意して旧い保存先を退避したら、元と先を items に載せる（エントリポイントを通らずに呼んだとき）
    state_home = tmp_path / "xdg-state"
    old = state_home / identity.load().name
    old.mkdir(parents=True)
    (old / "journal.jsonl").write_text("old")
    for name, value in {slots.SLOTS_DIR_ENV: "", "XDG_STATE_HOME": str(state_home), "CLAUDE_CONFIG_DIR": str(tmp_path / "config")}.items():
        monkeypatch.setenv(name, value)

    assert command.main(["report", "--since", "1d"]) == 0

    items = _result(capsys.readouterr().out, 0)["items"]
    assert [item["retired_from"] for item in items] == [str(old)]
    assert (Path(items[0]["retired_to"]) / "journal.jsonl").read_text() == "old"


def test_report_lists_the_locations_retired_before_preparing_failed(monkeypatch, capsys):
    # 退避の途中で失敗して止まるときも、先に退避した元と先を items に載せる
    retired = (identity.Retired(Path("/s/p"), Path("/s/p.v10-20261114")),)

    def fail(environ=None):
        raise identity.IdentityError("read-only", retired)

    monkeypatch.setattr(slots, "slots_location", fail)
    code = command._report("1d")
    got = _result(capsys.readouterr().out, code)
    assert got["items"] == [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]


def test_stopped_run_lists_the_old_state_locations_it_retired(capsys):
    retired = [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]
    code = command._stopped_from(slots.Stopped(records.GAVE_UP, 1.0, None, retired))
    got = _result(capsys.readouterr().out, code)
    assert got["items"] == retired


# --- 現状固定テスト（_run の経路: メモリで止める・中断） ---


def _gate_stopping_by_memory(command_text, cwd, *, options=None):
    """`slots.gate` の代わり。スロットを持たない実行を渡し、出るときにメモリで止めたとして `Stopped` を送る。"""

    @contextmanager
    def gate():
        yield slots.Held()
        raise slots.Stopped(records.MEMORY_STOPPED, 2.0, 100)

    return gate()


def test_memory_stop_exits_apart_from_a_failed_test(project, monkeypatch, capsys):
    # メモリで止めた実行は落ちたテストとして数えない（I9）。終了コードは落ちたテストの 1 と重ならない
    monkeypatch.chdir(project)
    monkeypatch.setattr(slots, "gate", _gate_stopping_by_memory)
    code = command.main(["run", "--", "true"])
    got = json.loads(capsys.readouterr().out)
    assert code == causes.exit_code("infra_wait") != causes.exit_code("test_failed")
    assert got["status"] == "stopped"
    assert got["cause"]["kind"] == "infra_wait" and got["cause"]["instance"] == records.MEMORY_STOPPED
    assert "next" not in got


def test_interrupted_run_writes_the_old_state_locations_it_retired(monkeypatch, tmp_path, capsys):
    # 中断で終わる run でも、退避した旧い保存先の元と先を標準エラーへ残す
    retired = [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]

    @contextlib.contextmanager
    def interrupted_gate(*_args, **_kwargs):
        raise slots.Interrupted(retired)
        yield  # pragma: no cover

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(slots, "gate", interrupted_gate)

    code = command._run("true", None)

    assert code == 128 + signal.SIGINT
    assert capsys.readouterr().err.splitlines()[-1] == command.RETIRED_LINE.format(**retired[0])
