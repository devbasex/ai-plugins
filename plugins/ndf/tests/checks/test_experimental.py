"""規則 experimental: 既定で動くもの（プラグインの根の下で配るファイル）から実験版の保存先への参照を違反にする。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ndf import identity
from ndf.checks import experimental, lint, run
from ndf.checks.model import CheckError, Scope

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
TABLE = experimental.table_rel()


def _needle() -> str:
    """実験版の保存先のパス（保存先の表の実験版の項目から得る）。"""
    rows = json.loads((PLUGIN_ROOT / TABLE).read_text(encoding="utf-8"))["rows"]
    return next(r["path"] for r in rows if r["id"] == experimental.ROW_ID)


@pytest.fixture
def table(plugin):
    """プラグインへ今の保存先の表を置く。"""
    dest = plugin.root / TABLE
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(PLUGIN_ROOT / TABLE, dest)
    return dest


def _check(plugin, rules):
    return experimental.check(plugin.scope(), rules)


def test_reference_from_src_is_a_finding(plugin, rules, table, where):
    plugin.write("src/ndf/a.py", f'"""doc"""\n\nPATH = "{_needle()}/x.py"\n')
    found = _check(plugin, rules)
    assert [f.rule for f in found] == ["experimental"]
    assert where(found) == [("a.py", 3)]


def test_reference_from_bin_script_is_a_finding(plugin, rules, table, where):
    plugin.write("bin/ndf", f"#!/bin/sh\nexec python3 {_needle()}/x.py\n")
    assert where(_check(plugin, rules)) == [("ndf", 2)]


def test_reference_from_skill_javascript_is_a_finding(plugin, rules, table, where):
    plugin.write("skills/release/assets/slides/build-deck.js", f"require('../../../../{_needle()}/x.js');\n")
    assert where(_check(plugin, rules)) == [("build-deck.js", 1)]


def test_reference_from_plugin_manifest_is_a_finding(plugin, rules, table, where):
    plugin.write(".codex-plugin/plugin.json", json.dumps({"hooks": f"{_needle()}/h.json"}))
    assert where(_check(plugin, rules)) == [("plugin.json", 1)]


def test_reference_from_readme_and_changelog_and_docs_is_allowed(plugin, rules, table):
    for rel in ("README.md", "CHANGELOG.md", "docs/a.md"):
        plugin.write(rel, f"試すときは python3 {_needle()}/x.py を打つ\n")
    assert _check(plugin, rules) == []


def test_location_column_of_the_experimental_row_is_allowed(plugin, rules, table):
    assert _check(plugin, rules) == []


def test_other_row_summary_in_the_table_is_a_finding(plugin, rules, table, where):
    data = json.loads(table.read_text(encoding="utf-8"))
    data["columns"]["summary"] = {"type": "string", "summary": "説明", "optional": True}
    data["rows"][0]["summary"] = f"{_needle()} を見る"
    table.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    found = _check(plugin, rules)
    assert [f.path.rsplit("/", 1)[-1] for f in found] == ["locations.json"]


def test_references_among_experimental_files_are_allowed(plugin, rules, table):
    (plugin.repo / _needle()).mkdir(parents=True)
    (plugin.repo / _needle() / "a.py").write_text(f"import runpy\nrunpy.run_path('{_needle()}/b.py')\n", encoding="utf-8")
    assert _check(plugin, rules) == []


def test_rule_implementation_and_its_test_are_allowed(plugin, rules, table):
    for rel in experimental.EXEMPT_FILES:
        plugin.write(rel, f"X = '{_needle()}'\n")
    assert _check(plugin, rules) == []


def test_without_the_experimental_row_there_is_no_finding(plugin, rules, table):
    data = json.loads(table.read_text(encoding="utf-8"))
    data["rows"] = [r for r in data["rows"] if r["id"] != experimental.ROW_ID]
    table.write_text(json.dumps(data), encoding="utf-8")
    plugin.write("src/ndf/a.py", f"X = '{_needle()}'\n")
    assert _check(plugin, rules) == []


def test_unreadable_table_stops_the_check(plugin, rules, table):
    table.write_text("{", encoding="utf-8")
    with pytest.raises(CheckError):
        _check(plugin, rules)


def test_link_to_outside_the_repository_is_not_read(plugin, tmp_path, monkeypatch, capsys, last_result):
    """CLI の通常の経路（`ndf check`）で、外を指すリンクの本文をどの規則も読まない（構文の確かめで本文が出ない）。"""
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET_IN_OUTSIDE = (\n", encoding="utf-8")
    (plugin.root / "link.py").symlink_to(outside)
    monkeypatch.setattr(identity, "ROOT", plugin.root)
    monkeypatch.setattr(lint, "run_ruff", lambda rules, paths: [])
    run.main([])
    out = capsys.readouterr()
    assert last_result(out.out)["status"] == "ok"
    assert "SECRET_IN_OUTSIDE" not in out.out + out.err and "link.py" not in out.out + out.err


def test_table_linked_to_outside_the_repository_is_not_read(plugin, rules, table, tmp_path):
    outside = tmp_path / "table.json"
    outside.write_text("{", encoding="utf-8")
    table.unlink()
    table.symlink_to(outside)
    assert all(s.path != table for s in plugin.scope().sources)
    assert _check(plugin, rules) == []


def test_this_repository_has_no_reference(rules):
    repo = PLUGIN_ROOT.parents[1]
    scope = Scope(repo, PLUGIN_ROOT, "ndf")
    scope.sources = run.collect(PLUGIN_ROOT, repo)
    assert experimental.check(scope, rules) == []
