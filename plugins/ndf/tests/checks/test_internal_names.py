"""規則 `internal-names`（名前の対応表に無い別名を落とす）。#2339 の受け入れ条件 2・3。"""

from __future__ import annotations

import json

import pytest

from ndf import contracts
from ndf.checks import internal_names, run
from ndf.checks.model import CheckError, Scope, load_rules
from ndf.contracts import internal

TIME = {"$ref": "common.schema.json#/$defs/time"}


@pytest.fixture
def names(tmp_path, monkeypatch):
    """合成した名前の対応表。"""
    table = {
        "時刻": {"key": "at", "aliases_removed": ["created_at"], "where": ["usage", "loop-event"]},
        "結果": {"key": "status", "aliases_removed": ["outcome"], "where": ["result"]},
        "ループの 1 回の終わり方": {"key": "outcome", "aliases_removed": [], "where": ["loop-event"]},
    }
    monkeypatch.setattr(internal, "names", lambda: table)
    return table


@pytest.fixture
def got(tmp_path, names):
    """合成したスキーマとコードの指摘（ファイル・行・名前）。"""

    def make(schemas: dict[str, dict], code: str = "") -> list[tuple[str, int, str]]:
        for kind, props in schemas.items():
            path = tmp_path / internal_names.SCHEMA_PREFIX / f"{kind}.schema.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"properties": props}, indent=1), encoding="utf-8")
        src = tmp_path / "src" / "ndf" / "w.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(code, encoding="utf-8")
        scope = Scope(tmp_path, tmp_path, "ndf")
        scope.sources = run.collect(tmp_path, tmp_path)
        found = internal_names.check(scope, load_rules())
        assert all(f.rule == "internal-names" for f in found)
        return [(f.path.rsplit("/", 1)[-1], f.line, f.detail.split("使えない名前 ")[1].split("（")[0]) for f in found]

    return make


def test_allowed_gives_the_names_of_each_kind(names):
    assert internal_names.allowed() == {
        "usage": frozenset({"at"}),
        "loop-event": frozenset({"at", "outcome"}),
        "result": frozenset({"status"}),
    }


def test_names_of_the_table_and_own_names_of_the_kind_pass(got):
    assert got({"usage": {"at": TIME, "caller": {"type": "string"}}, "loop-event": {"at": TIME, "outcome": {"type": "string"}}}) == []
    assert got({}) == []


def test_a_removed_alias_in_a_schema_is_found(got):
    assert got({"usage": {"at": TIME, "created_at": TIME}}) == [("usage.schema.json", 6, "created_at")]


def test_a_name_of_another_kind_is_found(got):
    # `outcome` は loop-event では別の意味の名前、usage では「結果」の消した別名
    assert got({"usage": {"outcome": {"type": "string"}}}) == [("usage.schema.json", 3, "outcome")]


@pytest.mark.parametrize("spec", [TIME, {"type": "string", "format": "date-time"}])
def test_a_time_column_outside_the_table_is_found_whatever_its_name(got, spec):
    assert [n for *_, n in got({"usage": {"items": {"properties": {"happened": spec}}}})] == ["happened"]


@pytest.mark.parametrize(
    "call",
    [
        'write_record("usage", row)',
        'internal.write_record("usage", row)',
        'jsonl.append(p, row, kind="usage")',
        'append_locked(p, row, kind="usage")',
    ],
)
def test_a_removed_alias_in_a_function_that_writes_the_kind_is_found(got, call):
    code = f'\n\ndef f(p, now):\n    row = {{"created_at": now}}\n    row["created_at"] = now\n    {call}\n'
    assert [(line, name) for _, line, name in got({}, code)] == [(4, "created_at"), (5, "created_at")]


@pytest.mark.parametrize(
    "code",
    [
        'def f(p, now):\n    write_record("usage", {"at": now, "caller": "c"})\n',
        'def f(now):\n    return {"created_at": now}\n',  # 記録を書かない関数
        'def f(rows, now):\n    rows.append({"created_at": now})\n',  # 並びの append
        'def f(p, kind, now):\n    jsonl.append(p, {"created_at": now}, kind=kind)\n',  # 種類は呼び手が決める
        'def f(p, row):\n    write_record("loop-event", row | {"outcome": "x"})\n',
    ],
)
def test_other_functions_pass(got, code):
    assert got({}, code) == []


def test_an_unreadable_table_stops_the_check(contracts_dir_without_names, tmp_path):
    with pytest.raises(contracts.ContractError):
        internal_names.allowed()
    with pytest.raises(CheckError):
        internal_names.check(Scope(tmp_path, tmp_path, "ndf"), load_rules())


@pytest.fixture
def contracts_dir_without_names(tmp_path):
    contracts.use_dir(tmp_path)
    yield
    contracts.use_dir(None)
