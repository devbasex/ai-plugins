"""課題の題と本文の写しを私有の形で書き、他の利用者が書ける写しを使わない（ndf.progress.invoke の `_issue_values`。PR 4307 の r7-3）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sprint_fixture import sprint_repo as _sprint_repo

from ndf.contracts import worker_paths
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
