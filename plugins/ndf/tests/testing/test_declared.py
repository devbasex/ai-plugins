from __future__ import annotations

import json

from ndf.testing import declared


def test_load_suites_skips_lint(tmp_path):
    (tmp_path / ".ndf").mkdir()
    suites = [{"runner": "pytest", "command": "pytest ."}, {"runner": "ruff", "kind": "lint", "command": "ruff ."}]
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": suites}}))
    assert [s["runner"] for s in declared.load_suites(tmp_path)] == ["pytest"]
