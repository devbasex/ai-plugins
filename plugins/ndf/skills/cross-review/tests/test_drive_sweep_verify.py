"""drive.py の最終スイープの検証コマンド: 不正な `test` 宣言は既定の探し方へ流さず止める（#1483 I13）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location("cross_review_drive_sweep_verify", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def _decl(root: Path, decl: dict) -> Path:
    (root / ".ndf").mkdir()
    (root / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    return root


def test_no_declaration_keeps_the_default_search(tmp_path):
    assert cr.sweep_verify_lines(tmp_path) == ""


def test_a_valid_declaration_names_the_commands(tmp_path):
    root = _decl(tmp_path, {"test": {"suites": [{"name": "py", "command": "pytest ."}]}})
    assert "  - pytest ." in cr.sweep_verify_lines(root)


def test_an_invalid_declaration_stops_with_the_key(tmp_path):
    root = _decl(tmp_path, {"test": {"suites": [{"name": "py", "kind": "bogus", "command": "pytest ."}]}})
    with pytest.raises(cr.Stop, match=r"test\.suites\[0\]\.kind") as e:
        cr.sweep_verify_lines(root)
    assert e.value.code == 2
