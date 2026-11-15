"""設定のキーの改名表と、旧いキーの一覧・変換・止まる理由（#3613。I9・受け入れ条件 11・12）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import settings_legacy as legacy
from ndf import contracts
from ndf.contracts import facts
from ndf.domains.settings import renames


def _schema(file: str) -> dict[str, Any]:
    return json.loads((contracts.DEFAULT_DIR / contracts.schema_path(f"ndf-settings/{file}")).read_text(encoding="utf-8"))


def _has(schema: dict[str, Any], pointer: str) -> bool:
    """スキーマの properties を位置の階層ごとにたどる（`*` は items。$ref は $defs を引く）。"""
    node = schema
    for t in renames.tokens(pointer):
        while "$ref" in node:
            node = schema["$defs"][node["$ref"].rsplit("/", 1)[-1]]
        node = node.get("items", {}) if t == renames.EVERY else node.get("properties", {}).get(t)
        if node is None:
            return False
    return True


def test_rename_table_matches_the_schemas():
    rows = renames.load()
    assert [r.id for r in rows] == ["worktree-release-strategy", "mvv-validation", "mvv-validation-result", "mvv-validation-checked-at"]
    for r in rows:
        assert r.old != r.new
        assert renames.tokens(r.old)[:-1] == renames.tokens(r.new)[:-1]
        assert _has(_schema(r.file), r.new), r.new
        assert not _has(_schema(r.file), r.old), r.old


def test_branch_flow_table_uses_only_the_new_values():
    assert "release_strategy" in facts.table("branch-flows").columns
    news = set(next(r for r in renames.load() if r.file == "worktree").values.values())
    assert {r["release_strategy"] for r in facts.rows("branch-flows")} <= news


def test_tokens_unescape_reference_tokens():
    assert renames.tokens("/a~1b/c~0d") == ["a/b", "c~d"]
    assert renames.tokens("") == []


def test_legacy_keys_list_each_array_element_of_the_parent_first():
    data = legacy.mvv(2)
    got = renames.legacy_keys("mvv", data)
    key = legacy.old_validation()[0]
    assert [(k.old, k.new) for k in got] == [(f"/versions/0/{key}", "/versions/0/validation"), (f"/versions/1/{key}", "/versions/1/validation")]
    half = copy.deepcopy(data)
    half["versions"] = [{"validation": v.pop(key)} for v in half["versions"]][:1]
    assert [k.new for k in renames.legacy_keys("mvv", half)] == ["/versions/0/validation/result", "/versions/0/validation/checked_at"]


def test_legacy_keys_are_empty_for_new_or_unknown_shapes():
    new, _ = renames.migrate("mvv", legacy.mvv(1))
    assert renames.legacy_keys("mvv", new) == []
    assert renames.legacy_keys("other", legacy.mvv(1)) == []
    assert renames.legacy_keys("mvv", {"versions": []}) == []
    assert renames.legacy_keys("mvv", {"versions": "not a list"}) == []


def test_migrate_renames_keys_in_place_and_maps_values():
    data = legacy.worktree("release_branch")
    before = copy.deepcopy(data)
    got, patch = renames.migrate("worktree", data)
    assert data == before
    assert list(got["branch_flow"]) == ["start", "release_strategy", "doc_prs"]
    assert got["branch_flow"]["release_strategy"] == "release_branch"
    assert [op["op"] for op in patch] == ["test", "move", "test", "replace"]
    assert contracts.schema_errors(got, contracts.schema_path("ndf-settings/worktree")) == []
    merged, _ = renames.migrate("worktree", legacy.worktree("merge_base_branch"))
    assert merged["branch_flow"]["release_strategy"] == "merge_base_branch"


def test_migrate_mvv_keeps_unlisted_keys_and_passes_the_schema():
    data = legacy.mvv(2)
    got, patch = renames.migrate("mvv", data)
    assert got["settings"] == {"revise_after": {"overrides": 3}}
    first = got["versions"][0]
    assert first["validation"] == {"result": "pass", "checked_at": "2026-01-01T00:00:00+00:00", "accepted_unknown": False}
    assert list(first) == ["version", "sha256", "approved_at", "approved_by", "validation", "changes", "body"]
    assert renames.legacy_keys("mvv", got) == []
    assert contracts.schema_errors(got, contracts.schema_path("ndf-settings/mvv")) == []
    for i, op in enumerate(patch):
        if op["op"] in ("move", "replace"):
            assert patch[i - 1]["op"] == "test"


def test_migrate_leaves_both_keys_when_the_user_wrote_both():
    data = legacy.worktree()
    data["branch_flow"]["release_strategy"] = "release_branch"
    got, patch = renames.migrate("worktree", data)
    assert patch == [] and got == data
    assert len(renames.legacy_keys("worktree", got)) == 1


def test_legacy_detail_names_every_key_and_the_command(tmp_path: Path):
    text = renames.legacy_detail("mvv", legacy.mvv(2), tmp_path)
    assert text is not None
    assert ".ndf/mvv.json" in text and "/versions/1/validation" in text and f"ndf settings migrate --root {tmp_path}" in text
    new, _ = renames.migrate("mvv", legacy.mvv(1))
    assert renames.legacy_detail("mvv", new, tmp_path) is None
