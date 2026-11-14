"""`ndf manifests render [--check]` の結果 JSON。"""

from __future__ import annotations

from ndf import identity
from ndf.domains.manifests import cli, render

from ._manifests import ROOT, _skill, _write_table


def test_the_plugin_definitions_are_what_render_makes(capsys, last_result):
    # CI で作り直した結果と照らす
    assert render.stale(ROOT) == []
    assert cli.main(["render", "--check"]) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"


def test_render_check_stops_on_a_difference(plugin, monkeypatch, capsys, last_result):
    _write_table(plugin, [{"name": "s", "runtimes": ["claude", "codex"]}])
    _skill(plugin, "s")
    monkeypatch.setattr(identity, "ROOT", plugin)
    assert cli.main(["render", "--check"]) != 0
    report = last_result(capsys.readouterr().out)
    assert report["cause"]["kind"] == "check_failed"
    assert report["items"] == [{"path": ".codex-plugin/plugin.json"}]
    _write_table(plugin, [{"name": "s", "runtimes": ["codex"], "why": "x"}])
    assert cli.main(["render"]) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "config_broken"
