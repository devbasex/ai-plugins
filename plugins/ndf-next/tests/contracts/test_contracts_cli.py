from __future__ import annotations

import json

from ndf import cli
from ndf.contracts import cli as contracts_cli


def _run(capsys, *argv):
    code = cli.main(["contracts", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


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


def test_check_reports_missing_ref_target(contracts_copy, capsys):
    table = {
        "version": 1,
        "summary": "s",
        "owner": "o",
        "columns": {"phase": {"type": "string", "summary": "s", "ref": "limits.json#monitor.phase."}},
        "rows": [{"id": "a", "phase": "nope"}],
    }
    contracts_copy("facts/sample.json", lambda _: table)
    assert contracts_cli.check_all() == ["sample:a の phase 'nope' の方針 monitor.phase.nope が上限の宣言に無い"]


def test_fact_ids_and_value(capsys):
    assert _run(capsys, "fact-ids", "exit-codes", "status=gate")[1].split() == ["gate", "pause"]
    assert _run(capsys, "fact-value", "exit-codes", "gate", "last")[1].strip() == "19"


def test_fact_value_prints_arrays_and_booleans(contracts_copy, capsys):
    table = {
        "version": 1,
        "summary": "s",
        "owner": "o",
        "columns": {"tags": {"type": "array", "summary": "s"}, "flag": {"type": "boolean", "summary": "s"}},
        "rows": [{"id": "a", "tags": ["x", "y"], "flag": False}],
    }
    contracts_copy("facts/sample.json", lambda _: table)
    assert _run(capsys, "fact-value", "sample", "a", "tags")[1].split() == ["x", "y"]
    assert _run(capsys, "fact-value", "sample", "a", "flag")[1].strip() == "false"


def test_fact_lookup_error_is_one_stderr_line(capsys):
    code, out, err = _run(capsys, "fact-value", "exit-codes", "nope", "code")
    assert code == 2 and out == ""
    assert len(err.strip().splitlines()) == 1


def test_emit_stopped_with_cause_takes_the_kind_code(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, _ = _run(capsys, "emit", "--tool", "w", "--status", "stopped", "--summary", "s", "--cause", "infra_wait", "Runner が付かない", "--out", str(dest))
    assert code == 75
    assert json.loads(out)["cause"]["kind"] == "infra_wait"
    assert json.loads(dest.read_text()) == json.loads(out)


def test_emit_incomplete_with_gaps(capsys):
    code, _, _ = _run(capsys, "emit", "--tool", "w", "--status", "stopped", "--summary", "s", "--cause", "incomplete", "欠け", "--gap", "test", "x", "未確認")
    assert code == 6


def test_emit_gate_with_routes(capsys):
    code, out, _ = _run(capsys, "emit", "--tool", "w", "--status", "gate", "--summary", "s", "--route", "approved", "ndf go", "--route", "rejected", "-")
    assert code == 10
    assert json.loads(out)["routes"] == {"approved": "ndf go", "rejected": None}


def test_emit_refuses_stopped_without_cause_and_writes_nothing(capsys, tmp_path):
    dest = tmp_path / "r.json"
    code, out, err = _run(capsys, "emit", "--tool", "w", "--status", "stopped", "--summary", "s", "--out", str(dest))
    assert code == 2 and out == ""
    assert "結果の形の誤り" in err
    assert not dest.exists()
