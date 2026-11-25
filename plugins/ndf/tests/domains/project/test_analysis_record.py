"""解析の記録（`.ndf/analysis/project.json`）の形・旧い形のキーの取り除き・読み込み（#4516）。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ndf import contracts
from ndf.domains.project import analysis_record, check
from ndf.domains.project.items import value_digest
from ndf.domains.records import files

RECORD = {
    "version": analysis_record.RECORD_VERSION,
    "analyzer": check.ANALYZER,
    "inputs": {"pyproject.toml": "3634277f"},
    "branches": {"head": "main", "present": ["main"]},
    "written_digest": "9f2c41aa" * 8,
}
OLD_CI = {
    "provider": "github-actions",
    "workflows": [{"path": ".github/workflows/ci.yml", "jobs": 1, "wall_seconds": 9.0}],
    "required_checks": ["pytest"],
}
OLD_LANGUAGES = [{"name": "python", "frameworks": [], "files": 9, "version": "3.11"}, {"name": "shell", "frameworks": [], "files": 2}]
OLD_DOC = {
    "version": 1,
    "languages": OLD_LANGUAGES,
    "test_duration": {"measured": [{"seconds": 1.0}]},
    "ci": OLD_CI,
    "analysis": {"analyzer": 3, "at": "2026-11-19T01:44:55+00:00", "inputs": {}, "branches": {}, "written": {"languages": "x"}},
}


# ---------------------------------------------------------------- strip_item


def test_strip_item_takes_the_files_out_of_each_language_row():
    got, names = analysis_record.strip_item("languages", OLD_LANGUAGES)
    assert got == [{"name": "python", "frameworks": [], "version": "3.11"}, {"name": "shell", "frameworks": []}]
    assert names == ["languages[].files"]
    assert OLD_LANGUAGES[0]["files"] == 9  # 入力を書き換えない


def test_strip_item_takes_the_workflows_and_the_provider_out_of_ci():
    got, names = analysis_record.strip_item("ci", OLD_CI)
    assert got == {"required_checks": ["pytest"]}
    assert names == ["ci.provider", "ci.workflows"]
    assert "provider" in OLD_CI


@pytest.mark.parametrize(
    "key,value",
    [
        ("languages", [{"name": "python", "frameworks": []}]),
        ("languages", []),
        ("languages", {"unknown": "測れない"}),
        ("ci", {"required_checks": ["pytest"]}),
        ("ci", {"unknown": "時間切れ"}),
        ("ci", "形の違う値"),
        ("checks", {"tools": [], "files": 3}),
    ],
)
def test_strip_item_leaves_values_without_old_keys(key, value):
    assert analysis_record.strip_item(key, value) == (value, [])


def test_strip_item_keeps_an_empty_ci_when_the_old_ci_had_no_required_checks():
    assert analysis_record.strip_item("ci", {"provider": "github-actions", "workflows": []}) == ({}, ["ci.provider", "ci.workflows"])


# ---------------------------------------------------------------- strip_legacy


def test_strip_legacy_takes_the_four_kinds_of_old_keys_out():
    before = copy.deepcopy(OLD_DOC)
    got, names = analysis_record.strip_legacy(OLD_DOC)
    assert names == [
        "languages[].files",
        "test_duration",
        "ci.provider",
        "ci.workflows",
        "analysis.analyzer",
        "analysis.at",
        "analysis.inputs",
        "analysis.branches",
    ]
    assert got == {
        "version": 1,
        "languages": [{"name": "python", "frameworks": [], "version": "3.11"}, {"name": "shell", "frameworks": []}],
        "ci": {"required_checks": ["pytest"]},
        "analysis": {"written": {"languages": "x"}},
    }
    assert OLD_DOC == before


def test_strip_legacy_leaves_a_new_settings_as_it_is():
    new = {"version": 1, "languages": [{"name": "python", "frameworks": []}], "ci": {}, "analysis": {"written": {}}}
    got, names = analysis_record.strip_legacy(new)
    assert names == [] and got == new


def test_strip_legacy_of_an_old_settings_without_written_and_required_checks_fits_the_schema():
    old = {
        "version": 1,
        "ci": {"provider": "github-actions", "workflows": []},
        "analysis": {"analyzer": 3, "at": "x", "inputs": {}, "branches": {}},
    }
    got, _ = analysis_record.strip_legacy(old)
    assert got == {"version": 1, "ci": {}, "analysis": {}}
    assert contracts.schema_errors(got, check.SCHEMA) == []


def test_strip_legacy_leaves_an_analysis_that_is_not_an_object():
    got, names = analysis_record.strip_legacy({"version": 1, "analysis": "壊れた値"})
    assert got == {"version": 1, "analysis": "壊れた値"} and names == []


# ---------------------------------------------------------------- load


def _put(main: Path, text: str | bytes) -> None:
    path = files.path(main, "project-analysis")
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("record", [RECORD, {**RECORD, "pending": True}])
def test_load_returns_a_record_that_fits_the_shape(tmp_path, record):
    _put(tmp_path, json.dumps(record))
    assert analysis_record.load(tmp_path) == (record, None)


def test_load_of_a_missing_record_gives_the_reason(tmp_path):
    assert analysis_record.load(tmp_path) == (None, "解析の記録が無い")


@pytest.mark.parametrize(
    "text,head",
    [
        ("{", "解析の記録が読めない: "),
        ("[]", "解析の記録が読めない: "),
        (b'{"version": "\xff"}', "解析の記録が読めない: UTF-8 として読めない（位置 "),
        (json.dumps({**RECORD, "at": "2026-11-19T01:44:55+00:00"}), "解析の記録が形に合わない: "),
        (json.dumps({k: v for k, v in RECORD.items() if k != "written_digest"}), "解析の記録が形に合わない: "),
        (json.dumps({**RECORD, "pending": False}), "解析の記録が形に合わない: "),
    ],
)
def test_load_of_an_unreadable_record_gives_the_reason(tmp_path, text, head):
    _put(tmp_path, text)
    record, why = analysis_record.load(tmp_path)
    assert record is None and why is not None and why.startswith(head)


# ---------------------------------------------------------------- written_digest


def _doc(written: dict, **rest) -> dict:
    return {"version": 1, **rest, "analysis": {"written": written}}


def test_written_digest_does_not_depend_on_the_key_order():
    a = _doc({"languages": "1", "ci": "2"})
    b = _doc({"ci": "2", "languages": "1"})
    assert analysis_record.written_digest(a) == analysis_record.written_digest(b) == value_digest({"languages": "1", "ci": "2"})


@pytest.mark.parametrize("doc", [None, {"version": 1}, {"version": 1, "analysis": {}}, {"version": 1, "analysis": "x"}])
def test_written_digest_without_written_is_the_one_of_an_empty_object(doc):
    assert analysis_record.written_digest(doc) == value_digest({})


def test_written_digest_ignores_values_outside_written():
    a = _doc({"languages": "1"}, checks={"tools": []})
    b = _doc({"languages": "1"}, checks={"tools": [{"name": "ruff"}]}, branch_strategy={"kind": "trunk"})
    assert analysis_record.written_digest(a) == analysis_record.written_digest(b)


def test_written_digest_changes_with_one_written_value():
    assert analysis_record.written_digest(_doc({"languages": "1"})) != analysis_record.written_digest(_doc({"languages": "2"}))
