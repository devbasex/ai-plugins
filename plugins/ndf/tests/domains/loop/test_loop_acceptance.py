"""`ndf loop acceptance`: 受け入れ条件の確認の区分の記録・読み取り・検証（#2144・#1682）。"""

from __future__ import annotations

import argparse
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

import pytest
from filelock import FileLock

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

- [ ] セクションの外は数えない
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


def test_means_with_a_pipe_or_a_newline_round_trip_through_the_table():
    # パイプや改行を含むコマンドを表へそのまま埋めると、列と行が割れて手段が欠ける
    means = "pytest tests/x.py -q | tail -3\ngrep 'a\\|b' x"
    body = acc.recorded_body(BODY, {1: means}, [2])
    assert acc.read_checks(body)[1] == acc.Check("マージ前", means)
    again = acc.recorded_body(body, {3: "docker build で確かめた"}, [])
    assert acc.read_checks(again)[1] == acc.Check("マージ前", means) and acc.unrecorded(again) == []


def test_recording_keeps_the_text_around_the_table_in_the_checks_section():
    # 確認のセクションの説明文・小見出しは利用者が書いたもので、表だけを置き換える
    section = f"{inputs.CHECKS_HEADING}\n\n前提: arm64 の機械で確かめる\n\n| 条件 | 区分 | 手段 |\n| :--- | :--- | :--- |\n| 1 | マージ前 | pytest |\n\n### 証跡\n\n- ログは x に置いた\n"
    body = acc.recorded_body(BODY + "\n" + section, {}, [2])
    assert "前提: arm64 の機械で確かめる" in body and "### 証跡" in body and "- ログは x に置いた" in body
    assert acc.read_checks(body) == {1: acc.Check("マージ前", "pytest"), 2: acc.Check("リリース後", "")}
    assert body.count("| 条件 | 区分 | 手段 |") == 1


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


def test_record_with_means_read_back_as_none_stops_as_invalid(issue, loop):
    # 手段 `—` は表の読み戻しで手段なしとなり行が消える。ok（変更なし）で返さず止める（#3886 の r2-1）
    got = loop("record", "--issue", "7", "--pre-merge", "1=—")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
    assert issue["body"] == BODY


def test_an_issue_without_criteria_has_nothing_to_check(issue, loop):
    issue["body"] = "## 目的\n\nx\n"
    assert loop("check", "--issue", "7")["status"] == "ok"


def test_record_keeps_the_existing_rows_and_check_names_only_the_numbers(issue, loop):
    # 課題 1 つ・--criteria なしの照らしの結果と、記録の追記が既存の行を残すことを縛る（#3879）
    assert loop("record", "--issue", "7", "--pre-merge", "1=pytest")["status"] == "ok"
    assert loop("record", "--issue", "7", "--post-release", "2")["status"] == "ok"
    assert acc.read_checks(issue["body"]) == {1: acc.Check("マージ前", "pytest"), 2: acc.Check("リリース後", "")}
    got = loop("check", "--issue", "7")
    assert got["status"] == "stopped" and [g["name"] for g in got["gaps"]] == ["3"]


@pytest.fixture
def issues(monkeypatch: pytest.MonkeyPatch) -> dict[int, str]:
    # 課題 1 は条件 1・2 に記録があり 3 に無い。課題 2 は記録が無い
    held = {1: acc.recorded_body(BODY, {1: "pytest"}, [2]), 2: BODY}
    monkeypatch.setattr(acceptance, "read_body", lambda cwd, n: held[n])
    return held


def test_check_with_criteria_looks_only_at_the_criteria_of_the_unit(issues, loop):
    assert loop("check", "--issue", "1", "--criteria", "1:1", "--criteria", "1:2")["status"] == "ok"
    got = loop("check", "--issue", "1")
    assert got["status"] == "stopped" and [g["name"] for g in got["gaps"]] == ["3"]


def test_check_with_criteria_returns_only_the_items_of_those_criteria(issues, loop):
    # 条件を指定した課題は items も同じ番号に絞る（#3886 の r1-2）
    got = loop("check", "--issue", "1", "--criteria", "1:1")
    assert got["status"] == "ok" and [i["name"] for i in got["items"]] == ["1:1"]


def test_check_over_several_issues_names_the_gap_with_the_issue(issues, loop):
    got = loop("check", "--issue", "1", "--issue", "2", "--criteria", "1:1", "--criteria", "2:3")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["2:3"]
    got = loop("check", "--issue", "1", "--issue", "2")
    assert [g["name"] for g in got["gaps"]] == ["1:3", "2:1", "2:2", "2:3"]


@pytest.mark.parametrize("value", ["1:9", "3:1", "1", "1:x"])
def test_check_refuses_criteria_outside_the_issues_or_the_body(issues, loop, value):
    got = loop("check", "--issue", "1", "--issue", "2", "--criteria", value)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"


def _record_args(cwd: Path, pre: str) -> argparse.Namespace:
    return argparse.Namespace(cwd=cwd, issue=7, pre_merge=[pre], post_release=[])


def test_two_records_on_one_issue_keep_both_rows(issue, loop, tmp_path, monkeypatch):
    # 単位を並列に流すと、同じ課題へ 2 つの record が届く。1 つ目が読んだ後・書く前に 2 つ目を流し、
    # 2 つ目が読み終えて書くまで 1 つ目を待たせる（ロックが無ければ 1 つ目が古い本文で上書きし条件 2 が消える）
    second_wrote = threading.Event()
    read = acceptance.read_body
    write = acceptance.write_body
    other: list[threading.Thread] = []

    def racing_read(cwd: Path, n: int) -> str:
        body = read(cwd, n)
        if threading.current_thread().name == "first" and not other:
            other.append(threading.Thread(target=acceptance._record, args=(_record_args(tmp_path, "2=pytest"),), name="second"))
            other[0].start()
            second_wrote.wait(0.5)
        return body

    def noted_write(cwd: Path, n: int, body: str) -> None:
        write(cwd, n, body)
        if threading.current_thread().name == "second":
            second_wrote.set()

    monkeypatch.setattr(acceptance, "read_body", racing_read)
    monkeypatch.setattr(acceptance, "write_body", noted_write)
    first = threading.Thread(target=acceptance._record, args=(_record_args(tmp_path, "1=pytest"),), name="first")
    first.start()
    first.join()
    other[0].join()
    assert acc.read_checks(issue["body"]) == {1: acc.Check("マージ前", "pytest"), 2: acc.Check("マージ前", "pytest")}


def _reverting(issue: dict[str, str], monkeypatch: pytest.MonkeyPatch, times: int) -> None:
    # ロックの外の書き手が、書いた直後に本文を記録の前へ戻す（times 回まで）
    left = {"n": times}

    def write(cwd: Path, n: int, body: str) -> None:
        issue["body"] = BODY if left["n"] > 0 else body
        left["n"] -= 1

    monkeypatch.setattr(acceptance, "write_body", write)


def test_record_reapplies_when_the_row_was_reverted(issue, loop, monkeypatch):
    _reverting(issue, monkeypatch, 1)
    assert loop("record", "--issue", "7", "--pre-merge", "1=pytest")["status"] == "ok"
    assert acc.read_checks(issue["body"]) == {1: acc.Check("マージ前", "pytest")}


def test_record_with_a_literal_br_in_the_means_writes_once(issue, loop, monkeypatch):
    # 手段のリテラルの `<br>` は表の読み戻しで改行になる。読み戻し同士で比べ、同じ本文を書き直さない（#3886 の r1-1）
    writes: list[str] = []
    monkeypatch.setattr(acceptance, "write_body", lambda cwd, n, body: (writes.append(body), issue.__setitem__("body", body)))
    got = loop("record", "--issue", "7", "--pre-merge", '1=grep "<br>" report.html')
    assert got["status"] == "ok" and len(writes) == 1
    assert loop("record", "--issue", "7", "--pre-merge", '1=grep "<br>" report.html')["status"] == "ok" and len(writes) == 1


def test_record_stops_as_incomplete_when_the_row_keeps_being_reverted(issue, loop, monkeypatch):
    _reverting(issue, monkeypatch, 100)
    got = loop("record", "--issue", "7", "--pre-merge", "1=pytest", "--post-release", "2")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert [g["name"] for g in got["gaps"]] == ["7:1", "7:2"]


def test_record_stops_as_store_in_use_while_another_holds_the_lock(issue, loop, tmp_path, monkeypatch):
    monkeypatch.setattr(acceptance.limits, "seconds", lambda name: 0.1)
    with FileLock(str(acceptance.lock_path(tmp_path, 7))):
        got = loop("record", "--issue", "7", "--pre-merge", "1=pytest")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "store_in_use"
    assert issue["body"] == BODY
