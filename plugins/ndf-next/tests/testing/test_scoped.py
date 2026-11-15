"""現状固定: 宣言の suite 1 件の解釈（`scoped.suite_of`）を、`ndf route test --failed-from`（`scoped.scoped_runs`）と
cross-refactoring の検証（`verify.suites`）の両方の呼び手から通す（I-012）。

dict でない要素・`scope_command` の型違いと `{paths}` の欠け・`paths` の省略・型違い・空の並びの扱いを、今の振る舞いの
まま記録する。
"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.domains.crossrefactor import verify
from ndf.testing import scoped

UNUSABLE = [
    "not a suite",
    None,
    3,
    {"name": "int-command", "scope_command": 5},
    {"name": "no-slot", "scope_command": "pytest -q"},
]


def _runs(suites, files):
    runs, rest = scoped.scoped_runs(suites, files)
    return [(r.suite, r.command, r.files) for r in runs], rest


def test_scoped_runs_skips_elements_that_are_not_dicts_or_cannot_take_paths():
    suites = [*UNUSABLE, {"name": "py", "scope_command": "pt {paths}", "paths": ["tests"]}]
    assert _runs(suites, ["tests/a.py", "other.py"]) == ([("py", "pt tests/a.py", ("tests/a.py",))], ["other.py"])


def test_scoped_runs_reads_omitted_or_non_list_paths_as_the_whole_and_an_empty_list_as_nothing():
    suites = [
        {"name": "empty", "scope_command": "e {paths}", "paths": []},
        {"name": "text", "scope_command": "s {paths}", "paths": "web"},
    ]
    assert _runs(suites, ["web/x.ts", "b.py"]) == ([("text", "s web/x.ts b.py", ("web/x.ts", "b.py"))], [])
    assert _runs([{"name": "all", "scope_command": "a {paths}"}], ["z/y.py"]) == ([("all", "a z/y.py", ("z/y.py",))], [])


def test_verify_suites_reads_the_same_ndf_settings_the_same_way(tmp_path: Path):
    suites = [
        *UNUSABLE,
        {"name": "empty", "kind": "lint", "scope_command": "e {paths}", "paths": []},
        {"name": "text", "scope_command": "s {paths}", "paths": "web"},
        {"name": "mixed", "scope_command": "o {paths}", "paths": ["a", 1]},
        {"name": "all", "scope_command": "a {paths}"},
    ]
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}), encoding="utf-8")
    found = [(s.name, s.template, s.paths, s.test) for s in verify.suites(tmp_path)]
    assert found == [
        ("empty", "e {paths}", (), False),
        ("text", "s {paths}", (".",), True),
        ("mixed", "o {paths}", ("a", "1"), True),
        ("all", "a {paths}", (".",), True),
    ]
