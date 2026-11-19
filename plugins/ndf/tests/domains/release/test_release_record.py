"""リリース記録を含めた PR の各々へ書き、リリース後テストの記録を同じ PR へ書く（#3487・#2783・#3128）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters.github import Authored
from ndf.domains.release import cli as release_cli
from ndf.domains.release import names_decide, record, record_decide

from ._fakes import FakeHub, pull, strategy

RELEASE = 50
VERSION = "1.2.4"


def _commit(sha: str, message: str, *parents: str) -> dict:
    return {"sha": sha, "commit": {"message": message}, "parents": [{"sha": p} for p in parents]}


# リリースの PR のコミット（HEAD から first-parent: 版上げ → #12 → #11 → 本番から戻す PR #9 → PR の外）
COMMITS = [
    _commit("r" * 40, "Release: ndf v1.2.4\n\n本文", "d2" * 20),
    _commit("d2" * 20, "Merge pull request #12 from o/feat/b", "d1" * 20, "x" * 40),
    _commit("x" * 40, "wip (#99)", "d0" * 20),
    _commit("d1" * 20, "Merge pull request #11 from o/feat/a", "s" * 40, "y" * 40),
    _commit("s" * 40, "Merge pull request #9 from o/main", "outside", "m" * 40),
]


# リリースの PR を本番ブランチへ入れたマージコミット
MERGE = "e" * 40


def _hub() -> FakeHub:
    released = pull(RELEASE, head="r" * 40, base="main", ref=f"release/v{VERSION}", merged=True)._replace(merge_sha=MERGE)
    return FakeHub(pulls={RELEASE: released}, commits_of={"r" * 40: list(COMMITS)})


def _written(hub: FakeHub, number: int) -> list[record_decide.Record]:
    return record_decide.records(c.body for c in hub.comments(number))


def test_issue_2783_records_go_to_each_included_pull() -> None:
    hub = _hub()
    made, written = record.write(hub, RELEASE, "main")
    assert made == record_decide.Record(VERSION, RELEASE, (11, 12))
    assert [(w.number, w.result) for w in written] == [(11, record.WRITTEN), (12, record.WRITTEN)]
    for n in (11, 12):
        assert _written(hub, n) == [made]
    # 本番から戻す PR・枝の中のコミット・リリースの PR には書かない
    assert set(hub.comments_of) == {11, 12}


def test_records_follow_the_pulls_the_release_commit_carries() -> None:
    """組み立てがリリースのコミットへ書いた承認資料の「含む PR」の行があれば、その番号（PR のコミットの外の PR も）へ書く。"""
    hub = _hub()
    trailer = record_decide.included_trailer([12, 3, 11])
    hub.commits_of["r" * 40] = [_commit("r" * 40, f"Release: ndf v1.2.4\n\n{trailer}\n", "d2" * 20), *COMMITS[1:]]
    made, _ = record.write(hub, RELEASE, "main")
    assert trailer == "Ndf-Included-Pulls: #3 #11 #12" and made.included == (3, 11, 12)
    assert record_decide.included_from_message(record_decide.included_trailer([])) == []
    assert record_decide.included_from_message("Ndf-Included-Pulls: 3 x") is None


def test_records_read_the_trailer_below_fix_commits() -> None:
    """組み立ての後にリリースブランチへ直しのコミットを積んでも、first-parent で辿った最も新しい「含む PR」の行から読む。"""
    hub = _hub()
    trailer = record_decide.included_trailer([3, 11])
    hub.commits_of["r" * 40] = [
        _commit("r" * 40, "fix: 直し", "q" * 40),
        _commit("q" * 40, f"Release: ndf v1.2.4\n\n{trailer}\n", "d2" * 20),
        *COMMITS[1:],
    ]
    made, _ = record.write(hub, RELEASE, "main")
    assert made.included == (3, 11)


def _closed_by_squash(message: str, status: str = "behind", tree: str = "t1") -> FakeHub:
    """`strict` の squash で本番へ入れて閉じたリリースの PR（マージ済みでない）と、本番へ入れたコミットのメッセージ・木と、
    本番ブランチとの compare の状態（`behind` は本番の履歴にある）。"""
    hub = _hub()
    hub.pulls[RELEASE] = hub.pulls[RELEASE]._replace(state="closed", merged=False, merge_sha="")
    hub.responses[("GET", f"/repos/o/r/git/commits/{MERGE}")] = {"message": message, "tree": {"sha": tree}}
    hub.responses[("GET", f"/repos/o/r/git/commits/{'r' * 40}")] = {"message": "Release", "tree": {"sha": "t1"}}
    hub.responses[("GET", f"/repos/o/r/compare/main...{MERGE}")] = {"status": status}
    return hub


def test_records_follow_a_release_landed_by_strict_squash() -> None:
    """squash で閉じた PR も、本番へ入れたコミットの trailer がその PR と承認済みの head を指せば記録を書く。"""
    hub = _closed_by_squash(f"Land #{RELEASE}\n\nNDF-Landed-PR: {RELEASE}\nNDF-Approved-Head: {'r' * 40}\nNDF-Tip: {'t' * 40}\n")
    made, written = record.write(hub, RELEASE, "main", production_sha=MERGE)
    assert made == record_decide.Record(VERSION, RELEASE, (11, 12)) and [w.number for w in written] == [11, 12]
    with pytest.raises(record.NotReleased):
        record.write(hub, RELEASE, "main")
    other = _closed_by_squash(f"Land #{RELEASE}\n\nNDF-Landed-PR: {RELEASE}\nNDF-Approved-Head: {'z' * 40}\n")
    with pytest.raises(record.NotReleased):
        record.write(other, RELEASE, "main", production_sha=MERGE)


def test_issue_3615_squash_records_do_not_wait_for_the_pull_to_close() -> None:
    """`land` が本番ブランチを進めてから PR を閉じるまでの間に読んでも、正規の squash なら記録を書く（公開を取りこぼさない）。"""
    hub = _closed_by_squash(f"Land #{RELEASE}\n\nNDF-Landed-PR: {RELEASE}\nNDF-Approved-Head: {'r' * 40}\n")
    hub.pulls[RELEASE] = hub.pulls[RELEASE]._replace(state="open")
    _made, written = record.write(hub, RELEASE, "main", production_sha=MERGE)
    assert [w.number for w in written] == [11, 12]


@pytest.mark.parametrize(("status", "tree"), [("diverged", "t1"), ("ahead", "t1"), ("behind", "t2")])
def test_squash_records_need_a_canonical_commit_on_the_production_branch(status: str, tree: str) -> None:
    """trailer が合っても、本番ブランチの履歴に無いコミット・承認済みの head と木の違うコミットでは記録を書かない。"""
    message = f"Land #{RELEASE}\n\nNDF-Landed-PR: {RELEASE}\nNDF-Approved-Head: {'r' * 40}\n"
    hub = _closed_by_squash(message, status, tree)
    with pytest.raises(record.NotReleased):
        record.write(hub, RELEASE, "main", production_sha=MERGE)
    assert hub.comments_of == {}


def test_records_carry_each_units_version_to_its_own_pulls() -> None:
    """単位が 2 つで別のバージョンへ上げたら、PR へはその単位のバージョンの記録を書く（ブランチ名の単位のバージョンで書かない）。"""
    hub = _hub()
    lines = "\n".join(
        [
            record_decide.included_trailer([11, 12]),
            record_decide.unit_trailer("a", "1.2.4", [11]),
            record_decide.unit_trailer("b", "2.0.1", [12]),
        ]
    )
    hub.commits_of["r" * 40] = [_commit("r" * 40, f"Release: a v1.2.4\n\n{lines}\n", "d2" * 20), *COMMITS[1:]]
    made, written = record.write(hub, RELEASE, "main")
    assert made.included == (11, 12) and [w.number for w in written] == [11, 12]
    assert [r.version for r in _written(hub, 11)] == ["1.2.4"] and [r.version for r in _written(hub, 12)] == ["2.0.1"]


def test_issue_3615_two_units_on_the_same_version_each_record_the_shared_pull() -> None:
    """2 つの単位を同じバージョンへ上げて同じ PR を含めても、単位ごとに記録を書く（別の単位の記録を記録済みと読まない）。"""
    hub = _hub()
    lines = "\n".join(
        [
            record_decide.included_trailer([11]),
            record_decide.unit_trailer("a", "1.2.4", [11]),
            record_decide.unit_trailer("b", "1.2.4", [11]),
        ]
    )
    hub.commits_of["r" * 40] = [_commit("r" * 40, f"Release: a v1.2.4\n\n{lines}\n", "d2" * 20), *COMMITS[1:]]
    _made, written = record.write(hub, RELEASE, "main")
    assert [(w.number, w.result) for w in written] == [(11, record.WRITTEN), (11, record.WRITTEN)]
    assert [r.unit for r in _written(hub, 11)] == ["a", "b"]


def test_rerun_on_same_version_does_not_stack_records() -> None:
    hub = _hub()
    record.write(hub, RELEASE, "main")
    _made, again = record.write(hub, RELEASE, "main")
    assert [w.result for w in again] == [record.KEPT, record.KEPT]
    assert [len(_written(hub, n)) for n in (11, 12)] == [1, 1]


def test_dev_or_unmerged_release_writes_nothing() -> None:
    hub = _hub()
    hub.pulls[RELEASE] = pull(RELEASE, base="develop", ref=f"release/v{VERSION}-dev.1", merged=True)
    with pytest.raises(record.NotReleased):
        record.write(hub, RELEASE, "main")
    hub.pulls[RELEASE] = pull(RELEASE, base="main", ref=f"release/v{VERSION}", merged=False)
    with pytest.raises(record.NotReleased):
        record.write(hub, RELEASE, "main")
    assert hub.comments_of == {}


def test_range_is_read_from_the_merge_parent_beyond_250_commits() -> None:
    # pulls/<番号>/commits の 250 件で切れず、マージ前の本番ブランチから HEAD までを読み切る
    hub = _hub()
    filler = [_commit(f"{i:040d}", "c", f"{i + 1:040d}") for i in range(300)]
    hub.commits_of["r" * 40] = [*COMMITS[:-1], _commit("s" * 40, "Merge pull request #9 from o/main", f"{0:040d}", "m" * 40), *filler]
    made, _ = record.write(hub, RELEASE, "main")
    assert made.included == (11, 12)
    assert hub.compared == [(f"parent-of-{MERGE}", "r" * 40)]


def test_release_without_merge_commit_is_refused() -> None:
    hub = _hub()
    hub.pulls[RELEASE] = hub.pulls[RELEASE]._replace(merge_sha="")
    with pytest.raises(record.RecordError):
        record.write(hub, RELEASE, "main")


def _verification(version: str = VERSION) -> dict:
    return {"version": version, "issues": [{"number": 3487, "result": "passed", "evidence": "記録が 2 本に書かれた"}]}


def test_issue_3128_verification_goes_to_the_record_pull_and_both_are_read() -> None:
    hub = _hub()
    made, _ = record.write(hub, RELEASE, "main")
    record.write_verification(hub, 11, _verification())
    got = record.read(hub, 11)
    assert got.release == made
    assert got.verifications == (_verification(),)
    # リリースの PR（main 宛て）はリリース記録を持たないので書かない
    with pytest.raises(record.RecordError):
        record.write_verification(hub, RELEASE, _verification())
    # 別のバージョンの記録も書かない・形の違う記録も書かない
    with pytest.raises(record.RecordError):
        record.write_verification(hub, 11, _verification("9.9.9"))
    with pytest.raises(record.RecordError):
        record.write_verification(hub, 11, {"version": VERSION, "issues": []})
    assert len(record.read(hub, 11).verifications) == 1


def test_untrusted_token_writes_no_record() -> None:
    # Actions の github.token（github-actions[bot]）で書いた記録は読まれず、打ち直しで重なるので書かずに止まる
    hub = _hub()
    hub.trusted = set()
    with pytest.raises(record.RecordError):
        record.write(hub, RELEASE, "main")
    assert hub.comments_of == {}


def test_read_skips_untrusted_writers() -> None:
    hub = _hub()
    fake = record_decide.render_record(record_decide.Record(VERSION, RELEASE, (11,)))
    hub.comments_of[11] = [Authored("stranger", "NONE", fake)]
    assert record.read(hub, 11).release is None
    with pytest.raises(record.RecordError):
        record.write_verification(hub, 11, _verification())


def test_body_without_json_block_is_not_a_record() -> None:
    assert record_decide.parse_record(f"{record_decide.RECORD_MARK}\n対象のバージョン: {VERSION}\n") is None
    assert record_decide.parse_record('```json\n{"version": "1.2.4", "release": 1, "included": []}\n```') is None


# マーカーとコードブロックを持つが JSON として読めないリリース記録
BROKEN = f'{record_decide.RECORD_MARK}\n## リリース記録\n\n```json\n{{"version": \n```\n'


def test_marked_body_with_broken_json_is_reported_not_skipped() -> None:
    with pytest.raises(record_decide.UnreadableRecord):
        record_decide.parse_record(BROKEN)


def test_broken_record_by_untrusted_writer_is_skipped_and_record_is_written() -> None:
    hub = _hub()
    hub.comments_of[11] = [Authored("stranger", "NONE", BROKEN)]
    made, written = record.write(hub, RELEASE, "main")
    assert [(w.number, w.result) for w in written] == [(11, record.WRITTEN), (12, record.WRITTEN)]
    trusted = [c.body for c in hub.comments(11) if hub.trusts(c)]
    assert record_decide.records(trusted) == [made]
    assert record.read(hub, 11).release == made


def test_broken_record_by_trusted_writer_stops_as_invalid() -> None:
    hub = _hub()
    hub.comments_of[11] = [Authored("octocat", "OWNER", BROKEN)]
    with pytest.raises(record_decide.UnreadableRecord) as caught:
        record.write(hub, RELEASE, "main")
    assert release_cli._stopped_of(caught.value)["cause"]["kind"] == "invalid"


def _names() -> tuple[str, names_decide.NameFormats]:
    """ブランチ戦略の設定から読んだ本番ブランチと書式の代わり。"""
    return "main", names_decide.NameFormats(None)


def _unread_names() -> tuple[str, names_decide.NameFormats]:
    pytest.fail("記録を持つ PR へ書く経路が設定を読んだ")


def _included_hub(included: list[int], holders: list[int]) -> FakeHub:
    """含めた PR が `included` のリリースの PR と、`holders` だけが同じバージョンのリリース記録を持つ偽の GitHub。"""
    hub = _hub()
    hub.commits_of["r" * 40] = [
        _commit("r" * 40, f"Release: ndf v1.2.4\n\n{record_decide.included_trailer(included)}\n", "d2" * 20),
        *COMMITS[1:],
    ]
    body = record_decide.render_record(record_decide.Record(VERSION, RELEASE, tuple(sorted(included))))
    for n in holders:
        hub.comments_of[n] = [Authored("octocat", "OWNER", body)]
    return hub


def test_issue_3852_release_pull_points_to_the_record_holders_and_writes_nothing() -> None:
    """リリースの PR を渡すと、含めた PR のうち記録を持つ PR だけを小さい順に返して止まり、何も書かない。"""
    hub = _included_hub([12, 3, 11], [12, 11])
    before = {n: list(c) for n, c in hub.comments_of.items()}
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(hub, RELEASE, _verification(), _names)
    assert caught.value.holders == (11, 12)
    assert hub.comments_of == before
    # 記録を持つ PR へは、呼ばれると落ちる設定の読み込みを渡しても書ける（設定を読まない）
    record.write_verification(hub, 11, _verification(), _unread_names)
    assert len(record.read(hub, 11).verifications) == 1


def test_issue_3852_only_the_pulls_with_records_are_holders() -> None:
    """含めた PR の片方だけが記録を持てば、その 1 本だけを示す。"""
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(_included_hub([11, 12], [12]), RELEASE, _verification(), _names)
    assert caught.value.holders == (12,)


def test_issue_3852_holders_come_from_the_unit_of_the_same_version() -> None:
    """単位が 2 つなら、バージョンの合う単位の PR だけを探し、単位ごとの記録（`unit` を持つ）を持つ PR も候補に入れる。"""
    hub = _hub()
    lines = "\n".join(
        [
            record_decide.included_trailer([11, 12]),
            record_decide.unit_trailer("a", "1.2.4", [11]),
            record_decide.unit_trailer("b", "2.0.1", [12]),
        ]
    )
    hub.commits_of["r" * 40] = [_commit("r" * 40, f"Release: a v1.2.4\n\n{lines}\n", "d2" * 20), *COMMITS[1:]]
    record.write(hub, RELEASE, "main")
    for version, holder in (("1.2.4", 11), ("2.0.1", 12)):
        with pytest.raises(record.RecordElsewhere) as caught:
            record.write_verification(hub, RELEASE, _verification(version), _names)
        assert caught.value.holders == (holder,)


# 含めた PR #11・#12 が記録を持つリリースの PR #50 で止まる文（書く先は番号が最も小さい #11）
ELSEWHERE_MESSAGE = (
    "#50 はリリースの PR で、v1.2.4 のリリース記録を持たない。記録は含めた PR の各々にある: #11, #12。--pr に 番号が最も小さい #11 を渡す"
)


def test_record_elsewhere_states_the_pull_version_and_smallest_holder() -> None:
    """止まる文は渡した PR・バージョン・記録を持つ PR（小さい順）・書く先を含み、RecordError として受けられる。"""
    with pytest.raises(record.RecordError) as caught:
        record.write_verification(_included_hub([12, 3, 11], [12, 11]), RELEASE, _verification(), _names)
    assert isinstance(caught.value, record.RecordElsewhere)
    assert str(caught.value) == ELSEWHERE_MESSAGE
    assert caught.value.holders == (11, 12)


def test_record_elsewhere_with_one_holder_points_to_it() -> None:
    """記録を持つ PR が 1 本なら、その 1 本を並べて書く先にする。"""
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(_included_hub([11, 12], [12]), RELEASE, _verification(), _names)
    assert str(caught.value) == (
        "#50 はリリースの PR で、v1.2.4 のリリース記録を持たない。記録は含めた PR の各々にある: #12。--pr に 番号が最も小さい #12 を渡す"
    )


def _verified_on(hub: FakeHub, number: int, author: str = "octocat") -> None:
    """PR `number` へ、書き手 `author` の同じバージョンのリリース後テストの記録を足す。"""
    hub.comments_of[number].append(Authored(author, "OWNER", record_decide.render_verification(_verification())))


def test_rerun_after_partial_failure_keeps_the_pull_with_the_verification() -> None:
    """#10 への書き込みだけ失敗して #11 へリリース後テストを書いた後、再実行で #10 に記録が付いても書く先は #11 のまま。"""
    hub = _included_hub([10, 11, 12], [11, 12])
    _verified_on(hub, 11)
    hub.comments_of[10] = list(hub.comments_of[12])  # 再実行で #10 にもリリース記録が付いた
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(hub, RELEASE, _verification(), _names)
    assert caught.value.holders[0] == 11
    assert "--pr に v1.2.4 のリリース後テストの記録を既に持つ #11 を渡す" in str(caught.value)
    # 記録を持つ #10 を直に渡しても、記録の `included` の #11 が既に持つので書かずに #11 を示す
    with pytest.raises(record.RecordElsewhere) as direct:
        record.write_verification(hub, 10, _verification(), _unread_names)
    assert direct.value.holders == (11, 10, 12)
    assert record.read(hub, 10).verifications == ()
    # #11 へは重ねて書ける（再検証）
    record.write_verification(hub, 11, _verification(), _unread_names)
    assert len(record.read(hub, 11).verifications) == 2


def test_verification_by_untrusted_writer_does_not_move_the_target() -> None:
    """権限を持たない書き手のリリース後テストの記録は数えず、書く先は番号が最も小さい PR のまま。"""
    hub = _included_hub([11, 12], [11, 12])
    _verified_on(hub, 12, author="stranger")
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(hub, RELEASE, _verification(), _names)
    assert caught.value.holders == (11, 12)
    record.write_verification(hub, 11, _verification(), _unread_names)
    assert len(record.read(hub, 11).verifications) == 1


def test_rerun_on_a_pull_that_has_a_verification_goes_to_the_smallest_one_with_it() -> None:
    """#11 と #12 の両方がリリース後テストの記録を持つとき、#12 を直に渡しても書かずに #11 を示す（閉じる側が読む先と同じ）。"""
    hub = _included_hub([11, 12], [11, 12])
    _verified_on(hub, 11)
    _verified_on(hub, 12)
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(hub, 12, _verification(), _unread_names)
    assert caught.value.holders == (11, 12)
    assert "--pr に v1.2.4 のリリース後テストの記録を既に持つ #11 を渡す" in str(caught.value)
    assert len(record.read(hub, 12).verifications) == 1
    record.write_verification(hub, 11, _verification(), _unread_names)
    assert len(record.read(hub, 11).verifications) == 2


def test_a_pull_without_verifications_points_to_the_smallest_holder() -> None:
    """どの PR もリリース後テストの記録を持たなければ、記録を持つ #12 を渡しても番号が最も小さい #11 を示す。"""
    with pytest.raises(record.RecordElsewhere) as caught:
        record.write_verification(_included_hub([11, 12], [11, 12]), 12, _verification(), _unread_names)
    assert caught.value.holders == (11, 12)
    assert "--pr に 番号が最も小さい #11 を渡す" in str(caught.value)


def test_read_lists_every_release_record_so_a_later_version_does_not_hide_the_earlier() -> None:
    """#11 に v1.2.4 の記録の後で v1.2.5 の記録が付いても、読む側は `releases` で v1.2.4 を見つける（書く側の候補と同じ範囲）。"""
    hub = _included_hub([11, 12], [11, 12])
    later = record_decide.Record("1.2.5", 60, (11,))
    hub.comments_of[11].append(Authored("octocat", "OWNER", record_decide.render_record(later)))
    record.write_verification(hub, 11, _verification(), _unread_names)
    got = record.read(hub, 11)
    assert got.release == later
    assert [r.version for r in got.releases] == [VERSION, "1.2.5"]
    assert got.verifications == (_verification(),)


def _refused(hub: FakeHub, number: int, version: str = VERSION, names=_names) -> record.RecordError:
    with pytest.raises(record.RecordError) as caught:
        record.write_verification(hub, number, _verification(version), names)
    assert not isinstance(caught.value, record.RecordElsewhere)
    assert record.WORKFLOW_HINT in str(caught.value)
    return caught.value


def test_issue_3852_refuses_without_holders_when_they_cannot_be_shown() -> None:
    """リリースの PR でない・バージョンが違う・含めた PR のどれも持たない・設定が読めないときは、示さずに止まる。"""
    hub = _included_hub([11, 12], [11, 12])
    hub.pulls[7] = pull(7, base="develop", ref="feat/x", merged=True)
    _refused(hub, 7)
    _refused(hub, RELEASE, "9.9.9")
    _refused(_included_hub([11, 12], []), RELEASE)
    _refused(hub, RELEASE, names=record._no_names)
    assert all(not record.read(hub, n).verifications for n in (7, 11, 12, RELEASE))


def _verification_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], hub: FakeHub, number: int
) -> tuple[int, dict]:
    path = tmp_path / "verification.json"
    path.write_text(json.dumps(_verification()), encoding="utf-8")
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: hub)
    code = release_cli.main(["verification-record", "--root", str(tmp_path), "--pr", str(number), "--record", str(path)])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_issue_3852_cli_shows_the_holders_as_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release_cli, "_strategy", lambda args: strategy("plugin"))
    code, out = _verification_cli(tmp_path, monkeypatch, capsys, _included_hub([12, 3, 11], [11, 12]), RELEASE)
    assert code == 3 and out["status"] == "stopped" and out["cause"]["kind"] == "precondition"
    assert out["items"] == [{"kind": "pull", "number": 11}, {"kind": "pull", "number": 12}]
    assert out["summary"] == ELSEWHERE_MESSAGE and out["cause"]["detail"] == ELSEWHERE_MESSAGE


def test_issue_3852_cli_writes_to_a_holder_without_the_branch_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ブランチ戦略の設定が無いディレクトリでも、記録を持つ PR へは書いて ok で終える。"""
    hub = _included_hub([11, 12], [11, 12])
    code, out = _verification_cli(tmp_path, monkeypatch, capsys, hub, 11)
    assert code == 0 and out["status"] == "ok"
    assert len(record.read(hub, 11).verifications) == 1
    # 設定が無いのでリリースの PR では探さず、items を持たずに止まる
    code, out = _verification_cli(tmp_path, monkeypatch, capsys, hub, RELEASE)
    assert code == 3 and out["cause"]["kind"] == "precondition" and not out.get("items")
