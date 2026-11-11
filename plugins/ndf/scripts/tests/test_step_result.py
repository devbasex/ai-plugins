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
        title="ndf v1.2.3 のリリース",
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


def test_deps_missing_is_stopped_and_owned_by_the_exit_table():
    """依存の欠け（#1654）は stopped で、結果を出す層と依存の確認の値は終了コードの表の定数そのもの（#2988）。"""
    import sys

    import deps

    exit_codes = sys.modules["exit_codes"]
    assert sr.EXIT_DEPS_MISSING is exit_codes.EXIT_DEPS_MISSING is deps.EXIT_DEPS_MISSING
    assert exit_codes.EXIT_DEPS_MISSING == 69
    assert sr.EXIT_LABELS is exit_codes.EXIT_LABELS and sr.label_of is exit_codes.label_of
    assert sr.code_matches("stopped", sr.EXIT_DEPS_MISSING)
    assert not sr.code_matches("ok", sr.EXIT_DEPS_MISSING) and not sr.code_matches("gate", sr.EXIT_DEPS_MISSING)


def test_infra_wait_is_stopped():
    """CI の基盤待ち（75。#1645）は stopped で、ok と gate には当たらない。"""
    assert sr.EXIT_INFRA_WAIT == 75
    assert sr.code_matches("stopped", 75)
    assert not sr.code_matches("ok", 75) and not sr.code_matches("gate", 75)


# --- 欠け・過程・判断の項目と終了コード 6（#2227） -----------------------------------------

GAP = {"kind": "record", "name": "#1", "reason": "gh が失敗"}


def test_ok_with_gaps_is_an_error():
    errs = sr.validate_result(sr.result("m", "ok", "s", gaps=[GAP]))
    assert any("gaps" in e for e in errs), errs


def test_emit_refuses_ok_with_gaps(capsys):
    with pytest.raises(SystemExit) as e:
        sr.emit(sr.result("m", "ok", "s", gaps=[GAP]))
    assert e.value.code == 2
    out = capsys.readouterr()
    assert out.out == "" and "gaps" in out.err


@pytest.mark.parametrize(
    "status, gaps, good",
    [("stopped", [GAP], True), ("stopped", [], False), ("ok", [], False), ("gate", [GAP], False)],
)
def test_exit_code_6_needs_stopped_and_gaps(status, gaps, good):
    obj = sr.result("m", status, "s", next="n", gaps=gaps)
    assert (sr.validate_result(obj, sr.EXIT_INCOMPLETE) == []) is good


def test_emit_defaults_stopped_with_gaps_to_6(capsys):
    with pytest.raises(SystemExit) as e:
        sr.emit(sr.result("m", "stopped", "s", gaps=[GAP]))
    assert e.value.code == sr.EXIT_INCOMPLETE
    assert json.loads(capsys.readouterr().out)["gaps"] == [GAP]


@pytest.mark.parametrize(
    "field, value",
    [
        ("gaps", [{"kind": "k", "name": "n"}]),
        ("gaps", [{"kind": "k", "name": "n", "reason": " "}]),
        ("gaps", [{"kind": "k", "name": "n", "reason": 1}]),
        ("process", ["run"]),
        ("process", {}),
        ("decisions", [{"decision": "d"}]),
    ],
)
def test_record_items_need_their_keys(field, value):
    obj = {**sr.result("m", "stopped", "s"), field: value}
    assert any(field in e for e in sr.validate_result(obj)), sr.validate_result(obj)


def test_records_with_keys_and_extra_keys_pass():
    obj = sr.result(
        "m",
        "ok",
        "s",
        process=[{"run": "gh issue view 1", "result": "読めた", "extra": 1}],
        decisions=[{"decision": "記録する", "basis": "工程名が既知"}],
    )
    assert sr.validate_result(obj, 0) == []


def test_result_omits_empty_records():
    assert set(sr.result("m", "ok", "s", gaps=[], process=None)) == set(sr.REQUIRED)


def test_cli_emit_builds_the_same_result(capsys):
    argv = ["emit", "--tool", "t", "--status", "stopped", "--summary", "書けなかった", "--code", "6"]
    argv += ["--item", "issue-body", "#1", "failed", "", "--gap", "issue-body", "#1", "gh: 404"]
    argv += ["--process", "gh issue view 1", "失敗", "--decision", "止めない", "進行管理", "--metric", "n", "3"]
    with pytest.raises(SystemExit) as e:
        sr.main(argv)
    assert e.value.code == 6
    obj = json.loads(capsys.readouterr().out)
    assert obj["items"] == [{"kind": "issue-body", "name": "#1", "result": "failed"}]
    assert obj["metrics"] == {"n": 3} and obj["gaps"][0]["reason"] == "gh: 404"
    assert sr.validate_result(obj, 6) == []


def test_cli_emit_refuses_ok_with_gap(capsys):
    with pytest.raises(SystemExit) as e:
        sr.main(["emit", "--tool", "t", "--status", "ok", "--summary", "s", "--gap", "k", "n", "r"])
    assert e.value.code == 2 and capsys.readouterr().out == ""


def test_emit_out_writes_the_same_line_to_the_file(tmp_path):
    import subprocess
    import sys

    out = tmp_path / "w" / "result.json"
    lib = Path(__file__).resolve().parents[1] / "lib"
    cmd = [sys.executable, str(lib / "step_result.py"), "emit", "--tool", "worker", "--status", "gate", "--summary", "s"]
    p = subprocess.run([*cmd, "--next", "判断が要る", "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 10 and json.loads(out.read_text()) == json.loads(p.stdout)
    bad = tmp_path / "bad.json"
    p = subprocess.run([*cmd, "--out", str(bad)], capture_output=True, text=True)  # gate に next が無い
    assert p.returncode == 2 and not bad.exists()


# ---------- 現状固定: status ごとに受ける終了コードの集合（I-002） ----------

# 現状の出力を書き写した値。定数の置き場を移しても、受ける集合が変わらないことを固定する
_ACCEPTED_CODES = {
    "ok": {0},
    "gate": set(range(10, 30)),
    "stopped": {1, 2, 3, 4, 5, 6, 69, 75},
}


@pytest.mark.parametrize("status", ["ok", "gate", "stopped", "unknown", ""])
def test_code_matches_accepts_exactly_the_current_codes(status):
    """現状固定: 0〜255 と負の値のうち、status ごとに一致と見る終了コード。知らない status は stopped と同じ扱い。"""
    expected = _ACCEPTED_CODES.get(status, _ACCEPTED_CODES["stopped"])
    accepted = {c for c in range(-5, 256) if sr.code_matches(status, c)}
    assert accepted == expected


def test_default_code_is_fixed_per_status():
    """現状固定: status から決める既定の終了コード。知らない status は KeyError。"""
    assert [sr.default_code(s) for s in ("ok", "gate", "stopped")] == [0, 10, 1]
    with pytest.raises(KeyError):
        sr.default_code("unknown")


# ---------- 現状固定: emit と --out の終了コードの決め方（I-003） ----------


def _emit_code(obj, code=None):
    with pytest.raises(SystemExit) as e:
        sr.emit(obj, code)
    return e.value.code


@pytest.mark.parametrize(
    "obj, code, expected",
    [
        ({**sr.result("m", "stopped", "s"), "gaps": []}, None, 1),  # 空の gaps は 6 にしない
        (sr.result("m", "stopped", "s", gaps=[{"kind": "k", "name": "n", "reason": "r"}]), None, 6),
        (sr.result("m", "stopped", "s", gaps=[{"kind": "k", "name": "n", "reason": "r"}]), 1, 1),  # 明示した値が勝つ
        (sr.result("m", "gate", "s", next="n"), None, 10),
        (sr.result("m", "gate", "s", next="n"), 25, 25),
        (sr.result("m", "ok", "s"), None, 0),
        ("not-a-dict", None, 2),
        ({**sr.result("m", "ok", "s"), "status": "odd"}, None, 2),
    ],
)
def test_emit_exit_code_is_fixed(obj, code, expected, capsys):
    """現状固定: emit が終える終了コード（status・gaps・明示の値・形の誤りの組み合わせ）。"""
    assert _emit_code(obj, code) == expected
    out = capsys.readouterr().out
    if expected == 2:
        assert out == ""
    else:
        assert json.loads(out) == obj


def test_emit_under_capture_raises_emitted_without_printing(monkeypatch, capsys):
    """現状固定: 核が動いている間は出さずに Emitted を投げる。status が読めなければ 1。stream を渡すと出す。"""
    monkeypatch.setattr(sr, "CAPTURE", True)
    monkeypatch.setattr(sr, "ATTACH", {"lease": {"k": "v"}})
    obj = sr.result("m", "stopped", "s", gaps=[{"kind": "k", "name": "n", "reason": "r"}])
    with pytest.raises(sr.Emitted) as e:
        sr.emit(obj)
    assert (e.value.code, e.value.result) == (6, obj)  # ATTACH はここでは足さない
    with pytest.raises(sr.Emitted) as e:
        sr.emit({"status": "odd"})
    assert e.value.code == 1
    with pytest.raises(sr.Emitted) as e:
        sr.emit(obj, 3)
    assert e.value.code == 3
    assert capsys.readouterr().out == ""

    import io

    buf = io.StringIO()
    with pytest.raises(SystemExit) as e:
        sr.emit(sr.result("m", "ok", "s"), stream=buf)
    assert e.value.code == 0
    assert json.loads(buf.getvalue()) == {**sr.result("m", "ok", "s"), "lease": {"k": "v"}}


def test_emit_attaches_and_validates_the_attached_fields(monkeypatch, capsys):
    """現状固定: ATTACH は出す前に足し、足した後の形を確かめる。"""
    monkeypatch.setattr(sr, "ATTACH", {"lease": {"owner": "a"}})
    assert _emit_code(sr.result("m", "ok", "s")) == 0
    assert json.loads(capsys.readouterr().out)["lease"] == {"owner": "a"}
    monkeypatch.setattr(sr, "ATTACH", {"lease": "x"})
    assert _emit_code(sr.result("m", "ok", "s")) == 2
    assert "lease はオブジェクト" in capsys.readouterr().err


@pytest.mark.parametrize(
    "extra, written, code",
    [
        (["--status", "stopped", "--gap", "k", "n", "r"], True, 6),  # 省いた code は gaps から 6
        (["--status", "stopped"], True, 1),
        (["--status", "stopped", "--gap", "k", "n", "r", "--code", "1"], True, 1),
        (["--status", "ok"], True, 0),
        (["--status", "ok", "--code", "1"], False, 2),  # status と code が合わない
        (["--status", "stopped", "--code", "6"], False, 2),  # 6 に gaps が無い
        (["--status", "gate"], False, 2),  # gate に next が無い
    ],
)
def test_cli_out_and_exit_code_agree(tmp_path, capsys, extra, written, code):
    """現状固定: --out の書き込みの有無と、emit の終了コード（--out と emit は同じ規則で code を決める）。"""
    out = tmp_path / "r.json"
    with pytest.raises(SystemExit) as e:
        sr.main(["emit", "--tool", "t", "--summary", "s", "--out", str(out), *extra])
    assert e.value.code == code
    assert out.exists() is written
    if written:
        assert json.loads(out.read_text()) == json.loads(capsys.readouterr().out)


# ---------- 現状固定: 配列の項目の検査が出す誤りの並び（I-009） ----------


def test_record_and_item_errors_are_fixed_in_order():
    """現状固定: items と gaps / process / decisions の誤りの文言と順序（items → gaps → process → decisions）。"""
    obj = {
        **sr.result("m", "stopped", "s"),
        "items": [1, {"x": 1}, "s"],
        "gaps": [{"kind": "k", "name": " ", "reason": 3}, "g", {}],
        "process": {"run": "r"},
        "decisions": [{"decision": "d", "basis": "b"}, None],
    }
    assert sr.validate_result(obj) == [
        "items[0] はオブジェクトで書く",
        "items[2] はオブジェクトで書く",
        "gaps[0] の name を空でない文字列で書く",
        "gaps[0] の reason を空でない文字列で書く",
        "gaps[1] はオブジェクトで書く",
        "gaps[2] の kind を空でない文字列で書く",
        "gaps[2] の name を空でない文字列で書く",
        "gaps[2] の reason を空でない文字列で書く",
        "process は配列で書く",
        "decisions[1] はオブジェクトで書く",
    ]


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("items", {}, ["items は配列で書く"]),
        ("items", None, ["items は配列で書く"]),
        ("items", [], []),
        ("gaps", None, ["gaps は配列で書く"]),
        ("gaps", [], []),
        ("process", [{"run": "r", "result": None}], ["process[0] の result を空でない文字列で書く"]),
        ("decisions", "x", ["decisions は配列で書く"]),
    ],
)
def test_single_record_errors_are_fixed(field, value, expected):
    """現状固定: 1 つの項目だけが誤るときの誤りの並び（None は配列でない扱い・空の配列は通す）。"""
    assert sr.validate_result({**sr.result("m", "stopped", "s"), field: value}) == expected
