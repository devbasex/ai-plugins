"""リリース記録を含めた PR の各々へ書き、リリース後テストの記録を同じ PR へ書く（#3487・#2783・#3128）。"""

from __future__ import annotations

import pytest

from ndf.adapters.github import Authored
from ndf.domains.release import cli as release_cli
from ndf.domains.release import record, record_decide

from ._fakes import FakeHub, pull

RELEASE = 50
VERSION = "1.2.4"


def _commit(sha: str, message: str, *parents: str) -> dict:
    return {"sha": sha, "commit": {"message": message}, "parents": [{"sha": p} for p in parents]}


# リリースの PR のコミット（先端から first-parent: 版上げ → #12 → #11 → 本番から戻す PR #9 → PR の外）
COMMITS = [
    _commit("r" * 40, "Release: ndf v1.2.4\n\n本文", "d2" * 20),
    _commit("d2" * 20, "Merge pull request #12 from o/feat/b", "d1" * 20, "x" * 40),
    _commit("x" * 40, "wip (#99)", "d0" * 20),
    _commit("d1" * 20, "Merge pull request #11 from o/feat/a", "s" * 40, "y" * 40),
    _commit("s" * 40, "Merge pull request #9 from o/main", "outside", "m" * 40),
]


def _hub() -> FakeHub:
    released = pull(RELEASE, head="r" * 40, base="main", ref=f"release/v{VERSION}", merged=True)
    return FakeHub(pulls={RELEASE: released}, commits_of={RELEASE: list(COMMITS)})


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


def test_range_that_github_truncates_is_refused() -> None:
    hub = _hub()
    hub.commits_of[RELEASE] = [_commit(f"{i:040d}", "c", f"{i + 1:040d}") for i in range(record.PULL_COMMITS_CAP)]
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
    # 別の版の記録も書かない・形の違う記録も書かない
    with pytest.raises(record.RecordError):
        record.write_verification(hub, 11, _verification("9.9.9"))
    with pytest.raises(record.RecordError):
        record.write_verification(hub, 11, {"version": VERSION, "issues": []})
    assert len(record.read(hub, 11).verifications) == 1


def test_read_skips_untrusted_writers() -> None:
    hub = _hub()
    fake = record_decide.render_record(record_decide.Record(VERSION, RELEASE, (11,)))
    hub.comments_of[11] = [Authored("stranger", "NONE", fake)]
    assert record.read(hub, 11).release is None
    with pytest.raises(record.RecordError):
        record.write_verification(hub, 11, _verification())


def test_body_without_json_block_is_not_a_record() -> None:
    assert record_decide.parse_record(f"{record_decide.RECORD_MARK}\n対象の版: {VERSION}\n") is None
    assert record_decide.parse_record('```json\n{"version": "1.2.4", "release": 1, "included": []}\n```') is None


# 印とコードブロックを持つが JSON として読めないリリース記録
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
