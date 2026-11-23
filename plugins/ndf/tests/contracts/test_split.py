from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, NamedTuple

import pytest

from ndf import contracts
from ndf.contracts import causes, facts, limits
from ndf.domains.hooks import tables

REL = "things"


class Reader(NamedTuple):
    """分けた表の読み手 1 つ（CLI 側は `ndf.contracts`、hook 側は標準ライブラリだけの `hooks.tables`）と、その誤り。"""

    load_split: Callable[[str], dict[str, Any]]
    error: type[Exception]


@pytest.fixture(params=["cli", "hook"])
def reader(request, tmp_path, monkeypatch):
    """同じ入力を 2 つの読み手へ渡す。どちらも `tmp_path` を読む。"""
    contracts.use_dir(tmp_path)
    monkeypatch.setattr(tables, "CONTRACTS", tmp_path)
    tables.load.cache_clear()
    yield Reader(contracts.load_split, contracts.ContractError) if request.param == "cli" else Reader(tables.load_split, tables.TableError)
    tables.load.cache_clear()


def _split_dir(root: Path, files: dict[str, Any]) -> str:
    """`root/things/` に項目のファイルを書き、`load_split` へ渡す相対パスを返す。値が文字列ならそのまま書き、ほかは JSON にして書く。"""
    d = root / REL
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return REL


def test_reads_each_file_as_an_item_keyed_by_its_name(reader, tmp_path):
    rel = _split_dir(
        tmp_path,
        {
            "b.json": {"seconds": 2},
            "a.json": {"seconds": 1},
            "test-run.test.json": {"seconds": 900},
            "_meta.json": {"owner": "o"},
        },
    )
    loaded = reader.load_split(rel)
    assert loaded == {"a": {"seconds": 1}, "b": {"seconds": 2}, "test-run.test": {"seconds": 900}}
    assert list(loaded) == ["a", "b", "test-run.test"]


def test_directory_without_items_is_an_empty_table(reader, tmp_path):
    rel = _split_dir(tmp_path, {"_meta.json": {"owner": "o"}})
    assert reader.load_split(rel) == {}


def test_missing_directory_stops_with_its_path(reader):
    with pytest.raises(reader.error, match=REL):
        reader.load_split(REL)


@pytest.mark.parametrize(
    ("files", "named"),
    [
        ({"a.json": "{not json"}, "a.json"),
        ({"a.json": {"x": 1}, "b.JSON": {"x": 2}}, "b.JSON"),
        ({"a.json": {"x": 1}, "notes.txt": "memo"}, "notes.txt"),
        ({"a.json": [1, 2]}, "a.json"),
        ({"a.json": '{"x": 1, "x": 2}'}, "a.json"),
    ],
    ids=["not-json", "upper-case-extension", "other-extension", "not-an-object", "same-key-twice"],
)
def test_broken_item_files_stop_and_name_the_file_or_key(reader, tmp_path, files, named):
    rel = _split_dir(tmp_path, files)
    with pytest.raises(reader.error, match=named):
        reader.load_split(rel)


def test_keys_differing_only_in_case_stop_and_name_both(reader, tmp_path, monkeypatch):
    # 大小文字を区別しないファイルシステムでは `A.json` を書くと `a.json` を上書きするため、
    # 実ファイルは 1 つだけ作り、ディレクトリの一覧に両方の名前を返させて衝突の判定を確かめる
    rel = _split_dir(tmp_path, {"a.json": {"x": 1}})
    items = tmp_path / REL
    original = os.listdir
    monkeypatch.setattr(os, "listdir", lambda p=".": ["a.json", "A.json"] if Path(p) == items else original(p))
    monkeypatch.setattr(Path, "iterdir", lambda self: (self / n for n in os.listdir(self)))
    with pytest.raises(reader.error, match="大小文字だけが違うキー") as caught:
        reader.load_split(rel)
    # パスの並びは OS で違う（Windows は大小文字を区別せずに並べる）ため、挙がったキーは順不同で比べる
    named = str(caught.value).rsplit(": ", 1)[1]
    assert sorted(named.split(", ")) == ["A", "a"]


@pytest.mark.parametrize(
    ("read", "files"),
    [
        (lambda: limits.value("cause.infra-wait", "attempts"), ["limits/cause.infra-wait.json"]),
        (lambda: causes.exit_code("unknown"), ["causes/unknown.json"]),
        (lambda: causes.policy_of("ci_pending"), ["causes/ci_pending.json", "limits/cause.ci-pending.json"]),
        (lambda: causes.errors({"kind": "no_such", "detail": "x"}), []),
    ],
    ids=["limits.value", "causes.exit_code", "causes.policy_of", "causes.errors"],
)
def test_reading_one_item_reads_only_its_files(monkeypatch, read, files):
    # 1 項目の参照で表の全件を読むと、CLI の起動ごとの読みが項目の数だけ増える（PR 4335 の r1-4）
    contracts.use_dir(None)
    seen = []
    original = contracts.load_json
    monkeypatch.setattr(contracts, "load_json", lambda rel: seen.append(rel) or original(rel))
    read()
    assert sorted(r for r in seen if r.startswith(("limits/", "causes/"))) == files


def test_reading_whole_tables_reads_each_file_once(monkeypatch):
    # 停止理由の全件の検査は、上限の全件で確かめた方針を読み直さない（PR 4335 の r2-1）
    contracts.use_dir(None)
    seen = []
    original = contracts.load_json
    monkeypatch.setattr(contracts, "load_json", lambda rel: seen.append(rel) or original(rel))
    limits.table()
    causes.table()
    assert contracts.duplicates(r for r in seen if r.startswith(("limits/", "causes/"))) == []


def test_split_tables_lists_limits_and_causes_with_where_the_key_comes_from():
    tables = contracts.split_tables()
    assert sorted(tables) == ["causes", "limits"]
    assert tables["limits"]["source_key"] == "policies"
    assert tables["causes"]["source_key"] == "causes"
    assert tables["causes"]["key_field"] == "kind"
    assert "key_field" not in tables["limits"]
    # 一覧は事実の表の形で書き、事実の表の読み手でも同じ行を読める
    assert facts.ids("split-tables") == ("limits", "causes")
