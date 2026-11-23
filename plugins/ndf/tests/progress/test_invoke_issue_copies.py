"""課題の題と本文の写しを私有の形で書き、他の利用者が書ける写しを使わない（ndf.progress.invoke の `_issue_values`。PR 4307 の r7-3）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sprint_fixture import sprint_repo as _sprint_repo

from ndf.contracts import result, worker_paths
from ndf.core import route
from ndf.progress import invoke

PROJECT = {
    "test": {"suites": [{"name": "pytest", "command": "pytest -q"}]},
    "ci": {"required_checks": ["pytest"]},
    "workflow": {"launch": {"review": ["ndf-review"], "rebuild": ["ndf-rebuild"]}},
}


def _stage_run_of(name: str, repo: Path, issues: list[int]) -> invoke.StageRun:
    inputs = {"issues": issues, "worktree": str(repo), "mode": "light"}
    return invoke.StageRun(invoke.PLAN, name, {k: inputs[k] for k in ("issues", "worktree", "mode")}, (), "none", name)


def test_issue_copies_others_can_write_are_not_used_for_the_plan(tmp_path, monkeypatch):
    # PR 4307 の r7-3: 共有の保存先に別の利用者が置ける題と本文の写しを、書き手の権限を確かめずに worker へ渡さない
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: pytest.fail("置かれた写しで読み直して上書きしない"))
    work = tmp_path / "w"
    work.mkdir()
    for name, text in ((invoke.ISSUE_TITLE_FILE, '{"title": "差し替えた題"}'), (invoke.ISSUE_BODY_FILE, "差し替えた本文")):
        (work / name.format(number=3092)).write_text(text, encoding="utf-8")
        (work / name.format(number=3092)).chmod(0o666)
    with pytest.raises(worker_paths.EntryUnsafe) as e:
        invoke._issue_values(_stage_run_of("impl", repo, [3092]), work, repo)
    assert e.value.kind == "foreign"


def test_issue_copies_are_written_without_write_access_for_others(tmp_path, monkeypatch):
    # PR 4307 の r7-3: 写しはグループとほかの利用者が書き込めない形で公開する（次の描画が私有の写しとして読める）
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    work.mkdir()
    before = os.umask(0o000)  # umask によらず書き込み権限を外す
    try:
        got = invoke._issue_values(_stage_run_of("impl", repo, [3092]), work, repo)
    finally:
        os.umask(before)
    for name in (invoke.ISSUE_TITLE_FILE, invoke.ISSUE_BODY_FILE):
        assert (work / name.format(number=3092)).stat().st_mode & 0o022 == 0
    assert Path(got["body_file"]).read_text(encoding="utf-8") == "#3092 の本文"


def test_issue_body_in_a_directory_others_can_rename_in_is_not_handed_to_the_pr_step(tmp_path, monkeypatch):
    # PR 4307 の r8-3: `gh pr create --body-file` は名前を開き直すので、ほかの利用者が名前を差し替えられる保存先の写しを渡さない
    repo = _sprint_repo(tmp_path, PROJECT)
    monkeypatch.setattr(invoke, "issue_text", lambda location, n: (f"#{n} の題", f"#{n} の本文"))
    work = tmp_path / "w"
    work.mkdir()
    work.chmod(0o777)  # sticky bit の無い共有の保存先
    try:
        with pytest.raises(worker_paths.EntryUnsafe) as e:
            invoke._issue_values(_stage_run_of("impl", repo, [3092]), work, repo)
    finally:
        work.chmod(0o700)
    assert e.value.kind == "shared"
    work.chmod(0o1777)  # sticky bit があれば、呼んだ利用者の写しはほかの利用者に消せない
    try:
        got = invoke._issue_values(_stage_run_of("impl", repo, [3092]), work, repo)
    finally:
        work.chmod(0o700)
    assert Path(got["body_file"]).read_text(encoding="utf-8") == "#3092 の本文"


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
