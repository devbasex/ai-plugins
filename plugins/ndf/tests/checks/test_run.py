from __future__ import annotations

import json

import pytest

from ndf import cli, identity
from ndf.checks import model, run
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


def test_only_keeps_the_named_rules(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    plugin.write("src/ndf/a.py", "import requests\n\n\ndef f(d):\n    return d.get('k', 1)\n")
    assert run.main(["--only", "I3"]) == exit_codes.code_of("failed")
    assert [i["rule"] for i in last_result(capsys.readouterr().out)["items"]] == ["I3"]
    assert run.main(["--only", "NDF999"]) == 0
    capsys.readouterr()


def _settings_rule(scope, rules):
    return [model.Finding("S1", "x", 1, str(scope.settings))]


def test_only_without_a_settings_rule_does_not_read_a_broken_settings_file(plugin, monkeypatch, capsys, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    monkeypatch.setitem(run.SETTINGS_RULES, "S1", _settings_rule)
    settings = plugin.repo / identity.FIXED_WORDS["ndf_settings_dir"] / "project.json"
    settings.parent.mkdir()
    settings.write_text("{", encoding="utf-8")
    plugin.write("src/ndf/a.py", "import requests\n")
    assert run.main(["--only", "I3"]) == exit_codes.code_of("failed")
    assert [i["rule"] for i in last_result(capsys.readouterr().out)["items"]] == ["I3"]
    assert run.main(["--only", "S1"]) == exit_codes.unreadable_code()
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "config_broken"


def _missing_rules(monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(run, "load_rules", lambda: model.load_rules(missing))
    return missing


def test_unreadable_rules_stop(plugin, monkeypatch, capsys, tmp_path, last_result):
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    _missing_rules(monkeypatch, tmp_path)
    assert run.main([]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert report["cause"] == {"kind": "config_broken", "detail": report["summary"]}


def test_collect_does_not_descend_into_skipped_dirs(plugin, monkeypatch):
    plugin.write("src/ndf/a.py", "x = 1\n")
    plugin.write(".venv/lib/site.py", "x = 1\n")
    walked = []
    real_walk = model.os.walk

    def spy(top):
        for entry in real_walk(top):
            walked.append(entry[0])
            yield entry

    monkeypatch.setattr(model.os, "walk", spy)
    found = [s.rel.rsplit("/", 1)[-1] for s in run.collect(plugin.root, plugin.repo)]
    assert "a.py" in found and "site.py" not in found
    assert not any(".venv" in top for top in walked)


# 結果のコントラクト（ndf.contracts.result）に合う 1 行を出し、種類の終了コードを返す（#3394 の 3394-2）
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
    items = [{"rule": "NDF502", "path": "a.py", "line": 2, "detail": "x"}]
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
    missing = _missing_rules(monkeypatch, tmp_path)
    assert run.main([]) == exit_codes.unreadable_code()
    report = last_result(capsys.readouterr().out)
    assert set(report) == {"tool", "status", "summary", "items", "metrics", "cause"}
    assert report["tool"] == run.TOOL
    assert report["status"] == "stopped"
    assert report["summary"].startswith(f"規則のデータを読めない: {missing}: ")
    assert report["items"] == [] and report["metrics"] == {}
    assert report["cause"] == {"kind": "config_broken", "detail": report["summary"]}
    assert result.errors(report, exit_codes.unreadable_code()) == []


def test_only_settings_rules_skip_collecting_and_the_other_rules(tmp_path, monkeypatch):
    # ワークフローだけを変えた Pull Request の `--only toolchain` は、プラグインのファイルを集めずにその規則だけを当てる
    monkeypatch.setitem(run.SETTINGS_RULES, "S1", lambda scope, rules: [model.Finding("S1", "x", 1, "d")])
    monkeypatch.setattr(run, "collect", lambda *a: (_ for _ in ()).throw(AssertionError("ファイルを集めた")))
    monkeypatch.setattr(run, "check", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ほかの規則を当てた")))
    scope = model.Scope(tmp_path, tmp_path / "p", "p")
    assert [f.rule for f in run.select(scope, {}, ["S1"])] == ["S1"]


def test_the_findings_of_each_rule_remain_when_the_next_rule_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "smell_findings", lambda *args: [])
    for module, name in ((run.structure, "check"), (run.structure, "cycles"), (run.facts, "check"), (run.words, "check")):
        rule = f"{module.__name__}.{name}"
        monkeypatch.setattr(module, name, lambda *args, rule=rule: [model.Finding(rule, rule, 1, "d")])

    def stop(*args):
        raise model.CheckError("pyproject.toml を読めない")

    monkeypatch.setattr(run.invariants, "check", stop)
    monkeypatch.setattr(run, "CONTRACT_RULES", {})
    with pytest.raises(run.Halted, match="pyproject.toml") as caught:
        run.check(model.Scope(tmp_path, tmp_path / "p", "p"), {})
    expected = ["ndf.checks.facts.check", "ndf.checks.structure.check", "ndf.checks.structure.cycles", "ndf.checks.words.check"]
    assert [f.rule for f in caught.value.found] == expected
