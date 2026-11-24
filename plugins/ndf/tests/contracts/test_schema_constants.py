"""スキーマから組み立てる定数（#3667）。スキーマだけを書き換え、モジュールを読み直した値が追うことを確かめる。"""

from __future__ import annotations

import json
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ndf import contracts
from ndf.adapters import notify
from ndf.contracts import chosen
from ndf.domains.gws import thumbnails
from ndf.domains.materials import collect
from ndf.progress import definition, model


def _only(root: Path, rel: str, change: Callable[[dict[str, Any]], None]) -> None:
    """書き換えたスキーマ `rel` の 1 ファイルだけを `root` へ置き、コントラクトの読み先にする。"""
    schema = contracts.load_json(rel)
    change(schema)
    path = root / rel
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    contracts.use_dir(root)


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
def test_a_value_added_to_the_schema_reaches_the_constant(tmp_path, module, name, rel, keys, added, want):
    _only(tmp_path, rel, lambda schema: _node(schema, *keys)["enum"].append(added))
    assert _reread(module)[name] == want


def test_a_kind_without_a_title_is_messaged_by_its_value(tmp_path):
    added = [{"const": "later"}, {"const": "soon", "title": "まもなく"}]
    _only(tmp_path, notify.NOTICE_SCHEMA, lambda schema: _node(schema, "properties", "kind")["oneOf"].extend(added))
    notice = _reread(notify)["Notice"]

    def message(kind: str) -> str:
        return notice(kind, "k", "本文", tmp_path, 0.0).message("")

    assert [message(k) for k in ("later", "soon", "done")] == ["later: 本文", "まもなく: 本文", "完了: 本文"]


@pytest.mark.parametrize(("pace", "ok"), [("normal", True), ("fast", True), ("auto", True), ("slow", False)])
def test_the_context_accepts_the_paces_of_both_groups(pace, ok):
    assert (contracts.schema_errors({"pace": pace}, definition.CONTEXT) == []) is ok


def test_a_schema_is_parsed_once_for_the_constant_and_the_validator(monkeypatch):
    read: list[Path] = []
    load = contracts.read_json
    monkeypatch.setattr(contracts, "read_json", lambda path: read.append(path) or load(path))
    contracts.use_dir(None)  # 読み手のキャッシュを捨てる
    contracts.schema_enum(chosen.SCHEMA, "/properties/chooser")
    contracts.schema_errors({}, chosen.SCHEMA)
    assert read == [contracts.contracts_dir() / chosen.SCHEMA]


def test_the_context_schema_is_parsed_once_for_the_paces_and_the_keys(monkeypatch):
    read: list[Path] = []
    load = contracts.read_text_json
    monkeypatch.setattr(contracts, "read_text_json", lambda path: read.append(path) or load(path))
    contracts.use_dir(None)  # 読み手のキャッシュを捨てる
    _reread(model)
    definition.context_source("pace")
    definition.context_source("pace")
    assert read == [contracts.contracts_dir() / definition.CONTEXT]


@pytest.mark.parametrize("pointer", ["/properties/none", "/properties/event", "/title/x"])
def test_a_pointer_without_an_enum_is_refused(pointer):
    with pytest.raises(contracts.ContractError, match="enum が無い"):
        contracts.schema_enum(chosen.SCHEMA, pointer)
