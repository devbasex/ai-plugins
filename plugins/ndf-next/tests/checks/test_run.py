from __future__ import annotations

import json

from ndf import cli
from ndf.checks import run
from ndf.checks.model import load_rules


def last_json(capsys) -> tuple[dict, str]:
    out = capsys.readouterr()
    return json.loads(out.out.strip().splitlines()[-1]), out.err


def test_clean_plugin_passes(plugin, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin.root))
    plugin.write("src/ndf/a.py", "def f(x):\n    return x\n")
    assert cli.main(["check"]) == 0
    report, _ = last_json(capsys)
    assert report["status"] == "ok"
    assert report["metrics"]["findings"] == {}


def test_violation_exits_1_and_lists_items(plugin, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin.root))
    plugin.write("src/ndf/a.py", "def f(d):\n    return d.get('k', 1)\n")
    assert run.main([]) == run.EXIT_VIOLATION
    report, err = last_json(capsys)
    assert report["status"] == "stopped"
    assert [i["rule"] for i in report["items"]] == ["NDF502"]
    assert "NDF502: " in err


def test_syntax_error_exits_2(plugin, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin.root))
    plugin.write("src/ndf/a.py", "def f(:\n")
    plugin.write("src/ndf/b.py", "import ndf.a\n")
    assert run.main([]) == run.EXIT_UNREADABLE
    report, _ = last_json(capsys)
    assert [i["rule"] for i in report["items"]] == ["syntax"]


def test_pr_body_is_read_from_event(plugin, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin.root))
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
    assert run.main(["--base", base, "--event", str(event)]) == run.EXIT_VIOLATION
    capsys.readouterr()


def test_unreadable_event_stops(plugin, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin.root))
    assert run.main(["--event", str(tmp_path / "missing.json")]) == run.EXIT_UNREADABLE
    report, _ = last_json(capsys)
    assert report["cause"] == {"kind": "config_broken"}
