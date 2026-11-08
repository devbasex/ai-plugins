"""上限の宣言の読み手（#2343 の受け入れ条件 1・7、不変条件 I1・I2）。

宣言を差し替えると同じ方針を引く部品の値が変わること、宣言の誤りでは組み込みの値へ戻らずに止まることを見る。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
LIMITS = LIB / "limits.py"


@pytest.fixture()
def limits():
    spec = importlib.util.spec_from_file_location("ndf_lib_limits_decl", LIMITS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _declare(tmp_path: pathlib.Path, policies: dict) -> pathlib.Path:
    path = tmp_path / "limits.json"
    path.write_text(json.dumps({"version": 1, "policies": policies}), encoding="utf-8")
    return path


def test_policy_reads_the_named_row(limits) -> None:
    p = limits.policy("monitor.phase.review")
    assert (p.name, p.seconds, p.attempts) == ("monitor.phase.review", 1200, None)


def test_swapping_the_declaration_changes_the_value_a_part_reads(limits, tmp_path) -> None:
    """受け入れ条件 1: 宣言の値を 1 つ変えると、その方針を引く部品（監視の上限）が変わる。"""
    raw = json.loads(limits.DECLARATION.read_text(encoding="utf-8"))
    raw["policies"]["monitor.phase.review"]["seconds"] = 77
    limits.use(_declare(tmp_path, raw["policies"]))
    assert limits.monitor_timeout("review", "codex") == 77
    assert limits.cli_timeout("review", "codex") == 77 + raw["policies"]["monitor.cli-margin"]["seconds"]


@pytest.mark.parametrize(
    ("row", "word"),
    [
        ({"summary": "s", "seconds": -1}, "seconds"),
        ({"summary": "s", "attempts": 1.5}, "attempts"),
        ({"summary": "s", "same_cause": 0}, "same_cause"),
        ({"summary": "s", "factor": 0.5}, "factor"),
        ({"summary": "s", "seconds": True}, "seconds"),
        ({"summary": "s", "secs": 1}, "知らないキー"),
        ({"summary": "", "seconds": 1}, "summary"),
        ({"summary": "s"}, "値が無い"),
    ],
)
def test_a_malformed_row_is_refused(limits, tmp_path, row, word) -> None:
    limits.use(_declare(tmp_path, {"x.y": row}))
    with pytest.raises(limits.LimitsError, match=word):
        limits.policy("x.y")


def test_a_duplicated_name_is_refused(limits, tmp_path) -> None:
    path = tmp_path / "limits.json"
    path.write_text('{"version": 1, "policies": {"a.b": {"summary": "s", "seconds": 1}, "a.b": {"summary": "s", "seconds": 2}}}', encoding="utf-8")
    limits.use(path)
    with pytest.raises(limits.LimitsError, match="同じ名前"):
        limits.load()


@pytest.mark.parametrize("call", ["policy", "seconds"])
def test_a_missing_name_raises_with_the_path_and_the_name(limits, call) -> None:
    with pytest.raises(limits.LimitsError) as got:
        getattr(limits, call)("no.such-policy")
    assert "no.such-policy" in str(got.value) and str(limits.DECLARATION) in str(got.value)


def test_a_missing_value_raises(limits) -> None:
    with pytest.raises(limits.LimitsError, match="attempts"):
        limits.value("monitor.phase.review", "attempts")


def _copy_reader(tmp_path: pathlib.Path) -> pathlib.Path:
    """読み手を `<tmp>/scripts/lib/limits.py` へ写す。宣言の置き場は `<tmp>/contracts/limits.json` になる。"""
    lib = tmp_path / "scripts" / "lib"
    lib.mkdir(parents=True)
    shutil.copy(LIMITS, lib / "limits.py")
    (tmp_path / "contracts").mkdir()
    return lib / "limits.py"


_PHASE = '{"version": 1, "policies": {"monitor.phase.review": {"summary": "s", "seconds": 1}'


@pytest.mark.parametrize(
    ("content", "word"),
    [
        (None, "読めない"),
        ("{not json", "壊れている"),
        (_PHASE + "}}", "monitor.cli-margin"),
        (_PHASE + ', "monitor.cli-margin": {"summary": "s", "interval": 1}}}', "seconds"),
    ],
)
def test_the_command_stops_with_exit_1_on_a_broken_declaration(tmp_path, content, word) -> None:
    """受け入れ条件 7: 無い・壊れた・名前が無い・値が無いの 4 通りで、組み込みの値へ戻らず終了コード 1 と理由。"""
    reader = _copy_reader(tmp_path)
    if content is not None:
        (tmp_path / "contracts" / "limits.json").write_text(content, encoding="utf-8")
    r = subprocess.run([sys.executable, str(reader), "cli-timeout", "review", "agy"], capture_output=True, text=True)
    assert r.returncode == 1 and r.stdout == ""
    assert word in r.stderr and str(tmp_path / "contracts" / "limits.json") in r.stderr


def test_check_on_a_broken_order_exits_1(tmp_path) -> None:
    """受け入れ条件 9: `check` は宣言の値で組を判定する。"""
    reader = _copy_reader(tmp_path)
    raw = json.loads((LIB.parents[1] / "contracts" / "limits.json").read_text(encoding="utf-8"))
    raw["policies"]["monitor.stall.codex"]["seconds"] = 5000
    (tmp_path / "contracts" / "limits.json").write_text(json.dumps(raw), encoding="utf-8")
    r = subprocess.run([sys.executable, str(reader), "check"], capture_output=True, text=True)
    assert r.returncode == 1 and "codex" in r.stderr
