from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf import contracts
from ndf.contracts import facts

REL = "things"


def _split_dir(root: Path, files: dict[str, Any]) -> str:
    """`root/things/` に項目のファイルを書き、読み先を `root` にして `load_split` へ渡す相対パスを返す。

    値が文字列ならそのまま書き、ほかは JSON にして書く。hook 側の読み手へ同じ入力を渡すときもこの関数で作る。
    """
    d = root / REL
    d.mkdir(parents=True)
    for name, body in files.items():
        (d / name).write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False), encoding="utf-8")
    contracts.use_dir(root)
    return REL


def test_reads_each_file_as_an_item_keyed_by_its_name(tmp_path):
    rel = _split_dir(
        tmp_path,
        {
            "b.json": {"seconds": 2},
            "a.json": {"seconds": 1},
            "test-run.test.json": {"seconds": 900},
            "_meta.json": {"owner": "o"},
        },
    )
    loaded = contracts.load_split(rel)
    assert loaded == {"a": {"seconds": 1}, "b": {"seconds": 2}, "test-run.test": {"seconds": 900}}
    assert list(loaded) == ["a", "b", "test-run.test"]


def test_directory_without_items_is_an_empty_table(tmp_path):
    rel = _split_dir(tmp_path, {"_meta.json": {"owner": "o"}})
    assert contracts.load_split(rel) == {}


def test_missing_directory_stops_with_its_path(tmp_path):
    contracts.use_dir(tmp_path)
    with pytest.raises(contracts.ContractError, match=REL):
        contracts.load_split(REL)


@pytest.mark.parametrize(
    ("files", "named"),
    [
        ({"a.json": "{not json"}, "a.json"),
        ({"a.json": {"x": 1}, "b.JSON": {"x": 2}}, "b.JSON"),
        ({"a.json": {"x": 1}, "notes.txt": "memo"}, "notes.txt"),
        ({"a.json": [1, 2]}, "a.json"),
    ],
    ids=["not-json", "upper-case-extension", "other-extension", "not-an-object"],
)
def test_broken_item_files_stop_and_name_the_file_or_key(tmp_path, files, named):
    rel = _split_dir(tmp_path, files)
    with pytest.raises(contracts.ContractError, match=named):
        contracts.load_split(rel)


def test_keys_differing_only_in_case_stop_and_name_both(tmp_path, monkeypatch):
    # 大小文字を区別しないファイルシステムでは `A.json` を書くと `a.json` を上書きするため、
    # 実ファイルは 1 つだけ作り、ディレクトリの一覧に両方の名前を返させて衝突の判定を確かめる
    rel = _split_dir(tmp_path, {"a.json": {"x": 1}})
    items = tmp_path / REL
    original = Path.iterdir

    def iterdir(self: Path):
        if self == items:
            return iter([items / "a.json", items / "A.json"])
        return original(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    with pytest.raises(contracts.ContractError, match="大小文字だけが違うキー") as caught:
        contracts.load_split(rel)
    # パスの並びは OS で違う（Windows は大小文字を区別せずに並べる）ため、挙がったキーは順不同で比べる
    named = str(caught.value).rsplit(": ", 1)[1]
    assert sorted(named.split(", ")) == ["A", "a"]


def test_split_tables_lists_limits_and_causes_with_where_the_key_comes_from():
    tables = contracts.split_tables()
    assert sorted(tables) == ["causes", "limits"]
    assert tables["limits"]["source_key"] == "policies"
    assert tables["causes"]["source_key"] == "causes"
    assert tables["causes"]["key_field"] == "kind"
    assert "key_field" not in tables["limits"]
    # 一覧は事実の表の形で書き、事実の表の読み手でも同じ行を読める
    assert facts.ids("split-tables") == ("limits", "causes")
