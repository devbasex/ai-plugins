"""`ndf document-rebuild run` の起点・差分・worker の終わり方・記録の確かめ（#4337 の条件 3〜5・10〜13、I1〜I7）。

git と `ndf worker run` は偽物に替える。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import git
from ndf.contracts import causes
from ndf.domains.crossreview import finish_decide
from ndf.domains.docrebuild import cli, drive
from ndf.domains.docrebuild.select_decide import Target

HEAD = "a" * 40
BASE_SHA = "b" * 40
LOCAL_SHA = "c" * 40
DESIGN = "issues/issue-4337-design.md"
OTHER_DESIGN = "issues/issue-4000-design.md"
REQ = "issues/issue-4337-requirements.md"
PLAN = "issues/issue-4337-plan.md"
OMITTED_PLAN = "# 計画\n\n省いた文書: 設計書（課題 1・公開インタフェース 変わらない）\n\n## 修正対象\n"


class FakeGit:
    """git の偽物。参照ごとの SHA・登録済みのリモート・差分の結果を持ち、打った引数を残す。"""

    def __init__(
        self,
        refs: Mapping[str, str],
        remotes: Sequence[str] = ("origin",),
        diff: Sequence[str] = (),
        diff_code: int = 0,
    ) -> None:
        self.refs = dict(refs)
        self.remotes = list(remotes)
        self.diff = list(diff)
        self.diff_code = diff_code
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: Optional[Path]) -> git.Completed:
        self.calls.append(args)
        if args[0] == "rev-parse":
            ref = args[-1].removesuffix("^{commit}")
            sha = self.refs.get(ref)
            return git.Completed(0, sha + "\n", "") if sha else git.Completed(1, "", "")
        if args == ["remote"]:
            return git.Completed(0, "".join(f"{r}\n" for r in self.remotes), "")
        if args[0] == "diff":
            if self.diff_code:
                return git.Completed(self.diff_code, "", "fatal: no merge base\n")
            return git.Completed(0, "".join(f"{p}\0" for p in self.diff), "")
        raise AssertionError(f"想定していない git: {args}")

    def git_of(self, cwd: Path) -> git.Git:
        return git.Git(cwd, self)


def _write(root: Path, rel: str, text: str = "# 文書\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _args(cwd: Path, *, base: str = "main", remote: str = "origin", issues=(4337,), kinds=("design",)) -> drive.RunArgs:
    return drive.RunArgs(base, remote, tuple(issues), tuple(kinds), cwd / "state", cwd)


def _worker_out(status: str = "ok", **extra: Any) -> dict[str, Any]:
    out = {
        "tool": "worker",
        "status": status,
        "summary": "終えた",
        "items": [{"kind": "session", "session": "s"}],
        "metrics": {"attempts": 1},
    }
    out.update(extra)
    return out


def _reader(doc: str, verdict: str = "pass", checks: Optional[list] = None, reason: Optional[str] = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"doc": doc, "verdict": verdict}
    rec["checks"] = (
        checks if checks is not None else ([{"verdict": verdict, "restatements": ["要点"]}] if verdict in ("pass", "fail") else [])
    )
    if reason is not None:
        rec["reason"] = reason
    return rec


class FakeWorker:
    """`ndf worker run` の偽物。起動指示を読み、結果ファイルを書き、標準出力に結果 JSON を出す。"""

    def __init__(self, report: Optional[dict[str, Any]] = None, out: Optional[dict[str, Any]] = None, stdout: Optional[str] = None) -> None:
        self.report = report
        self.out = out if out is not None else _worker_out()
        self.stdout = stdout
        self.argv: list[list[str]] = []
        self.prompt = ""

    def __call__(self, argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.argv.append(argv)
        self.prompt = Path(argv[argv.index("--prompt-file") + 1]).read_text(encoding="utf-8")
        if self.report is not None:
            Path(argv[argv.index("--result-file") + 1]).write_text(json.dumps(self.report, ensure_ascii=False), encoding="utf-8")
        stdout = self.stdout if self.stdout is not None else json.dumps(self.out, ensure_ascii=False) + "\n"
        return subprocess.CompletedProcess(argv, 0, stdout, "")


def _report(items: Sequence[dict[str, Any]] = (), readers: Optional[list] = None) -> dict[str, Any]:
    metrics = {"readers": readers if readers is not None else [_reader(DESIGN)]}
    return {"tool": "worker", "status": "ok", "summary": "組み直した", "items": list(items), "metrics": metrics}


def _run(cwd: Path, fake_git: FakeGit, worker: FakeWorker, **kw: Any) -> dict[str, Any]:
    return drive.drive(_args(cwd, **kw), git_of=fake_git.git_of, runner=worker)


# --- 起点と差分 ---


def test_start_point_uses_the_remote_tracking_branch(tmp_path: Path) -> None:
    fake = FakeGit({"refs/remotes/origin/main": BASE_SHA, "refs/heads/main": LOCAL_SHA})
    assert drive.start_point(fake.git_of(tmp_path), "main", "origin") == ("refs/remotes/origin/main", BASE_SHA)
    assert not any(c == ["remote"] for c in fake.calls)


def test_start_point_follows_the_given_names(tmp_path: Path) -> None:
    """条件 5: ベースブランチが `trunk`、リモートが `up` でも同じ規則で起点を決める。"""
    fake = FakeGit({"refs/remotes/up/trunk": BASE_SHA}, remotes=("up",))
    assert drive.start_point(fake.git_of(tmp_path), "trunk", "up") == ("refs/remotes/up/trunk", BASE_SHA)


@pytest.mark.parametrize("remotes", [("origin",), ()])
def test_start_point_falls_back_to_the_local_branch(tmp_path: Path, remotes: tuple[str, ...]) -> None:
    """登録済みでリモート追跡が無いときと、リモートが 1 つも無いときは手元の参照を使う。"""
    fake = FakeGit({"refs/heads/main": LOCAL_SHA}, remotes=remotes)
    assert drive.start_point(fake.git_of(tmp_path), "main", "origin") == ("refs/heads/main", LOCAL_SHA)


def test_start_point_stops_when_only_other_remotes_exist(tmp_path: Path) -> None:
    fake = FakeGit({"refs/heads/main": LOCAL_SHA}, remotes=("upstream",))
    with pytest.raises(drive.Precondition) as caught:
        drive.start_point(fake.git_of(tmp_path), "main", "origin")
    assert "origin" in caught.value.detail and ".ndf/worktree.json の remote" in caught.value.detail


def test_start_point_stops_when_neither_ref_is_readable(tmp_path: Path) -> None:
    """条件 11: 起点の参照がどちらも読めなければ、参照の名前を示して止まる。"""
    fake = FakeGit({}, remotes=("origin",))
    with pytest.raises(drive.Precondition) as caught:
        drive.start_point(fake.git_of(tmp_path), "main", "origin")
    assert "refs/remotes/origin/main" in caught.value.detail and "refs/heads/main" in caught.value.detail


def test_changed_paths_reads_nul_separated_names(tmp_path: Path) -> None:
    fake = FakeGit({}, diff=["docs/日本語 の 文書.md", DESIGN])
    assert drive.diff_paths(fake.git_of(tmp_path), BASE_SHA, HEAD) == ["docs/日本語 の 文書.md", DESIGN]
    diff = next(c for c in fake.calls if c[0] == "diff")
    assert "--diff-filter=d" in diff and "-z" in diff and f"{BASE_SHA}...{HEAD}" in diff


def test_changed_paths_empty(tmp_path: Path) -> None:
    assert drive.diff_paths(FakeGit({}).git_of(tmp_path), BASE_SHA, HEAD) == []


def test_changed_paths_stops_without_a_merge_base(tmp_path: Path) -> None:
    fake = FakeGit({}, diff_code=128)
    with pytest.raises(drive.Precondition):
        drive.diff_paths(fake.git_of(tmp_path), BASE_SHA, HEAD)
    assert not any(c[0] == "merge-base" for c in fake.calls)


# --- 作業場所の確かめ ---


def test_inspect_sorts_regular_files_links_and_unreadable_plans(tmp_path: Path) -> None:
    _write(tmp_path, DESIGN)
    _write(tmp_path, "docs/real.md")
    os.symlink(tmp_path / "docs/real.md", tmp_path / "docs/link.md")
    (tmp_path / "issues/issue-1-plan.md").write_bytes(b"\xff\xfe")
    _write(tmp_path, "issues/real-plan.md", OMITTED_PLAN)
    os.symlink(tmp_path / "issues/real-plan.md", tmp_path / "issues/issue-2-plan.md")
    _write(tmp_path, PLAN, OMITTED_PLAN)
    found = [
        Target(DESIGN, "design"),
        Target("docs/link.md", "manuscript"),
        Target("docs/gone.md", "manuscript"),
        Target("issues/issue-1-plan.md", "plan"),
        Target("issues/issue-2-plan.md", "plan"),
        Target(PLAN, "plan"),
        Target("issues/issue-3-plan.md", "plan"),
    ]
    seen = drive.inspect(tmp_path, found)
    assert seen.regular == {DESIGN, "issues/issue-1-plan.md", PLAN}
    assert "issues/issue-3-plan.md" not in seen.present and "docs/link.md" in seen.present
    assert seen.excluded["docs/link.md"] == "通常ファイルでない"
    assert seen.excluded["issues/issue-2-plan.md"] == "通常ファイルでない"
    assert seen.excluded["docs/gone.md"] == "作業場所に無い"
    assert seen.plans == {PLAN: OMITTED_PLAN}
    assert seen.unreadable == {"issues/issue-1-plan.md": "UTF-8 で読めない"}


def test_requirements_follow_every_issue_in_the_design_name(tmp_path: Path) -> None:
    """課題 7 だけを進めても、`issue-7-8-design.md` には課題 7 と 8 の要求仕様が組になり、別の設計書の要求仕様は混ざらない。"""
    for rel in ("issues/issue-7-requirements.md", "issues/issue-8-requirements.md", "issues/issue-9-requirements.md"):
        _write(tmp_path, rel)
    targets = [Target("issues/issue-7-8-design.md", "design"), Target("issues/issue-9-design.md", "design"), Target(PLAN, "plan")]
    assert drive.requirements_of(tmp_path, targets) == {
        "issues/issue-7-8-design.md": ["issues/issue-7-requirements.md", "issues/issue-8-requirements.md"],
        "issues/issue-9-design.md": ["issues/issue-9-requirements.md"],
    }


# --- 足すべき内容と確かめの記録 ---

TARGETS = (Target(DESIGN, "design"), Target(PLAN, "plan"), Target("docs/a.md", "manuscript"))


def test_added_items_renumber_ids_and_keep_every_item() -> None:
    """I6: worker の `id` は使わず、重なる `id`・`reader-1`・`id` 無しも全件を残す。"""
    raw = [
        {"id": "R1", "text": "a", "doc": DESIGN},
        {"id": "R1", "text": "b", "doc": PLAN},
        {"id": "reader-1", "text": "c", "doc": DESIGN},
        {"text": "d", "doc": DESIGN},
    ]
    got = drive.added_items(raw, TARGETS)
    assert [i["id"] for i in got] == ["add-1", "add-2", "add-3", "add-4"]
    assert [i["text"] for i in got] == ["a", "b", "c", "d"]


@pytest.mark.parametrize(
    "raw",
    [
        [{"doc": DESIGN}],
        [{"text": " ", "doc": DESIGN}],
        [{"text": "a", "doc": "docs/other.md"}],
        [{"text": "a", "doc": [DESIGN]}],
        [{"text": "a", "doc": {"path": DESIGN}}],
        ["a"],
        {"text": "a"},
    ],
)
def test_added_items_reject_bad_shapes(raw: Any) -> None:
    """I3: `text` が無い・`doc` が文字列でない・`doc` が対象に無い項目は `BadReport`。"""
    with pytest.raises(drive.BadReport):
        drive.added_items(raw, TARGETS)


FAIL_THEN_PASS = [{"verdict": "fail", "restatements": ["x"], "reason": "質問 1 が誤読された"}, {"verdict": "pass", "restatements": ["y"]}]


@pytest.mark.parametrize(
    "readers",
    [
        [_reader(DESIGN), _reader(PLAN, "self", reason="実装計画"), _reader("docs/a.md", "self", reason="AI への手順書")],
        [
            _reader(DESIGN, "not_run", reason="Tool が無い"),
            _reader(PLAN, "self", reason="実装計画"),
            _reader("docs/a.md", "not_run", reason="Tool が無い"),
        ],
        [_reader(DESIGN, "pass", FAIL_THEN_PASS), _reader(PLAN, "self", reason="実装計画"), _reader("docs/a.md")],
        [
            _reader(DESIGN, "fail", [FAIL_THEN_PASS[0]], reason="2 回目を立てられない"),
            _reader(PLAN, "self", reason="実装計画"),
            _reader("docs/a.md"),
        ],
        [
            _reader(DESIGN, "fail", [FAIL_THEN_PASS[0], {"verdict": "fail", "restatements": ["z"], "reason": "まだ誤読"}]),
            _reader(PLAN, "self", reason="実装計画"),
            _reader("docs/a.md"),
            {"doc": "docs/outside.md", "verdict": "whatever"},
            "形の違う記録",
        ],
    ],
)
def test_reader_gaps_accept_complete_records(readers: list) -> None:
    assert drive.reader_gaps(readers, TARGETS) == []


def _gap_for(readers: list, target: Target = Target(DESIGN, "design")) -> list[dict[str, str]]:
    return drive.reader_gaps(readers, (target,))


@pytest.mark.parametrize(
    ("readers", "target"),
    [
        ([], Target(DESIGN, "design")),
        ([_reader(DESIGN), _reader(DESIGN)], Target(DESIGN, "design")),
        ([_reader(DESIGN, checks=[{"verdict": "pass", "restatements": []}])], Target(DESIGN, "design")),
        ([_reader(DESIGN, checks=[{"verdict": "pass"}])], Target(DESIGN, "design")),
        ([_reader(PLAN)], Target(PLAN, "plan")),
        ([_reader(PLAN, "not_run", reason="x")], Target(PLAN, "plan")),
        ([_reader(DESIGN, "self", reason="設計書")], Target(DESIGN, "design")),
        (
            [_reader(DESIGN, "pass", [{"verdict": "pass", "restatements": ["a"]}, {"verdict": "pass", "restatements": ["b"]}])],
            Target(DESIGN, "design"),
        ),
        ([_reader(DESIGN, "not_run")], Target(DESIGN, "design")),
        ([_reader("docs/a.md", "self")], Target("docs/a.md", "manuscript")),
        ([_reader(DESIGN, "not_run", checks=[{"verdict": "pass", "restatements": ["a"]}], reason="x")], Target(DESIGN, "design")),
        ([_reader(DESIGN, "fail", [{"verdict": "fail", "restatements": ["a"], "reason": "r"}])], Target(DESIGN, "design")),
        ([_reader(DESIGN, "pass", [{"verdict": "fail", "restatements": ["a"]}])], Target(DESIGN, "design")),
        ([_reader(DESIGN, "unknown", [])], Target(DESIGN, "design")),
        ([_reader(DESIGN, "pass", [])], Target(DESIGN, "design")),
    ],
)
def test_reader_gaps_report_each_broken_record(readers: list, target: Target) -> None:
    """I4: 記録の欠けは文書ごとに `gaps` の 1 件になる。"""
    gaps = _gap_for(readers, target)
    assert len(gaps) == 1 and gaps[0]["kind"] == "reader" and gaps[0]["name"] == target.path and gaps[0]["reason"]


def test_reader_gaps_when_readers_is_not_a_list() -> None:
    assert [g["name"] for g in drive.reader_gaps(None, TARGETS)] == [t.path for t in TARGETS]


def test_reader_items_turn_failures_into_items_and_warnings() -> None:
    """I5: `fail` の文書は `reader-1` の項目と `warnings` の 1 件になる。"""
    fail = _reader(DESIGN, "fail", [FAIL_THEN_PASS[0], {"verdict": "fail", "restatements": ["z"], "reason": "質問 2 が誤読された"}])
    items, warnings = drive.reader_items([_reader("docs/a.md"), fail])
    assert items == [{"id": "reader-1", "text": warnings[0], "doc": DESIGN}]
    assert DESIGN in warnings[0] and "質問 2 が誤読された" in warnings[0]
    assert drive.reader_items([_reader(DESIGN)]) == ([], [])


# --- 1 回の実行 ---


def test_drive_hands_only_this_issues_design(tmp_path: Path) -> None:
    """条件 3: スプリントブランチの別の課題の設計書は渡らず、この課題の設計書だけが渡る。"""
    _write(tmp_path, DESIGN)
    _write(tmp_path, OTHER_DESIGN)
    _write(tmp_path, REQ)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[OTHER_DESIGN, DESIGN, REQ])
    worker = FakeWorker(_report())
    got = _run(tmp_path, fake, worker)
    assert got["status"] == "ok", got
    assert got["metrics"]["targets"] == [DESIGN]
    assert f"`{DESIGN}`" in worker.prompt and OTHER_DESIGN not in worker.prompt
    assert f"`{REQ}`" in worker.prompt


def test_drive_pairs_requirements_with_each_design(tmp_path: Path) -> None:
    """工程の課題が 7 だけでも、`issue-7-8-design.md` の行に課題 8 の要求仕様が添えられる。"""
    design = "issues/issue-7-8-design.md"
    for rel in (design, "issues/issue-7-requirements.md", "issues/issue-8-requirements.md"):
        _write(tmp_path, rel)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[design])
    worker = FakeWorker(_report(readers=[_reader(design)]))
    assert _run(tmp_path, fake, worker, issues=(7,))["status"] == "ok"
    line = next(ln for ln in worker.prompt.splitlines() if "組になる要求仕様のコピー:" in ln)
    assert "`issues/issue-7-requirements.md`" in line and "`issues/issue-8-requirements.md`" in line


@pytest.mark.parametrize(("base", "remote"), [("main", "origin"), ("trunk", "up")])
def test_drive_on_the_sprint_branch_itself(tmp_path: Path, base: str, remote: str) -> None:
    """条件 4・5: 作業場所がスプリントブランチそのものでも、起点はベースブランチなので設計書が対象に入る。"""
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, f"refs/remotes/{remote}/{base}": BASE_SHA, "refs/heads/sprint": HEAD}, remotes=(remote,), diff=[DESIGN])
    got = _run(tmp_path, fake, FakeWorker(_report()), base=base, remote=remote)
    assert got["status"] == "ok" and got["metrics"]["targets"] == [DESIGN]
    diff = next(c for c in fake.calls if c[0] == "diff")
    assert f"{BASE_SHA}...{HEAD}" in diff


def test_drive_without_targets_does_not_launch_the_worker(tmp_path: Path) -> None:
    """条件 10: 対象が 0 件なら worker を起動せず `ok` で、要約から対象が無かったことが分かる。"""
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=["src/a.py"])
    worker = FakeWorker()
    got = _run(tmp_path, fake, worker)
    assert got["status"] == "ok" and got["items"] == [] and got["metrics"] == {"targets": []}
    assert "組み直す文書が無い" in got["summary"] and "refs/remotes/origin/main" in got["summary"]
    assert worker.argv == []


def test_drive_reports_skipped_links(tmp_path: Path) -> None:
    """I1: 差分の `.md` のリンクは対象にならず、`warnings` に出る。"""
    _write(tmp_path, "docs/real.md")
    os.symlink(tmp_path / "docs/real.md", tmp_path / "docs/link.md")
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=["docs/link.md"])
    got = _run(tmp_path, fake, FakeWorker(), kinds=("design", "plan", "manuscript"))
    assert got["status"] == "ok" and got["warnings"] == ["docs/link.md: 通常ファイルでない"]


def test_drive_picks_an_omitted_plan_with_an_empty_diff(tmp_path: Path) -> None:
    """決定 9: 設計 PR をマージコミットでマージして差分が空でも、省いた行の実装計画を組み直す（設計の工程では選ばない）。"""
    _write(tmp_path, PLAN, OMITTED_PLAN)
    refs = {"HEAD": HEAD, "refs/remotes/origin/main": HEAD}
    report = _report(readers=[_reader(PLAN, "self", reason="実装計画")])
    got = _run(tmp_path, FakeGit(refs), FakeWorker(report), kinds=("design", "plan"))
    assert got["status"] == "ok" and got["metrics"]["targets"] == [PLAN]
    worker = FakeWorker()
    assert _run(tmp_path, FakeGit(refs), worker, kinds=("design",))["metrics"]["targets"] == []
    assert worker.argv == []


@pytest.mark.parametrize(
    "fake",
    [
        FakeGit({"HEAD": HEAD}, remotes=("origin",)),
        FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff_code=128),
        FakeGit({}),
    ],
)
def test_drive_stops_when_the_start_cannot_be_decided(tmp_path: Path, fake: FakeGit) -> None:
    """条件 11・I2: 起点を決められなければ 0 件とみなさず `stopped`（precondition）。"""
    worker = FakeWorker()
    got = _run(tmp_path, fake, worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert worker.argv == []


def test_drive_launches_only_git_reads_and_worker_run(tmp_path: Path) -> None:
    """条件 13: 起動する子は git の読み取りと `ndf worker run` だけで、push も commit も無い。起動指示はコミットまでと書く。"""
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN])
    worker = FakeWorker(_report())
    _run(tmp_path, fake, worker)
    assert {c[0] for c in fake.calls} <= {"rev-parse", "remote", "diff"}
    assert len(worker.argv) == 1 and worker.argv[0][1:4] == ["worker", "run", "--kind"]
    assert "push も Pull Request の作成・更新もしない" in worker.prompt


@pytest.mark.parametrize(
    ("out", "kind"),
    [
        (
            _worker_out("stopped", cause={"kind": "incomplete", "detail": "残した"}, gaps=[{"kind": "work", "name": "x", "reason": "y"}]),
            "incomplete",
        ),
        (_worker_out("stopped", cause={"kind": "work_stopped", "detail": "止めた"}), "work_stopped"),
        (_worker_out("gate", routes={"retry": None}), "work_stopped"),
    ],
)
def test_drive_does_not_read_the_result_file_unless_ok(tmp_path: Path, out: dict[str, Any], kind: str) -> None:
    """I7: worker が `stopped`・`gate` なら結果ファイルを読まずに `stopped`。`incomplete` は worker の `gaps` を載せる。"""
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN])
    got = _run(tmp_path, fake, FakeWorker({"broken": True}, out))
    assert got["status"] == "stopped" and got["cause"]["kind"] == kind
    assert ("gaps" in got) is (kind == "incomplete")


def test_drive_reads_items_from_the_result_file_not_stdout(tmp_path: Path) -> None:
    """I7・条件 12: `ok` なら標準出力のセッション情報でなく結果ファイルの `items` を足すべき内容にする。"""
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN])
    got = _run(tmp_path, fake, FakeWorker(_report([{"id": "R1", "text": "決定 2 の根拠が無い", "doc": DESIGN}])))
    assert got["items"] == [{"id": "add-1", "text": "決定 2 の根拠が無い", "doc": DESIGN}]


@pytest.mark.parametrize(
    ("worker", "kind"),
    [
        (FakeWorker(_report([{"text": "a", "doc": "docs/other.md"}], readers=[])), "bad_report"),
        (FakeWorker(_report(readers=[])), "incomplete"),
        (FakeWorker(_report(readers=[_reader(DESIGN), _reader(DESIGN)])), "incomplete"),
        (FakeWorker(None), "bad_report"),
        (FakeWorker(_report(), stdout="壊れた出力\n"), "bad_report"),
    ],
)
def test_drive_stops_on_bad_reports(tmp_path: Path, worker: FakeWorker, kind: str) -> None:
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN])
    got = _run(tmp_path, fake, worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == kind
    if kind == "incomplete":
        assert got["gaps"][0]["name"] == DESIGN


def test_drive_keeps_failures_and_drops_records_outside_the_targets(tmp_path: Path) -> None:
    """I5: 不合格の文書は `ok` のまま `items` と `warnings` に出る。対象の外の記録はどこにも出ず、件数だけ `warnings` に出る。"""
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN])
    fail = _reader(DESIGN, "fail", [FAIL_THEN_PASS[0], {"verdict": "fail", "restatements": ["z"], "reason": "質問 2 が誤読された"}])
    outside = [
        _reader("docs/outside.md", "fail", [{"verdict": "fail", "restatements": ["q"], "reason": "外"}]),
        {"doc": "docs/x.md", "verdict": "?"},
    ]
    got = _run(tmp_path, fake, FakeWorker(_report(readers=[fail, *outside])))
    assert got["status"] == "ok"
    assert [i["id"] for i in got["items"]] == ["reader-1"]
    assert [r["doc"] for r in got["metrics"]["readers"]] == [DESIGN]
    assert got["warnings"][-1] == "対象の外の文書の記録を 2 件外した"
    assert all("docs/outside.md" not in w for w in got["warnings"])
    written, code = cli.adopt(got)
    assert written is got and code == 0


@pytest.mark.parametrize("reason", ["", None, "読めた"])
def test_drive_drops_the_reason_of_a_passed_check(tmp_path: Path, reason: Any) -> None:
    """合格の確かめの `reason` は出力に載せない。空・null でも結果のスキーマに通る。"""
    _write(tmp_path, DESIGN)
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN])
    passed = _reader(DESIGN, checks=[{"verdict": "pass", "restatements": ["要点"], "reason": reason}])
    got = _run(tmp_path, fake, FakeWorker(_report(readers=[passed])))
    assert got["status"] == "ok" and got["metrics"]["readers"][0]["checks"] == [{"verdict": "pass", "restatements": ["要点"]}]
    written, code = cli.adopt(got)
    assert written is got and code == 0


# --- 止まる理由の種類と説明（drive の例外の受け方を変えても同じに保つ） ---


class RaisingGit(FakeGit):
    """`fail_on` の git（引数の先頭）を打つと、git を起動できないときと同じ `GitError` を投げる偽物。"""

    def __init__(self, refs: Mapping[str, str], fail_on: str, **kw: Any) -> None:
        super().__init__(refs, **kw)
        self.fail_on = fail_on

    def __call__(self, args: list[str], cwd: Optional[Path]) -> git.Completed:
        if args[0] == self.fail_on:
            self.calls.append(args)
            raise git.GitError("git を起動できない: 無い")
        return super().__call__(args, cwd)


class BrokenRemoteGit(FakeGit):
    """`git remote` だけ終了コード 2 で失敗する偽物。"""

    def __call__(self, args: list[str], cwd: Optional[Path]) -> git.Completed:
        if args == ["remote"]:
            self.calls.append(args)
            return git.Completed(2, "", "fatal: 壊れた設定\n")
        return super().__call__(args, cwd)


@pytest.mark.parametrize("cls", [drive.Precondition, drive.BadReport])
def test_stop_exceptions_carry_their_detail(cls: type[Exception]) -> None:
    """止まる理由の例外は説明を `detail` と `str()` の両方で持ち、互いに別の種類として受けられる。"""
    exc = cls("説明")
    assert exc.detail == "説明" and str(exc) == "説明"  # type: ignore[attr-defined]
    assert isinstance(exc, Exception)
    other = drive.BadReport if cls is drive.Precondition else drive.Precondition
    assert not isinstance(exc, other)


@pytest.mark.parametrize(
    ("fake", "detail"),
    [
        (FakeGit({"refs/remotes/origin/main": BASE_SHA}), "HEAD を読めない: "),
        (RaisingGit({"HEAD": HEAD}, "rev-parse"), "HEAD を読めない: git を起動できない: 無い"),
        (
            FakeGit({"HEAD": HEAD}, remotes=("upstream",)),
            f"リモート origin が登録されていない（登録済み: upstream）。{drive.WORKTREE_REMOTE} を直す",
        ),
        (FakeGit({"HEAD": HEAD}), "起点の参照 refs/remotes/origin/main と refs/heads/main をどちらも読めない"),
        (BrokenRemoteGit({"HEAD": HEAD}), "登録済みのリモートを読めない: "),
        (
            FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff_code=128),
            "起点 refs/remotes/origin/main から差分を読めない: 分岐点が無い: fatal: no merge base",
        ),
        (
            FakeGit({"HEAD": HEAD, "refs/heads/main": LOCAL_SHA}, remotes=(), diff_code=1),
            "起点 refs/heads/main から差分を読めない: 差分を読めない（git の終了コード 1）: fatal: no merge base",
        ),
        (RaisingGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, "diff"), "git を打てない: git を起動できない: 無い"),
    ],
)
def test_drive_names_why_the_start_or_diff_failed(tmp_path: Path, fake: FakeGit, detail: str) -> None:
    """起点・差分を決められないときの説明。差分の失敗には起点の参照の名前を頭に付け、包まれない `GitError` は「git を打てない」にする。"""
    worker = FakeWorker()
    got = _run(tmp_path, fake, worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["cause"]["detail"].startswith(detail)
    assert got["summary"] == got["cause"]["detail"]
    assert worker.argv == []


def test_drive_stops_before_diff_when_the_start_fails(tmp_path: Path) -> None:
    """起点を決められなければ差分を読まない。"""
    fake = FakeGit({"HEAD": HEAD}, remotes=("upstream",))
    _run(tmp_path, fake, FakeWorker())
    assert all(c[0] != "diff" for c in fake.calls)


def test_drive_names_the_start_ref_when_nothing_is_chosen(tmp_path: Path) -> None:
    """選んだ文書が無いときの要約は、種類と決めた起点の参照の名前を載せる。"""
    fake = FakeGit({"HEAD": HEAD, "refs/heads/main": LOCAL_SHA}, remotes=())
    got = _run(tmp_path, fake, FakeWorker(), kinds=("design", "plan"))
    assert got["status"] == "ok" and got["items"] == []
    assert got["summary"] == "組み直す文書が無い（種類: design・plan・起点: refs/heads/main）"


def test_drive_diffs_from_the_start_sha_to_head(tmp_path: Path) -> None:
    """差分は起点の SHA から HEAD の SHA までの 3 ドット差分で読む。"""
    fake = FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA})
    _run(tmp_path, fake, FakeWorker())
    diffs = [c for c in fake.calls if c[0] == "diff"]
    assert len(diffs) == 1 and f"{BASE_SHA}...{HEAD}" in diffs[0]


def test_drive_stops_when_the_prompt_template_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """起動指示のテンプレートを読めなければ worker を起動せず `stopped`（precondition）。"""
    _write(tmp_path, DESIGN)
    monkeypatch.setattr(drive, "_root", lambda: tmp_path / "no-root")
    worker = FakeWorker(_report())
    got = _run(tmp_path, FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN]), worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["cause"]["detail"].startswith(f"起動指示のテンプレート {tmp_path / 'no-root' / drive.PROMPT_TEMPLATE} を読めない: ")
    assert worker.argv == []


def test_drive_stops_when_the_state_dir_is_unwritable(tmp_path: Path) -> None:
    """起動指示を保存先へ書けなければ worker を起動せず `stopped`（precondition）。"""
    _write(tmp_path, DESIGN)
    (tmp_path / "state").write_text("ファイル", encoding="utf-8")
    worker = FakeWorker(_report())
    got = _run(tmp_path, FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN]), worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["cause"]["detail"].startswith("起動指示を保存先 ")
    assert worker.argv == []


@pytest.mark.parametrize(
    ("worker", "detail"),
    [
        (FakeWorker(_report(), stdout="壊れた出力\n"), "ndf worker run の標準出力に結果 JSON が無い（終了コード 0）"),
        (FakeWorker(_report(), stdout=""), "ndf worker run の標準出力に結果 JSON が無い（終了コード 0）"),
        (FakeWorker(None), "worker の結果ファイル "),
        (FakeWorker([1, 2]), "worker の結果ファイル "),  # type: ignore[arg-type]
        (FakeWorker(_report([{"text": "a", "doc": "docs/other.md"}])), "items の 0 番目の doc が対象の文書でない"),
    ],
)
def test_drive_names_why_the_report_is_bad(tmp_path: Path, worker: FakeWorker, detail: str) -> None:
    """worker の出力・結果ファイル・足すべき内容の形の誤りは `stopped`（bad_report）で、何が誤りかを説明に書く。"""
    _write(tmp_path, DESIGN)
    got = _run(tmp_path, FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN]), worker)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "bad_report"
    assert got["cause"]["detail"].startswith(detail)


def test_drive_reports_a_non_object_result_file(tmp_path: Path) -> None:
    """結果ファイルの中身がオブジェクトでなければ、そう書いた `stopped`（bad_report）。"""
    _write(tmp_path, DESIGN)
    got = _run(tmp_path, FakeGit({"HEAD": HEAD, "refs/remotes/origin/main": BASE_SHA}, diff=[DESIGN]), FakeWorker([1, 2]))  # type: ignore[arg-type]
    assert got["cause"]["kind"] == "bad_report" and got["cause"]["detail"].endswith("の中身がオブジェクトでない")


# --- エントリポイント ---


def test_adopt_keeps_a_correct_result() -> None:
    ok = {
        "tool": "document-rebuild",
        "status": "ok",
        "summary": "s",
        "items": [{"id": "add-1", "text": "t", "doc": DESIGN}],
        "metrics": {"targets": [DESIGN]},
    }
    assert cli.adopt(ok) == (ok, 0)
    stopped = {
        "tool": "document-rebuild",
        "status": "stopped",
        "summary": "s",
        "items": [],
        "metrics": {},
        "cause": {"kind": "precondition", "detail": "d"},
    }
    assert cli.adopt(stopped) == (stopped, causes.exit_code("precondition"))


@pytest.mark.parametrize(
    ("items", "metrics"),
    [
        ([{"id": "add-1", "text": "t"}], {"targets": [DESIGN]}),
        ([], {"targets": [DESIGN], "readers": [{"doc": DESIGN, "verdict": "maybe", "checks": []}]}),
    ],
)
def test_adopt_replaces_a_wrong_result(items: list, metrics: dict) -> None:
    bad = {"tool": "document-rebuild", "status": "ok", "summary": "s", "items": items, "metrics": metrics}
    out, code = cli.adopt(bad)
    assert out["status"] == "stopped" and out["cause"]["kind"] == "invalid" and out["items"] == []
    assert code == causes.exit_code("invalid")


def _main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, got: dict[str, Any]) -> tuple[int, dict[str, Any], str]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(drive, "drive", lambda args: got)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = cli.main(
            ["run", "--base", "main", "--remote", "origin", "--issue", "4337", "--kind", "design", "--state-dir", "st", "--out", "out.json"]
        )
    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    return code, written, stdout.getvalue().strip().splitlines()[-1]


def test_main_writes_the_same_result_to_out_and_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """条件 12: `--out` の `items` は `id` と `text` を持ち、設計のレビューの読み取り（`as_handed`）が読める。"""
    got = {
        "tool": "document-rebuild",
        "status": "ok",
        "summary": "s",
        "items": [{"id": "add-1", "text": "t", "doc": DESIGN}],
        "metrics": {"targets": [DESIGN]},
    }
    code, written, last = _main(tmp_path, monkeypatch, got)
    assert code == 0 and written == got and json.loads(last) == written
    assert [(h.id, h.text) for h in finish_decide.as_handed(written["items"])] == [("add-1", "t")]


def test_main_writes_the_replacement_to_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = {"tool": "document-rebuild", "status": "ok", "summary": "s", "items": [{"id": "add-1", "text": "t"}], "metrics": {}}
    code, written, last = _main(tmp_path, monkeypatch, bad)
    assert code == causes.exit_code("invalid")
    assert written["status"] == "stopped" and written["items"] == [] and json.loads(last) == written


def test_main_rejects_bad_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", "--base", "main"]) == causes.exit_code("invalid")
    assert cli.main(
        ["run", "--base", "main", "--remote", "o", "--issue", "x", "--kind", "design", "--state-dir", "s", "--out", "o"]
    ) == causes.exit_code("invalid")
    assert cli.main(
        ["run", "--base", "main", "--remote", "o", "--issue", "1", "--kind", "other", "--state-dir", "s", "--out", "o"]
    ) == causes.exit_code("invalid")
