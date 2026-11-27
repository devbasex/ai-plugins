"""バージョンの無いリリース記録を書く・対象の一覧を訂正する（`record --version`・`--correct-targets`。#4580）。

GitHub と git は偽物で、並行した書き込みは偽の GitHub のコメントの順で作る。子のプロセスを起動せず、本物の時間を待たない。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from ndf.adapters.github import Authored
from ndf.domains.release import cli as release_cli
from ndf.domains.release import record, record_decide, record_unversioned

from ._fakes import FakeHub, pull, section, strategy
from .test_release_delivery import FakeGit, FakeRun

PROD = 60
COMMIT = "abc1234" + "0" * 33
VALUE = f"main {COMMIT[:7]}"
BEFORE, AFTER = "b0" * 20, "c3" + "0" * 38
WEB, WORKER = "web", "worker"
SUCCESS = {"status": "completed", "conclusion": "success"}
RUNNING = {"status": "in_progress", "conclusion": None}
FAILURE = {"status": "completed", "conclusion": "failure"}


def _commit(sha: str, message: str, *parents: str) -> dict:
    return {"sha": sha, "commit": {"message": message}, "parents": [{"sha": p} for p in parents]}


# 本番の PR のコミット（作業の PR #11・#12 をマージしたスプリントのブランチ）
COMMITS = [
    _commit("d2" * 20, "Merge pull request #12 from o/feat/b", "d1" * 20, "x" * 40),
    _commit("d1" * 20, "Merge pull request #11 from o/feat/a", "s" * 40, "y" * 40),
]


def _hub(**more: Any) -> FakeHub:
    merged = pull(PROD, head="d2" * 20, base="main", ref="sprint/x", merged=True)._replace(merge_sha=COMMIT)
    return FakeHub(pulls={PROD: merged}, commits_of={"d2" * 20: list(COMMITS)}, **more)


def _row(name: str, **more: Any) -> dict:
    return {"target": name, "kind": "auto", "trigger": "deploy", "versioned": False, **more}


CD_ROWS = [
    _row(WEB, result={"workflow": "web.yml"}, paths=["web/**"]),
    _row(WORKER, result={"workflow": "worker.yml"}, paths=["worker/**"]),
]


class Repo(FakeGit):
    """手元の git の偽物（本番ブランチの第一親のコミット・差分・push の時点の設定・共通の git ディレクトリ）。"""

    def __init__(self, tmp_path: Path, deliveries: list[dict] | None = CD_ROWS, diffs: dict | None = None) -> None:
        project = section("web-trunk")
        if deliveries is not None:
            project["release"]["deliveries"] = deliveries
        objects = {
            f"{AFTER}^{{commit}}": b"c",
            f"{AFTER}:.ndf/project.json": json.dumps({"version": 1, "branch_strategy": project}).encode(),
        }
        super().__init__(objects, tmp_path)
        self.common = tmp_path / ".git"
        self.diffs = (
            diffs
            if diffs is not None
            else {(f"{COMMIT}^1", COMMIT): ["web/a.ts", "worker/b.py"], (BEFORE, AFTER): ["web/a.ts", "worker/b.py"]}
        )
        self.revs: list[str] = []
        # 反映したコミットから第一親をたどったコミット（新しい順）
        self.chain = [COMMIT, "r2" * 20, "r1" * 20, "m0" * 20]

    def run(self, *args: str) -> str:
        self.calls.append(args)
        if any(a.startswith("--max-count=") for a in args):
            count = int(next(a for a in args if a.startswith("--max-count=")).split("=")[1])
            return "".join(f"{c}\n" for c in self.chain[:count])
        return f"{AFTER}\n{COMMIT}\n"

    def diff_between(self, a: str, b: str, *options: str) -> str:
        self.calls.append(("diff", a, b, *options))
        return "".join(f"{p}\0" for p in self.diffs[(a, b)])

    def common_dir(self) -> Path:
        return self.common


def _release(deliveries: list[dict] | None = CD_ROWS) -> dict:
    got = section("web-trunk")["release"]
    if deliveries is not None:
        got["deliveries"] = deliveries
    return got


def _args(*environments: str, targets: tuple[str, ...] = (), observed: frozenset[str] = frozenset()) -> record_unversioned.Unversioned:
    return record_unversioned.Unversioned(VALUE, environments, targets, observed, None, "refs/remotes/origin/main")


def _hands(repo: Repo, run: FakeRun | None = None) -> record_unversioned.Hands:
    return record_unversioned.Hands(repo, lambda: 0.0, lambda s: None, run or FakeRun(0))


def _push_activity(hub: FakeHub) -> FakeHub:
    hub.activities = [{"before": BEFORE, "after": AFTER, "activity_type": "merge_queue_merge"}]
    return hub


def _written(hub: FakeHub, number: int) -> list[record_decide.Record]:
    return record_decide.records(c.body for c in hub.comments(number))


def _write(hub: FakeHub, repo: Repo, args: record_unversioned.Unversioned, release: dict | None = None) -> record_unversioned.Outcome:
    return record_unversioned.write_unversioned(hub, _hands(repo), PROD, "main", release if release is not None else _release(), args)


def test_writes_each_environment_to_every_included_pull_with_the_push_and_targets(tmp_path: Path) -> None:
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    got = _write(hub, repo, _args(WEB, WORKER))
    assert (got.recorded, got.kept, got.failed, got.pending, got.complete, got.missing) == ((WEB, WORKER), (), {}, {}, True, ())
    for n in (11, 12, PROD):
        rows = _written(hub, n)
        assert sorted(r.environments for r in rows) == [(WEB,), (WORKER,)]
        assert {(r.version, r.commit, r.targets, r.push) for r in rows} == {
            (VALUE, COMMIT, (WEB, WORKER), record_decide.Push(BEFORE, AFTER))
        }
        assert {tuple(r.confirmation.items()) for r in rows} == {((WEB, "cd"),), ((WORKER, "cd"),)}
    # CD は push の after で照会し、共通の前処理の保存先は complete で消える
    assert {q[2] for q in hub.run_queries} == {AFTER} and not (repo.common / "ndf-release-record" / f"{PROD}.json").exists()


def test_writes_to_the_production_pull_when_it_has_no_work_pulls(tmp_path: Path) -> None:
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    hub.commits_of["d2" * 20] = [_commit("d2" * 20, "fix: 直し", "s" * 40)]
    got = _write(hub, Repo(tmp_path), _args(WEB))
    assert got.record.included == (PROD,) and [r.environments for r in _written(hub, PROD)] == [(WEB,)]
    assert (got.complete, got.missing) == (False, (WORKER,))


def test_rerun_with_the_same_arguments_keeps_and_does_not_query_cd(tmp_path: Path) -> None:
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _write(hub, repo, _args(WEB))
    queries, requests = len(hub.run_queries), len(hub.requests)
    hub.activities = []  # 既存の記録の push を使い、更新の記録を読まない
    got = _write(hub, repo, _args(WEB))
    assert (got.recorded, got.kept) == ((), (WEB,)) and len(hub.run_queries) == queries
    assert not [r for r in hub.requests[requests:] if r[0] == "POST"]
    assert [len(_written(hub, n)) for n in (11, 12, PROD)] == [1, 1, 1]


def test_a_rerun_after_a_partial_write_writes_only_the_missing_pulls(tmp_path: Path) -> None:
    """一部の PR への投稿が失敗した後の打ち直しは、書けた PR を飛ばし、書けなかった PR だけへ書く。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _write(hub, repo, _args(WEB))
    hub.comments_of[12] = []
    hub.comments_of[PROD] = []
    got = _write(hub, repo, _args(WEB))
    assert got.recorded == (WEB,) and [len(_written(hub, n)) for n in (11, 12, PROD)] == [1, 1, 1]
    assert sorted((w.number, w.result) for w in got.written) == [(11, record.KEPT), (12, record.WRITTEN), (PROD, record.WRITTEN)]


def test_splits_recorded_and_pending_without_waiting_for_two_environments(tmp_path: Path) -> None:
    hub = _push_activity(_hub())
    hub.cd_runs = [[SUCCESS]]
    runs = {"web.yml": [SUCCESS], "worker.yml": [RUNNING]}
    hub.workflow_runs = lambda workflow, branch, commit, timeout=None: list(runs[workflow])  # type: ignore[method-assign]
    got = _write(hub, Repo(tmp_path), _args(WEB, WORKER))
    assert got.recorded == (WEB,) and set(got.pending) == {WORKER} and got.pending[WORKER] and not got.complete
    assert [r.environments for r in _written(hub, 11)] == [(WEB,)]


def test_all_pending_or_failed_write_nothing(tmp_path: Path) -> None:
    hub = _push_activity(_hub(cd_runs=[[RUNNING]]))
    got = _write(hub, Repo(tmp_path), _args(WEB, WORKER))
    assert (got.recorded, got.kept, set(got.pending)) == ((), (), {WEB, WORKER}) and hub.comments_of == {}
    hub = _push_activity(_hub(cd_runs=[[FAILURE]]))
    got = _write(hub, Repo(tmp_path), _args(WEB))
    assert set(got.failed) == {WEB} and hub.comments_of == {}


@pytest.mark.parametrize(
    ("args", "change", "match"),
    [
        (_args(WEB).__class__("main 1234567", (WEB,), (), frozenset(), None, "t"), None, "先頭 7 文字以上"),  # 確かめ 2
        (_args(WEB, targets=(WEB,)), None, "--target を渡さず"),  # 確かめ 3: push の時点に設定がある
        (_args("db"), None, "今回の対象の届け先"),  # 確かめ 4
        (_args(WEB, observed=frozenset({WORKER})), None, "--observed"),  # 確かめ 4
        (_args(WEB), "no_push", "時間を置いて打ち直す"),  # push を決められない
        (_args(WEB), "cancelled", "打ち消された"),  # 確かめ 3: 対象が空
    ],
)
def test_checks_one_to_six_write_nothing(tmp_path: Path, args: Any, change: str | None, match: str) -> None:
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    if change == "no_push":
        hub.activities = [{"before": "x" * 40, "after": "y" * 40}]
    if change == "cancelled":
        repo.diffs[(BEFORE, AFTER)] = ["docs/readme.md"]
    with pytest.raises(record.RecordError, match=match):
        _write(hub, repo, args)
    assert hub.comments_of == {} and hub.run_queries == []


R2, R1 = "r2" * 20, "r1" * 20


@pytest.mark.parametrize(
    ("commits", "linked", "base", "asked"),
    [
        (3, {R2: [11]}, f"{COMMIT}^1", [R2]),  # squash: 1 つ前は別の PR のコミット
        (3, {R2: [PROD], R1: [PROD, 70]}, f"{R1}^1", [R2, R1]),  # rebase で 3 コミット
        (3, {R2: [77], R1: [PROD]}, f"{COMMIT}^1", [R2]),  # 途中に別の PR のコミットが挟まる
        (1, {R2: [PROD]}, f"{COMMIT}^1", []),  # PR のコミットが 1 つなら照会しない
    ],
)
def test_the_pull_diff_starts_below_the_commits_linked_to_the_pull(
    tmp_path: Path, commits: int, linked: dict, base: str, asked: list
) -> None:
    """本番の PR の差分の起点は、反映したコミットから第一親をたどり、同じ PR に紐づく最後のコミットの第一親（PR のコミットの数まで）。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    hub.commit_counts[PROD], hub.linked = commits, linked
    repo = Repo(tmp_path, diffs={(base, COMMIT): ["web/a.ts"], (BEFORE, AFTER): ["web/a.ts"]})
    got = _write(hub, repo, _args(WEB))
    assert got.record.targets == (WEB,) and hub.linked_asked == asked


def test_the_pull_diff_stops_when_links_cannot_be_read(tmp_path: Path) -> None:
    from ndf.adapters.github import GitHubError

    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    hub.commit_counts[PROD] = 3
    hub.linked = {R2: GitHubError("No commit found", 422)}
    with pytest.raises(record.RecordError, match="打ち直す"):
        _write(hub, Repo(tmp_path), _args(WEB))
    assert hub.comments_of == {} and hub.run_queries == []


def test_targets_come_from_the_push_time_paths_and_moved_files(tmp_path: Path) -> None:
    """両方の差分に出るパスだけが当たり、移動の前のパス（--no-renames で出る）でも当たる。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path, diffs={(f"{COMMIT}^1", COMMIT): ["worker/old.py", "web/x.ts"], (BEFORE, AFTER): ["worker/old.py", "lib/new.py"]})
    got = _write(hub, repo, _args(WORKER))
    assert got.record.targets == (WORKER,) and got.complete
    assert ("diff", BEFORE, AFTER, "--name-only", "-z", "--no-renames") in repo.calls


def test_a_repository_without_deliveries_takes_targets_and_attests(tmp_path: Path) -> None:
    hub = _hub()
    repo = Repo(tmp_path)
    got = _write(hub, repo, _args(WEB, targets=(WORKER, WEB)), _release(None))
    assert got.recorded == (WEB,) and got.record.targets == (WEB, WORKER) and got.missing == (WORKER,)
    assert [r.confirmation for r in _written(hub, 11)] == [{WEB: "attested"}] and [r.push for r in _written(hub, 11)] == [None]
    assert repo.calls == [] and hub.activities == [] and not [r for r in hub.requests if r[1].endswith("/activity")]


def test_a_push_without_the_setting_needs_targets(tmp_path: Path) -> None:
    hub = _push_activity(_hub())
    with pytest.raises(record.RecordError, match="--target"):
        _write(hub, Repo(tmp_path, deliveries=None), _args(WEB))
    got = _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB,)))
    assert got.recorded == (WEB,) and [r.confirmation for r in _written(hub, 11)] == [{WEB: "attested"}]


def _other_writer(hub: FakeHub, number: int, made: record_decide.Record, author: str = "octocat") -> None:
    hub.made += 1
    hub.comments_of.setdefault(number, []).append(Authored(author, "OWNER", record_decide.render_record(made), comment_id=hub.made))


def _made(environment: str, targets: tuple[str, ...], version: str = VALUE, commit: str = COMMIT) -> record_decide.Record:
    return record_decide.Record(version, PROD, (11, 12, PROD), None, (environment,), targets, commit, None, {environment: "attested"})


def test_targets_different_from_the_first_record_stop_with_the_correction_command(tmp_path: Path) -> None:
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,)))
    with pytest.raises(record.RecordError, match="--correct-targets --target web"):
        _write(hub, Repo(tmp_path), _args(WORKER, targets=(WEB, WORKER)), _release(None))
    # 信用しない書き手の最初の記録は正の一覧にならない
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,)), author="stranger")
    assert _write(hub, Repo(tmp_path), _args(WORKER, targets=(WEB, WORKER)), _release(None)).recorded == (WORKER,)


def test_one_commit_with_two_values_stops(tmp_path: Path) -> None:
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,), version=f"main {COMMIT[:9]}"))
    with pytest.raises(record.RecordError, match="別の値"):
        _write(hub, Repo(tmp_path), _args(WEB, targets=(WEB,)), _release(None))


class RacingHub(FakeHub):
    """記録の PR へ書いた直後に、並行した `record` の記録を自分より前へ差し込む偽の GitHub。"""

    racer: record_decide.Record | None = None

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if method == "POST" and path.endswith("/11/comments") and self.racer is not None:
            racer, self.racer = self.racer, None
            _other_writer(self, 11, racer)
        return super().rest(method, path, payload, params)


def _racing(racer: record_decide.Record) -> RacingHub:
    base = _hub()
    hub = RacingHub(pulls=base.pulls, commits_of=base.commits_of)
    hub.racer = racer
    return hub


def test_a_parallel_run_with_other_targets_wins_and_we_remove_ours(tmp_path: Path) -> None:
    hub = _racing(_made(WEB, (WEB,)))
    with pytest.raises(record.RecordError, match="正の一覧（web）"):
        _write(hub, Repo(tmp_path), _args(WEB, targets=(WEB, WORKER)), _release(None))
    assert [r.targets for r in _written(hub, 11)] == [(WEB,)] and 12 not in hub.comments_of and len(hub.deleted) == 1


def test_a_parallel_run_with_a_longer_sha_value_wins_and_we_remove_ours(tmp_path: Path) -> None:
    hub = _racing(_made(WEB, (WEB,), version=f"main {COMMIT[:9]}"))
    with pytest.raises(record.RecordError, match="値とコミットの組"):
        _write(hub, Repo(tmp_path), _args(WEB, targets=(WEB,)), _release(None))
    assert [r.version for r in _written(hub, 11)] == [f"main {COMMIT[:9]}"]


def test_two_parallel_runs_with_the_same_arguments_end_with_one_record(tmp_path: Path) -> None:
    """同じ引数の 2 回が確かめを通って両方書いたら、後に書いた側が自分のコメントを消し、どの PR も 1 件に戻る。"""
    hub = _racing(_made(WEB, (WEB,)))
    other = _made(WEB, (WEB,))

    def racing_others(method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if method == "POST" and path.endswith("/12/comments") and not hub.comments_of.get(12):
            _other_writer(hub, 12, other)
        return RacingHub.rest(hub, method, path, payload, params)

    hub.rest = racing_others  # type: ignore[method-assign]
    got = _write(hub, Repo(tmp_path), _args(WEB, targets=(WEB,)), _release(None))
    assert got.recorded == (WEB,) and [len(_written(hub, n)) for n in (11, 12, PROD)] == [1, 1, 1]
    assert sorted(w.result for w in got.written if w.number in (11, 12)) == [record_unversioned.REMOVED, record_unversioned.REMOVED]


def test_a_comment_that_cannot_be_removed_stops_with_its_url(tmp_path: Path) -> None:
    hub = _racing(_made(WEB, (WEB,)))
    hub.undeletable = {2}  # 差し込んだ記録が 1、自分が書いたコメントが 2
    with pytest.raises(record_unversioned.CommentLeft) as caught:
        _write(hub, Repo(tmp_path), _args(WEB, targets=(WEB, WORKER)), _release(None))
    assert caught.value.urls == ("https://example.invalid/11",)


def test_a_command_delivery_runs_in_a_checked_out_push(tmp_path: Path) -> None:
    rows = [_row(WEB, result={"command": "./status {revision}"})]
    hub = _push_activity(_hub())
    run = FakeRun(0)
    got = record_unversioned.write_unversioned(hub, _hands(Repo(tmp_path, rows), run), PROD, "main", _release(rows), _args(WEB))
    assert got.recorded == (WEB,) and [r.confirmation for r in _written(hub, 11)] == [{WEB: "cd"}]
    add, command, remove = (c[0] for c in run.calls)
    assert add[3:5] == ["worktree", "add"] and command == f"./status {AFTER}" and remove[3:5] == ["worktree", "remove"]


def test_shared_prefix_is_computed_once_and_redone_for_another_commit(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    calls: list[str] = []

    def compute(commit: str = COMMIT) -> record_unversioned.Prefix:
        calls.append(commit)
        return record_unversioned.Prefix(commit, record_decide.Push(BEFORE, AFTER), None, frozenset({"a"}), frozenset())

    first = record_unversioned.shared_prefix(repo, PROD, COMMIT, compute)
    assert record_unversioned.shared_prefix(repo, PROD, COMMIT, compute) == first and calls == [COMMIT]
    record_unversioned.shared_prefix(repo, PROD, "f" * 40, lambda: compute("f" * 40))
    assert calls == [COMMIT, "f" * 40]
    path = repo.common / "ndf-release-record" / f"{PROD}.json"
    path.write_text("{", encoding="utf-8")
    record_unversioned.shared_prefix(repo, PROD, COMMIT, compute)
    assert calls[-1] == COMMIT and len(calls) == 3
    record_unversioned.sweep_shares(repo, 60, now=lambda: path.stat().st_mtime + 61)
    assert not path.exists()


def _correction_hub() -> FakeHub:
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,)))
    return hub


def test_correct_targets_adds_a_correction_and_keeps_the_records(tmp_path: Path) -> None:
    hub = _correction_hub()
    fix = (VALUE, (WORKER, WEB), "worker を落としていた", None)
    url = record_unversioned.correct_targets(hub, _hands(Repo(tmp_path)), PROD, "main", _release(None), fix)
    assert url == "https://example.invalid/11"
    bodies = [c.body for c in hub.comments(11)]
    assert record_decide.parse_correction(bodies[-1]) == record_decide.Correction(VALUE, COMMIT, (WEB, WORKER), (WEB,), fix[2])
    assert len(_written(hub, 11)) == 1
    # 訂正の後は新しい一覧で打ち直せる（前の一覧の記録は正と違うので数えない）
    got = _write(hub, Repo(tmp_path), _args(WEB, targets=(WEB, WORKER)), _release(None))
    assert got.recorded == (WEB,) and got.missing == (WORKER,)


def test_correct_targets_refuses_without_records_with_a_setting_or_an_untrusted_token(tmp_path: Path) -> None:
    fix = (VALUE, (WEB,), "理由", None)
    with pytest.raises(record.RecordError, match="先に record --version"):
        record_unversioned.correct_targets(_hub(), _hands(Repo(tmp_path)), PROD, "main", _release(None), fix)
    with pytest.raises(record.RecordError, match="--correct-targets では直せない"):
        record_unversioned.correct_targets(_correction_hub(), _hands(Repo(tmp_path)), PROD, "main", _release(), fix)
    hub = _correction_hub()
    hub.trusted = {"octocat"}
    hub.token_trusted = lambda: False  # type: ignore[method-assign]
    with pytest.raises(record.RecordError, match="write 以上"):
        record_unversioned.correct_targets(hub, _hands(Repo(tmp_path)), PROD, "main", _release(None), fix)
    assert len(hub.comments(11)) == 1


def _cli_args(**more: Any) -> argparse.Namespace:
    base = {
        "version": None,
        "environment": [],
        "target": [],
        "observed": [],
        "correct_targets": False,
        "reason": None,
        "production_sha": None,
    }
    return argparse.Namespace(**{**base, **more})


@pytest.mark.parametrize(
    ("kind", "given", "match"),
    [
        ("web-develop", {"version": VALUE, "environment": [WEB]}, "none でない"),
        ("web-trunk", {"version": VALUE}, "そろえて"),
        ("web-trunk", {"environment": [WEB]}, "そろえて"),
        ("web-trunk", {"version": VALUE, "environment": [WEB]}, "--target"),
        ("web-trunk", {"correct_targets": True, "version": VALUE, "target": [WEB]}, "--reason"),
        ("web-trunk", {"correct_targets": True, "version": VALUE, "target": [WEB], "reason": "r", "environment": [WEB]}, "受けない"),
        ("web-trunk", {"reason": "r"}, "--correct-targets と一緒"),
    ],
)
def test_record_args_check_zero(kind: str, given: dict, match: str) -> None:
    with pytest.raises(record.RecordError, match=match):
        release_cli._record_args(_cli_args(**given), strategy(kind))


def test_record_args_take_the_unversioned_form() -> None:
    got = release_cli._record_args(_cli_args(version=VALUE, environment=[WEB, WEB], target=[WEB], observed=[WEB]), strategy("web-trunk"))
    assert got == record_unversioned.Unversioned(VALUE, (WEB,), (WEB,), frozenset({WEB}), None, "")
    assert release_cli._record_args(_cli_args(), strategy("web-develop")) is None


def _outcome(**more: Any) -> record_unversioned.Outcome:
    rep = record_decide.Record(VALUE, PROD, (11, PROD), None, (), (WEB, WORKER), COMMIT)
    base = {
        "record": rep,
        "recorded": (),
        "kept": (),
        "failed": {},
        "pending": {},
        "complete": False,
        "missing": (WEB, WORKER),
        "written": [],
    }
    return record_unversioned.Outcome(**{**base, **more})


@pytest.mark.parametrize(
    ("outcome", "status", "kind"),
    [
        (_outcome(recorded=(WEB,), pending={WORKER: "未完了"}, missing=(WORKER,), written=[record.Written(11, "written")]), "ok", None),
        (_outcome(kept=(WEB,), failed={WORKER: "失敗"}), "ok", None),
        (_outcome(failed={WEB: "失敗"}, pending={WORKER: "未完了"}), "stopped", "refused"),
        (_outcome(pending={WEB: "未完了", WORKER: "未完了"}), "stopped", "precondition"),
    ],
)
def test_cmd_record_ends_as_the_table_says(monkeypatch, tmp_path: Path, outcome: Any, status: str, kind: str | None) -> None:
    """設計書の「record の終わり方」の表。結果は release-steps の結果のスキーマに合う。"""
    from ndf.contracts import result

    monkeypatch.setattr(release_cli, "_strategy", lambda args: strategy("web-trunk"))
    monkeypatch.setattr(release_cli, "_remote_name", lambda args: "origin")
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: FakeHub())
    monkeypatch.setattr(record_unversioned, "write_unversioned", lambda *a: outcome)
    args = _cli_args(root=str(tmp_path), pr=PROD, production="main", version=VALUE, environment=[WEB, WORKER], target=[WEB, WORKER])
    got = release_cli.cmd_record(args)
    assert got["status"] == status and (kind is None or got["cause"]["kind"] == kind)
    assert result.errors(got) == []
    assert got["outputs"]["product_version"] == VALUE and "release" not in got["outputs"] and got["outputs"]["pending"] == outcome.pending
    reasons = {i["name"]: i["reason"] for i in got["items"] if i["kind"] == "delivery"}
    assert reasons == {**outcome.failed, **outcome.pending}


def test_cmd_record_reports_a_comment_left(monkeypatch, tmp_path: Path) -> None:
    from ndf.adapters.github import GitHubError
    from ndf.contracts import result

    def left(*a: Any) -> Any:
        raise record_unversioned.CommentLeft(GitHubError("消せない", 403), ["https://example.invalid/11"])

    monkeypatch.setattr(release_cli, "_strategy", lambda args: strategy("web-trunk"))
    monkeypatch.setattr(release_cli, "_remote_name", lambda args: "origin")
    monkeypatch.setattr(release_cli, "_hub", lambda args, remote=None: FakeHub())
    monkeypatch.setattr(record_unversioned, "write_unversioned", left)
    got = release_cli.cmd_record(_cli_args(root=str(tmp_path), pr=PROD, production="main", version=VALUE, environment=[WEB], target=[WEB]))
    assert got["cause"]["kind"] == "unknown" and got["items"] == [{"kind": "comment", "url": "https://example.invalid/11"}]
    assert result.errors(got) == []
