from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import folded
import pytest

from ndf.adapters import run_log
from ndf.contracts import limits
from ndf.contracts import result as results

NOW = datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_root_run_writes_start_line_under_repo_and_utc_date():
    log = run_log.open_run("entry", run_id="r1", location="repo", now=NOW)

    assert log.path == run_log.runs_dir() / "repo" / "2026-11-14" / "r1.jsonl"
    start = lines(log.path)[0]
    assert (start["run_id"], start["parent_id"], start["entry"], start["event"]) == ("r1", None, "entry", "start")
    assert run_log.current() is log
    assert log.child_env() == {run_log.ENV_RUN_ID: "r1", run_log.ENV_RUN_LOG: str(log.path)}


def test_child_writes_to_parent_file_with_parent_id(monkeypatch):
    parent = run_log.open_run("parent", run_id="p", location="repo", now=NOW)
    run_log.close()
    for key, val in parent.child_env().items():
        monkeypatch.setenv(key, val)

    child = run_log.open_run("child", run_id="c")

    assert child.path == parent.path
    assert [(row["run_id"], row["parent_id"]) for row in lines(parent.path)] == [("p", None), ("c", "p")]


def test_open_twice_in_one_process_is_refused():
    run_log.open_run("a", location="repo")

    with pytest.raises(run_log.RunLogError):
        run_log.open_run("b", location="repo")


def test_root_run_prunes_dirs_older_than_keep_days():
    repo = run_log.runs_dir() / "repo"
    for name in ("2026-10-14", "2026-10-15", "notes"):
        (repo / name).mkdir(parents=True)

    run_log.open_run("entry", location="repo", now=NOW)

    assert not (repo / "2026-10-14").exists()
    assert (repo / "2026-10-15").is_dir() and (repo / "notes").is_dir()


def test_unwritable_location_and_unknown_sink_are_refused(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    with pytest.raises(run_log.RunLogError):
        run_log.open_run("entry", location="repo")

    monkeypatch.setenv(run_log.ENV_RUN_LOG_SINK, "nowhere")
    with pytest.raises(run_log.RunLogError):
        run_log.open_run("entry", location="repo")
    assert run_log.current() is None


def test_stderr_sink_writes_lines_to_stderr(monkeypatch, capsys):
    monkeypatch.setenv(run_log.ENV_RUN_LOG_SINK, "stderr")

    log = run_log.open_run("entry", run_id="s")

    assert log.path is None and run_log.ENV_RUN_LOG not in log.child_env()
    assert json.loads(capsys.readouterr().err)["event"] == "start"


def test_diag_writes_same_text_to_stderr_and_record(capsys):
    log = run_log.open_run("entry", location="repo")

    run_log.diag("全文の診断")
    run_log.diag("長い全文", short="短い文", kind="stderr")

    assert capsys.readouterr().err == "全文の診断\n短い文\n"
    diags = [row["fields"] for row in lines(log.path) if row["event"] == "diag"]
    assert diags == [{"text": "全文の診断", "display": None}, {"text": "長い全文", "display": "短い文", "kind": "stderr"}]


def test_diag_hides_secret_env_values(monkeypatch, capsys):
    monkeypatch.setenv("GH_TOKEN", "ghp_secretvalue123")
    log = run_log.open_run("entry", location="repo")

    run_log.diag("token は ghp_secretvalue123 だった")
    run_log.diag("全文", short="短い文の ghp_secretvalue123")

    assert "ghp_secretvalue123" not in log.path.read_text()
    assert run_log.MASK in log.path.read_text()
    err = capsys.readouterr().err
    assert "ghp_secretvalue123" not in err
    assert run_log.MASK in err


def test_diag_opens_child_run_when_parent_id_is_inherited(monkeypatch, capsys):
    parent = run_log.open_run("parent", run_id="p", location="repo")
    run_log.close()
    for key, val in parent.child_env().items():
        monkeypatch.setenv(key, val)

    run_log.diag("子の診断")

    rows = lines(parent.path)
    assert rows[-1]["event"] == "diag" and rows[-1]["parent_id"] == "p"


def test_clip_keeps_full_text_only_when_cut():
    log = run_log.open_run("entry", location="repo")

    assert run_log.clip("abc", 3, "what") == "abc"
    assert [row["event"] for row in lines(log.path)] == ["start"]
    assert run_log.clip("abcd", 3, "what") == "abc"
    last = lines(log.path)[-1]
    assert last["event"] == "clip" and last["fields"] == {"what": "what", "full": "abcd", "shown": "abc"}
    with pytest.raises(ValueError):
        run_log.clip("x", 0, "what")


def test_clip_without_record_only_cuts():
    assert run_log.clip("abcd", 2, "what") == "ab"


def test_long_strings_go_to_full_text_file():
    log = run_log.open_run("entry", location="repo")
    text = "x" * 40000

    log.write("big", body=text)

    body = lines(log.path)[-1]["fields"]["body"]
    assert Path(body["full_text"]).read_text() == text and body["head"] == text[:200]


def test_finish_writes_end_and_stops_when_record_failed(capsys):
    log = run_log.open_run("entry", location="repo")
    obj = {"tool": "entry", "status": "ok", "summary": "", "items": [], "metrics": {}}

    assert log.finish(obj, 0) == (obj, 0)
    assert lines(log.path)[-1]["fields"] == {"result": obj, "exit": 0}

    log.sink.path = log.path.parent / "gone" / "x.jsonl"
    stopped, code = log.finish(obj, 0)
    assert stopped["status"] == "stopped" and code != 0
    assert stopped["original_result"] == obj and results.errors(stopped, code) == []
    assert "実行の記録に書けない" in capsys.readouterr().err


def test_half_written_line_is_closed_before_next(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_bytes(b'{"cut": ')

    run_log._append_line(path, b'{"a": 1}\n')

    assert path.read_bytes().splitlines()[-1] == b'{"a": 1}'


def test_token_shapes_are_hidden_without_secret_env():
    log = run_log.open_run("entry", location="repo")

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

    with pytest.raises(run_log.RunLogError):
        run_log.open_run("entry", location=location, now=NOW)

    assert old.is_dir() and run_log.current() is None


@pytest.mark.parametrize("run_id", ["../outside", "/abs/outside", "..", "a/b"])
def test_run_id_outside_day_dir_is_refused_before_writing(run_id):
    victim = run_log.runs_dir() / "repo" / "outside.jsonl"  # "../outside" が指す先
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_text("利用者のファイル\n")

    with pytest.raises(run_log.RunLogError):
        run_log.open_run("entry", run_id=run_id, location="repo", now=NOW)

    assert victim.read_text() == "利用者のファイル\n" and run_log.current() is None


def test_lines_go_through_dedicated_logger_handler():
    log = run_log.open_run("entry", run_id="h", location="repo", now=NOW)
    logger = logging.getLogger(run_log.LOGGER_NAME)

    assert logger.handlers == [log.sink] and logger.propagate is False
    assert isinstance(log.sink, logging.Handler)
    run_log.close()
    assert logger.handlers == []


def test_stderr_sink_keeps_full_text_in_part_lines(monkeypatch, capsys):
    monkeypatch.setenv(run_log.ENV_RUN_LOG_SINK, "stderr")
    log = run_log.open_run("entry", run_id="s")
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
    log = run_log.open_run("entry", location="repo")
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
    log = run_log.open_run("entry", location="repo")
    many = ["z" * 150 for _ in range(400)]

    log.write("end", result=many)

    raw = log.path.read_bytes().splitlines()[-1]
    assert len(raw) + 1 <= 32000
    moved = json.loads(raw)["fields"]["fields_json"]
    assert json.loads(Path(moved["full_text"]).read_text()) == {"result": many}


def test_open_run_prepares_state_dir_so_next_prepare_keeps_it():
    """記録が先に保存先を作っても、次の用意が保存先ごと退避しない（#3394）。"""
    from ndf import identity

    log = run_log.open_run("entry", location="repo")
    ident = identity.load()

    assert identity.prepare_state_dir(ident).path == ident.state_dir
    assert log.path.exists() and not list(ident.state_dir.parent.glob(ident.state_dir.name + identity.RETIRED_SUFFIX + "*"))


def test_redact_hides_dict_keys_bytes_and_non_json_values(monkeypatch):
    """文字列以外の値と辞書のキーも伏せ、数・真偽値・None だけをそのまま返す（#3394）。"""
    monkeypatch.setenv("NDF_TEST_TOKEN", "s3cr3t-value-123")
    token = "ghp_abcdefgh12345678"

    got = run_log.redact(
        {
            token: 1,
            "raw": b"use s3cr3t-value-123",
            "path": Path("/tmp") / token,
            "error": RuntimeError(f"failed with {token}"),
            "set": {token},
            "keep": [3, 1.5, True, None],
        }
    )

    assert token not in json.dumps(got) and "s3cr3t-value-123" not in json.dumps(got)
    assert got["keep"] == [3, 1.5, True, None]
    assert got["raw"] == "use " + run_log.MASK and got[run_log.MASK] == 1


SECRET = "s3cr3t-value-123"


def _secret_spans_are_the_ranges_redact_replaces(monkeypatch):
    # #3959 Task 9: 秘密の一致範囲を原文の上で求め、redact はその範囲を伏せ字にする
    monkeypatch.setenv("NDF_TEST_TOKEN", SECRET)
    text = f"a {SECRET} b https://user:pa55@github.com/o/r Authorization: Bearer x.y"
    spans = run_log.secret_spans(text)
    assert [text[s:e] for s, e in spans] == [SECRET, "user:pa55", "Bearer x.y"]
    assert run_log.redact(text) == f"a {run_log.MASK} b https://{run_log.MASK}@github.com/o/r Authorization: {run_log.MASK}"


def _overlapping_matches_of_a_repeating_secret_are_all_masked():
    # 繰り返しのある秘密が重なって現れても、後の一致の末尾を伏せ字から漏らさない
    secret = "abcabcab"
    text = "x abcabcabcab y"
    assert run_log.secret_spans(text, [secret]) == [(2, 13)]
    assert run_log.redact(text, [secret]) == f"x {run_log.MASK} y"


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("ssh://git@github.com/o/r.git", True),
        ("ssh://git@github.com:22/o/r.git", True),
        ("https://git@github.com/o/r", False),
        ("ssh://git@github.com.example/o/r", False),
        ("ssh://git:pw@github.com/o/r", False),
        ("ssh://deploy@github.com/o/r", False),
        ("xssh://git@github.com/o/r", False),
    ],
)
def _only_the_fixed_ssh_user_of_github_is_not_a_secret(text, kept):
    # スキームが ssh・利用者名が git・ホストが github.com の 3 つがそろうときだけ、利用者名を秘密に数えない
    assert (run_log.secret_spans(text) == []) is kept
    assert (run_log.redact(text) == text) is kept


# #3959 で足した場合を 1 つのテストの関数へ束ねる（テストの関数の数の予算。束ね方は tests/support/folded.py）
@pytest.mark.parametrize(
    "case",
    folded.cases(
        _secret_spans_are_the_ranges_redact_replaces,
        _overlapping_matches_of_a_repeating_secret_are_all_masked,
        _only_the_fixed_ssh_user_of_github_is_not_a_secret,
    ),
)
def test_cli_run_log_cases(case: Any, request: pytest.FixtureRequest) -> None:
    folded.run(case, request)
