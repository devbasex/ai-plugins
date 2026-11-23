"""スキーマから組み立てる定数（#3667）。スキーマだけを書き換え、モジュールを読み直した値が追うことを確かめる。"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

from ndf import contracts
from ndf.adapters import notify
from ndf.contracts import chosen
from ndf.domains.gws import thumbnails
from ndf.domains.materials import collect
from ndf.progress import definition, model


def _reread(module: Any) -> dict[str, Any]:
    """`module` のファイルを、読み込み済みのモジュールを置き換えずに実行し直した最上位の名前。"""
    return runpy.run_path(module.__file__, run_name=module.__name__)


def _node(schema: dict[str, Any], *keys: str) -> dict[str, Any]:
    node = schema
    for key in keys:
        node = node[key]
    return node


ITEM = ("properties", "items", "items", "properties")


@pytest.mark.parametrize(
    ("module", "name", "rel", "keys", "added", "want"),
    [
        (chosen, "CHOOSERS", chosen.SCHEMA, ("properties", "chooser"), "later", ("script", "conductor", "human", "later")),
        (model, "MVV_PACES", definition.CONTEXT, ("$defs", "mvv_pace"), "later", ("fast", "auto", "later")),
        (
            thumbnails,
            "SIZES",
            "schemas/io/gws.result.schema.json",
            ("properties", "metrics", "properties", "size"),
            "HUGE",
            ("SMALL", "MEDIUM", "LARGE", "HUGE"),
        ),
        (
            collect,
            "INTERFACE_VALUES",
            "schemas/io/materials.result.schema.json",
            (*ITEM, "public_interface", "properties", "value"),
            "変わらないと見込む",
            ("変わらないと見込む", "変わらない", "変わる"),
        ),
    ],
    ids=["CHOOSERS", "MVV_PACES", "SIZES", "INTERFACE_VALUES"],
)
def test_a_value_added_to_the_schema_reaches_the_constant(contracts_copy, module, name, rel, keys, added, want):
    def add(schema: dict[str, Any]) -> dict[str, Any]:
        _node(schema, *keys)["enum"].append(added)
        return schema

    contracts_copy(rel, add)
    assert _reread(module)[name] == want


def test_a_kind_without_a_title_is_messaged_by_its_value(contracts_copy, tmp_path):
    def add(schema: dict[str, Any]) -> dict[str, Any]:
        _node(schema, "properties", "kind")["oneOf"] += [{"const": "later"}, {"const": "soon", "title": "まもなく"}]
        return schema

    contracts_copy(notify.NOTICE_SCHEMA, add)
    notice = _reread(notify)["Notice"]

    def message(kind: str) -> str:
        return notice(kind, "k", "本文", tmp_path, 0.0).message("")

    assert [message(k) for k in ("later", "soon", "done")] == ["later: 本文", "まもなく: 本文", "完了: 本文"]


@pytest.mark.parametrize(("pace", "ok"), [("normal", True), ("fast", True), ("auto", True), ("slow", False)])
def test_the_context_accepts_the_paces_of_both_groups(pace, ok):
    assert (contracts.schema_errors({"pace": pace}, definition.CONTEXT) == []) is ok


def test_a_schema_is_parsed_once_for_the_constant_and_the_validator(contracts_copy, monkeypatch):
    read: list[Path] = []
    load = contracts.read_text_json
    monkeypatch.setattr(contracts, "read_text_json", lambda path: read.append(path) or load(path))
    contracts.use_dir(contracts_copy.root)
    contracts.schema_enum(chosen.SCHEMA, "/properties/chooser")
    contracts.schema_errors({}, chosen.SCHEMA)
    assert read == [contracts_copy.root / chosen.SCHEMA]


@pytest.mark.parametrize("pointer", ["/properties/none", "/properties/event", "/title/x"])
def test_a_pointer_without_an_enum_is_refused(pointer):
    with pytest.raises(contracts.ContractError, match="enum が無い"):
        contracts.schema_enum(chosen.SCHEMA, pointer)
