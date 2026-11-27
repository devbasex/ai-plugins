"""バージョンの無いリリース記録を書く・対象の一覧を訂正する（`record --version`・`--correct-targets`。#4580）。

GitHub と git は偽物で、並行した書き込みは偽の GitHub のコメントの順で作る。子のプロセスを起動せず、本物の時間を待たない。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import pytest

from ndf.adapters.git import GitError
from ndf.adapters.github import Authored, GitHubError
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
        # push の after と反映したコミットの時点の設定（どちらも `deliveries`）
        body = json.dumps({"version": 1, "branch_strategy": project}).encode()
        objects = {f"{sha}{suffix}": got for sha in (AFTER, COMMIT) for suffix, got in (("^{commit}", b"c"), (":.ndf/project.json", body))}
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
        # 反映したコミットの後から追跡参照までの第一親のコミット（新しい順）と、追跡参照が指すコミット
        self.later = [AFTER]
        self.tip: str | None = None
        # 履歴が途中で切れている（浅い clone の境界で、いちばん古いコミットに親が無い）
        self.cut = False
        # 反映したコミットの後に第一親で設定のファイル（.ndf/project.json）を変えたコミット
        self.project_changes: list[str] = []

    def run(self, *args: str) -> str:
        self.calls.append(args)
        if any(a.startswith("--max-count=") for a in args):
            count = int(next(a for a in args if a.startswith("--max-count=")).split("=")[1])
            return "".join(f"{c}\n" for c in self.chain[:count])
        if args[0] == "log":
            out = ""
            for i, sha in enumerate(self.later):
                parent = self.later[i + 1] if i + 1 < len(self.later) else ("" if self.cut else COMMIT)
                out += f"\x01{sha} {parent}".rstrip() + "\n\n" + (".ndf/project.json\n" if sha in self.project_changes else "")
            return out
        return ""

    def run_bytes(self, *args: str, stdin: bytes | None = None, codes: Any = (0,)) -> bytes:
        """`cat-file --batch` の見出しに、コミットを指す名前ではそのコミットの SHA を載せる（追跡参照は `tip`）。"""
        self.calls.append(args)
        out = b""
        for name in (stdin or b"").decode().splitlines():
            if name.startswith("refs/") and name.endswith("^{commit}"):
                tip = self.tip if self.tip is not None else (self.later[0] if self.later else COMMIT)
                out += f"{tip} commit 1\nc\n".encode()
            elif name in self.objects:
                body = self.objects[name]
                sha = name[: -len("^{commit}")] if name.endswith("^{commit}") else "0" * 40
                out += f"{sha} blob {len(body)}\n".encode() + body + b"\n"
            else:
                out += f"{name} missing\n".encode()
        return out

    def diff_between(self, a: str, b: str, *options: str) -> str:
        self.calls.append(("diff", a, b, *options))
        if (a, b) not in self.diffs:  # 浅い clone の外の親など、手元に無いコミット
            raise GitError(f"fatal: bad revision '{a}'")
        return "".join(f"{p}\0" for p in self.diffs[(a, b)])

    def common_dir(self) -> Path:
        self.calls.append(("rev-parse", "--git-common-dir"))
        return self.common


def _release(deliveries: list[dict] | None = CD_ROWS) -> dict:
    got = section("web-trunk")["release"]
    if deliveries is not None:
        got["deliveries"] = deliveries
    return got


def _args(*environments: str, targets: tuple[str, ...] = (), observed: frozenset[str] = frozenset()) -> record_unversioned.Unversioned:
    return record_unversioned.Unversioned(VALUE, environments, targets, observed, None, "refs/remotes/origin/main")


def _hands(repo: Repo, run: FakeRun | None = None) -> record_unversioned.Hands:
    """CLI と同じく、共通の git ディレクトリは解決済みの値を渡す。"""
    return record_unversioned.Hands(repo, lambda: 0.0, lambda s: None, run or FakeRun(0), common=repo.common)


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
    assert {q[2] for q in hub.run_queries} == {AFTER} and not (repo.common / "ndf" / "release-record" / f"{PROD}.json").exists()
    # 共有の置き場は CLI が解決済みの共通の git ディレクトリの下に置き、git を起動して解決し直さない
    assert "rev-parse" not in [c[0] for c in repo.calls]


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
        (_args(WEB), "stale", r"git fetch origin \+refs/heads/main:refs/remotes/origin/main"),  # 手元の追跡参照が反映したコミットより古い
        (_args(WEB), "cancelled", "打ち消された"),  # 確かめ 3: 対象が空
    ],
)
def test_checks_one_to_six_write_nothing(tmp_path: Path, args: Any, change: str | None, match: str) -> None:
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    if change == "no_push":
        hub.activities = [{"before": "x" * 40, "after": "y" * 40}]
    if change == "stale":
        repo.later, repo.tip = [], BEFORE
    if change == "cancelled":
        repo.diffs[(BEFORE, AFTER)] = ["docs/readme.md"]
    with pytest.raises(record.RecordError, match=match):
        _write(hub, repo, args)
    assert hub.comments_of == {} and hub.run_queries == []


R2, R1 = "r2" * 20, "r1" * 20


@pytest.mark.parametrize(
    ("commits", "linked", "base", "asked"),
    [
        (3, {R2: [11]}, f"{COMMIT}^1", [R2, R1]),  # squash: 1 つ前は別の PR のコミット
        (3, {R2: [PROD], R1: [PROD, 70]}, f"{R1}^1", [R2, R1]),  # rebase で 3 コミット
        (3, {R2: [77], R1: [PROD]}, f"{COMMIT}^1", [R2, R1]),  # 途中に別の PR のコミットが挟まる
        (1, {R2: [PROD]}, f"{COMMIT}^1", []),  # PR のコミットが 1 つなら照会しない
    ],
)
def test_the_pull_diff_starts_below_the_commits_linked_to_the_pull(
    tmp_path: Path, commits: int, linked: dict, base: str, asked: list
) -> None:
    """本番の PR の差分の起点は、反映したコミットから第一親をたどり、同じ PR に紐づく最後のコミットの第一親（PR のコミットの数まで）。
    たどったコミットはコミットの数によらず 1 回の照会でまとめて読む。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    hub.commit_counts[PROD], hub.linked = commits, linked
    repo = Repo(tmp_path, diffs={(base, COMMIT): ["web/a.ts"], (BEFORE, AFTER): ["web/a.ts"]})
    got = _write(hub, repo, _args(WEB))
    assert got.record.targets == (WEB,) and hub.linked_asked == asked and hub.linked_batches == (1 if asked else 0)


@pytest.mark.parametrize(
    ("linked", "asked"),
    [
        ({R2: [11], R1: GitHubError("後のバッチ", 502)}, [R2]),  # 起点が決まれば後のバッチは照会しない
        ({R2: [PROD], R1: [PROD]}, [R2, R1]),  # 同じ PR のコミットが続けば次のバッチも読む
    ],
)
def test_the_pull_diff_reads_links_batch_by_batch(monkeypatch, tmp_path: Path, linked: dict, asked: list) -> None:
    original = record_unversioned.limits.value
    monkeypatch.setattr(record_unversioned.limits, "value", lambda name, key: 1 if name == "github.graphql-batch" else original(name, key))
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    hub.commit_counts[PROD], hub.linked = 3, linked
    base = f"{COMMIT}^1" if asked == [R2] else f"{R1}^1"
    repo = Repo(tmp_path, diffs={(base, COMMIT): ["web/a.ts"], (BEFORE, AFTER): ["web/a.ts"]})
    assert _write(hub, repo, _args(WEB)).record.targets == (WEB,)
    assert hub.linked_asked == asked and hub.linked_batches == len(asked)


def test_the_pull_diff_stops_when_links_cannot_be_read(tmp_path: Path) -> None:
    from ndf.adapters.github import GitHubError

    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    hub.commit_counts[PROD] = 3
    hub.linked = {R2: GitHubError("Something went wrong", 502)}
    with pytest.raises(record.RecordError, match="打ち直す"):
        _write(hub, Repo(tmp_path), _args(WEB))
    hub.linked = {R2: [PROD], R1: None}  # GitHub に無いコミット
    with pytest.raises(record.RecordError, match="GitHub に無く"):
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
    with pytest.raises(record.RecordError, match="--target"):
        _write(_hub(), Repo(tmp_path, deliveries=None), _args(WEB), _release(None))
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    got = _write(hub, repo, _args(WEB, targets=(WORKER, WEB)), _release(None))
    assert got.recorded == (WEB,) and got.record.targets == (WEB, WORKER) and got.missing == (WORKER,)
    assert [r.confirmation for r in _written(hub, 11)] == [{WEB: "attested"}] and [r.push for r in _written(hub, 11)] == [None]
    # 第一親の履歴と設定のファイルの変更の 1 回と、設定の読み取りの 1 回だけで、push も差分も読まず、置き場にも書かない
    assert [c[0] for c in repo.calls] == ["log", "cat-file"]
    assert not [r for r in hub.requests if r[1].endswith("/activity")]
    assert not list((repo.common / "ndf" / "release-record").glob("*.json"))


def test_a_later_change_without_deliveries_does_not_read_the_push(tmp_path: Path) -> None:
    """反映したコミットの後に設定のファイルを変えても、どの時点にも届け先の設定が無ければ、push を読まずに --target で受ける。"""
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    later = "e5" * 20
    repo.objects[f"{later}:.ndf/project.json"] = repo.objects[f"{COMMIT}:.ndf/project.json"]
    repo.project_changes = [later]
    got = _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None))
    assert got.recorded == (WEB,) and [c[0] for c in repo.calls] == ["log", "cat-file"]
    assert not [r for r in hub.requests if r[1].endswith("/activity")]


def test_a_setting_added_later_in_the_same_push_is_read_from_the_push(tmp_path: Path) -> None:
    """反映したコミットに設定が無くても、同じ push の後の PR が設定を足していれば、push の時点の設定で決めて --target を断る。"""
    hub = _push_activity(_hub(cd_runs=[[FAILURE]]))
    repo = Repo(tmp_path, deliveries=None)
    repo.objects.update({k: v for k, v in Repo(tmp_path).objects.items() if k.startswith(AFTER)})
    repo.project_changes = [AFTER]
    with pytest.raises(record.RecordError, match="--target を渡さず"):
        _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None))
    got = _write(hub, repo, _args(WEB), _release(None))
    assert set(got.failed) == {WEB} and got.recorded == ()


def test_a_shallow_clone_without_the_parent_records_without_the_push(tmp_path: Path) -> None:
    """push を読むかの判定は反映したコミットの親を解決しないので、浅い clone の境界に反映したコミットがあっても記録できる。"""
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    original = repo.run

    def shallow(*args: str) -> str:
        if any(a.endswith("^1") or "^1.." in a for a in args):
            raise GitError("fatal: bad revision")
        return original(*args)

    repo.run = shallow  # type: ignore[method-assign]
    assert _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None)).recorded == (WEB,)


@pytest.mark.parametrize(
    ("cut", "later", "tip", "match"),
    [
        (True, [AFTER], None, r"git fetch --unshallow origin \+refs/heads/main"),  # 浅い clone の履歴が途中で切れている
        (False, [], BEFORE, r"git fetch origin \+refs/heads/main"),  # 追跡参照が反映したコミットより古い
    ],
)
def test_targets_are_refused_when_the_history_does_not_reach_the_commit(
    tmp_path: Path, cut: bool, later: list, tip: str | None, match: str
) -> None:
    """追跡参照から反映したコミットまで第一親の履歴がつながらなければ、後の設定を確かめられないので --target を受けずに止まる。"""
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    repo.cut, repo.later, repo.tip = cut, later, tip
    with pytest.raises(record.RecordError, match=match):
        _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None))
    assert hub.comments_of == {}


def test_a_later_parallel_call_reuses_the_shared_push_without_deciding_again(tmp_path: Path) -> None:
    """並べて打った後の呼び出しは、前の呼び出しが push を読んで置き場に書いた結果を使い、判定の git も起動しない。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _write(hub, repo, _args(WORKER))
    repo.calls.clear()
    _write(hub, repo, _args(WEB))
    assert [c[0] for c in repo.calls] == []


def test_a_left_share_is_swept_even_when_the_push_is_not_read(tmp_path: Path) -> None:
    """宣言を外した後に --target だけで記録しても、期限を過ぎて残った置き場を消す。"""
    repo = Repo(tmp_path, deliveries=None)
    left = repo.common / "ndf" / "release-record" / "59.json"
    left.parent.mkdir(parents=True)
    made = record_unversioned.Prefix(COMMIT, record_decide.Push(BEFORE, AFTER), None, frozenset(), frozenset())
    left.write_text(json.dumps(made.as_json()), encoding="utf-8")
    mine = left.with_name("notes.json")  # NDF が書いていないファイルは消さない
    mine.write_text("{}", encoding="utf-8")
    other = left.with_name("58.json")
    other.write_text("{}", encoding="utf-8")
    old = left.stat().st_mtime - 4000
    for path in (left, mine, other):
        os.utime(path, (old, old))
    _write(_hub(), repo, _args(WEB, targets=(WEB,)), _release(None))
    assert not left.exists() and mine.exists() and other.exists()


def test_a_share_with_another_push_than_the_trusted_record_is_not_used(tmp_path: Path) -> None:
    """同じ値の信用する記録の push は保存先より優先し、別の push を指す保存先は使わずに作り直す。"""
    share = tmp_path / "share"
    other = record_unversioned.Prefix(COMMIT, record_decide.Push("e1" * 20, "e2" * 20), None, frozenset(), frozenset())
    trusted = record_decide.Push(BEFORE, AFTER)
    record_unversioned.shared_prefix(share, PROD, COMMIT, lambda: other)
    made = record_unversioned.Prefix(COMMIT, trusted, None, frozenset({"web/a.ts"}), frozenset())
    assert record_unversioned.shared_prefix(share, PROD, COMMIT, lambda: made, trusted) == made


def test_a_missing_parent_for_the_pull_diff_stops_with_a_deeper_fetch(tmp_path: Path) -> None:
    """本番の PR の差分の起点が浅い clone の外にあれば、履歴を深く取得するコマンドを示して止まる。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path, diffs={(BEFORE, AFTER): ["web/a.ts"]})
    with pytest.raises(record.RecordError, match=r"git fetch --deepen=2 origin \+refs/heads/main:refs/remotes/origin/main"):
        _write(hub, repo, _args(WEB))
    assert hub.comments_of == {}


def test_the_first_record_after_removing_the_setting_uses_the_setting_of_the_commit(tmp_path: Path) -> None:
    """設定を外した後に、外す前の push を初めて記録しても、反映したコミットの時点の設定で対象と CD の確かめを決める。"""
    hub = _push_activity(_hub(cd_runs=[[FAILURE]]))
    repo = Repo(tmp_path)
    with pytest.raises(record.RecordError, match="--target を渡さず"):
        _write(hub, repo, _args(WEB, targets=(WEB,)), _release(None))
    got = _write(hub, repo, _args(WEB), _release(None))
    assert set(got.failed) == {WEB} and got.record.targets == (WEB, WORKER)
    with pytest.raises(record.RecordError, match="直せない"):
        record_unversioned.correct_targets(_correction_hub(), _hands(repo), PROD, "main", _release(None), (VALUE, (WEB,), "理由", None))


def test_a_commit_missing_locally_stops_with_an_explicit_fetch(tmp_path: Path) -> None:
    repo = Repo(tmp_path, deliveries=None)
    repo.objects = {}
    with pytest.raises(record.RecordError, match=r"git fetch origin \+refs/heads/main:refs/remotes/origin/main"):
        _write(_hub(), repo, _args(WEB, targets=(WEB,)), _release(None))


def test_a_rerun_after_removing_the_setting_still_uses_the_setting_of_the_push(tmp_path: Path) -> None:
    """設定を外した後の打ち直しでも、同じ値の記録の push の時点の設定で対象と CD の確かめを決め、手入力の対象と訂正を断る。"""
    hub = _push_activity(_hub(cd_runs=[[SUCCESS]]))
    repo = Repo(tmp_path)
    _write(hub, repo, _args(WEB))
    (repo.common / "ndf" / "release-record" / f"{PROD}.json").unlink()
    hub.activities = []  # 記録の push を使い、更新の記録を読まない
    with pytest.raises(record.RecordError, match="--target を渡さず"):
        _write(hub, repo, _args(WORKER, targets=(WEB, WORKER)), _release(None))
    hub.cd_runs = [[FAILURE]]
    got = _write(hub, repo, _args(WORKER), _release(None))
    assert set(got.failed) == {WORKER} and got.recorded == ()
    with pytest.raises(record.RecordError, match="--correct-targets では直せない"):
        record_unversioned.correct_targets(hub, _hands(repo), PROD, "main", _release(None), (VALUE, (WEB,), "理由", None))


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
        _write(hub, Repo(tmp_path, deliveries=None), _args(WORKER, targets=(WEB, WORKER)), _release(None))
    # 信用しない書き手の最初の記録は正の一覧にならない
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,)), author="stranger")
    assert _write(hub, Repo(tmp_path, deliveries=None), _args(WORKER, targets=(WEB, WORKER)), _release(None)).recorded == (WORKER,)


def test_one_commit_with_two_values_stops(tmp_path: Path) -> None:
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,), version=f"main {COMMIT[:9]}"))
    with pytest.raises(record.RecordError, match="別の値"):
        _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB,)), _release(None))


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
        _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB, WORKER)), _release(None))
    assert [r.targets for r in _written(hub, 11)] == [(WEB,)] and 12 not in hub.comments_of and len(hub.deleted) == 1


def test_a_parallel_run_with_a_longer_sha_value_wins_and_we_remove_ours(tmp_path: Path) -> None:
    hub = _racing(_made(WEB, (WEB,), version=f"main {COMMIT[:9]}"))
    with pytest.raises(record.RecordError, match="値とコミットの組"):
        _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB,)), _release(None))
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
    got = _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB,)), _release(None))
    assert got.recorded == (WEB,) and [len(_written(hub, n)) for n in (11, 12, PROD)] == [1, 1, 1]
    assert sorted(w.result for w in got.written if w.number in (11, 12)) == [record_unversioned.REMOVED, record_unversioned.REMOVED]


def test_complete_counts_a_parallel_run_that_wrote_after_our_first_read(tmp_path: Path) -> None:
    """届け先ごとに並べて打った別の呼び出しが、こちらの確かめの後に書いた記録も、書いた後の読み直しで complete に数える。"""
    hub = _racing(_made(WORKER, (WEB, WORKER)))
    got = _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB, WORKER)), _release(None))
    assert got.recorded == (WEB,) and (got.complete, got.missing) == (True, ())


def test_check_seven_reads_each_included_pull_once_for_any_number_of_environments(tmp_path: Path) -> None:
    hub = _hub()
    repo = Repo(tmp_path, deliveries=None)
    _write(hub, repo, _args(WEB, WORKER, targets=(WEB, WORKER)), _release(None))
    reads: list[int] = []
    original = hub.comments

    def counting(number: int) -> list[Authored]:
        reads.append(number)
        return original(number)

    hub.comments = counting  # type: ignore[method-assign]
    got = _write(hub, repo, _args(WEB, WORKER, targets=(WEB, WORKER)), _release(None))
    assert got.kept == (WEB, WORKER) and sorted(reads) == [11, 12, PROD]


def test_a_comment_that_cannot_be_removed_stops_with_its_url(tmp_path: Path) -> None:
    hub = _racing(_made(WEB, (WEB,)))
    hub.undeletable = {2}  # 差し込んだ記録が 1、自分が書いたコメントが 2
    with pytest.raises(record_unversioned.CommentLeft) as caught:
        _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB, WORKER)), _release(None))
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

    share = record_unversioned.share_dir(_hands(repo))
    first = record_unversioned.shared_prefix(share, PROD, COMMIT, compute)
    assert record_unversioned.shared_prefix(share, PROD, COMMIT, compute) == first and calls == [COMMIT]
    record_unversioned.shared_prefix(share, PROD, "f" * 40, lambda: compute("f" * 40))
    assert calls == [COMMIT, "f" * 40]
    path = repo.common / "ndf" / "release-record" / f"{PROD}.json"
    path.write_text("{", encoding="utf-8")
    record_unversioned.shared_prefix(share, PROD, COMMIT, compute)
    assert calls[-1] == COMMIT and len(calls) == 3
    record_unversioned.sweep_shares(share, 60, now=lambda: path.stat().st_mtime + 61)
    assert not path.exists()


def _correction_hub() -> FakeHub:
    hub = _hub()
    _other_writer(hub, 11, _made(WEB, (WEB,)))
    return hub


def test_correct_targets_adds_a_correction_and_keeps_the_records(tmp_path: Path) -> None:
    hub = _correction_hub()
    fix = (VALUE, (WORKER, WEB), "worker を落としていた", None)
    url = record_unversioned.correct_targets(hub, _hands(Repo(tmp_path, deliveries=None)), PROD, "main", _release(None), fix)
    assert url == "https://example.invalid/11"
    bodies = [c.body for c in hub.comments(11)]
    assert record_decide.parse_correction(bodies[-1]) == record_decide.Correction(VALUE, COMMIT, (WEB, WORKER), (WEB,), fix[2])
    assert len(_written(hub, 11)) == 1
    # 訂正の後は新しい一覧で打ち直せる（前の一覧の記録は正と違うので数えない）
    got = _write(hub, Repo(tmp_path, deliveries=None), _args(WEB, targets=(WEB, WORKER)), _release(None))
    assert got.recorded == (WEB,) and got.missing == (WORKER,)


def test_correct_targets_refuses_without_records_with_a_setting_or_an_untrusted_token(tmp_path: Path) -> None:
    fix = (VALUE, (WEB,), "理由", None)
    with pytest.raises(record.RecordError, match="先に record --version"):
        record_unversioned.correct_targets(_hub(), _hands(Repo(tmp_path, deliveries=None)), PROD, "main", _release(None), fix)
    with pytest.raises(record.RecordError, match="--correct-targets では直せない"):
        record_unversioned.correct_targets(_correction_hub(), _hands(Repo(tmp_path)), PROD, "main", _release(), fix)
    hub = _correction_hub()
    hub.trusted = {"octocat"}
    hub.token_trusted = lambda: False  # type: ignore[method-assign]
    with pytest.raises(record.RecordError, match="write 以上"):
        record_unversioned.correct_targets(hub, _hands(Repo(tmp_path, deliveries=None)), PROD, "main", _release(None), fix)
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
    # 設定の無いリポジトリでも --target の要否は確かめ 3 で決める（同じ値の記録の push の時点に設定があれば受けない）
    assert release_cli._record_args(_cli_args(version=VALUE, environment=[WEB]), strategy("web-trunk")).targets == ()


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
