"""課題の題と本文のコピーを私有の形で書き、他の利用者が書けるコピーを使わない（ndf.progress.invoke の `_issue_values`。PR 4307 の r7-3）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sprint_fixture import sprint_repo as _sprint_repo

from ndf.adapters import github
from ndf.contracts import result, worker_paths
from ndf.core import durable, route
from ndf.core.plan import runner as plan_runner
from ndf.progress import invoke, issue_values

PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"]}},
}


def _stage_run_of(name: str, repo: Path, issues: list[int]) -> invoke.StageRun:
    inputs = {"issues": issues, "worktree": str(repo), "mode": "light"}
    return invoke.StageRun(invoke.PLAN, name, {k: inputs[k] for k in ("issues", "worktree", "mode")}, (), "none", name)


def test_issue_copies_others_can_write_are_not_used_for_the_plan(tmp_path, monkeypatch):
    # PR 4307 の r7-3: 共有の保存先に別の利用者が置ける題と本文のコピーを、作成者の権限を確かめずに worker へ渡さない
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: pytest.fail("置かれたコピーで読み直して上書きしない"))
    work = tmp_path / "w"
    work.mkdir()
    for name, text in ((issue_values.ISSUE_TITLE_FILE, '{"title": "差し替えた題"}'), (issue_values.ISSUE_BODY_FILE, "差し替えた本文")):
        (work / name.format(number=3092)).write_text(text, encoding="utf-8")
        (work / name.format(number=3092)).chmod(0o666)
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        issue_values.issue_values(_stage_run_of("impl", repo, [3092]).inputs, work, repo)
    assert e.value.kind == "foreign"


def test_issue_copies_are_written_without_write_access_for_others(tmp_path, monkeypatch):
    # PR 4307 の r7-3: コピーはグループとほかの利用者が書き込めない形で公開する（次の描画が私有のコピーとして読める）
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    work.mkdir()
    before = os.umask(0o000)  # umask によらず書き込み権限を外す
    try:
        got = issue_values.issue_values(_stage_run_of("impl", repo, [3092]).inputs, work, repo)
    finally:
        os.umask(before)
    for name in (issue_values.ISSUE_TITLE_FILE, issue_values.ISSUE_BODY_FILE):
        assert (work / name.format(number=3092)).stat().st_mode & 0o022 == 0
    assert Path(got["body_file"]).read_text(encoding="utf-8") == "#3092 の本文"


def test_issue_body_in_a_directory_others_can_rename_in_is_not_handed_to_the_pr_step(tmp_path, monkeypatch):
    # PR 4307 の r8-3: `gh pr create --body-file` は名前を開き直すので、ほかの利用者が名前を差し替えられる保存先のコピーを渡さない
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    work.mkdir()
    work.chmod(0o777)  # sticky bit の無い共有の保存先
    try:
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            issue_values.issue_values(_stage_run_of("impl", repo, [3092]).inputs, work, repo)
    finally:
        work.chmod(0o700)
    assert e.value.kind == "shared"
    work.chmod(0o1777)  # sticky bit があれば、呼んだ利用者のコピーはほかの利用者に消せない
    try:
        got = issue_values.issue_values(_stage_run_of("impl", repo, [3092]).inputs, work, repo)
    finally:
        work.chmod(0o700)
    assert Path(got["body_file"]).read_text(encoding="utf-8") == "#3092 の本文"


def _reading(monkeypatch) -> list[tuple[int, str | None]]:
    """課題の題と本文の読み取りを差し替え、読んだ（番号, リポジトリ）を返す。"""
    read: list[tuple[int, str | None]] = []

    def fake(location, n, repo=None):
        read.append((n, repo))
        return f"{repo} #{n} の題", f"{repo} #{n} の本文"

    monkeypatch.setattr(issue_values, "issue_text", fake)
    return read


def test_the_given_issue_and_repository_are_copied_and_reused(tmp_path, monkeypatch):
    read = _reading(monkeypatch)
    inputs, work = {"issues": [3, 4]}, tmp_path / "w"
    for _ in range(2):
        got = issue_values.issue_values(inputs, work, tmp_path, number=4, repo="o/r")
    assert got["issue"] == "4" and got["title"] == "o/r #4 の題" and read == [(4, "o/r")]
    assert json.loads((work / "issue-4-title.json").read_text()) == {"title": "o/r #4 の題", "repo": "o/r"}


def test_a_copy_of_the_same_number_in_another_repository_is_not_reused(tmp_path, monkeypatch):
    read = _reading(monkeypatch)
    work = tmp_path / "w"
    issue_values.issue_values({"issues": [3]}, work, tmp_path)
    got = issue_values.issue_values({"issues": [3]}, work, tmp_path, repo="o/r")
    assert read == [(3, None), (3, "o/r")] and Path(got["body_file"]).read_text(encoding="utf-8") == "o/r #3 の本文"


def test_a_reread_stopped_in_the_middle_leaves_no_title_copy_for_the_other_body(tmp_path, monkeypatch):
    # 題のコピーを消してから本文を書く。途中で止まっても、前のリポジトリの題が残って新しい本文と組にならない
    _reading(monkeypatch)
    work = tmp_path / "w"
    issue_values.issue_values({"issues": [3]}, work, tmp_path)
    held = worker_paths.write_file

    def fail_on_title(wd, name, text, **kw):
        if name.endswith("-title.json"):
            raise OSError("書けない")
        held(wd, name, text, **kw)

    monkeypatch.setattr(worker_paths, "write_file", fail_on_title)
    with pytest.raises(OSError):
        issue_values.issue_values({"issues": [3]}, work, tmp_path, repo="o/r")
    assert not (work / "issue-3-title.json").exists()


def test_an_untrusted_author_of_the_given_issue_gives_no_values(tmp_path, monkeypatch):
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: None)
    assert issue_values.issue_values({"issues": [3]}, tmp_path / "w", tmp_path, number=5, repo="o/r") is None
    assert list((tmp_path / "w").iterdir()) == []


def test_the_given_repository_is_read_and_its_permissions_are_asked(monkeypatch):
    seen: list[str] = []

    class Hub:
        def __init__(self, repo, client):
            seen.append(repo)

        def issue_text(self, number):
            return "題", github.Authored("owner", "OWNER", "本文")

        def trusts(self, authored):
            return True

    monkeypatch.setattr(issue_values.github, "GitHub", Hub)
    monkeypatch.setattr(issue_values.github, "client", lambda: None)
    monkeypatch.setattr(issue_values.git.Git, "remote_repo", lambda self: "o/own")
    assert issue_values.issue_text(Path("."), 1, "o/other") == ("題", "本文")
    assert issue_values.issue_text(Path("."), 1) == ("題", "本文")
    assert seen == ["o/other", "o/own"]


def _plan_path(tmp_path: Path, monkeypatch, work: Path) -> Path:
    plan = {"name": "design", "steps": [{"id": "a", "run": ["true"]}]}
    monkeypatch.setattr(route, "templates", lambda: {"design": route.Template("design", "design", "試験のテンプレート", (), plan)})
    work.mkdir(exist_ok=True)
    return invoke.plan_path(invoke.StageRun(invoke.PLAN, "design", {"issues": [1]}, (), "none", "design"), work, tmp_path)


def test_plan_is_written_without_write_access_for_others_and_does_not_follow_a_link(tmp_path, monkeypatch):
    # PR 4325 の r1-1: プランも values.json と同じ形で公開し、リンクの先を上書きしない
    path = _plan_path(tmp_path, monkeypatch, tmp_path / "w")
    assert path.stat().st_mode & 0o022 == 0
    outside = tmp_path / "outside"
    outside.write_text("利用者のファイル", encoding="utf-8")
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(worker_paths.EntryUnsafe):
        _plan_path(tmp_path, monkeypatch, tmp_path / "w")
    assert outside.read_text(encoding="utf-8") == "利用者のファイル"


def test_plan_in_a_directory_others_can_rename_in_is_not_handed_to_the_run(tmp_path, monkeypatch):
    # PR 4325 の r1-1: `ndf plan next` は名前を開き直すので、ほかの利用者が差し替えられる保存先のプランを流さない
    work = tmp_path / "w"
    work.mkdir()
    work.chmod(0o777)
    try:
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            _plan_path(tmp_path, monkeypatch, work)
    finally:
        work.chmod(0o700)
    assert e.value.kind == "shared"


def test_the_run_gets_the_checked_directory_for_the_plan_and_the_state(tmp_path, monkeypatch):
    # PR 4325 の r2-1: リンクを経た保存先は、解いて確かめたディレクトリをプランと --state-dir の両方に渡す
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (tmp_path / "link").symlink_to(real)
    _plan_path(tmp_path, monkeypatch, real / "w")
    seen: list[list[str]] = []

    def runner(argv, cwd, env):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(result.result("plan", "ok", "済んだ")) + "\n", "")

    run = invoke.StageRun(invoke.PLAN, "design", {"issues": [1]}, (), "none", "design")
    assert invoke.invoke(run, tmp_path, tmp_path / "link" / "w", runner=runner).status == "ok"
    argv = seen[0]
    assert argv[argv.index("next") + 1] == str(real / "w" / "design.json")
    assert argv[argv.index("--state-dir") + 1] == str(real / "w")


def _forged(work: Path) -> Path:
    """承認ゲートで終わった前の回の記録と結果のバックアップを置き、結果のパスを返す。"""
    (work / plan_runner.RESULTS_DIR).mkdir(parents=True)
    (work / plan_runner.RUN_FILE).write_text(json.dumps({"key": "k"}), encoding="utf-8")
    path = plan_runner.result_path(work, durable.run_name("k"))
    path.write_text(json.dumps(result.result("plan", "gate", "待つ", routes={"approved": None})), encoding="utf-8")
    for p in (work, work / plan_runner.RESULTS_DIR):
        p.chmod(0o700)
    for p in (work / plan_runner.RUN_FILE, path):
        p.chmod(0o600)
    return path


@pytest.mark.parametrize("which", ["dir", "run", "results", "result"])
def test_a_stored_result_others_can_replace_does_not_skip_the_run(tmp_path, monkeypatch, which):
    # PR 4325 の r3-3: ほかの利用者が差し替えられる前の結果で、流すべきプランを飛ばして成功にしない
    work = tmp_path / "w"
    stored = _forged(work)
    target = {"dir": work, "run": work / plan_runner.RUN_FILE, "results": stored.parent, "result": stored}[which]
    plan = {"name": "design", "steps": [{"id": "a", "run": ["true"]}]}
    monkeypatch.setattr(route, "templates", lambda: {"design": route.Template("design", "design", "試験のテンプレート", (), plan)})
    run = invoke.StageRun(invoke.PLAN, "design", {"issues": [1]}, (), "none", "design")
    never = lambda *a: pytest.fail("打たない")  # noqa: E731
    assert invoke.invoke(run, tmp_path, work, "approved", runner=never).status == "ok"
    target.chmod(target.stat().st_mode | 0o002)
    got = invoke.invoke(run, tmp_path, work, "approved", runner=never)
    target.chmod(0o700)
    assert got.status == "stopped" and got.cause["kind"] == "precondition"


@pytest.mark.parametrize("answer", [None, "approved"])
def test_a_stored_result_of_a_newer_version_stops_before_its_routes_are_used(tmp_path, monkeypatch, answer):
    work = tmp_path / "w"
    stored = _forged(work)
    stored.write_text(json.dumps({**json.loads(stored.read_text(encoding="utf-8")), "version": 2}), encoding="utf-8")
    plan = {"name": "design", "steps": [{"id": "a", "run": ["true"]}]}
    monkeypatch.setattr(route, "templates", lambda: {"design": route.Template("design", "design", "試験のテンプレート", (), plan)})
    run = invoke.StageRun(invoke.PLAN, "design", {"issues": [1]}, (), "none", "design")
    got = invoke.invoke(run, tmp_path, work, answer, runner=lambda *a: pytest.fail("打たない"))
    assert got.status == "stopped" and got.cause["kind"] == "precondition" and got.outputs == {}
