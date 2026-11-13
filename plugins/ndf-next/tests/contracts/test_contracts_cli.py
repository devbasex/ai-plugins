from __future__ import annotations

import json

import pytest

from ndf import cli
from ndf.contracts import cli as contracts_cli


def _run(capsys, *argv):
    code = cli.main(["contracts", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def _emit(capsys, status, *extra):
    """`emit --tool w --status <status> --summary s` に、テストごとの差（cause・gap・route・out）を足して打つ。"""
    return _run(capsys, "emit", "--tool", "w", "--status", status, "--summary", "s", *extra)


def test_check_passes_on_plugin_contracts(capsys):
    code, out, _ = _run(capsys, "check")
    assert code == 0
    assert json.loads(out)["status"] == "ok"


def test_check_reports_every_broken_table(contracts_copy, capsys):
    contracts_copy("causes.json", text="{")
    contracts_copy("limits.json", lambda d: {**d, "version": 2})
    code, out, _ = _run(capsys, "check")
    report = json.loads(out)
    assert code == 2
    assert report["cause"]["kind"] == "invalid"
    assert len(report["items"]) >= 2


def test_check_reports_missing_ref_target(contracts_copy, fact_table):
    table = fact_table({"phase": {"type": "string", "summary": "s", "ref": "limits.json#monitor.phase."}}, [{"id": "a", "phase": "nope"}])
    contracts_copy("facts/sample.json", lambda _: table)
    assert contracts_cli.check_all() == ["sample:a の phase 'nope' の方針 monitor.phase.nope が上限の宣言に無い"]


def test_fact_ids_and_value(capsys):
    assert _run(capsys, "fact-ids", "exit-codes", "status=gate")[1].split() == ["gate", "pause"]
    assert _run(capsys, "fact-value", "exit-codes", "gate", "last")[1].strip() == "19"


def test_fact_value_prints_arrays_and_booleans(contracts_copy, fact_table, capsys):
    table = fact_table(
        {"tags": {"type": "array", "summary": "s"}, "flag": {"type": "boolean", "summary": "s"}},
        [{"id": "a", "tags": ["x", "y"], "flag": False}],
    )
    contracts_copy("facts/sample.json", lambda _: table)
    assert _run(capsys, "fact-value", "sample", "a", "tags")[1].split() == ["x", "y"]
    assert _run(capsys, "fact-value", "sample", "a", "flag")[1].strip() == "false"


def test_fact_lookup_error_is_one_stderr_line(capsys):
    code, out, err = _run(capsys, "fact-value", "exit-codes", "nope", "code")
    assert code == 2 and out == ""
    assert len(err.strip().splitlines()) == 1


def test_emit_stopped_with_cause_takes_the_kind_code(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, _ = _emit(capsys, "stopped", "--cause", "infra_wait", "Runner が付かない", "--out", str(dest))
    assert code == 75
    assert json.loads(out)["cause"]["kind"] == "infra_wait"
    assert json.loads(dest.read_text()) == json.loads(out)


def test_emit_incomplete_with_gaps(capsys):
    code, _, _ = _emit(capsys, "stopped", "--cause", "incomplete", "欠け", "--gap", "test", "x", "未確認")
    assert code == 6


def test_emit_gate_with_routes(capsys):
    code, out, _ = _emit(capsys, "gate", "--route", "approved", "ndf go", "--route", "rejected", "-")
    assert code == 10
    assert json.loads(out)["routes"] == {"approved": "ndf go", "rejected": None}


def test_emit_refuses_stopped_without_cause_and_writes_nothing(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, err = _emit(capsys, "stopped", "--out", str(dest))
    assert code == 2 and out == ""
    assert "結果の形の誤り" in err
    assert not dest.exists()


# 現状固定（I-007）: fact-value の値の取り出しと、断るときの理由の順（行 → 列の宣言 → 行の列）。
@pytest.mark.parametrize(
    ("argv", "printed"),
    [
        (("exit-codes", "gate", "id"), "gate\n"),
        (("exit-codes", "ok", "code"), "0\n"),
        (("exit-codes", "ok", "label"), "成功\n"),
        (("exit-codes", "gate", "last"), "19\n"),
    ],
)
def test_fact_value_prints_one_value(capsys, argv, printed):
    assert _run(capsys, "fact-value", *argv) == (0, printed, "")


@pytest.mark.parametrize(
    ("argv", "err"),
    [
        (("exit-codes", "ok", "last"), "contracts: 事実の表 exit-codes の行 ok は列 last を持たない\n"),
        (("exit-codes", "ok", "nope"), "contracts: 事実の表 exit-codes に列 nope が無い\n"),
        (("exit-codes", "nope", "nope"), "contracts: 事実の表 exit-codes に行 'nope' が無い\n"),
        (("exit-codes", "nope", "code"), "contracts: 事実の表 exit-codes に行 'nope' が無い\n"),
        (("Bad", "a", "b"), "contracts: 表の名前が違う形: 'Bad'\n"),
    ],
)
def test_fact_value_refusals(capsys, argv, err):
    assert _run(capsys, "fact-value", *argv) == (2, "", err)


def test_fact_value_missing_table(capsys):
    code, out, err = _run(capsys, "fact-value", "no-such", "a", "b")
    assert (code, out) == (2, "")
    assert err.startswith("contracts: 契約 ") and "facts/no-such.json を読めない" in err


# 現状固定（I-010）: check_all が確かめるスキーマの名前と、壊れた 1 ファイルごとの誤りの数と順。
@pytest.mark.parametrize(
    ("broken", "count"),
    [
        ("schemas/result.schema.json", 1),
        ("schemas/causes.schema.json", 2),
        ("schemas/limits.schema.json", 3),
        ("schemas/release-verification.schema.json", 1),
        ("causes.json", 1),
        ("facts/exit-codes.json", 2),
    ],
)
def test_check_all_reports_one_broken_file(contracts_copy, broken, count):
    path = contracts_copy(broken, text="{")
    errs = contracts_cli.check_all()
    assert len(errs) == count
    assert all(e.startswith(f"契約 {path} が JSON として壊れている: ") for e in errs)


def test_check_all_broken_limits_also_breaks_causes(contracts_copy):
    path = contracts_copy("limits.json", text="{")
    errs = contracts_cli.check_all()
    assert len(errs) == 2
    assert errs[0].startswith(f"契約 {path} が JSON として壊れている: ")
    assert errs[1].startswith("止まった理由の表 causes.json の誤り: ci_pending の再試行の方針: ")


def test_check_all_ignores_schemas_outside_its_list(contracts_copy):
    contracts_copy("schemas/extra.schema.json", text="{")
    assert contracts_cli.check_all() == []


# 現状固定（I-011）: emit の --code の扱いと、--out を書くのは形と終了コードが合うときだけであること。
def test_emit_explicit_code_writes_out(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, err = _emit(capsys, "ok", "--code", "0", "--out", str(dest))
    line = '{"tool": "w", "status": "ok", "summary": "s", "items": [], "metrics": {}}\n'
    assert (code, out, err) == (0, line, "")
    assert dest.read_text(encoding="utf-8") == line


@pytest.mark.parametrize(
    ("status", "extra", "err"),
    [
        ("ok", ("--code", "3"), "結果の形の誤り: status ok と終了コード 3 が合わない\n"),
        (
            "stopped",
            ("--cause", "infra_wait", "d", "--code", "2"),
            "結果の形の誤り: cause.kind infra_wait の終了コードは 75（渡された値 2）\n",
        ),
        ("stopped", ("--cause", "nope", "d"), "結果の形の誤り: cause.kind 'nope' は止まった理由の表に無い\n"),
        ("gate", (), "結果の形の誤り: (最上位): 'routes' is a required property\n"),
    ],
)
def test_emit_refusals_write_nothing(capsys, tmp_path, status, extra, err):
    dest = tmp_path / "r.json"
    assert _emit(capsys, status, *extra, "--out", str(dest)) == (2, "", err)
    assert not dest.exists()


def test_emit_passes_every_field(capsys):
    code, out, _ = _emit(
        capsys,
        "stopped",
        "--cause",
        "infra_wait",
        "d",
        "--cause-instance",
        "i1",
        "--retry-after",
        "30",
        "--item",
        '{"a": 1}',
        "--metrics",
        '{"n": 2}',
        "--next",
        "nx",
        "--presentation-path",
        "p.md",
    )
    assert code == 75
    assert json.loads(out) == {
        "tool": "w",
        "status": "stopped",
        "summary": "s",
        "items": [{"a": 1}],
        "metrics": {"n": 2},
        "cause": {"kind": "infra_wait", "detail": "d", "instance": "i1", "retry_after": 30.0},
        "presentation_path": "p.md",
        "next": "nx",
    }


# 現状固定（I-011）: --out の既存ファイルは断ったとき書き換えず、書けない宛先では標準出力へも出さない。
def test_emit_mismatched_code_keeps_existing_out(capsys, tmp_path):
    dest = tmp_path / "r.json"
    dest.write_text("old\n", encoding="utf-8")
    assert _emit(capsys, "ok", "--code", "3", "--out", str(dest)) == (2, "", "結果の形の誤り: status ok と終了コード 3 が合わない\n")
    assert dest.read_text(encoding="utf-8") == "old\n"


def test_emit_unwritable_out_raises_before_printing(capsys, tmp_path):
    with pytest.raises(IsADirectoryError):
        _emit(capsys, "ok", "--out", str(tmp_path))
    assert capsys.readouterr() == ("", "")
