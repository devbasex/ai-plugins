"""承認した文書の反映（`ndf.progress.units_sync`。#3702 の決定 1・11、受け入れ条件 1 の部品）のテスト。

送り先の URL は `git@github.com:o/r.git` で、`GIT_SSH_COMMAND` の偽の ssh が手元の bare リポジトリで受ける
（`insteadOf` で手元へ向けると、送り先の確かめが拒否するため）。PR の読み取りは偽の hub、承認の記録の読み取りは差し替える。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import git_sandbox
import pytest
import strategy_fixture
from result_json import last_result

from ndf.adapters import github
from ndf.adapters.git import Git
from ndf.progress import command, definition, design_record, gate, run_view, stages, store, units_cli, units_sync

URL, REPO = "git@github.com:o/r.git", "o/r"
SPRINT, ISSUE, PR = "sprint/s1", 3, 7
DOCS = {
    "issues/issue-3-plan.units.json": '{"issues": [3]}\n',
    "issues/issue-3-plan.md": "# 実装計画\n",
    "issues/issue-3-requirements.md": "# 要求\n",
    "issues/issue-3-design.md": "# 設計\n",
    "issues/issue-3-design-detail.md": "# 詳細設計\n",
}
PLAN = "issues/issue-3-plan.md"
PROJECT = {"branch_strategy": strategy_fixture.section("plugin")}


def _sh(cwd: Path, *args: str) -> str:
    return git_sandbox.sh(cwd, *args, strip=True, identity=True)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def _commit(root: Path, files: dict[str, str], message: str) -> str:
    _write(root, files)
    _sh(root, "add", "--", *files)
    _sh(root, "commit", "-q", "-m", message)
    return _sh(root, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    """送り先の bare リポジトリと作業場所。設計 PR の head は `design` ブランチで、送り先ではスプリントへマージ済みの扱い。"""
    base = tmp_path_factory.mktemp("sync-template")
    work, bare = base / "work", base / "bare.git"
    _sh(base, "init", "-q", "--bare", "-b", "main", str(bare))
    _sh(base, "init", "-q", "-b", "main", str(work))
    for key, value in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
        _sh(work, "config", key, value)
    _commit(work, {".ndf/project.json": json.dumps(PROJECT), ".gitignore": "ignored\n", "README.md": "x\n"}, "init")
    _sh(work, "push", "-q", str(bare), "HEAD:refs/heads/main", f"HEAD:refs/heads/{SPRINT}")
    _sh(work, "switch", "-q", "-c", "design")
    head = _commit(work, DOCS, "設計")
    _sh(work, "push", "-q", str(bare), "HEAD:refs/heads/design")
    _sh(work, "switch", "-q", "-c", SPRINT, "main")
    _sh(work, "remote", "add", "origin", URL)
    (base / "head").write_text(head, encoding="utf-8")
    return base


@pytest.fixture
def world(template, tmp_path, monkeypatch):
    """作業場所・送り先・偽の ssh・承認の記録・ステートマシン・偽の hub。"""
    shutil.copytree(template / "work", tmp_path / "work")
    shutil.copytree(template / "bare.git", tmp_path / "bare.git")
    work, bare = tmp_path / "work", tmp_path / "bare.git"
    ssh = tmp_path / "ssh"
    ssh.write_text(f'#!/bin/sh\nfor last; do :; done\nexec "${{last%% *}}" "{bare}"\n', encoding="utf-8")
    ssh.chmod(0o755)
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(ssh))
    monkeypatch.setenv("GIT_SSH_VARIANT", "simple")
    monkeypatch.delenv("NDF_MACHINE_ID", raising=False)
    monkeypatch.chdir(work)
    head = (template / "head").read_text(encoding="utf-8")
    record = {
        "review": {"repo": REPO, "pr": PR, "head": head, "base": SPRINT},
        "sprint_branch": SPRINT,
        "targets": {"issues": [ISSUE]},
        "units": [{"file": "issues/issue-3-plan.units.json"}],
    }
    monkeypatch.setattr(design_record, "approved_of", lambda v: w.record)
    pull = SimpleNamespace(merged=True, head_sha=head, base_ref=SPRINT, head_ref="design")
    read: list[int] = []

    def hub(repo: str):
        return SimpleNamespace(pull=lambda n: read.append(n) or w.pull)

    root = tmp_path / "store"
    root.mkdir()
    w = SimpleNamespace(work=work, bare=bare, head=head, record=record, pull=pull, read=read, hub=hub)
    with store.open_store(f"sqlite:///{root / 'progress.sqlite'}", root) as st:
        w.st = st
        w.context = {"issues": [ISSUE], "mode": "standard", "branch": SPRINT, "worktree": str(work)}
        w.machine = _machine(st, "s1", w.context)
        yield w


def _machine(st, name: str, context: dict, repo: str = URL) -> str:
    doc = {**stages.snapshot_dict(definition.load_definition("standard")), "context": context, "enablement": {}}
    with store.transaction(st) as conn:
        return store.create_machine(conn, ("sprint", repo, name), None, doc, [])


def _sync(w, machine: str | None = None) -> dict:
    return units_sync.sync_approved(w.st, machine or w.machine, w.work, w.hub)


def _remote(w, ref: str = SPRINT) -> str:
    return _sh(w.bare, "rev-parse", f"refs/heads/{ref}")


def _kind(out: dict) -> str:
    return out["cause"]["kind"]


def _untouched(w, out: dict, kind: str) -> None:
    """止まり、コミットも push も起きていない。"""
    before_head, before_remote = w.before
    assert out["status"] == "stopped" and _kind(out) == kind, out
    assert (_sh(w.work, "rev-parse", "HEAD"), _remote(w)) == (before_head, before_remote)


def _snapshot(w) -> None:
    w.before = (_sh(w.work, "rev-parse", "HEAD"), _remote(w))


def _delivered(w) -> None:
    assert _remote(w) == _sh(w.work, "rev-parse", "HEAD")
    for name, text in DOCS.items():
        assert _sh(w.bare, "show", f"refs/heads/{SPRINT}:{name}") == text.strip()


def test_the_approved_documents_are_committed_and_pushed(world):
    (world.work / "other.txt").write_text("staged\n", encoding="utf-8")
    _sh(world.work, "add", "other.txt")

    out = _sync(world)

    assert out["status"] == "ok", out
    _delivered(world)
    # 反映と関係ないステージ済みの変更は、コミットに入らない
    assert _sh(world.work, "diff-tree", "-r", "--no-commit-id", "--name-only", "HEAD").split() == sorted(DOCS)
    saved = json.loads((run_view.work_root(world.st.root, world.machine) / "design-sync.json").read_text(encoding="utf-8"))
    assert saved == {"record_head": world.head, "commit": _sh(world.work, "rev-parse", "HEAD")}
    # 打ち直しは PR を読まず、コミットも足さない
    committed, world.read[:] = _sh(world.work, "rev-parse", "HEAD"), []
    assert _sync(world)["status"] == "ok"
    assert _sh(world.work, "rev-parse", "HEAD") == committed and world.read == []


def test_a_branch_that_already_has_the_approved_head_needs_no_pull_request(world):
    _sh(world.work, "merge", "-q", "--ff-only", "design")
    _snapshot(world)

    assert _sync(world)["status"] == "ok"
    assert world.read == [] and (_sh(world.work, "rev-parse", "HEAD"), _remote(world)) == world.before


def test_a_machine_without_an_approval_stops(world):
    world.record = None
    _snapshot(world)

    _untouched(world, _sync(world), "precondition")


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:o/other.git",
        "https://notgithub.com/o/r",
        "https://github.com.example.com/o/r",
        "https://example.com/github.com/o/r",
        "git@notgithub.com:o/r",
        "file:///srv/o/r.git",
        "/srv/o/r.git",
        "http://github.com/o/r",
        "git@github-work:o/r",
    ],
)
def test_a_push_url_of_another_destination_is_refused(world, url):
    _sh(world.work, "config", "remote.origin.pushurl", url)
    _snapshot(world)

    _untouched(world, _sync(world), "refused")
    assert world.read == []


def test_a_push_url_with_credentials_is_refused_without_showing_it(world, monkeypatch):
    token = "dummytoken"
    _sh(world.work, "config", "remote.origin.pushurl", f"https://{token}@github.com/o/r")
    _snapshot(world)
    calls: list[list[str]] = []
    real = Git.attempt
    monkeypatch.setattr(Git, "attempt", lambda self, *a: calls.append(list(a)) or real(self, *a))

    out = _sync(world)

    _untouched(world, out, "refused")
    assert token not in json.dumps(out, ensure_ascii=False) and not any(token in a for args in calls for a in args)


@pytest.mark.parametrize(
    "setting",
    [
        [("remote.origin.pushurl", URL), ("remote.origin.pushurl", URL)],
        [("url.git@github.com:o/other.insteadOf", "git@github.com:o/r")],
        [("url.git@github.com:o/other.pushInsteadOf", "git@github.com:")],
        [(f"remote.{URL}.url", "git@github.com:o/other.git")],
    ],
    ids=["2 つの push の URL", "insteadOf", "pushInsteadOf", "URL と同じ名前のリモート"],
)
def test_a_url_git_would_change_or_send_twice_is_refused(world, setting):
    if setting[0][0].startswith("url."):
        # 1 回目の読み替えで得た URL に、もう一度当たる設定
        _sh(world.work, "config", "remote.origin.pushurl", URL)
    for key, value in setting:
        _sh(world.work, "config", "--add", key, value)
    _snapshot(world)

    _untouched(world, _sync(world), "refused")


@pytest.mark.parametrize("case", ["別のブランチの作業場所", "記録と別のブランチ", "文脈に branch が無い", "記録に sprint_branch が無い",
                                  "別のリポジトリのステートマシン", "リリース先のブランチ"])  # fmt: skip
def test_a_destination_that_differs_from_the_record_is_refused(world, case):
    machine = None
    if case == "別のブランチの作業場所":
        _sh(world.work, "switch", "-q", "-c", "sprint/other")
    elif case == "記録と別のブランチ":
        _sh(world.work, "switch", "-q", "-c", "sprint/other")
        machine = _machine(world.st, "s2", {**world.context, "branch": "sprint/other"})
    elif case == "文脈に branch が無い":
        machine = _machine(world.st, "s2", {k: v for k, v in world.context.items() if k != "branch"})
    elif case == "記録に sprint_branch が無い":
        del world.record["sprint_branch"]
    elif case == "別のリポジトリのステートマシン":
        machine = _machine(world.st, "s2", world.context, "git@github.com:o/other.git")
    else:
        _sh(world.work, "switch", "-q", "main")
        world.record["sprint_branch"] = "main"
        machine = _machine(world.st, "s2", {**world.context, "branch": "main"})
    before = (_sh(world.work, "rev-parse", "HEAD"), _sh(world.bare, "for-each-ref"))

    out = _sync(world, machine)

    assert out["status"] == "stopped" and _kind(out) == "refused", out
    assert (_sh(world.work, "rev-parse", "HEAD"), _sh(world.bare, "for-each-ref")) == before


@pytest.mark.parametrize(
    ("change", "kind"),
    [({"merged": False}, "precondition"), ({"head_sha": "b" * 40}, "refused"), ({"base_ref": "main"}, "refused")],
)
def test_a_pull_request_that_is_not_the_approved_merge_stops(world, change, kind):
    for key, value in change.items():
        setattr(world.pull, key, value)
    _snapshot(world)

    _untouched(world, _sync(world), kind)


def test_a_record_base_that_is_not_the_documents_target_is_refused(world):
    world.record["review"]["base"] = world.pull.base_ref = "main"
    _snapshot(world)

    _untouched(world, _sync(world), "refused")


def test_a_github_failure_stops(world):
    def failing(repo):
        raise github.GitHubError("boom", 404)

    world.hub = failing
    _snapshot(world)

    _untouched(world, _sync(world), "precondition")


def test_an_uncommitted_edit_is_kept(world):
    _write(world.work, {PLAN: "手元の編集\n"})
    _snapshot(world)

    out = _sync(world)

    _untouched(world, out, "precondition")
    assert PLAN in out["summary"] and (world.work / PLAN).read_text(encoding="utf-8") == "手元の編集\n"


def test_a_merge_in_progress_stops(world):
    (world.work / ".git" / "MERGE_HEAD").write_text(world.head + "\n", encoding="utf-8")
    _snapshot(world)

    _untouched(world, _sync(world), "precondition")


def test_a_document_changed_on_the_sprint_branch_is_a_conflict(world):
    _commit(world.work, {PLAN: "別の内容\n"}, "手元")
    _sh(world.work, "push", "-q", str(world.bare), f"HEAD:refs/heads/{SPRINT}")
    _snapshot(world)

    out = _sync(world)

    _untouched(world, out, "base_conflict")
    assert PLAN in out["summary"]


def test_a_local_commit_the_destination_lacks_is_not_published(world):
    _commit(world.work, {"local.txt": "x\n"}, "手元だけ")
    _snapshot(world)

    out = _sync(world)

    _untouched(world, out, "refused")
    assert "local.txt" in out["summary"]


def test_a_commit_that_differs_from_the_approved_documents_is_not_pushed(world):
    hook = world.work / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho rewritten > {PLAN}\ngit add {PLAN}\n", encoding="utf-8")
    hook.chmod(0o755)
    _snapshot(world)

    out = _sync(world)

    assert out["status"] == "stopped" and _kind(out) == "refused", out
    assert _remote(world) == world.before[1]


def _reject_pushes(w) -> Path:
    hook = w.bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    return hook


def test_a_rejected_push_is_sent_again_on_the_next_run(world):
    hook, remote = _reject_pushes(world), _remote(world)

    out = _sync(world)

    assert out["status"] == "stopped" and _kind(out) == "precondition" and "git rebase" in out["summary"], out
    committed = _sh(world.work, "rev-parse", "HEAD")
    again = _sync(world)
    assert again["status"] == "stopped" and _remote(world) == remote
    hook.unlink()
    assert _sync(world)["status"] == "ok"
    assert _remote(world) == committed == _sh(world.work, "rev-parse", "HEAD")


def test_a_recorded_commit_that_was_dropped_is_made_again(world):
    hook = _reject_pushes(world)
    assert _sync(world)["status"] == "stopped"
    _sh(world.work, "reset", "-q", "--hard", "HEAD^")
    hook.unlink()

    assert _sync(world)["status"] == "ok"
    _delivered(world)


def test_the_push_url_is_used_even_when_the_fetch_url_differs(world, monkeypatch):
    _sh(world.work, "config", "remote.origin.url", "git@github.com:o/fetch.git")
    _sh(world.work, "config", "remote.origin.pushurl", URL)
    calls: list[list[str]] = []
    real = Git.attempt
    monkeypatch.setattr(Git, "attempt", lambda self, *a: calls.append(list(a)) or real(self, *a))

    assert _sync(world)["status"] == "ok"

    _delivered(world)
    sent = [a for a in calls if a[0] in ("fetch", "push")]
    assert sent and all(a[a.index("--") + 1] == URL for a in sent), sent


def test_the_command_takes_the_machine_from_the_environment(world, monkeypatch, capsys):
    monkeypatch.setattr(command, "with_store", lambda cwd, handler, a: handler(a, cwd, world.st))
    monkeypatch.setattr(units_cli.github, "GitHub", lambda repo, client: world.hub(repo))
    monkeypatch.setattr(units_cli.github, "client", lambda: None)
    monkeypatch.setenv("NDF_MACHINE_ID", world.machine)

    assert gate.progress_main(["units", "sync"]) == 0

    assert last_result(capsys.readouterr().out)["status"] == "ok"
    _delivered(world)


def test_the_command_without_a_machine_stops(world, monkeypatch, capsys):
    monkeypatch.setattr(command, "with_store", lambda cwd, handler, a: handler(a, cwd, world.st))

    assert gate.progress_main(["units", "sync"]) != 0

    assert "NDF_MACHINE_ID" in last_result(capsys.readouterr().out)["summary"]


def test_an_approved_head_missing_locally_is_fetched_from_the_push_url(world, tmp_path):
    other = tmp_path / "other"
    _sh(tmp_path, "clone", "-q", "-b", "design", str(world.bare), str(other))
    head = _commit(other, {PLAN: "# 直した実装計画\n"}, "指摘の直し")
    _sh(other, "push", "-q", "origin", "HEAD:refs/heads/design")
    world.record["review"]["head"] = world.pull.head_sha = head

    out = _sync(world)

    assert out["status"] == "ok", out
    assert _sh(world.bare, "show", f"refs/heads/{SPRINT}:{PLAN}") == "# 直した実装計画"
