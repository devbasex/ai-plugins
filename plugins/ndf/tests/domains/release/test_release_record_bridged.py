"""`record_read._bridged` の広げ方（I-005 の逐次更新の前に固定する）。

起点の書き手を照会し、信用した記録と値かコミットを共有する記録へだけ広げる。広げる先の照会は、
起点 → その次に共有する記録 → … の波ごとに、波の中は起点の順・記録の古い順で行い、同じ記録を 2 度照会しない。
信用しない書き手の記録は残さず、その値やコミットで広げもしない。結果は古い順（`at` の順）。
"""

from __future__ import annotations

import pytest

from ndf.adapters.github import Authored, PermissionUnknown
from ndf.domains.release import record_read

from ._fakes import FakeHub
from .test_release_record import _unversioned

C1, C2, C3, C9 = ("1" * 40), ("2" * 40), ("3" * 40), ("9" * 40)


def _found(at: int, writer: str, version: str, commit: str) -> record_read._Found:
    return record_read._Found(at, Authored(writer, "MEMBER", ""), _unversioned(version=version, commit=commit))


def _hub(trusted: tuple[str, ...] = ("octocat",), unknown: tuple[str, ...] = ()) -> FakeHub:
    return FakeHub(trusted=set(trusted), unknown=set(unknown))


def _ats(got: list[record_read._Found]) -> list[int]:
    return [f.at for f in got]


def test_bridged_follows_values_and_commits_transitively_in_waves() -> None:
    # S(v1,c1) → 値 v1 で A(v1,c2)、コミット c1 で C(v3,c1) → コミット c2 で B(v2,c2) → 値 v2 で D(v2,c3)
    s, a, b, c, d = (
        _found(0, "s", "v1", C1),
        _found(1, "a", "v1", C2),
        _found(2, "b", "v2", C2),
        _found(3, "c", "v3", C1),
        _found(4, "d", "v2", C3),
    )
    other = _found(5, "other", "v9", C9)  # どれとも共有しない
    hub = _hub(trusted=("s", "a", "b", "c", "d", "other"))
    got = record_read._bridged(hub, [s, a, b, c, d, other], [s])
    assert _ats(got) == [0, 1, 2, 3, 4]
    # 波ごと（S → A・C → B → D）に照会し、共有しない記録は照会しない
    assert hub.asked == ["s", "a", "c", "b", "d"]


def test_bridged_does_not_spread_through_untrusted_records() -> None:
    s = _found(0, "octocat", "v1", C1)
    untrusted = _found(1, "reader", "v1", C2)  # 値を共有するが信用しない
    behind = _found(2, "octocat", "v2", C2)  # 信用しない記録とだけコミットを共有する
    hub = _hub()
    got = record_read._bridged(hub, [s, untrusted, behind], [s])
    assert _ats(got) == [0] and hub.asked == ["octocat", "reader"]


def test_bridged_untrusted_seed_is_asked_but_neither_kept_nor_spread() -> None:
    seed = _found(0, "reader", "v1", C1)
    same_value = _found(1, "octocat", "v1", C2)
    hub = _hub()
    assert record_read._bridged(hub, [seed, same_value], [seed]) == [] and hub.asked == ["reader"]


def test_bridged_asks_each_record_once_and_returns_the_oldest_first() -> None:
    # 起点を新しい順に渡しても結果は古い順。起点どうしが値を共有しても照会し直さない
    old, mid, new = _found(0, "old", "v1", C1), _found(1, "mid", "v1", C2), _found(2, "new", "v2", C2)
    hub = _hub(trusted=("old", "mid", "new"))
    got = record_read._bridged(hub, [old, mid, new], [new, old])
    assert _ats(got) == [0, 1, 2]
    # 起点は渡した順（new → old）、次の波は古い順（mid）
    assert hub.asked == ["new", "old", "mid"]


def test_bridged_asks_seeds_in_the_given_order() -> None:
    first, second = _found(0, "a", "v1", C1), _found(1, "b", "v2", C2)
    hub = _hub(trusted=("a", "b"))
    record_read._bridged(hub, [first, second], [second, first])
    assert hub.asked == ["b", "a"]


def test_bridged_without_seeds_asks_nobody() -> None:
    hub = _hub()
    assert record_read._bridged(hub, [_found(0, "octocat", "v1", C1)], []) == [] and hub.asked == []


def test_bridged_seeds_need_not_be_among_the_unversioned_records() -> None:
    # 起点は広げる候補の外でもよい（照会して残す）。広げる先は候補からだけ選ぶ
    seed = _found(7, "octocat", "v1", C1)
    linked = _found(1, "octocat", "v1", C2)
    hub = _hub()
    assert _ats(record_read._bridged(hub, [linked], [seed])) == [1, 7]


def test_bridged_stops_on_an_unknown_writer_reached_by_sharing() -> None:
    s = _found(0, "octocat", "v1", C1)
    ghost = _found(1, "ghost", "v2", C1)  # コミットを共有するので照会される
    hub = _hub(unknown=("ghost",))
    with pytest.raises(PermissionUnknown):
        record_read._bridged(hub, [s, ghost], [s])
    assert hub.asked == ["octocat", "ghost"]


def test_bridged_does_not_ask_an_unknown_writer_out_of_reach() -> None:
    s = _found(0, "octocat", "v1", C1)
    ghost = _found(1, "ghost", "v2", C2)
    hub = _hub(unknown=("ghost",))
    assert _ats(record_read._bridged(hub, [s, ghost], [s])) == [0] and hub.asked == ["octocat"]
