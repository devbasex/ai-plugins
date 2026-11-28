"""Issue の本文の書き換え: 進捗記録・クリーンアップの記録・確認の記録が共に通る関数（#4735 の受け入れ条件 10）。"""

from __future__ import annotations

import subprocess
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from filelock import FileLock, Timeout

from ndf.adapters.github import Authored, GitHubError
from ndf.core import issue_body
from ndf.domains.loop import acceptance
from ndf.domains.records import cleanup_stage
from ndf.progress import records

PROGRESS = f"本文\n\n{records.ISSUE_SECTION}\n\n- [ ] クリーンアップ\n{records.SECTION_END}\n"


@dataclass
class Store:
    """本文の保存先のダミー。呼び出しを順に残す。"""

    bodies: dict[int, str] = field(default_factory=dict)
    calls: list[tuple[str, int]] = field(default_factory=list)
    broken_read: set[int] = field(default_factory=set)
    broken_write: set[int] = field(default_factory=set)

    def read(self, number: int) -> str:
        self.calls.append(("read", number))
        if number in self.broken_read:
            raise GitHubError("読めない")
        return self.bodies.get(number, "本文\n")

    def write(self, number: int, body: str) -> None:
        self.calls.append(("write", number))
        if number in self.broken_write:
            raise GitHubError("書けない")
        self.bodies[number] = body


@dataclass
class Hub:
    """クリーンアップの記録が使う GitHub の面。本文の読み書きは `Store` へ流す。"""

    store: Store
    repo: str = "o/r"

    def nodes(self, numbers: Any, node: str, fields: str) -> dict[int, dict]:
        return {n: {"closingIssuesReferences": {"nodes": [{"number": 1}]}} for n in numbers}

    def issue_body(self, number: int) -> Authored:
        return Authored("octocat", "OWNER", self.store.read(number))

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        assert (method, path.rsplit("/", 1)[0]) == ("PATCH", "/repos/o/r/issues")
        self.store.write(int(path.rsplit("/", 1)[1]), payload["body"])


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _view(issues: list[int], pr: int | None = None) -> Any:
    snap = SimpleNamespace(mode="standard", stages=[])
    ctx: dict[str, Any] = {"issues": issues, **({"pr": pr} if pr else {})}
    return SimpleNamespace(rows={}, snap=snap, context=lambda: ctx)


# --- 進捗記録とクリーンアップの記録の振る舞い（退行テスト） ---


def test_progress_records_read_once_and_write_only_a_changed_body(cwd: Path) -> None:
    store = Store()
    items, errors = records.sync_records(_view([1]), cwd, lambda _: store)
    assert (items, errors) == ([{"kind": "issue", "number": 1, "changed": True}], [])
    assert store.calls == [("read", 1), ("write", 1)]
    store.calls.clear()
    items, errors = records.sync_records(_view([1]), cwd, lambda _: store)
    assert (items, errors) == ([{"kind": "issue", "number": 1, "changed": False}], [])
    assert store.calls == [("read", 1)]


@pytest.mark.parametrize("broken", ["broken_read", "broken_write"])
def test_progress_records_report_a_failed_target_and_continue(cwd: Path, broken: str) -> None:
    store = Store(**{broken: {1}})
    items, errors = records.sync_records(_view([1, 2], pr=3), cwd, lambda _: store)
    assert [(i["kind"], i["number"]) for i in items] == [("issue", 2), ("pr", 3)]
    assert len(errors) == 1 and errors[0].startswith("issue #1 の進捗記録を書けない: ")


def test_progress_records_report_a_store_that_cannot_be_made(cwd: Path) -> None:
    def make(_: Path) -> Store:
        raise GitHubError("認証が無い")

    assert records.sync_records(_view([1]), cwd, make) == ([], ["進捗記録を書けない: 認証が無い"])


def test_cleanup_record_reports_a_failed_write(cwd: Path) -> None:
    store = Store(bodies={1: PROGRESS}, broken_write={1})
    got = cleanup_stage.record_cleanup_stage(Hub(store), [10], cwd)  # type: ignore[arg-type]
    assert [(i["issue"], i["result"]) for i in got] == [(1, "failed")]
    assert store.calls == [("read", 1), ("write", 1)]


def test_a_batch_resolves_the_git_common_dir_once(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 番号ごとに解決すると、対象の件数だけ git の起動が増える
    resolved: list[Path] = []
    original = issue_body.Git.common_dir

    def common_dir(git: Any) -> Path:
        resolved.append(git.cwd)
        return original(git)

    monkeypatch.setattr(issue_body.Git, "common_dir", common_dir)
    store = Store()
    assert records.sync_records(_view([1, 2], pr=3), cwd, lambda _: store)[1] == []
    assert len(resolved) == 1

    class TwoIssues(Hub):
        def nodes(self, numbers: Any, node: str, fields: str) -> dict[int, dict]:
            return {n: {"closingIssuesReferences": {"nodes": [{"number": 1}, {"number": 2}]}} for n in numbers}

    resolved.clear()
    assert len(cleanup_stage.record_cleanup_stage(TwoIssues(store), [10], cwd)) == 2  # type: ignore[arg-type]
    assert len(resolved) == 1


# --- 共通の書き換え ---


def _lock_path(cwd: Path, issue: int) -> Path:
    return issue_body.BodyLocks(cwd).path(issue)


def _held(cwd: Path, issue: int) -> bool:
    """番号のロックを誰かが持っているか（待たずに取ってみる）。"""
    try:
        with FileLock(str(_lock_path(cwd, issue)), timeout=0):
            return False
    except Timeout:
        return True


@dataclass
class Watched(Store):
    """読み書きの時点で番号のロックが取られているかを残す保存先。"""

    cwd: Path = Path()
    unlocked: list[tuple[str, int]] = field(default_factory=list)

    def read(self, number: int) -> str:
        self._watch("read", number)
        return super().read(number)

    def write(self, number: int, body: str) -> None:
        self._watch("write", number)
        super().write(number, body)

    def _watch(self, kind: str, number: int) -> None:
        if not _held(self.cwd, number):
            self.unlocked.append((kind, number))


def _append(line: str) -> Any:
    return lambda body: body if line in body else f"{body}{line}\n"


def test_lock_path_is_shared_by_the_repository(cwd: Path) -> None:
    assert _lock_path(cwd, 7) == cwd.resolve() / ".git" / "ndf" / "acceptance-7.lock"


def test_rewrite_reads_once_and_writes_nothing_when_the_body_does_not_change(cwd: Path) -> None:
    store = Store(bodies={7: "本文\n"})
    got = issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, lambda body: body)
    assert got == issue_body.Rewritten("本文\n", False, True)
    assert store.calls == [("read", 7)]


def test_rewrite_patches_a_changed_body_without_reading_it_again(cwd: Path) -> None:
    store = Store(bodies={7: "本文\n"})
    got = issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, _append("追記"))
    assert got == issue_body.Rewritten("本文\n追記\n", True, True)
    assert store.calls == [("read", 7), ("write", 7)]


def test_rewrite_reads_and_patches_only_while_holding_the_lock_of_the_number(cwd: Path) -> None:
    # ロックの外で読んだ本文を PATCH すると、並行する書き手のセクションが消える
    store = Watched(bodies={7: "本文\n"}, cwd=cwd)
    issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, _append("確認"), verify=lambda body: "確認" in body)
    assert [kind for kind, _ in store.calls] == ["read", "write", "read"] and store.unlocked == []
    assert not _held(cwd, 7)


def test_rewrite_does_not_write_a_body_that_is_already_settled(cwd: Path) -> None:
    store = Store(bodies={7: "確認\n"})
    got = issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, lambda body: f"{body}追記", verify=lambda body: "確認" in body, retries=3)
    assert got == issue_body.Rewritten("確認\n", False, True)
    assert store.calls == [("read", 7)]


@dataclass
class Reverted(Store):
    """ロックの外の書き手が、書いた直後の本文を `times` 回だけ元へ戻す保存先。"""

    times: int = 1

    def write(self, number: int, body: str) -> None:
        super().write(number, body)
        if self.times:
            self.times -= 1
            self.bodies[number] = "他の書き手\n"


@pytest.mark.parametrize(("times", "settled", "writes"), [(1, True, 2), (3, True, 4), (4, False, 4)])
def test_rewrite_reapplies_to_the_read_body_up_to_the_limit(cwd: Path, times: int, settled: bool, writes: int) -> None:
    store = Reverted(bodies={7: "本文\n"}, times=times)
    got = issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, _append("確認"), verify=lambda body: "確認" in body, retries=3)
    assert (got.wrote, got.settled) == (True, settled)
    assert [kind for kind, _ in store.calls].count("write") == writes
    assert got.body == ("他の書き手\n確認\n" if settled else "他の書き手\n")


def test_rewrite_is_not_settled_when_the_change_cannot_satisfy_the_check(cwd: Path) -> None:
    store = Store(bodies={7: "本文\n"})
    got = issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, lambda body: body, verify=lambda body: False, retries=3)
    assert got == issue_body.Rewritten("本文\n", False, False)
    assert store.calls == [("read", 7)]


def test_rewrite_leaves_a_busy_lock_to_the_caller(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(issue_body.limits, "seconds", lambda name: 0)
    path = _lock_path(cwd, 7)
    path.parent.mkdir(parents=True)
    store = Store()
    with FileLock(str(path)), pytest.raises(Timeout):
        issue_body.rewrite(store, issue_body.BodyLocks(cwd), 7, _append("追記"))
    assert store.calls == []


def test_the_three_writers_take_the_same_lock_and_keep_each_others_sections(
    cwd: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # 確認の記録・進捗記録・クリーンアップの記録が同じ本文を書き換えても、どの内容も残る
    store = Watched(bodies={1: "## 受け入れ条件\n\n- [ ] 条件\n"}, cwd=cwd)
    monkeypatch.setattr(acceptance, "read_body", lambda _cwd, n: store.read(n))
    monkeypatch.setattr(acceptance, "write_body", lambda _cwd, n, body: store.write(n, body))
    stages = [SimpleNamespace(name="クリーンアップ")]
    view: Any = SimpleNamespace(rows={}, snap=SimpleNamespace(mode="standard", stages=stages), context=lambda: {"issues": [1]})
    assert records.sync_records(view, cwd, lambda _: store)[1] == []
    record = SimpleNamespace(cwd=cwd, issue=1, pre_merge=["1=pytest"], post_release=[])
    assert acceptance._record(record)["status"] == "ok"  # type: ignore[arg-type]
    assert cleanup_stage.record_cleanup_stage(Hub(store), [10], cwd)[0]["result"] == "recorded"  # type: ignore[arg-type]
    assert store.unlocked == []
    assert [kind for kind, _ in store.calls].count("write") == 3
    for kept in ("## 受け入れ条件\n", records.ISSUE_SECTION, "- [x] クリーンアップ", "| 1 | マージ前 | pytest |"):
        assert kept in store.bodies[1]


@pytest.mark.parametrize("cause", ["busy", "unusable"])
@pytest.mark.parametrize("writer", ["progress", "cleanup"])
def test_an_unavailable_lock_is_reported_per_target_without_touching_the_body(
    cwd: Path, monkeypatch: pytest.MonkeyPatch, writer: str, cause: str
) -> None:
    monkeypatch.setattr(issue_body.limits, "seconds", lambda name: 0)
    path = _lock_path(cwd, 1)
    store = Store(bodies={1: PROGRESS})
    if cause == "busy":
        path.parent.mkdir(parents=True)
        held: Any = FileLock(str(path))
    else:
        path.parent.write_text("ディレクトリを作れない")
        held = nullcontext()
    with held:
        if writer == "progress":
            errors = records.sync_records(_view([1]), cwd, lambda _: store)[1]
            assert len(errors) == 1 and errors[0].startswith("issue #1 の進捗記録を書けない: ")
        else:
            got = cleanup_stage.record_cleanup_stage(Hub(store), [10], cwd)  # type: ignore[arg-type]
            assert got[0]["result"] == "failed"
    assert store.calls == []
