from __future__ import annotations

import json

from ndf import cli, identity
from ndf.checks import run
from ndf.checks.model import load_rules
from ndf.contracts import exit_codes, result


def test_clean_plugin_passes(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    plugin.write("src/ndf/a.py", "def f(x):\n    return x\n")
    assert cli.main(["check"]) == 0
    report = last_result(capsys.readouterr().out)
    assert report["status"] == "ok"
    assert report["metrics"]["findings"] == {}


def test_violation_exits_1_and_lists_items(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    plugin.write("src/ndf/a.py", "def f(d):\n    return d.get('k', 1)\n")
    assert run.main([]) == exit_codes.code_of("failed")
    out = capsys.readouterr()
    report, err = last_result(out.out), out.err
    assert report["status"] == "stopped"
    assert [i["rule"] for i in report["items"]] == ["NDF502"]
    assert "NDF502: " in err


def test_syntax_error_exits_2(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    plugin.write("src/ndf/a.py", "def f(:\n")
    plugin.write("src/ndf/b.py", "import ndf.a\n")
    assert run.main([]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert [i["rule"] for i in report["items"]] == ["syntax"]
    assert report["cause"]["kind"] == "invalid"
    assert result.errors(report, exit_codes.unreadable_code()) == []


def test_pr_body_is_read_from_event(plugin, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    base = plugin.commit("base")
    old = plugin.repo / "plugins" / "ndf"
    old.mkdir()
    (old / "x.py").write_text("x = 1\n")
    plugin.commit("old")
    mark = load_rules()["invariants"]["immediate_fix_mark"]
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"body": f"{mark} 直す"}}, ensure_ascii=False))
    assert run.main(["--base", base, "--event", str(event)]) == 0
    event.write_text(json.dumps({"pull_request": {"body": None}}))
    assert run.main(["--base", base, "--event", str(event)]) == exit_codes.code_of("failed")
    capsys.readouterr()


def test_unreadable_event_stops(plugin, monkeypatch, capsys, tmp_path, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    assert run.main(["--event", str(tmp_path / "missing.json")]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert report["cause"] == {"kind": "config_broken", "detail": report["summary"]}


def test_collect_does_not_descend_into_skipped_dirs(plugin, monkeypatch):
    plugin.write("src/ndf/a.py", "x = 1\n")
    plugin.write(".venv/lib/site.py", "x = 1\n")
    walked = []
    real_walk = run.os.walk

    def spy(top):
        for entry in real_walk(top):
            walked.append(entry[0])
            yield entry

    monkeypatch.setattr(run.os, "walk", spy)
    found = [s.rel.rsplit("/", 1)[-1] for s in run.collect(plugin.root, plugin.repo)]
    assert "a.py" in found and "site.py" not in found
    assert not any(".venv" in top for top in walked)


# 結果の契約（ndf.contracts.result）に合う 1 行を出し、種類の終了コードを返す（#3394 の 3394-2）
def test_emit_ok_prints_one_line_without_cause(capsys):
    assert run.emit("3 ファイルに違反なし", [], {"files": 3, "findings": {}}) == exit_codes.code_of("ok")
    out = capsys.readouterr()
    assert out.err == ""
    assert out.out.count("\n") == 1
    assert json.loads(out.out) == {
        "tool": run.TOOL,
        "status": "ok",
        "summary": "3 ファイルに違反なし",
        "items": [],
        "metrics": {"files": 3, "findings": {}},
    }


def test_emit_violation_has_check_failed_cause(capsys):
    items = [{"rule": "NDF502", "path": "a.py", "line": 2}]
    assert run.emit("違反 1 件", items, {"files": 1, "findings": {"NDF502": 1}}, "check_failed") == exit_codes.code_of("failed")
    assert json.loads(capsys.readouterr().out) == {
        "tool": run.TOOL,
        "status": "stopped",
        "summary": "違反 1 件",
        "items": items,
        "metrics": {"files": 1, "findings": {"NDF502": 1}},
        "cause": {"kind": "check_failed", "detail": "違反 1 件"},
    }


def test_emit_unreadable_is_config_broken_with_detail(capsys):
    assert run.emit("読めない", [], {}, "config_broken") == exit_codes.unreadable_code()
    out = capsys.readouterr().out
    assert "読めない" in out  # 非 ASCII をそのまま出す
    assert json.loads(out)["cause"] == {"kind": "config_broken", "detail": "読めない"}


def test_main_ok_report_shape(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    plugin.write("src/ndf/a.py", "def f(x):\n    return x\n")
    assert run.main([]) == exit_codes.code_of("ok")
    report = last_result(capsys.readouterr().out)
    assert set(report) == {"tool", "status", "summary", "items", "metrics"}
    assert report["tool"] == run.TOOL
    assert report["summary"] == f"{report['metrics']['files']} ファイルに違反なし"
    assert report["items"] == []


def test_main_violation_report_shape(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    plugin.write("src/ndf/a.py", "def f(d):\n    return d.get('k', 1)\n")
    assert run.main([]) == exit_codes.code_of("failed")
    report = last_result(capsys.readouterr().out)
    assert set(report) == {"tool", "status", "summary", "items", "metrics", "cause"}
    assert result.errors(report, exit_codes.code_of("failed")) == []
    assert report["cause"] == {"kind": "check_failed", "detail": "違反 1 件"}
    assert report["summary"] == "違反 1 件"
    assert report["metrics"]["findings"] == {"NDF502": 1}


def test_main_unreadable_report_shape(plugin, monkeypatch, capsys, tmp_path, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    missing = tmp_path / "missing.json"
    assert run.main(["--event", str(missing)]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert set(report) == {"tool", "status", "summary", "items", "metrics", "cause"}
    assert report["tool"] == run.TOOL
    assert report["status"] == "stopped"
    assert report["summary"].startswith(f"事象の JSON を読めない: {missing}: ")
    assert report["items"] == [] and report["metrics"] == {}
    assert report["cause"] == {"kind": "config_broken", "detail": report["summary"]}
    assert result.errors(report, exit_codes.unreadable_code()) == []
