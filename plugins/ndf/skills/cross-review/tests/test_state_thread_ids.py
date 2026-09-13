"""fix の戻り値を Resolve 申告のスレッド識別子へ正規化する分岐（`_thread_ids`）。

`_count` / `_normalized_body` は直接固定されているが、`_thread_ids` は `cmd_merge_fix`
経由でしか通っていない。dict の list / 単一 dict / 件数(int) / None / `thread_id` を持た
ない dict のどれで来ても識別子の一覧を返す現状の振る舞いを記録する（現状固定テスト。
正しさを主張しない）。
"""
from __future__ import annotations


def test_a_list_of_dicts_returns_ids_in_order(state_mod):
    """dict の list（thread_id あり）は、識別子の一覧を順序どおり返す。"""
    value = [{"thread_id": "T1"}, {"thread_id": "T2"}, {"thread_id": "T3"}]

    assert state_mod._thread_ids(value) == ["T1", "T2", "T3"]


def test_a_single_dict_returns_one_id(state_mod):
    """単一 dict は 1 要素の一覧になる。"""
    assert state_mod._thread_ids({"thread_id": "T9"}) == ["T9"]


def test_a_count_int_returns_an_empty_list(state_mod):
    """件数(int) は識別子を取り出せず、空の一覧になる。"""
    assert state_mod._thread_ids(3) == []


def test_none_returns_an_empty_list(state_mod):
    """None は空の一覧になる。"""
    assert state_mod._thread_ids(None) == []


def test_a_dict_without_thread_id_is_dropped(state_mod):
    """thread_id を持たない dict は、その要素だけ除かれる。"""
    value = [{"thread_id": "T1"}, {"other": "x"}, {"thread_id": "T2"}]

    assert state_mod._thread_ids(value) == ["T1", "T2"]


def test_ids_are_coerced_to_strings(state_mod):
    """thread_id は文字列へ揃える（数値で来ても文字列になる）。"""
    assert state_mod._thread_ids([{"thread_id": 123}]) == ["123"]
