"""中継のアカウントの使用率のキャッシュ・認証の失敗で外した間・上限の観測（#3864 の 1〜3・6・11、I6・I7・I8・I11）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ndf.domains.relay import accounts
from ndf.domains.relay.account_decide import Usage

NOW = 1_795_000_000.0
LATER = NOW + 3600


def _cache(state: Path, name: str, generation: str, windows: dict) -> Path:
    path = accounts.usage_path(state, name, generation)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"windows": windows, "recorded_at": NOW}), encoding="utf-8")
    return path


def _windows(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["windows"]


def test_3864_1_write_usage_keeps_the_newer_value_per_window(tmp_path):
    path = accounts.usage_path(tmp_path, "work1", "g1")
    assert path == tmp_path / "relay" / "accounts" / "usage" / "work1" / "g1.json"
    assert accounts.usage_path(tmp_path, "work1", "") == path.with_name("_.json")
    accounts.write_usage(path, {"five_hour": (100.0, LATER), "seven_day": (30.0, LATER + 86400)}, NOW)
    assert _windows(path) == {
        "five_hour": {"percent": 100.0, "resets_at": LATER},
        "seven_day": {"percent": 30.0, "resets_at": LATER + 86400},
    }
    # 同じ窓の古い入力（同じ resets_at で小さい使用率）・resets_at の古い入力・既に失効した入力では下げない
    accounts.write_usage(path, {"five_hour": (40.0, LATER)}, NOW)
    accounts.write_usage(path, {"five_hour": (10.0, LATER - 60)}, NOW)
    accounts.write_usage(path, {"five_hour": (5.0, NOW - 1)}, NOW)
    assert _windows(path)["five_hour"] == {"percent": 100.0, "resets_at": LATER}
    # 新しい窓は使用率が小さくても採る。入力に無い窓とモデル別の窓は前の値を残す
    scoped = {"claude-opus-4": {"percent": 31.0, "resets_at": LATER}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data["windows"]["seven_day_scoped"] = scoped
    path.write_text(json.dumps(data), encoding="utf-8")
    accounts.write_usage(path, {"five_hour": (3.0, LATER + 18000)}, NOW)
    got = _windows(path)
    assert got["five_hour"] == {"percent": 3.0, "resets_at": LATER + 18000}
    assert got["seven_day"]["percent"] == 30.0 and got["seven_day_scoped"] == scoped
    # 前の窓が失効した後に、それより古い resets_at の入力を書いても前の値のまま
    accounts.write_usage(path, {"five_hour": (50.0, LATER + 100)}, LATER + 20000)
    assert _windows(path)["five_hour"] == {"percent": 3.0, "resets_at": LATER + 18000}


def test_write_usage_skips_while_locked_and_takes_a_stale_lock(tmp_path):
    path = accounts.usage_path(tmp_path, "work1", "g1")
    lock = path.with_name(path.name + ".lock")
    lock.mkdir(parents=True)
    accounts.write_usage(path, {"five_hour": (10.0, LATER)}, NOW)
    assert not path.exists()
    os.utime(lock, (NOW - 11, NOW - 11))
    accounts.write_usage(path, {"five_hour": (10.0, LATER)}, NOW)
    assert _windows(path) == {"five_hour": {"percent": 10.0, "resets_at": LATER}} and not lock.exists()
    before = path.stat().st_mtime_ns
    accounts.write_usage(path, {"five_hour": (10.0, LATER)}, NOW)  # 同じ窓なら書かない
    assert path.stat().st_mtime_ns == before


def test_i6_one_account_record_leaves_the_other_files_alone(tmp_path):
    a = accounts.usage_path(tmp_path, "work1", "g1")
    b = accounts.usage_path(tmp_path, "work2", "g2")
    accounts.write_usage(a, {"five_hour": (10.0, LATER)}, NOW)
    accounts.write_usage(b, {"five_hour": (20.0, LATER)}, NOW)
    accounts.write_usage(a, {"five_hour": (15.0, LATER)}, NOW)
    assert _windows(b) == {"five_hour": {"percent": 20.0, "resets_at": LATER}}
    got = accounts.usages(tmp_path, tmp_path / "store", NOW, {"work1": "g1", "work2": "g2"})
    assert got == [Usage("work1", 15.0, False), Usage("work2", 20.0, False)]


def test_3864_3_reset_windows_read_as_zero_and_not_limited(tmp_path):
    gens = {"work1": "g1", "work2": "g2", "work3": "g3", "work4": "g4", "work5": "g5"}
    _cache(
        tmp_path, "work1", "g1", {"five_hour": {"percent": 100.0, "resets_at": NOW - 1}, "seven_day": {"percent": 40.0, "resets_at": NOW}}
    )
    _cache(
        tmp_path, "work2", "g2", {"seven_day": {"percent": 100.0, "resets_at": LATER}, "five_hour": {"percent": 20.0, "resets_at": LATER}}
    )
    _cache(tmp_path, "work3", "g3", {})  # 窓を持たない記録は未観測
    _cache(tmp_path, "work4", "old", {"five_hour": {"percent": 5.0, "resets_at": LATER}})  # 旧い世代は読まない
    scoped = {"claude-opus-4": {"percent": 70.0, "resets_at": LATER}, "x": {"percent": 100.0, "resets_at": NOW - 5}}
    _cache(tmp_path, "work5", "g5", {"seven_day": {"percent": 10.0, "resets_at": LATER}, "seven_day_scoped": scoped})
    _cache(tmp_path, "other", "g", {"five_hour": {"percent": 1.0, "resets_at": LATER}})  # 登録の無い名前は返さない
    got = accounts.usages(tmp_path, tmp_path / "store", NOW, gens)
    assert got == [Usage("work1", 0.0, False), Usage("work2", 100.0, True), Usage("work5", 70.0, False)]


def test_i7_unreadable_usage_files_read_as_unknown(tmp_path):
    path = accounts.usage_path(tmp_path, "work1", "g1")
    path.parent.mkdir(parents=True)
    for text in ("{", "[]", '{"windows": [1]}', '{"windows": {"five_hour": {"percent": "x", "resets_at": 1}}}'):
        path.write_text(text, encoding="utf-8")
        assert accounts.usages(tmp_path, tmp_path / "store", NOW, {"work1": "g1"}) == []
    # 壊れた記録へも次の書き込みは通る
    accounts.write_usage(path, {"five_hour": (10.0, LATER)}, NOW)
    assert accounts.usages(tmp_path, tmp_path / "store", NOW, {"work1": "g1"}) == [Usage("work1", 10.0, False)]


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


def test_i11_a_seen_limit_holds_until_until_or_a_new_window(tmp_path):
    gens = {"work1": "g1"}
    store = tmp_path / "store"
    assert accounts.limits_path(tmp_path) == tmp_path / "relay" / "accounts" / "limited.json"
    _cache(
        tmp_path,
        "work1",
        "g1",
        {"five_hour": {"percent": 92.0, "resets_at": LATER}, "seven_day": {"percent": 50.0, "resets_at": LATER + 86400}},
    )
    accounts.see_limit(tmp_path, "work1", "g1", NOW, (LATER, "five_hour", LATER))
    assert accounts.usages(tmp_path, store, NOW, gens) == [Usage("work1", 92.0, True)]  # 古い 92% の記録でも上限
    # 同じ窓の値の増加・上限より前の入力では解かない
    accounts.write_usage(accounts.usage_path(tmp_path, "work1", "g1"), {"five_hour": (93.0, LATER)}, NOW)
    accounts.write_usage(accounts.usage_path(tmp_path, "work1", "g1"), {"five_hour": (10.0, LATER - 600)}, NOW)
    assert accounts.usages(tmp_path, store, NOW + 60, gens)[0].limited
    # 観測の窓が新しい窓（resets_at が後で 100 未満）に替われば until の前でも解く
    accounts.write_usage(accounts.usage_path(tmp_path, "work1", "g1"), {"five_hour": (4.0, LATER + 18000)}, NOW)
    assert accounts.usages(tmp_path, store, NOW + 60, gens) == [Usage("work1", 50.0, False)]


def test_i11_estimated_and_weekly_limits_are_not_released_by_another_window(tmp_path):
    store = tmp_path / "store"
    gens = {"work1": "g1", "work2": "g2"}
    _cache(tmp_path, "work1", "g1", {"five_hour": {"percent": 92.0, "resets_at": LATER + 18000}})
    _cache(
        tmp_path,
        "work2",
        "g2",
        {"five_hour": {"percent": 92.0, "resets_at": LATER + 18000}, "seven_day": {"percent": 95.0, "resets_at": LATER + 86400}},
    )
    accounts.see_limit(tmp_path, "work1", "g1", NOW, (LATER, None, None))  # (4) の推定で窓が null
    accounts.see_limit(tmp_path, "work2", "g2", NOW, (LATER + 86400, "seven_day", LATER + 86400))
    windows = {
        "work1": {"five_hour": (92.0, LATER + 18000)},
        "work2": {"five_hour": (5.0, LATER + 36000), "seven_day": (95.0, LATER + 86400)},
    }
    assert set(accounts.limits_seen(tmp_path, NOW, gens, windows)) == {"work1", "work2"}
    assert set(accounts.limits_seen(tmp_path, LATER, gens, windows)) == {"work2"}  # until を過ぎれば解く
    assert accounts.limits_seen(tmp_path, NOW, {"work1": "g9"}, windows) == {}  # 旧い世代の観測は読まない
    # 使用率のファイルが無い名前に観測だけがあると、使用率の分からない上限の行になる
    accounts.see_limit(tmp_path, "work3", "g3", NOW, (LATER, None, None))
    rows = accounts.usages(tmp_path, store, NOW, {"work3": "g3"})
    assert rows == [Usage("work3", None, True)]
    assert accounts.usages(tmp_path, store, LATER, {"work3": "g3"}) == []
    accounts.limits_path(tmp_path).write_text("{", encoding="utf-8")
    assert accounts.limits_seen(tmp_path, NOW, gens, windows) == {}


def test_i11_see_limit_keeps_the_new_generation_in_either_order(tmp_path):
    for first, second in (("old", "new"), ("new", "old")):
        accounts.limits_path(tmp_path).unlink(missing_ok=True)
        accounts.see_limit(tmp_path, "work1", first, NOW, (LATER, None, None))
        accounts.see_limit(tmp_path, "work1", second, NOW, (LATER, None, None))
        assert accounts.limits_seen(tmp_path, NOW, {"work1": "new"}, {}) == {"work1": (NOW, LATER)}


def test_limit_until_follows_the_rules_in_order():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tokyo = ZoneInfo("Asia/Tokyo")
    seen = datetime(2026, 11, 21, 13, 0, tzinfo=tokyo).timestamp()
    three = datetime(2026, 11, 21, 15, 0, tzinfo=tokyo).timestamp()
    five = {"five_hour": (80.0, three), "seven_day": (90.0, seen + 86400)}
    # (1) 行のリセットの時刻。窓は行の語、無ければ同じ時と分の窓
    assert accounts.limit_until("You've hit your session limit · resets 3pm (Asia/Tokyo)", five, seen, timezone.utc) == (
        three,
        "five_hour",
        three,
    )
    assert accounts.limit_until("Limit reached · resets 3pm", five, seen, tokyo) == (three, "five_hour", three)
    assert accounts.limit_until("Limit reached · resets 12:30pm", {}, seen, tokyo)[0] == three - 9000 + 86400  # 見た時刻より後で最も近い
    # (2) 窓の語だけ
    assert accounts.limit_until("weekly limit reached", five, seen, tokyo) == (seen + 86400, "seven_day", seen + 86400)
    # (3) 100 以上の窓のうち遅いほう
    full = {"five_hour": (100.0, three), "seven_day": (100.0, seen + 86400)}
    assert accounts.limit_until("limit reached", full, seen, tokyo) == (seen + 86400, "seven_day", seen + 86400)
    # (4) 推定: 5 時間の窓、無ければ見た時刻 + relay.limit-unknown。窓は null
    assert accounts.limit_until("limit reached", five, seen, tokyo) == (three, None, None)
    assert accounts.limit_until("limit reached · resets soon", {}, seen, tokyo) == (seen + 18000, None, None)


def test_forget_keeps_only_the_current_generation(tmp_path):
    store = tmp_path / "store"
    for g in ("old", "new"):
        accounts.write_usage(accounts.usage_path(tmp_path, "work1", g), {"five_hour": (10.0, LATER)}, NOW)
        accounts.hold(tmp_path, "work1", g, LATER, NOW)
        accounts.see_limit(tmp_path, "work1", g, NOW, (LATER, None, None))
    accounts.hold(tmp_path, "work2", "g2", LATER, NOW)
    accounts.forget(tmp_path, store, "work1", "new")
    assert sorted(p.name for p in accounts.usage_path(tmp_path, "work1", "new").parent.iterdir()) == ["new.json"]
    assert accounts.held(tmp_path, NOW, {"work1": "old", "work2": "g2"}) == {"work2": LATER}
    assert accounts.held(tmp_path, NOW, {"work1": "new"}) == {"work1": LATER}
    assert accounts.limits_seen(tmp_path, NOW, {"work1": "old"}, {}) == {}
    accounts.forget(tmp_path, store, "work1", None)
    assert not accounts.usage_path(tmp_path, "work1", "new").parent.exists()
    assert accounts.held(tmp_path, NOW, {"work1": "new", "work2": "g2"}) == {"work2": LATER}
    assert accounts.limits_seen(tmp_path, NOW, {"work1": "new"}, {}) == {}
