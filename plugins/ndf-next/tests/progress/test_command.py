"""副命令 `ndf progress`（受け入れ条件 1・2・3・5・10。P1-2 の戻し）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ndf import identity
from ndf.core import durable
from ndf.progress import command, model, store


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "work"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    monkeypatch.chdir(path)
    yield path
    durable.release_store(_root(path))


def _root(cwd: Path) -> Path:
    return durable.store_root(identity.load().name, cwd)


def _run(capsys, *argv: str) -> tuple[int, dict]:
    code = command.main(list(argv))
    lines = capsys.readouterr().out.strip().splitlines()
    return code, json.loads(lines[-1])


def _new(capsys, *extra: str) -> tuple[int, dict]:
    return _run(capsys, "new", "s1", "--mode", "standard", "--issues", "3369", *extra)


def test_new_lays_out_every_row_of_the_mode(repo, capsys):
    code, out = _new(capsys)
    assert code == 0 and out["status"] == "ok"
    rows = out["items"]
    assert [r["stage"] for r in rows][0] == "要求と受け入れ条件" and rows[-1]["stage"] == "棚卸し"
    assert len(rows) == 18 and {r["label"] for r in rows} == {"未着手"}


def test_second_table_for_the_same_sprint_is_refused_with_the_current_id(repo, capsys):
    _, first = _new(capsys)
    code, out = _new(capsys)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"
    assert out["metrics"]["current_id"] == first["metrics"]["table_id"]
    assert code != 0


def test_disabled_rows_come_from_the_argument_and_the_project_declaration(repo, capsys):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(json.dumps({"workflow": {"stages": {"リファクタリング": {"enabled": False, "reason": "止めている"}}}}))
    _, out = _new(capsys, "--disable", "振り返り=今回は不要")
    reasons = {r["stage"]: r["reason"] for r in out["items"] if r["state"] == "disabled"}
    assert reasons == {"リファクタリング": "止めている", "振り返り": "今回は不要"}


def test_unknown_stage_in_disable_is_an_invocation_error(repo, capsys):
    code, out = _new(capsys, "--disable", "存在しない=理由")
    assert out["cause"]["kind"] == "invalid" and code == 2


def test_requirements_output_marks_the_first_row_finished(repo, capsys, tmp_path):
    good = tmp_path / "req.json"
    good.write_text(json.dumps({"steps": [], "summary": "要求を書いた"}))
    _, out = _new(capsys, "--requirements", str(good))
    assert out["items"][0]["label"] == "終了"


def test_conductor_operations_leave_reason_and_time(repo, capsys, tmp_path):
    _, made = _new(capsys)
    tid = made["metrics"]["table_id"]
    assert _run(capsys, "disable", tid, "実装", "--reason", "外す")[0] == 0
    assert _run(capsys, "enable", tid, "実装", "--reason", "戻す")[0] == 0
    out_file = tmp_path / "plan.json"
    out_file.write_text(json.dumps({"steps": [], "summary": "手で計画した"}))
    assert _run(capsys, "finish", tid, "計画", "--output", str(out_file), "--reason", "手で行った")[0] == 0
    _, shown = _run(capsys, "show", tid)
    history = shown["metrics"]["history"]
    assert [(h["kind"], h["reason"]) for h in history[-3:]] == [("disabled", "外す"), ("enabled", "戻す"), ("manual_finished", "手で行った")]
    assert all(h["at"] for h in history)
    assert {r["stage"]: r["label"] for r in shown["items"]}["計画"] == "終了"


def _start(tid: str, row: str) -> None:
    root = _root(Path.cwd())
    with store.open_store(store.default_url(root), root) as st, store.transaction(st) as conn:
        last = store.read_events(conn, tid)[-1].seq
        store.append_events(conn, tid, [model.EventDraft("started", "workflow", row, rev=1)], last)


def test_running_row_cannot_be_disabled_and_bad_output_cannot_finish(repo, capsys, tmp_path):
    _, made = _new(capsys)
    tid = made["metrics"]["table_id"]
    _start(tid, "設計")
    _, out = _run(capsys, "disable", tid, "設計", "--reason", "止めたい")
    assert out["cause"]["kind"] == "refused" and "実行中" in out["cause"]["detail"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"steps": []}))
    _, out = _run(capsys, "finish", tid, "要求と受け入れ条件", "--output", str(bad), "--reason", "手で")
    assert out["cause"]["kind"] == "refused" and "規約に合わない" in out["cause"]["detail"]


def test_retry_only_on_a_failed_row(repo, capsys):
    _, made = _new(capsys)
    tid = made["metrics"]["table_id"]
    _, out = _run(capsys, "retry", tid, "設計", "--reason", "やり直す")
    assert out["cause"]["kind"] == "refused"


def test_cancel_closes_the_remaining_rows_and_the_table(repo, capsys):
    _, made = _new(capsys, "--disable", "振り返り=不要")
    tid = made["metrics"]["table_id"]
    _, out = _run(capsys, "cancel", tid, "--reason", "計画から外した")
    assert out["metrics"]["cancelled_rows"] == 17
    _, shown = _run(capsys, "show", tid)
    assert shown["metrics"]["state"] == "cancelled"
    labels = {r["stage"]: r["label"] for r in shown["items"]}
    assert labels["振り返り"] == "無効" and labels["設計"] == "中止"
    _, again = _run(capsys, "enable", tid, "振り返り", "--reason", "戻す")
    assert again["cause"]["kind"] == "refused"


def test_restore_command_puts_back_a_backup(repo, capsys):
    _new(capsys)
    root = _root(repo)
    durable.release_store(root)
    backup = store.latest_backup(root)
    code, out = _run(capsys, "restore", "--from", str(backup))
    assert code == 0 and out["items"][0]["from"] == str(backup)


def test_restore_command_refuses_a_postgresql_url(repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("NDF_PROGRESS_DB", "postgresql://ndf@127.0.0.1:1/none")
    code, out = _run(capsys, "restore", "--from", str(tmp_path / "x.sqlite"))
    assert out["cause"]["kind"] == "restore_unsupported" and code == 3


def test_show_of_an_unknown_table_is_refused(repo, capsys):
    _, out = _run(capsys, "show", "sprint:nope:s9")
    assert out["status"] == "stopped" and out["cause"]["kind"] == "refused"
