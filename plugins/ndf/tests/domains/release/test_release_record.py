"""リリース記録を含めた PR の各々へ書き、リリース後テストの記録を同じ PR へ書く（#3487・#2783・#3128）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.adapters.github import Authored, PermissionUnknown
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
    # #4446: 承認資料を取り直すときの含む PR も同じ行の番号で、行に無い PR が後から積まれていれば止まる
    formats = names_decide.NameFormats(None)
    assert record.pinned_included(hub, RELEASE, "main", formats) == [3, 11]
    hub.commits_of["r" * 40][0] = _commit("r" * 40, "fix: 直し (#77)", "q" * 40)
    with pytest.raises(record.RecordError, match="#77"):
        record.pinned_included(hub, RELEASE, "main", formats)


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
    "#50 はリリースの PR で、v1.2.4 のリリース記録を持たない。記録は含めた PR の各々にある: #11, #12。--pr に番号が最も小さい #11 を渡す"
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
        "#50 はリリースの PR で、v1.2.4 のリリース記録を持たない。記録は含めた PR の各々にある: #12。--pr に番号が最も小さい #12 を渡す"
    )


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


# --- #4580: バージョン番号を持たないリポジトリの記録と、閉じる手順の読み取り（records --version など） ---

X = "abc1234" + "0" * 33  # 反映したコミット
Y = "def5678" + "0" * 33
WEB, WORKER = "web", "worker"
TARGETS = (WEB, WORKER)


def _unversioned(
    version: str = "main abc1234",
    commit: str = X,
    environments: tuple[str, ...] = (WEB,),
    targets: tuple[str, ...] = TARGETS,
    release: int = RELEASE,
    unit: str | None = None,
    included: tuple[int, ...] = (11, 12),
) -> record_decide.Record:
    return record_decide.Record(
        version,
        release,
        included,
        unit,
        environments,
        targets,
        commit,
        record_decide.Push("p" * 40, commit),
        {e: "cd" for e in environments},
    )


def _row(number: int, result: str, retest: str | None = None) -> dict:
    row = {"number": number, "result": result, "evidence": "確かめた"}
    if retest is not None:
        row["retest_reason"] = retest
    return row


def _check(environments: tuple[str, ...], *rows: dict, version: str = "main abc1234") -> dict:
    return {"version": version, "environments": list(environments), "issues": list(rows)}


def test_unversioned_record_reads_back_with_its_targets_and_evidence() -> None:
    observed = _unversioned(environments=(WEB, WORKER))._replace(
        confirmation={WEB: "cd", WORKER: "observed"}, evidence={WORKER: record_decide.Evidence("https://example.invalid/v", X)}
    )
    assert record_decide.parse_record(record_decide.render_record(observed)) == observed
    # バージョン番号を持つリポジトリの記録は足した項目を書かず、今と同じに読む
    versioned = record_decide.Record(VERSION, RELEASE, (11,))
    assert set(versioned.as_json()) == {"version", "release", "included"}
    assert record_decide.parse_record(record_decide.render_record(versioned)) == versioned


@pytest.mark.parametrize(
    "change",
    [
        {"confirmation": {WEB: "observed"}},  # observed で evidence が無い
        {"evidence": {WEB: {"url": "https://example.invalid/v", "value": X}}},  # observed でない届け先に evidence
        {"confirmation": {WEB: "observed"}, "evidence": {WEB: {"url": "https://example.invalid/v", "value": 1}}},
        {"targets": []},
        {"commit": ""},
    ],
)
def test_unversioned_record_of_a_wrong_shape_is_not_a_record(change: dict) -> None:
    payload = {**_unversioned().as_json(), **change}
    body = f"{record_decide.RECORD_MARK}\n```json\n{json.dumps(payload)}\n```\n"
    assert record_decide.parse_record(body) is None


def test_correction_reads_back_and_a_wrong_shape_is_none() -> None:
    made = record_decide.Correction("main abc1234", X, TARGETS, (WEB,), "worker を落としていた")
    assert record_decide.parse_correction(record_decide.render_correction(made)) == made
    broken = record_decide.render_correction(made).replace('"reason": "worker を落としていた"', '"reason": 3')
    assert record_decide.parse_correction(broken) is None
    assert record_decide.parse_correction(record_decide.render_record(_unversioned())) is None


def test_value_pairs_follow_the_older_record() -> None:
    first, short = _unversioned(), _unversioned(version="main abc12340")  # 同じコミットを違う長さの SHA で書いた値
    other = _unversioned(version="main abc1234 def5678", commit=X)
    moved = _unversioned(version="main abc1234 def5678", commit=Y)  # 2 つの SHA を含む同じ値を別のコミットで
    versioned = record_decide.Record(VERSION, RELEASE, (11,))
    pairs, valid = record_decide.value_pairs([first, short, versioned, first])
    assert pairs == {"main abc1234": X} and valid == [first, versioned, first]
    pairs, valid = record_decide.value_pairs([other, moved])
    assert pairs == {"main abc1234 def5678": X} and valid == [other]
    # 渡した順だけで決まり、release や unit を見ない
    _pairs, valid = record_decide.value_pairs([first._replace(release=9, unit="b"), short._replace(release=1)])
    assert valid == [first._replace(release=9, unit="b")]


def test_authoritative_targets_take_the_first_record_and_the_last_correction() -> None:
    first, later = _unversioned(targets=(WEB,)), _unversioned(targets=TARGETS)
    assert record_decide.authoritative_targets([first, later], [], "main abc1234") == (WEB,)
    fixes = [record_decide.Correction("main abc1234", X, t, (WEB,), "直す") for t in (TARGETS, (WORKER,))]
    assert record_decide.authoritative_targets([first, later], fixes, "main abc1234") == (WORKER,)
    assert record_decide.authoritative_targets([], [], "main abc1234") is None


def test_representative_is_the_last_record_on_the_authoritative_targets() -> None:
    right, wrong = _unversioned(environments=(WEB,)), _unversioned(environments=(WORKER,), targets=(WORKER,))
    assert record_decide.representative([right, wrong], TARGETS) == right
    versioned = [record_decide.Record(VERSION, RELEASE, (11,)), record_decide.Record(VERSION, 51, (11,))]
    assert record_decide.representative(versioned, None) == versioned[-1]
    assert record_decide.representative([wrong], TARGETS) is None


@pytest.mark.parametrize(
    ("rows", "verdict"),
    [
        ([_row(1, "passed"), _row(1, "failed")], "failed"),
        ([_row(1, "failed"), _row(1, "passed")], "failed"),  # 理由の無い合格で不合格を覆さない
        ([_row(1, "failed"), _row(1, "passed", "検証環境が落ちていた")], "passed"),
        ([_row(1, "not_run"), _row(1, "passed")], "passed"),
        ([_row(1, "not_run")], "not_run"),
        ([_row(2, "failed")], None),
    ],
)
def test_pair_verdict_does_not_depend_on_the_order_of_posting(rows: list[dict], verdict: str | None) -> None:
    checks = [_check((WEB,), row) for row in rows]
    assert record_decide.pair_verdict(checks, WEB, 1) == verdict
    assert record_decide.pair_verdict(list(reversed(checks)), WEB, 1) in (
        {verdict} if rows[-1].get("retest_reason") is None else {"failed"}
    )
    # ほかの届け先の行は数えない
    assert record_decide.pair_verdict(checks, WORKER, 1) is None


def test_to_test_takes_pairs_without_rows_or_on_hold() -> None:
    checks = [_check((WEB,), _row(1, "passed"), _row(2, "not_run")), _check((WORKER,), _row(1, "failed"))]
    got = record_decide.to_test([WEB, WORKER], [1, 2, 3], checks)
    assert got == [
        {"environment": WEB, "issue": 2},
        {"environment": WEB, "issue": 3},
        {"environment": WORKER, "issue": 2},
        {"environment": WORKER, "issue": 3},
    ]


def test_complete_of_counts_only_the_same_release_unit_and_commit() -> None:
    rep = _unversioned(environments=(WEB,))
    other_unit, other_release = _unversioned(environments=(WORKER,), unit="b"), _unversioned(environments=(WORKER,), release=51)
    wrong_targets = _unversioned(environments=(WORKER,), targets=(WORKER,))
    assert record_decide.complete_of([rep, other_unit, other_release, wrong_targets], rep, TARGETS) == (False, (WORKER,))
    assert record_decide.complete_of([rep, _unversioned(environments=(WORKER,))], rep, TARGETS) == (True, ())


def _comment(writer: str, body: str) -> Authored:
    return Authored(writer, "MEMBER", body)


def _records_hub(*comments: Authored, trusted: tuple[str, ...] = ("octocat",), unknown: tuple[str, ...] = ()) -> FakeHub:
    hub = FakeHub(trusted=set(trusted), unknown=set(unknown))
    hub.comments_of[11] = list(comments)
    return hub


def _read(hub: FakeHub, version: str | None = "main abc1234", release: int | None = None, unit: str | None = None, **kw) -> dict:
    return record.read_release(hub, 11, record.Selection(version, release, unit), kw.get("bodies", False), kw.get("issues"))


def _rec(writer: str, made: record_decide.Record) -> Authored:
    return _comment(writer, record_decide.render_record(made))


def test_read_release_takes_the_given_value_and_unit_and_counts_a_duplicate_once() -> None:
    later = _unversioned(version="main def5678", commit=Y)
    a, b = _unversioned(unit="a"), _unversioned(unit="b", environments=(WORKER,))
    hub = _records_hub(_rec("octocat", a), _rec("octocat", b), _rec("octocat", a), _rec("octocat", later))
    got = _read(hub, unit="a")
    assert got["release"] == a.as_json() and got["releases"] == [a.as_json()] and got["targets"] == list(TARGETS)
    assert got["selection"] == {"version": "main abc1234", "release": None, "unit": "a"} and got["command"] == "records"


def test_read_release_keeps_the_authoritative_record_and_skips_writers_of_other_releases() -> None:
    right, wrong = _unversioned(), _unversioned(environments=(WORKER,), targets=(WORKER,))
    other = _unversioned(version="main def5678", commit=Y)
    hub = _records_hub(_rec("octocat", right), _rec("someone", other), _rec("octocat", wrong), unknown=("someone",))
    got = _read(hub)
    assert got["release"] == right.as_json() and "someone" not in hub.asked
    # 候補の書き手を照会できなければ、記録を選ばずに止まる
    with pytest.raises(PermissionUnknown) as caught:
        _read(hub, version="main def5678")
    assert caught.value.login == "someone"


def test_read_release_drops_writers_without_write_and_does_not_ask_about_other_comments() -> None:
    fake = _unversioned(targets=(WEB,))
    talk = _comment("stranger", "ただの会話")
    hub = _records_hub(talk, _rec("reader", fake), _rec("octocat", _unversioned()), unknown=("stranger",))
    got = _read(hub)
    assert got["targets"] == list(TARGETS) and "stranger" not in hub.asked


def test_read_release_marks_bodies_of_writers_without_write_and_stops_on_unknown_ones() -> None:
    hub = _records_hub(_rec("octocat", _unversioned()))
    hub.bodies_of = {11: Authored("octocat", "OWNER", "Closes #3"), 12: Authored("reader", "MEMBER", "Closes #4")}
    got = _read(hub, bodies=True)
    assert [(b["number"], b["trusted"], b["body"]) for b in got["bodies"]] == [(11, True, "Closes #3"), (12, False, "Closes #4")]
    # 本文の書き手の課題は信用する本文からだけ集める
    assert got["to_test"] == [{"environment": WEB, "issue": 3}]
    hub.unknown = {"reader"}
    with pytest.raises(PermissionUnknown):
        _read(hub, bodies=True)


def test_read_release_with_issues_does_not_read_bodies() -> None:
    hub = _records_hub(
        _rec("octocat", _unversioned()), _comment("octocat", record_decide.render_verification(_check((WEB,), _row(5, "passed"))))
    )
    got = _read(hub, issues=[5, 6])
    assert got["to_test"] == [{"environment": WEB, "issue": 6}] and got["bodies"] is None
    assert not [r for r in hub.requests if "/issues/" in r[1]]


def test_read_release_takes_the_corrections_of_trusted_records_only() -> None:
    fix = record_decide.Correction("main abc1234", X, (WEB,), TARGETS, "worker は配らない")
    stray = record_decide.Correction("main def5678", Y, (WEB,), TARGETS, "信用しない記録にだけ対応する")
    hub = _records_hub(
        _rec("octocat", _unversioned()),
        _rec("reader", _unversioned(version="main def5678", commit=Y)),
        _comment("someone", record_decide.render_correction(stray)),
        _comment("octocat", record_decide.render_correction(fix)),
        _rec("octocat", _unversioned(targets=(WEB,))),
        unknown=("someone",),
    )
    got = _read(hub, version=None)
    assert got["targets"] == [WEB] and got["release"]["targets"] == [WEB] and "someone" not in hub.asked
    # --version を渡せば、組を決めるために集めた別の値の訂正の書き手は照会しない
    # （(A, X) とコミットを共有する (B, X) と、それと値を共有する (B, Y) で、B は Y と組になる）
    hub.comments_of[11][1:1] = [_rec("octocat", _unversioned(version="B", commit=X)), _rec("octocat", _unversioned(version="B", commit=Y))]
    hub.comments_of[11].append(_comment("someone", record_decide.render_correction(fix._replace(version="B", commit=Y))))
    assert _read(hub)["targets"] == [WEB] and "someone" not in hub.asked
    with pytest.raises(PermissionUnknown):
        _read(hub, version=None)
    hub.comments_of[11].append(_comment("ghost", record_decide.render_correction(fix)))
    hub.unknown.add("ghost")
    with pytest.raises(PermissionUnknown):
        _read(hub)


def test_read_release_does_not_take_the_loser_of_a_concurrent_write() -> None:
    winner = _unversioned()
    loser = _unversioned(version="main abc12340", release=51)  # 同じコミットを別の値で後に書いた
    hub = _records_hub(_rec("octocat", winner), _rec("octocat", loser))
    assert _read(hub, version=None, release=51)["release"] is None
    # 同じコミットに残った後の値の記録は、先の値の読み取りに影響しない
    assert _read(hub)["release"] == winner.as_json()


def test_read_release_with_a_value_keeps_only_that_value() -> None:
    a_x, b_x, b_y = _unversioned(version="A abc1234"), _unversioned(version="B abc1234"), _unversioned(version="B def5678", commit=Y)
    hub = _records_hub(_rec("octocat", a_x), _rec("octocat", b_x), _rec("octocat", b_y))
    got = _read(hub, version="A abc1234")
    assert got["releases"] == [a_x.as_json()] and got["release"] == a_x.as_json()


def test_read_release_by_the_release_or_the_unit_alone() -> None:
    first = _unversioned(targets=(WEB,))
    later = _unversioned(targets=TARGETS, environments=(WORKER,), release=51)  # 同じ値・同じコミットで targets の違う後の記録
    hub = _records_hub(_rec("octocat", first), _rec("octocat", later))
    got = _read(hub, version=None, release=51)
    assert got["targets"] == [WEB] and got["release"] is None
    assert _read(hub, version=None, release=RELEASE)["release"] == first.as_json()
    unit_hub = _records_hub(
        _rec("octocat", _unversioned(unit="a")), _rec("octocat", _unversioned(version="main def5678", commit=Y, unit="b"))
    )
    assert _read(unit_hub, version=None, unit="b")["release"]["version"] == "main def5678"


def test_read_release_asks_only_about_records_bridged_from_trusted_ones() -> None:
    base = _unversioned()
    shared_value = _unversioned(environments=(WORKER,))  # 信用する記録と値を共有する
    via_untrusted = _unversioned(version="main def5678", commit=Y)  # 信用しない書き手の記録とだけコミットを共有する
    hub = _records_hub(
        _rec("octocat", base),
        _rec("member", shared_value),
        _rec("reader", via_untrusted._replace(version="other", commit=Y)),
        _rec("someone", via_untrusted._replace(version="third", commit=Y)),
        trusted=("octocat", "member"),
        unknown=("someone",),
    )
    got = _read(hub)
    assert [r["environments"] for r in got["releases"]] == [[WEB], [WORKER]] and "someone" not in hub.asked
    hub.unknown.add("member")
    hub.asked.clear()
    with pytest.raises(PermissionUnknown):
        _read(hub)


def test_read_release_of_versioned_records_is_not_narrowed_by_targets() -> None:
    old, new = record_decide.Record("1.2.3", 49, (11,)), record_decide.Record(VERSION, RELEASE, (11,))
    check = {"version": VERSION, "issues": [_row(7, "passed")]}
    hub = _records_hub(_rec("octocat", old), _rec("octocat", new), _comment("octocat", record_decide.render_verification(check)))
    got = _read(hub, version=VERSION, issues=[7, 8])
    assert got["release"] == new.as_json() and got["targets"] is None and got["verifications"] == [check]
    assert got["to_test"] == [{"environment": None, "issue": 8}]
    assert _read(hub, version="1.2.3")["verifications"] == []


def test_pr_body_only_returns_that_pull_body_alone() -> None:
    hub = _records_hub(_rec("octocat", _unversioned()))
    hub.bodies_of = {7: Authored("octocat", "OWNER", "Closes #9")}
    got = record.pr_body_only(hub, 7)
    assert got["bodies"] == [{"number": 7, "author": "octocat", "trusted": True, "body": "Closes #9"}]
    assert got["release"] is None and got["releases"] == [] and hub.asked == ["octocat"]


def _records_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], hub: FakeHub, *extra: str) -> tuple[int, dict]:
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: hub)
    code = release_cli.main(["records", "--pr", "11", *extra])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_records_cli_reads_the_release_and_stops_on_an_unknown_writer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = _records_hub(_rec("octocat", _unversioned()))
    code, out = _records_cli(monkeypatch, capsys, hub, "--version", "main abc1234", "--issues", "3")
    assert (
        code == 0
        and out["outputs"]["release"] == _unversioned().as_json()
        and out["outputs"]["to_test"] == [{"environment": WEB, "issue": 3}]
    )
    hub.comments_of[11].append(_rec("someone", _unversioned(environments=(WORKER,))))
    hub.unknown = {"someone"}
    code, out = _records_cli(monkeypatch, capsys, hub, "--version", "main abc1234")
    assert code != 0 and out["status"] == "stopped" and out["cause"]["kind"] == "unknown" and "someone" in out["cause"]["detail"]
    # 新しい引数が無ければ今の読み方のまま
    code, out = _records_cli(monkeypatch, capsys, hub)
    assert code == 0 and set(out["outputs"]) == {"release", "verifications"}


def test_records_cli_pr_body_only_refuses_a_selection(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    hub = _records_hub()
    hub.bodies_of = {11: Authored("octocat", "OWNER", "Closes #9")}
    code, out = _records_cli(monkeypatch, capsys, hub, "--pr-body-only")
    assert code == 0 and out["outputs"]["bodies"][0]["number"] == 11
    code, out = _records_cli(monkeypatch, capsys, hub, "--pr-body-only", "--version", "main abc1234")
    assert code == 3 and out["cause"]["kind"] == "precondition"
