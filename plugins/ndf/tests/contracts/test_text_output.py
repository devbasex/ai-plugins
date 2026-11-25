"""標準出力が端末のときだけ結果を文で出す（#4003）。"""

from __future__ import annotations

import io
import json

import pytest

from ndf import cli
from ndf.contracts import result, text_view
from ndf.domains.relay import account_registry


class _Tty(io.StringIO):
    """端末として振る舞う標準出力。"""

    def isatty(self) -> bool:
        return True


# pytest の捕捉は fixture の後、テストの本体の前に sys.stdout を差し直すので、差し替えはテストの本体で呼ぶ
@pytest.fixture
def tty(monkeypatch):
    def put() -> io.StringIO:
        out = _Tty()
        monkeypatch.setattr("sys.stdout", out)
        return out

    return put


@pytest.fixture
def pipe(monkeypatch):
    def put() -> io.StringIO:
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        return out

    return put


def _stopped():
    return result.stopped("t", "test_failed", "テストが落ちた", warnings=["遅い"], items=[{"name": "a", "n": 1}])


def test_a_terminal_gets_text_with_the_summary_first(tty):
    tty = tty()
    obj = result.result("t", "ok", "済んだ", items=[{"name": "ａｂ", "n": 1}, {"name": "x", "n": None, "more": {"k": [1, 2]}}])
    assert result.emit(obj) == 0
    lines = tty.getvalue().splitlines()
    assert lines[0] == "済んだ"
    head, _, first, second = lines[2:6]
    assert head.split() == ["name", "n", "more"]
    assert second.split() == ["x", "-", "k=1,", "2"]
    # 全角は幅 2 で数え、2 列目の始まりがそろう
    assert text_view.display_width(first[: first.index("1")]) == text_view.display_width(second[: second.index("-")])


def test_a_pipe_gets_the_same_one_line_json(pipe):
    pipe = pipe()
    obj = result.result("t", "ok", "済んだ", items=[{"name": "a"}])
    assert result.emit(obj) == 0
    assert pipe.getvalue() == result.dumps_line(obj) + "\n"


@pytest.mark.parametrize("fixture", ["tty", "pipe"])
def test_the_exit_code_does_not_depend_on_the_terminal(fixture, request):
    request.getfixturevalue(fixture)()
    assert result.emit(_stopped()) == result.code_for(_stopped())


def test_a_stopped_result_shows_the_kind_detail_and_warnings(tty):
    tty = tty()
    result.emit(_stopped())
    text = tty.getvalue()
    assert "停止理由: test_failed" in text
    assert "  テストが落ちた" in text.splitlines()
    assert "警告: 遅い" in text.splitlines()
    with pytest.raises(json.JSONDecodeError):
        json.loads(text.splitlines()[0])


@pytest.mark.ndf_bad_result
def test_a_wrong_shape_is_still_refused_on_a_terminal(tty):
    tty = tty()
    code = result.emit({"tool": "t", "status": "ok"})
    assert code == result.code_for(result.stopped("x", "invalid", "x"))
    assert "停止理由: invalid" in tty.getvalue()


def test_a_given_stream_stays_json_even_with_text_chosen(tty):
    tty = tty()
    buffer = io.StringIO()
    result.choose_format(result.TEXT)
    try:
        result.emit(result.result("t", "ok", "済んだ"), stream=buffer)
    finally:
        result.choose_format(None)
    assert json.loads(buffer.getvalue())["summary"] == "済んだ"


def test_declared_columns_set_the_order_and_headings(tty):
    tty = tty()
    rows = [{"name": "main", "kind": "oauth", "email": "a@example.com", "pinned": True, "percent": 79.0, "state": "使える"}]
    result.emit(result.result("relay", "ok", "登録", items=rows), columns=account_registry.LIST_COLUMNS)
    head, _, row = tty.getvalue().splitlines()[2:5]
    assert head.split() == ["名前", "種類", "識別", "使用率", "状態"]
    assert row.split() == ["*", "main", "oauth", "a@example.com", "79%", "使える"]


@pytest.mark.parametrize("given", [["--output-format=json"], ["--output-format", "json"]])
def test_the_entry_option_chooses_json_on_a_terminal(given, tty):
    tty = tty()
    assert cli.main([*given, "nosuch"]) == result.code_for(result.stopped("x", "invalid", "x"))
    assert json.loads(tty.getvalue().splitlines()[-1])["status"] == "stopped"


def test_the_entry_option_chooses_text_on_a_pipe(pipe):
    pipe = pipe()
    code = cli.main(["--output-format=text", "nosuch"])
    assert code == result.code_for(result.stopped("x", "invalid", "x"))
    assert "停止理由: invalid" in pipe.getvalue()
    # 呼び終えたら明示は残らない
    pipe.truncate(0)
    pipe.seek(0)
    result.emit(result.result("t", "ok", "済んだ"))
    assert json.loads(pipe.getvalue())["tool"] == "t"


def test_an_unknown_format_is_a_usage_error(pipe):
    pipe = pipe()
    assert cli.main(["--output-format=yaml", "relay"]) == result.code_for(result.stopped("x", "invalid", "x"))
    assert json.loads(pipe.getvalue())["cause"]["kind"] == "invalid"


def test_the_option_after_the_subcommand_is_left_to_it():
    fmt, rest, bad = cli._output_format(["relay", "claude", "--output-format", "json"])
    assert (fmt, rest, bad) == (None, ["relay", "claude", "--output-format", "json"], None)
