from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ndf.adapters import cli, run_log
from ndf.contracts import limits

SRC = Path(__file__).resolve().parents[2] / "src"
NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_root_run_writes_start_line_under_repo_and_utc_date():
    log = cli.open_run("entry", run_id="r1", location="repo", now=NOW)

    assert log.path == cli.runs_dir() / "repo" / "2026-11-14" / "r1.jsonl"
    start = lines(log.path)[0]
    assert (start["run_id"], start["parent_id"], start["entry"], start["event"]) == ("r1", None, "entry", "start")
    assert cli.current() is log
    assert log.child_env() == {cli.ENV_RUN_ID: "r1", cli.ENV_RUN_LOG: str(log.path)}


def test_child_writes_to_parent_file_with_parent_id(monkeypatch):
    parent = cli.open_run("parent", run_id="p", location="repo", now=NOW)
    cli.close()
    for key, val in parent.child_env().items():
        monkeypatch.setenv(key, val)

    child = cli.open_run("child", run_id="c")

    assert child.path == parent.path
    assert [(row["run_id"], row["parent_id"]) for row in lines(parent.path)] == [("p", None), ("c", "p")]


def test_open_twice_in_one_process_is_refused():
    cli.open_run("a", location="repo")

    with pytest.raises(cli.RunLogError):
        cli.open_run("b", location="repo")


def test_root_run_prunes_dirs_older_than_keep_days():
    repo = cli.runs_dir() / "repo"
    for name in ("2026-10-14", "2026-10-15", "notes"):
        (repo / name).mkdir(parents=True)

    cli.open_run("entry", location="repo", now=NOW)

    assert not (repo / "2026-10-14").exists()
    assert (repo / "2026-10-15").is_dir() and (repo / "notes").is_dir()


def test_unwritable_place_and_unknown_sink_are_refused(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    with pytest.raises(cli.RunLogError):
        cli.open_run("entry", location="repo")

    monkeypatch.setenv(cli.ENV_RUN_LOG_SINK, "nowhere")
    with pytest.raises(cli.RunLogError):
        cli.open_run("entry", location="repo")
    assert cli.current() is None


def test_stderr_sink_writes_lines_to_stderr(monkeypatch, capsys):
    monkeypatch.setenv(cli.ENV_RUN_LOG_SINK, "stderr")

    log = cli.open_run("entry", run_id="s")

    assert log.path is None and cli.ENV_RUN_LOG not in log.child_env()
    assert json.loads(capsys.readouterr().err)["event"] == "start"


def test_diag_writes_same_text_to_stderr_and_record(capsys):
    log = cli.open_run("entry", location="repo")

    cli.diag("全文の診断")
    cli.diag("長い全文", short="短い文", kind="stderr")

    assert capsys.readouterr().err == "全文の診断\n短い文\n"
    diags = [row["fields"] for row in lines(log.path) if row["event"] == "diag"]
    assert diags == [{"text": "全文の診断", "display": None}, {"text": "長い全文", "display": "短い文", "kind": "stderr"}]


def test_diag_hides_secret_env_values(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_secretvalue123")
    log = cli.open_run("entry", location="repo")

    cli.diag("token は ghp_secretvalue123 だった")

    assert "ghp_secretvalue123" not in log.path.read_text()
    assert cli.MASK in log.path.read_text()


def test_diag_opens_child_run_when_parent_id_is_inherited(monkeypatch, capsys):
    parent = cli.open_run("parent", run_id="p", location="repo")
    cli.close()
    for key, val in parent.child_env().items():
        monkeypatch.setenv(key, val)

    cli.diag("子の診断")

    rows = lines(parent.path)
    assert rows[-1]["event"] == "diag" and rows[-1]["parent_id"] == "p"


def test_clip_keeps_full_text_only_when_cut():
    log = cli.open_run("entry", location="repo")

    assert cli.clip("abc", 3, "what") == "abc"
    assert [row["event"] for row in lines(log.path)] == ["start"]
    assert cli.clip("abcd", 3, "what") == "abc"
    last = lines(log.path)[-1]
    assert last["event"] == "clip" and last["fields"] == {"what": "what", "full": "abcd", "shown": "abc"}
    with pytest.raises(ValueError):
        cli.clip("x", 0, "what")


def test_clip_without_record_only_cuts():
    assert cli.clip("abcd", 2, "what") == "ab"


def test_long_strings_go_to_full_text_file():
    log = cli.open_run("entry", location="repo")
    text = "x" * 40000

    log.write("big", body=text)

    body = lines(log.path)[-1]["fields"]["body"]
    assert Path(body["full_text"]).read_text() == text and body["head"] == text[:200]


def test_finish_writes_end_and_stops_when_record_failed(capsys):
    log = cli.open_run("entry", location="repo")
    obj = {"tool": "entry", "status": "ok", "summary": "", "items": [], "metrics": {}}

    assert log.finish(obj, 0) == (obj, 0)
    assert lines(log.path)[-1]["fields"] == {"result": obj, "exit": 0}

    log.path = log.path.parent / "gone" / "x.jsonl"
    stopped, code = log.finish(obj, 0)
    assert stopped["status"] == "stopped" and code != 0
    assert "実行の記録に書けない" in capsys.readouterr().err


def test_parallel_children_write_whole_lines():
    parent = cli.open_run("parent", run_id="p", location="repo")
    script = "from ndf.adapters import cli\nlog = cli.open_run('child')\nfor i in range(100):\n    log.write('n', i=i)\n"
    env = {**os.environ, **parent.child_env(), "PYTHONPATH": str(SRC)}

    procs = [subprocess.Popen([sys.executable, "-c", script], env=env) for _ in range(4)]

    assert [p.wait(timeout=60) for p in procs] == [0, 0, 0, 0]
    rows = lines(parent.path)
    assert len([row for row in rows if row["event"] == "n"]) == 400
    assert len({row["run_id"] for row in rows}) == 5


def test_half_written_line_is_closed_before_next(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"cut": ')

    run_log._append_line(path, b'{"a": 1}\n')

    assert path.read_bytes().splitlines()[-1] == b'{"a": 1}'


def test_token_shapes_are_hidden_without_secret_env():
    log = cli.open_run("entry", location="repo")

    log.write(
        "fail",
        out="push https://user:pa55word@github.com/o/r.git で ghp_abcdefgh12345678 を使った",
        headers=["Authorization: Bearer abc.def.ghi"],
    )

    text = log.path.read_text()
    assert "pa55word" not in text and "ghp_abcdefgh12345678" not in text and "abc.def.ghi" not in text


@pytest.mark.parametrize("location", ["../outside", "/abs/outside", "..", "a/b"])
def test_location_outside_runs_is_refused_before_touching_dirs(tmp_path, location):
    old = run_log.runs_dir().parent / "outside" / "2000-01-01"
    old.mkdir(parents=True)

    with pytest.raises(cli.RunLogError):
        cli.open_run("entry", location=location, now=NOW)

    assert old.is_dir() and cli.current() is None


def test_stderr_sink_keeps_full_text_in_part_lines(monkeypatch, capsys):
    monkeypatch.setenv(cli.ENV_RUN_LOG_SINK, "stderr")
    log = cli.open_run("entry", run_id="s")
    capsys.readouterr()
    text = ('あ\x01"x' * 20000) + "末尾"

    log.write("big", body=text)

    raw = capsys.readouterr().err.splitlines()
    limit = int(limits.value("run-log.line", "count"))
    assert all(len(line.encode()) + 1 <= limit for line in raw)
    rows = [json.loads(line) for line in raw]
    parts = [r["fields"] for r in rows if r["event"] == "text_part"]
    body = rows[-1]["fields"]["body"]
    assert "".join(p["text"] for p in sorted(parts, key=lambda p: p["part"])) == text
    assert body["parts"] == len(parts) and body["head"] == text[:200] and body["chars"] == len(text)


def test_whole_line_fits_limit_with_nested_strings():
    log = cli.open_run("entry", location="repo")
    a, b = "a" * 20000, "b" * 20000

    log.write("end", result={"summary": a, "items": [{"text": b}]}, exit=0)

    raw = log.path.read_bytes().splitlines()[-1]
    assert len(raw) + 1 <= 32000
    result = json.loads(raw)["fields"]["result"]
    values = (result["summary"], result["items"][0]["text"])
    moved = [v for v in values if isinstance(v, dict)]
    kept = [v for v in values if isinstance(v, str)]
    assert len(moved) == 1 and {Path(moved[0]["full_text"]).read_text(), *kept} == {a, b}


def test_whole_fields_move_when_many_short_strings_overflow():
    log = cli.open_run("entry", location="repo")
    many = ["z" * 150 for _ in range(400)]

    log.write("end", result=many)

    raw = log.path.read_bytes().splitlines()[-1]
    assert len(raw) + 1 <= 32000
    moved = json.loads(raw)["fields"]["fields_json"]
    assert json.loads(Path(moved["full_text"]).read_text()) == {"result": many}
