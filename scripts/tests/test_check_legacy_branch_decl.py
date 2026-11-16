"""check-legacy-branch-decl.py（#3672 の受け入れ条件 21）のテスト。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("check_legacy_branch_decl", REPO_ROOT / "scripts/check-legacy-branch-decl.py")
chk = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(chk)


def write(tmp_path: Path, rows: list[dict], files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return data


def test_hits_each_key_form(tmp_path):
    rows = [{"id": "D12", "path": "a.py", "words": ["base_branch"]}]
    text = 'x = d["base_branch"]\ny = d.get(\'base_branch\')\nz = jq .base_branch\nw = "`base_branch` を読む"\nok = existing_base_branch(root)\n'
    data = write(tmp_path, rows, {"a.py": text})
    hits = chk.scan_rows(tmp_path, chk.load_rows(data))
    assert [h["line"] for h in hits] == [1, 2, 3, 4]
    assert chk.main(["--root", str(tmp_path), "--data", str(data)]) == 1


def test_missing_file_passes_and_clean_file_passes(tmp_path):
    rows = [{"id": "D11", "path": "gone.py", "words": ["branch_flow"]}, {"id": "D12", "path": "b.py", "words": ["branch_flow"]}]
    data = write(tmp_path, rows, {"b.py": "import branch_strategy\n"})
    assert chk.scan_rows(tmp_path, chk.load_rows(data)) == []
    assert chk.main(["--root", str(tmp_path), "--data", str(data)]) == 0


@pytest.mark.parametrize("rows", [[{"id": "X1", "path": "a", "words": ["w"]}], [{"id": "D1", "path": "a", "words": []}], "nope"])
def test_bad_data_is_2(tmp_path, rows):
    data = tmp_path / "d.json"
    data.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    assert chk.main(["--root", str(tmp_path), "--data", str(data)]) == 2


def test_repository_data_shape():
    rows = chk.load_rows(chk.DATA)
    ids = {r["id"].split("-")[0] for r in rows}
    assert {"D11", "D15", "D17", "D31", "D32"} <= ids
    paths = {r["path"] for r in rows}
    # 旧いキーが残ってよい置き場（I8）は一覧に載せない
    for allowed in ("plugins/ndf/scripts/project_lib/branch_strategy_migrate.py", "plugins/ndf/scripts/lib/branch_strategy_legacy.py",
                    "plugins/ndf/scripts/data/ndf-settings-renames.json", "CHANGELOG.md"):
        assert allowed not in paths


# --- check-skill-repo-assumptions.py の branch_strategy_problems（受け入れ条件 16・I14） -----------------

_SPEC2 = importlib.util.spec_from_file_location("check_skill_repo_assumptions", REPO_ROOT / "scripts/check-skill-repo-assumptions.py")
assumptions = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(assumptions)


def _strategy_tree(tmp_path: Path) -> Path:
    for rel in (assumptions.BRANCH_STRATEGY_SCHEMA, assumptions.BRANCH_STRATEGY_PRESETS, *assumptions.BRANCH_STRATEGY_CODE):
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_branch_strategy_problems_clean_on_repository():
    assert assumptions.branch_strategy_problems(REPO_ROOT) == []


def test_branch_strategy_problems_catch_each_kind(tmp_path):
    root = _strategy_tree(tmp_path)
    schema = root / assumptions.BRANCH_STRATEGY_SCHEMA
    d = json.loads(schema.read_text(encoding="utf-8"))
    d["BranchStrategy"]["properties"]["kind"]["default"] = "plugin"
    schema.write_text(json.dumps(d), encoding="utf-8")
    presets = root / assumptions.BRANCH_STRATEGY_PRESETS
    p = json.loads(presets.read_text(encoding="utf-8"))
    p["kinds"]["plugin"]["section"]["release"]["plugin"] = "ndf"
    p["kinds"]["web-develop"]["section"]["merge"]["admin"] = True
    presets.write_text(json.dumps(p, indent=1), encoding="utf-8")
    code = root / assumptions.BRANCH_STRATEGY_CODE[1]
    code.write_text(code.read_text(encoding="utf-8") + '\nTARGET = "develop"\n', encoding="utf-8")
    text = "\n".join(assumptions.branch_strategy_problems(root))
    assert "default" in text and '"plugin": "ndf"' in text and "merge.admin" in text and "決め打って" in text


def test_branch_strategy_problems_unreadable_is_scan_error(tmp_path):
    with pytest.raises(assumptions.ScanError):
        assumptions.branch_strategy_problems(tmp_path)
