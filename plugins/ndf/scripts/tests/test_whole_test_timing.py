"""実験版 `whole-test-timing.py` の計測と照合（#2291 の決定 7）。

- measure は宣言の順に suite を流し、最初の起動から最後の終了までを壁時計にする。負荷が CPU の数を超えたら参考の値にする
- junit の skip を件数から外し、遅いテストを秒の長い順に、ファイルを秒の合計の順に並べる
- diff は台帳に無い消えたテストと、skip に変わったテストを、nodeid を丸めずに出す
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "experimental" / "whole-test-timing.py"
_spec = importlib.util.spec_from_file_location("whole_test_timing", SCRIPT)
wtt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wtt)

JUNIT = """<?xml version="1.0"?><testsuites><testsuite>
<testcase classname="pkg.tests.test_a" file="pkg/tests/test_a.py" name="test_fast" time="0.1"/>
<testcase classname="pkg.tests.test_a.TestK" file="pkg/tests/test_a.py" name="test_slow[x-1]" time="3.0"/>
<testcase classname="pkg.tests.test_b" file="pkg/tests/test_b.py" name="test_mid" time="1.0"><failure/></testcase>
<testcase classname="pkg.tests.test_b" file="pkg/tests/test_b.py" name="test_skip" time="0.0"><skipped/></testcase>
</testsuite></testsuites>"""


def _repo(tmp_path: Path, suites: list[dict]) -> Path:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}))
    return tmp_path


def _measure(monkeypatch, tmp_path, suites, load):
    monkeypatch.setattr(wtt.os, "getloadavg", lambda: (load, 0.0, 0.0))
    monkeypatch.setattr(wtt.os, "cpu_count", lambda: 8)
    root = _repo(tmp_path, suites)
    out = tmp_path / "out.json"
    with pytest.raises(SystemExit) as e:
        wtt.main(["measure", "--root", str(root), "--out", str(out)])
    return e.value.code, json.loads(out.read_text())


def test_measure_runs_suites_in_order_and_reads_junit(monkeypatch, tmp_path, capsys):
    (tmp_path / "j.xml").write_text(JUNIT)
    suites = [
        {"name": "first", "command": "echo 1 >> order", "junit": "j.xml"},
        {"name": "second", "command": "sleep 0.2; echo 2 >> order"},
    ]
    code, out = _measure(monkeypatch, tmp_path, suites, load=3.0)
    assert code == 0
    assert (tmp_path / "order").read_text().split() == ["1", "2"]
    m = out["metrics"]
    assert [s["name"] for s in m["suites"]] == ["first", "second"]
    assert m["wall_seconds"] >= 0.2 and m["reference"] is False
    assert (m["tests"], m["skipped"], m["sum_seconds"]) == (3, 1, 4.1)
    assert out["tests"]["pkg/tests/test_a.py::TestK::test_slow[x-1]"] == {"outcome": "passed", "seconds": 3.0}
    items = json.loads(capsys.readouterr().out)["items"]
    tests = [i["name"] for i in items if i["kind"] == "test"]
    assert tests[:2] == ["pkg/tests/test_a.py::TestK::test_slow[x-1]", "pkg/tests/test_b.py::test_mid"]
    assert [(i["name"], i["seconds"]) for i in items if i["kind"] == "file"] == [
        ("pkg/tests/test_a.py", 3.1),
        ("pkg/tests/test_b.py", 1.0),
    ]


def test_measure_marks_high_load_as_reference_and_fails_on_a_failed_suite(monkeypatch, tmp_path):
    code, out = _measure(monkeypatch, tmp_path, [{"name": "bad", "command": "exit 3"}], load=9.5)
    assert code == 1
    assert out["metrics"]["reference"] is True
    assert out["metrics"]["suites"][0]["exit"] == 3


def _diff(tmp_path, before, after, ledger):
    for name, tests in (("b.json", before), ("a.json", after)):
        (tmp_path / name).write_text(json.dumps({"tests": tests}))
    (tmp_path / "ledger.md").write_text(ledger)
    with pytest.raises(SystemExit) as e:
        wtt.main(
            ["diff", "--before", str(tmp_path / "b.json"), "--after", str(tmp_path / "a.json"), "--ledger", str(tmp_path / "ledger.md")]
        )
    return e.value.code


def test_diff_lists_removed_tests_missing_from_the_ledger_without_rounding_params(tmp_path, capsys):
    ran = {"outcome": "passed", "seconds": 1.0}
    before = {"t.py::a[1]": ran, "t.py::a[2]": ran, "t.py::b": ran}
    after = {"t.py::b": ran}
    code = _diff(tmp_path, before, after, "| `t.py::a[1]` | 消した | 1 | `t.py::b` |\n| `t.py::a` | 消した |\n")
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert [i["name"] for i in out["items"]] == ["t.py::a[2]"]


def test_diff_lists_tests_that_became_skipped(tmp_path, capsys):
    before = {"t.py::a": {"outcome": "passed", "seconds": 1.0}, "t.py::s": {"outcome": "skipped", "seconds": 0.0}}
    after = {"t.py::a": {"outcome": "skipped", "seconds": 0.0}}
    assert _diff(tmp_path, before, after, "") == 1
    out = json.loads(capsys.readouterr().out)
    assert [(i["kind"], i["name"]) for i in out["items"]] == [("now_skipped", "t.py::a")]
    assert _diff(tmp_path, before, before, "") == 0
