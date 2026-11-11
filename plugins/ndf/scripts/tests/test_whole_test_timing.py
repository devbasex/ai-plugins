"""実験版 `whole-test-timing.py` の計測と照合（#2291 の決定 7）。

- measure は宣言の順に suite を流し、最初の起動から最後の終了までを壁時計にする。負荷が CPU の数を超えたら参考の値にする
- junit の skip を件数から外し、遅いテストを秒の長い順に、ファイルを秒の合計の順に並べる
- diff は台帳に無い消えたテストと、skip・deselect に変わったテストを、nodeid を丸めずに出す。台帳に載る消したテストの
  定義が着手後の木に残っていれば出す（#2309）
- measure は集められて実行から外された nodeid を deselect として記録し、入れ子の pytest へプラグインを渡さない（#2309）
- startup はスクリプトの起動を N 回流し、中央値と終了コードを返す（#2309）
"""

from __future__ import annotations

import importlib.util
import json
import sys
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


def test_a_gzip_record_is_read_back_by_diff(tmp_path, capsys):
    rec = {"tests": {"t.py::a": {"outcome": "passed", "seconds": 1.0}}}
    wtt.write_timing_record(tmp_path / "b.json.gz", rec)
    assert wtt.read_timing_record(tmp_path / "b.json.gz") == rec
    (tmp_path / "ledger.md").write_text("")
    with pytest.raises(SystemExit) as e:
        wtt.main(
            [
                "diff",
                "--before",
                str(tmp_path / "b.json.gz"),
                "--after",
                str(tmp_path / "b.json.gz"),
                "--ledger",
                str(tmp_path / "ledger.md"),
            ]
        )
    assert e.value.code == 0


def test_measure_marks_high_load_as_reference_and_fails_on_a_failed_suite(monkeypatch, tmp_path):
    code, out = _measure(monkeypatch, tmp_path, [{"name": "bad", "command": "exit 3"}], load=9.5)
    assert code == 1
    assert out["metrics"]["reference"] is True
    assert out["metrics"]["suites"][0]["exit"] == 3


CONFTEST = """
def pytest_collection_modifyitems(config, items):
    items[:] = [i for i in items if "drop" not in i.name]
"""
NESTED = """
import os

def test_keep():
    assert "WTT_COLLECT_DIR" not in os.environ
    assert "wtt_collect" not in os.environ.get("PYTEST_PLUGINS", "")

def test_drop():
    pass
"""


def test_measure_records_deselected_tests_and_hides_its_plugin_from_the_tests(monkeypatch, tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_n.py").write_text(NESTED)
    cmd = f"{sys.executable} -m pytest -q -p no:cacheprovider -n 2 -o junit_family=xunit1 --junitxml=j.xml"
    code, out = _measure(monkeypatch, tmp_path, [{"name": "pytest", "command": cmd, "junit": "j.xml"}], load=1.0)
    assert code == 0
    assert out["tests"]["test_n.py::test_keep"]["outcome"] == "passed"
    assert out["tests"]["test_n.py::test_drop"] == {"outcome": "deselected", "seconds": 0.0}
    assert (out["metrics"]["tests"], out["metrics"]["deselected"]) == (1, 1)


def _diff(tmp_path, before, after, ledger):
    for name, tests in (("b.json", before), ("a.json", after)):
        (tmp_path / name).write_text(json.dumps({"tests": tests}))
    (tmp_path / "ledger.md").write_text(ledger)
    args = ["--before", str(tmp_path / "b.json"), "--after", str(tmp_path / "a.json"), "--ledger", str(tmp_path / "ledger.md")]
    with pytest.raises(SystemExit) as e:
        wtt.main(["diff", *args, "--root", str(tmp_path)])
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


RAN = {"outcome": "passed", "seconds": 1.0}


def test_diff_lists_deselected_tests_even_when_they_are_in_the_ledger(tmp_path, capsys):
    before = {"t.py::a": RAN, "t.py::b": RAN}
    after = {"t.py::a": {"outcome": "deselected", "seconds": 0.0}, "t.py::b": RAN}
    assert _diff(tmp_path, before, after, "| `t.py::a` | 消した | 1 | `t.py::b` |\n") == 1
    out = json.loads(capsys.readouterr().out)
    assert [(i["kind"], i["name"]) for i in out["items"]] == [("now_deselected", "t.py::a")]


@pytest.mark.parametrize(
    "source, after, flagged",
    [
        ("def test_b():\n    pass\n", {"t.py::test_b": RAN}, []),
        ("class TestK:\n    def test_a(self):\n        pass\n", {}, ["t.py::TestK::test_a"]),
        ("def test_p(x):\n    pass\n", {"t.py::test_p[2]": RAN}, []),
        ("def test_p(x):\n    pass\n", {}, ["t.py::test_p[1]"]),
    ],
    ids=["function-gone", "function-left", "case-gone", "file-ignored"],
)
def test_diff_lists_removed_tests_whose_definition_is_left(tmp_path, capsys, source, after, flagged):
    (tmp_path / "t.py").write_text(source)
    before = {"t.py::TestK::test_a": RAN, "t.py::test_p[1]": RAN, "t.py::test_b": RAN}
    ledger = "| `t.py::TestK::test_a` | 消した | 1 | x |\n| `t.py::test_p[1]` | 消した | 5 | x |\n| `t.py::test_b` | 速くした |\n"
    after = {**after, "t.py::test_b": RAN}
    if "TestK" not in source:
        after.setdefault("t.py::TestK::test_a", RAN)
    code = _diff(tmp_path, before, after, ledger)
    out = json.loads(capsys.readouterr().out)
    assert [i["name"] for i in out["items"] if i["kind"] == "still_defined"] == flagged
    assert code == (1 if flagged else 0)


def _startup(tmp_path, *args):
    with pytest.raises(SystemExit) as e:
        wtt.main(["startup", "--root", str(tmp_path), *args])
    return e.value.code


def test_startup_reports_the_median_and_the_exit_code_of_each_script(tmp_path, capsys):
    (tmp_path / "ok.py").write_text("import sys\nsys.exit(0 if sys.argv[1:] == ['--version'] else 4)\n")
    assert _startup(tmp_path, "--runs", "3", "ok.py", "--", "--version") == 0
    out = json.loads(capsys.readouterr().out)
    item = out["items"][0]
    assert (item["name"], item["result"], out["metrics"]["runs"]) == ("ok.py", "0", 3)
    assert item["min_ms"] <= item["median_ms"] <= item["max_ms"]


def test_startup_fails_when_the_exit_code_varies_or_the_script_is_absent(tmp_path, capsys):
    (tmp_path / "flip.py").write_text(
        "import pathlib, sys\np = pathlib.Path('n')\nn = int(p.read_text()) if p.exists() else 0\n"
        "p.write_text(str(n + 1))\nsys.exit(n % 2)\n"
    )
    assert _startup(tmp_path, "--runs", "2", "flip.py") == 1
    assert json.loads(capsys.readouterr().out)["items"][0]["result"] == "varied"
    assert _startup(tmp_path, "absent.py") == 2


# ---- peak: 全体テストを同時に流したメモリの山（#2998） ----


class FakeHost:
    """procs.host_sample の代わり。呼ぶたびに `avail` の次の値を返す（尽きたら最後の値）。"""

    def __init__(self, avail: list[int], pytest_count: int = 0):
        self.avail, self.pytest_count, self.calls = list(avail), pytest_count, 0

    def __call__(self):
        v = self.avail[min(self.calls, len(self.avail) - 1)]
        self.calls += 1
        return {"mem_available_mib": v, "swap_total_mib": 100, "swap_free_mib": 40, "pytest": self.pytest_count if self.calls == 1 else 1}


def _peak(monkeypatch, tmp_path, host, roots=2, queues=0, extra=()):
    procs = wtt._procs()
    monkeypatch.setattr(procs, "host_sample", host)
    monkeypatch.setattr(procs, "count_cmdline", lambda *w: queues)
    monkeypatch.setattr(procs, "meminfo_mib", lambda path: {"MemTotal": 1000})
    dirs = []
    for i in range(roots):
        d = tmp_path / f"w{i}"
        d.mkdir()
        dirs.append(_repo(d, [{"name": "t", "command": f"echo {i} >> {tmp_path}/started; sleep 0.3"}]))
    argv = ["peak", *[x for d in dirs for x in ("--root", str(d))], "--interval", "0.05", *extra]
    with pytest.raises(SystemExit) as e:
        wtt.main(argv)
    return e.value.code


def test_peak_measures_each_count_of_runs(monkeypatch, tmp_path, capsys):
    """F4: 1 本・2 本の順に流し、本数ごとに山と秒と終了コードを出す。始める前の値と版を載せる。"""
    code = _peak(monkeypatch, tmp_path, FakeHost([900, 700, 600, 800]), extra=("--floor-mib", "500"))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "ok"
    assert [i["runs"] for i in out["items"]] == [1, 2]
    assert out["items"][0]["peak_used_mib"] >= 300 and out["items"][0]["peak_swap_used_mib"] == 60
    assert all(i["exits"] == [0] * i["runs"] and i["seconds"] >= 0.3 for i in out["items"])
    assert (tmp_path / "started").read_text().split() == ["0", "0", "1"]
    m = out["metrics"]
    assert m["mem_total_mib"] == 1000 and m["before_used_mib"] == 100 and m["before_swap_used_mib"] == 60
    assert m["ndf_version"] and m["cpus"] >= 1 and m["floor_mib"] == 500


def test_peak_refuses_to_start_while_other_work_runs(monkeypatch, tmp_path, capsys):
    """I6a: ほかの queue か pytest が動いていれば、全体テストを 1 本も流さず 3 で返す。"""
    assert _peak(monkeypatch, tmp_path, FakeHost([900], pytest_count=1)) == 3
    (tmp_path / "q").mkdir()
    assert _peak(monkeypatch, tmp_path / "q", FakeHost([900]), queues=1) == 3
    assert not (tmp_path / "started").exists() and not (tmp_path / "q" / "started").exists()


def test_peak_stops_below_the_floor_and_skips_the_rest(monkeypatch, tmp_path, capsys):
    """I6b・受け入れ条件 10: 下限を割ったら動いている全体テストを止め、打ち切った本数とその時点の値を残す。"""
    for i in range(2):
        (tmp_path / f"w{i}").mkdir()
    procs = wtt._procs()
    host = FakeHost([900])

    def low_after_start():  # 全体テストが始まった後に下限を割る
        got = host()
        return {**got, "mem_available_mib": 100} if (tmp_path / "started").exists() else got

    monkeypatch.setattr(procs, "host_sample", low_after_start)
    monkeypatch.setattr(procs, "count_cmdline", lambda *w: 0)
    monkeypatch.setattr(procs, "meminfo_mib", lambda path: {"MemTotal": 1000})
    roots = [_repo(tmp_path / f"w{i}", [{"name": "t", "command": f"echo {i} >> {tmp_path}/started; sleep 30"}]) for i in range(2)]
    with pytest.raises(SystemExit) as e:
        wtt.main(["peak", "--root", str(roots[0]), "--root", str(roots[1]), "--interval", "0.05", "--floor-mib", "500"])
    out = json.loads(capsys.readouterr().out)
    assert e.value.code == 1 and out["status"] == "stopped"
    assert len(out["items"]) == 1 and out["items"][0]["aborted"] is True and out["items"][0]["runs"] == 1
    assert out["items"][0]["mem_available_mib"] == 100 and out["items"][0]["swap_free_mib"] == 40
    assert out["items"][0]["seconds"] < 20  # 眠り 30 秒を待たずに止めた
    assert (tmp_path / "started").read_text().split() == ["0"]


def test_peak_rejects_more_than_three_roots(tmp_path):
    with pytest.raises(SystemExit) as e:
        wtt.main(["peak", *[x for i in range(4) for x in ("--root", str(tmp_path))]])
    assert e.value.code == 2
