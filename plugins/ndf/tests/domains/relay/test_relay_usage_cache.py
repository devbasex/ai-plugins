"""中継のアカウントの使用率の読み取り（取得の記録）と認証の失敗で外した間（#3864 の 3・6、I6・I7・I8）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ndf.domains.relay import accounts
from ndf.domains.relay.account_decide import Usage

NOW = 1_795_000_000.0
LATER = NOW + 3600


def _record(store: Path, name: str, generation: str, windows: dict, **extra) -> Path:
    """取得の記録を書く（`write_fetch` が書く形）。"""
    path = accounts.fetch_path(store, name, generation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": NOW, "fetched_at": NOW, "error": None, "windows": windows, **extra}), encoding="utf-8")
    return path


def test_fetch_paths_stay_inside_the_store(tmp_path):
    assert accounts.fetch_path(tmp_path, "work1", "g1") == tmp_path / ".usage-fetch" / "work1" / "g1.json"
    assert accounts.fetch_path(tmp_path, "work1", "").name == "_.json"
    for name in ("..", "a/../b", "/abs", "a/b"):
        with pytest.raises(accounts.AccountError):
            accounts.fetch_path(tmp_path, name, "g1")
    for generation in ("..", "../x", "/abs", "a/b", ".hidden"):
        with pytest.raises(accounts.AccountError):
            accounts.fetch_path(tmp_path, "work1", generation)
        with pytest.raises(accounts.AccountError):
            accounts.usages(tmp_path, NOW, {"work1": generation})


def test_i6_each_account_reads_its_own_record(tmp_path):
    _record(tmp_path, "work1", "g1", {"five_hour": {"percent": 15.0, "resets_at": LATER}})
    _record(tmp_path, "work2", "g2", {"five_hour": {"percent": 20.0, "resets_at": LATER}})
    got = accounts.usages(tmp_path, NOW, {"work1": "g1", "work2": "g2"})
    assert got == [Usage("work1", 15.0, 15.0, None, None, False), Usage("work2", 20.0, 20.0, None, None, False)]
    assert accounts.windows_of(tmp_path, "work1", "g1") == {"five_hour": (15.0, LATER)}


def test_an_account_seen_at_the_limit_is_limited_until_a_later_fetch(tmp_path):
    # 上限を見た後の取得がまだ成功していなければ、記録の窓が 55% でも上限として読む（同じ読みの fetched_at で決める）
    _record(tmp_path, "team", "g1", {"five_hour": {"percent": 55.0, "resets_at": LATER}})
    gens = {"team": "g1"}
    assert accounts.usage_rows(tmp_path, NOW, gens, {"team": ("g1", NOW + 1)})[0] == [Usage("team", 55.0, 55.0, None, None, True)]
    # 見た時刻より後の取得が届けば、記録だけで決める
    assert accounts.usage_rows(tmp_path, NOW, gens, {"team": ("g1", NOW - 1)})[0] == [Usage("team", 55.0, 55.0, None, None, False)]
    # 違う世代で見た上限は効かせない
    assert accounts.usage_rows(tmp_path, NOW, gens, {"team": ("g0", NOW + 1)})[0] == [Usage("team", 55.0, 55.0, None, None, False)]
    # 記録が無ければ、使用率の分からない上限の行で返す
    assert accounts.usage_rows(tmp_path, NOW, {"work9": "g9"}, {"work9": ("g9", NOW)})[0] == [Usage("work9", None, None, None, None, True)]


def test_3864_3_reset_windows_read_as_zero_and_full_windows_are_limited(tmp_path):
    gens = {"work1": "g1", "work2": "g2", "work3": "g3", "work4": "g4", "work5": "g5"}
    past = {"five_hour": {"percent": 100.0, "resets_at": NOW - 1}, "seven_day": {"percent": 40.0, "resets_at": NOW}}
    _record(tmp_path, "work1", "g1", past)
    full = {"seven_day": {"percent": 100.0, "resets_at": LATER}, "five_hour": {"percent": 20.0, "resets_at": LATER}}
    _record(tmp_path, "work2", "g2", full)
    _record(tmp_path, "work3", "g3", {})  # 窓を持たない記録は未観測
    _record(tmp_path, "work4", "old", {"five_hour": {"percent": 5.0, "resets_at": LATER}})  # 旧い世代は読まない
    scoped = {"claude-opus-4": {"percent": 70.0, "resets_at": LATER}, "x": {"percent": 100.0, "resets_at": NOW - 5}}
    _record(tmp_path, "work5", "g5", {"seven_day": {"percent": 10.0, "resets_at": LATER}, "seven_day_scoped": scoped})
    _record(tmp_path, "other", "g", {"five_hour": {"percent": 1.0, "resets_at": LATER}})  # 登録の無い名前は返さない
    got = accounts.usages(tmp_path, NOW, gens)
    # 窓ごとの値: 過ぎた窓は 0% で 7 日のリセットは分からない。7 日はモデル別のスロットのうち過ぎていないものの最大と比べる
    assert got == [
        Usage("work1", 0.0, 0.0, 0.0, None, False),
        Usage("work2", 100.0, 20.0, 100.0, LATER, True),
        Usage("work5", 70.0, None, 70.0, LATER, False),
    ]


def test_the_last_fetch_decides_without_merging(tmp_path):
    """resets_at が早まった新しい応答でも、最後の取得の値をそのまま読む（合わせ込みで 55% に止まった不具合）。"""
    path = accounts.fetch_path(tmp_path, "team", "g1")
    from ndf.adapters import claude_usage

    accounts.write_fetch(path, claude_usage.Usage(fetched_at=NOW, five_hour=(55.0, LATER)), NOW)
    accounts.write_fetch(path, claude_usage.Usage(fetched_at=NOW + 60, five_hour=(100.0, LATER - 0.6)), NOW + 60)
    assert accounts.usages(tmp_path, NOW + 60, {"team": "g1"}) == [Usage("team", 100.0, 100.0, None, None, True)]


def test_spend_limit_reached_is_limited(tmp_path):
    gens = {"work1": "g1", "work2": "g2", "work3": "g3"}
    _record(tmp_path, "work1", "g1", {"five_hour": {"percent": 30.0, "resets_at": LATER}}, spend_limit_reached=True)
    _record(tmp_path, "work2", "g2", {}, spend_limit_reached=True)  # 窓が無くても使用率の分からない上限の行
    _record(tmp_path, "work3", "g3", {"five_hour": {"percent": 30.0, "resets_at": LATER}}, spend_limit_reached=False)
    rows, spent = accounts.usage_rows(tmp_path, NOW, gens, {})
    assert rows == [
        Usage("work1", 30.0, 30.0, None, None, True),
        Usage("work2", None, None, None, None, True),
        Usage("work3", 30.0, 30.0, None, None, False),
    ]
    assert spent == frozenset({"work1", "work2"})
    # 世代の違う記録の上限の到達は読まない
    assert accounts.usage_rows(tmp_path, NOW, {"work1": "g9"}, {}) == ([], frozenset())


def test_i7_unreadable_records_read_as_unknown(tmp_path):
    path = accounts.fetch_path(tmp_path, "work1", "g1")
    path.parent.mkdir(parents=True)
    for text in ("{", "[]", '{"windows": [1]}', '{"windows": {"five_hour": {"percent": "x", "resets_at": 1}}}'):
        path.write_text(text, encoding="utf-8")
        assert accounts.usages(tmp_path, NOW, {"work1": "g1"}) == []


def test_i8_held_accounts_until_the_end_and_unhold(tmp_path):
    assert accounts.held_path(tmp_path) == tmp_path / "relay" / "accounts" / "held.json"
    assert accounts.held(tmp_path, NOW, {"work1": "g1"}) == {}
    accounts.hold(tmp_path, "work1", "g1", NOW + 3600, NOW)
    accounts.hold(tmp_path, "work2", "g2", NOW + 10, NOW)
    gens = {"work1": "g1", "work2": "g2"}
    assert accounts.held(tmp_path, NOW, gens) == {"work1": NOW + 3600, "work2": NOW + 10}
    assert accounts.held(tmp_path, NOW + 10, gens) == {"work1": NOW + 3600}  # 終わりちょうどで返らない
    assert accounts.held(tmp_path, NOW, {"work1": "g9", "work2": "g2"}) == {"work2": NOW + 10}  # 世代の違う項目は返らない
    accounts.hold(tmp_path, "work3", "g3", NOW + 100, NOW + 20)  # 終わった項目は次の書き込みで消える
    data = json.loads(accounts.held_path(tmp_path).read_text(encoding="utf-8"))
    assert set(data) == {"work1", "work3"}
    accounts.unhold(tmp_path, "work1")
    assert accounts.held(tmp_path, NOW, gens) == {}


def test_i8_hold_keeps_the_new_generation_in_either_order(tmp_path):
    for first, second in (("old", "new"), ("new", "old")):
        accounts.held_path(tmp_path).unlink(missing_ok=True)
        accounts.hold(tmp_path, "work1", first, NOW + 100, NOW)
        accounts.hold(tmp_path, "work1", second, NOW + 100, NOW)
        assert accounts.held(tmp_path, NOW, {"work1": "new"}) == {"work1": NOW + 100}
    accounts.held_path(tmp_path).write_text("{", encoding="utf-8")
    assert accounts.held(tmp_path, NOW, {"work1": "new"}) == {}
    accounts.hold(tmp_path, "work1", "new", NOW + 5, NOW)  # 壊れたファイルへも書ける
    assert accounts.held(tmp_path, NOW, {"work1": "new"}) == {"work1": NOW + 5}


def test_forget_drops_only_the_given_generation(tmp_path):
    store = tmp_path / "store"
    for g in ("old", "new"):
        _record(store, "work1", g, {"five_hour": {"percent": 10.0, "resets_at": LATER}})
        accounts.hold(tmp_path, "work1", g, LATER, NOW)
    accounts.hold(tmp_path, "work2", "g2", LATER, NOW)
    accounts.forget(tmp_path, store, "work1", "old")
    assert sorted(p.name for p in accounts.fetch_path(store, "work1", "new").parent.iterdir()) == ["new.json"]
    assert accounts.held(tmp_path, NOW, {"work1": "old", "work2": "g2"}) == {"work2": LATER}
    assert accounts.held(tmp_path, NOW, {"work1": "new"}) == {"work1": LATER}
    accounts.forget(tmp_path, store, "work1", "new")
    assert not accounts.fetch_path(store, "work1", "new").parent.exists()
    assert accounts.held(tmp_path, NOW, {"work1": "new", "work2": "g2"}) == {"work2": LATER}
