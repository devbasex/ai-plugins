"""`ndf loop acceptance`: 受け入れ条件の確認の区分の記録・読み取り・照らし合わせ（#2144・#1682）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from ndf.contracts import result
from ndf.core.plan import inputs_decide as inputs
from ndf.domains.loop import acceptance
from ndf.domains.loop import acceptance_decide as acc
from ndf.domains.loop import cli as loop_cli

BODY = """## 目的

x

## 受け入れ条件

- [ ] 1 つ目
- [x] 2 つ目
```text
- [ ] コードブロックの中は数えない
```
- [ ] 3 つ目（手動確認（マージ前）: arm64 で建てる）

## 関連

- [ ] 節の外は数えない
"""


def test_criteria_are_counted_outside_code_blocks_in_the_section():
    found = acc.criteria(BODY)
    assert [(c.number, c.text) for c in found] == [
        (1, "1 つ目"),
        (2, "2 つ目"),
        (3, "3 つ目（手動確認（マージ前）: arm64 で建てる）"),
    ]


def test_recording_adds_the_table_and_a_pre_merge_row_without_means_counts_as_unrecorded():
    body = acc.recorded_body(BODY, {1: "pytest tests/x.py"}, [2])
    assert acc.read_checks(body) == {1: acc.Check("マージ前", "pytest tests/x.py"), 2: acc.Check("リリース後", "")}
    assert [c.number for c in acc.unrecorded(body)] == [3]
    assert body.startswith(BODY.rstrip("\n")) and acc.criteria(body) == acc.criteria(BODY)
    again = acc.recorded_body(body, {3: "docker build で確かめた"}, [])
    assert acc.unrecorded(again) == [] and again.count(inputs.CHECKS_HEADING) == 1
    no_means = again.replace("docker build で確かめた", "—")
    assert [c.number for c in acc.unrecorded(no_means)] == [3]


@pytest.mark.parametrize(("pre", "post"), [({9: "x"}, []), ({1: "x"}, [1])])
def test_recording_refuses_unknown_numbers_and_both_kinds(pre, post):
    with pytest.raises(acc.AcceptanceError):
        acc.recorded_body(BODY, pre, post)


@pytest.fixture
def issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    held = {"body": BODY}
    monkeypatch.setattr(acceptance, "read_body", lambda cwd, n: held["body"])
    monkeypatch.setattr(acceptance, "write_body", lambda cwd, n, body: held.update(body=body))
    return held


@pytest.fixture
def loop(tmp_path: Path, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]) -> Callable[..., dict]:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def run(*argv: str) -> dict[str, Any]:
        loop_cli.main(["--cwd", str(tmp_path), "acceptance", *argv])
        got = last_result(capsys.readouterr().out)
        assert result.errors(got) == []
        return got

    return run


def test_check_stops_as_incomplete_while_a_manual_check_is_left(issue, loop):
    # #1682: 手動確認（マージ前）を行えなかった条件は記録が無く、実装のプランが完了で返らない
    got = loop("check", "--issue", "7")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["1", "2", "3"]
    assert loop("record", "--issue", "7", "--pre-merge", "1=pytest", "--pre-merge", "2=pytest", "--post-release", "3")["status"] == "ok"
    assert loop("check", "--issue", "7")["status"] == "ok"
    assert [i["name"] for i in loop("show", "--issue", "7")["items"]] == ["1", "2", "3"]


def test_record_with_a_bad_argument_stops_as_invalid(issue, loop):
    got = loop("record", "--issue", "7", "--pre-merge", "1")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
    assert issue["body"] == BODY


def test_an_issue_without_criteria_has_nothing_to_check(issue, loop):
    issue["body"] = "## 目的\n\nx\n"
    assert loop("check", "--issue", "7")["status"] == "ok"
