"""手順のスクリプトの結果の共通の形（`lib/step_result.py`、#846）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib" / "step_result.py"
_spec = importlib.util.spec_from_file_location("ndf_lib_step_result", LIB)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def ok_result(**kw):
    return {**sr.result("merged", "ok", "終わった"), **kw}


def test_valid_result_has_no_errors():
    assert sr.validate_result(ok_result()) == []
    assert sr.validate_result(ok_result(), 0) == []


@pytest.mark.parametrize(
    "obj, needle",
    [
        ([], "オブジェクト"),
        ({"status": "ok"}, "tool が無い"),
        ({**sr.result("m", "ok", "s"), "status": "fail"}, "status は"),
        ({**sr.result("m", "ok", "s"), "items": {}}, "items は配列"),
        ({**sr.result("m", "ok", "s"), "items": [1]}, "items[0]"),
        ({**sr.result("m", "ok", "s"), "metrics": []}, "metrics は"),
        ({**sr.result("m", "ok", "s"), "extra": 1}, "知らない項目: extra"),
        (sr.result("m", "gate", "s"), "presentation_path か next"),
    ],
)
def test_invalid_results_are_reported(obj, needle):
    errs = sr.validate_result(obj)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize(
    "status, code, good",
    [
        ("ok", 0, True),
        ("ok", 1, False),
        ("gate", 10, True),
        ("gate", 19, True),
        ("gate", 25, True),
        ("gate", 0, False),
        ("gate", 1, False),
        ("stopped", 1, True),
        ("stopped", 2, True),
        ("stopped", 3, True),
        ("stopped", 0, False),
        ("stopped", 10, False),
    ],
)
def test_status_and_exit_code_must_agree(status, code, good):
    obj = sr.result("m", status, "s", next="n")
    assert (sr.validate_result(obj, code) == []) is good


@pytest.mark.parametrize("status, code", [("ok", 0), ("gate", 10), ("stopped", 1)])
def test_emit_prints_one_json_line_and_exits_with_default_code(status, code, capsys):
    with pytest.raises(SystemExit) as e:
        sr.emit(sr.result("m", status, "s", next="n"))
    assert e.value.code == code
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert json.loads(out)["status"] == status


def test_emit_uses_explicit_code(capsys):
    with pytest.raises(SystemExit) as e:
        sr.emit(sr.result("m", "stopped", "読めない"), sr.EXIT_UNREADABLE)
    assert e.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "stopped"


def test_emit_refuses_malformed_result_with_2(capsys):
    with pytest.raises(SystemExit) as e:
        sr.emit({"status": "ok"})
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert cap.out == ""
    assert "tool が無い" in cap.err


def test_emit_refuses_status_code_mismatch(capsys):
    with pytest.raises(SystemExit) as e:
        sr.emit(sr.result("m", "ok", "s"), 1)
    assert e.value.code == 2
    assert capsys.readouterr().out == ""


def test_approval_present_writes_two_layers(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_PRESENTATION_DIR", str(tmp_path / "pres"))
    path = sr.approval_present(
        "release",
        "v1.2.3",
        title="ndf v1.2.3 の本番への配布",
        targets=[{"url": "https://github.com/o/r/compare/a...b", "title": "a → b", "base_head": "main ← develop"}],
        change="3 ファイル / +10 / −2",
        judge=[("版数", "1.2.3"), ("含む PR", "#1 x | y\n#2 z")],
        consent=["main へ出す"],
        rollback="タグを消す",
    )
    p = Path(path)
    assert p.parent == tmp_path / "pres"
    text = p.read_text(encoding="utf-8")
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    assert heads == ["## 1. 対象を開くためのもの", "## 2. 承認の判断に使うもの", "## 同意を求めること", "## 戻し方"]
    # URL は生のまま（Markdown のリンクにしない）
    assert "- https://github.com/o/r/compare/a...b  a → b" in text
    assert "ベースと head: main ← develop" in text
    assert "変更量: 3 ファイル / +10 / −2" in text
    assert "| 含む PR | #1 x \\| y<br>#2 z |" in text
    assert "- [ ] main へ出す" in text
    assert text.rstrip().endswith("タグを消す")


def test_approval_present_honours_explicit_path(tmp_path):
    out = tmp_path / "x" / "p.md"
    got = sr.approval_present("m", "1", title="t", targets=[{"url": "u"}], change="c", judge=[], consent=["c"], rollback="r", path=out)
    assert got == str(out) and out.is_file()


@pytest.mark.parametrize("kw", [{"targets": []}, {"consent": []}, {"rollback": " "}])
def test_approval_present_requires_targets_consent_and_rollback(tmp_path, kw):
    args = dict(title="t", targets=[{"url": "u"}], change="c", judge=[], consent=["c"], rollback="r", path=tmp_path / "p.md")
    args.update(kw)
    with pytest.raises(ValueError):
        sr.approval_present("m", "1", **args)


def test_deps_missing_is_stopped_and_owned_by_deps():
    """依存の欠け（#1654）は stopped で、値は deps.py の定数そのもの（写さない）。"""
    import sys

    deps = sys.modules["deps"]
    assert sr.EXIT_DEPS_MISSING == deps.EXIT_DEPS_MISSING
    assert sr.code_matches("stopped", sr.EXIT_DEPS_MISSING)
    assert not sr.code_matches("ok", sr.EXIT_DEPS_MISSING) and not sr.code_matches("gate", sr.EXIT_DEPS_MISSING)


def test_infra_wait_is_stopped():
    """CI の基盤待ち（75。#1645）は stopped で、ok と gate には当たらない。"""
    assert sr.EXIT_INFRA_WAIT == 75
    assert sr.code_matches("stopped", 75)
    assert not sr.code_matches("ok", 75) and not sr.code_matches("gate", 75)
