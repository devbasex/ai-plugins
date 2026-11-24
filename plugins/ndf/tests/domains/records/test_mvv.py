"""MVV の記録: worktree のキー（#2603）・ MVV 判定の再利用（#2614）・覆しの数え方（#2777）・数えるバージョン（#2970）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import git_sandbox
import pytest

from ndf import identity
from ndf.adapters import git
from ndf.adapters.github import GitHubError
from ndf.contracts.internal import InternalError
from ndf.domains.records import jsonl, mvv
from ndf.domains.records import mvv_decide as md


def _decl(version: int, limits: dict | None = None) -> dict:
    out: dict[str, Any] = {"body": "mvv.md", "versions": [{"version": version, "sha256": "s"}]}
    if limits is not None:
        out["settings"] = {md.REVISE_AFTER: limits}
    return out


def test_override_kind_does_not_count_a_passed_unknown_as_an_override_but_signals_count_it() -> None:
    assert md.override_kind(md.FOLLOW, approved=False) == md.OVERRIDE_REJECT
    assert md.override_kind(md.NOT_FOLLOW, approved=True) == md.OVERRIDE_PASS
    assert md.override_kind(md.UNKNOWN, approved=True) == md.OVERRIDE_UNKNOWN_PASS
    assert md.override_kind(md.FOLLOW, approved=True) is None
    # 兆候は「判定できない」をどの回答でも数える
    assert md.signal_kinds(md.UNKNOWN, approved=True) == [md.UNKNOWN, md.OVERRIDE_UNKNOWN_PASS]
    assert md.signal_kinds(md.UNKNOWN, approved=False) == [md.UNKNOWN]
    assert md.signal_kinds(md.FOLLOW, approved=True) == []


def test_count_signals_dedupes_pairs_and_reads_only_the_current_version_and_repo() -> None:
    row = {"repo": "/m", "mvv_version": 2, "kind": md.OVERRIDE_PASS, "state": "s", "gate": "g", "pr": 1}
    rows = [row, dict(row), {**row, "pr": 2}, {**row, "mvv_version": 1, "pr": 3}, {**row, "repo": "/other", "pr": 4},
            {**row, "kind": md.OVERRIDE_UNKNOWN_PASS, "pr": 5}]  # fmt: skip
    got = md.count_signals(rows, _decl(2, {"overrides": 2}), "/m")
    assert (got.mvv_version, got.overrides, got.unknown_pass, got.unknown, got.revision_due) == (2, 2, 1, 0, True)
    assert md.count_signals(rows, _decl(2), "/m").revision_due is False


def test_status_compares_the_body_with_the_approved_record() -> None:
    assert md.status(None, None) == md.State.ABSENT
    assert md.status({"versions": []}, "s") == md.State.UNAPPROVED
    assert md.status(_decl(1), "s") == md.State.APPROVED
    assert md.status(_decl(1), "t") == md.State.MISMATCH
    assert md.status(_decl(1), None) == md.State.UNREADABLE


def test_reusable_takes_only_the_latest_follow_for_the_same_key() -> None:
    key = md.mvv_validation_key({"sprint": "a", "project": "b"})
    assert key == md.mvv_validation_key({"project": "b", "sprint": "a"}) and key != md.mvv_validation_key({"sprint": "a", "project": "c"})
    first = md.judged_row(key, md.FOLLOW, "t1", "/m", {"usd": 0.1})
    assert md.reusable([first], key) == first
    assert md.reusable([first, md.judged_row(key, md.UNKNOWN, "t2", "/m", {})], key) is None
    again = md.reuse_row(first, "t3", "/w")
    assert again["reused_from"] == "t1" and "usd" not in again
    assert md.reuse_row(again, "t4", "/w")["reused_from"] == "t1"


def test_reusable_does_not_go_back_past_an_excluded_row_that_may_be_the_latest() -> None:
    follow = md.judged_row("k", md.FOLLOW, "t1", "/m", {})
    assert md.reusable([follow], "k", [(1, {"key": "k"})]) is None
    assert md.reusable([follow], "k", [(1, None)]) is None and md.reusable([follow], "k", [(1, {})]) is None
    assert md.reusable([follow], "k", [(1, {"key": None})]) is None and md.reusable([follow], "k", [(1, {"key": 7})]) is None
    assert md.reusable([follow], "k", [(1, {"key": "other"})]) == follow
    assert md.reusable([follow], "k", [(0, {"key": "k"}), (0, None)]) == follow


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    origin, main = tmp_path / "origin.git", tmp_path / "main"
    git_sandbox.sh(tmp_path, "init", "-q", "--bare", str(origin))
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(main))
    (main / ".ndf").mkdir()
    for branch, version in (("main", 1), ("develop", 2)):
        if branch == "develop":
            git_sandbox.sh(main, "checkout", "-q", "-b", "develop")
        (main / ".ndf" / "mvv.json").write_text(json.dumps(_decl(version)), encoding="utf-8")
        git_sandbox.sh(main, "add", "-A")
        git_sandbox.sh(main, "commit", "-q", "-m", branch)
    git_sandbox.sh(main, "remote", "add", "origin", str(origin))
    git_sandbox.sh(main, "push", "-q", "origin", "main", "develop")
    git_sandbox.sh(main, "fetch", "-q", "origin")
    git_sandbox.sh(main, "worktree", "add", "-q", "-b", "w", str(main / ".worktrees" / "w"))
    return main


def test_repo_key_is_the_main_directory_from_a_worktree_and_refuses_outside_git(repo: Path, tmp_path: Path) -> None:
    assert mvv.repo_key(repo / ".worktrees" / "w") == mvv.repo_key(repo) == str(repo.resolve())
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(mvv.MvvError):
        mvv.repo_key(plain)


def test_signals_count_with_the_base_branch_version_from_a_worktree(repo: Path) -> None:
    wt = repo / ".worktrees" / "w"
    for pr in (1, 2):
        mvv.record_answer(wt, {"verdict": md.NOT_FOLLOW, "approved": True, "state": "s", "gate": "g", "pr": pr}, 2)
    assert mvv.record_answer(wt, {"verdict": md.FOLLOW, "approved": True}, 2) == []
    mvv.record_answer(repo, {"verdict": md.UNKNOWN, "approved": True, "pr": 9}, 2)
    mvv.record_answer(repo, {"verdict": md.UNKNOWN, "approved": False, "pr": 10}, 2)
    got = mvv.signals(wt, "develop", warnings=[])
    assert (got.mvv_version, got.overrides, got.unknown_pass, got.unknown) == (2, 2, 1, 2)
    assert mvv.signals(repo, "main", warnings=[]).overrides == 0


@pytest.fixture
def main_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Git を起動しないメインディレクトリ（ベースブランチの設定はバージョン 2）。"""
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    monkeypatch.setattr(mvv, "repo_key", str)
    monkeypatch.setattr(mvv, "decl_at", lambda root, ref: _decl(2))
    monkeypatch.setattr(mvv.git_mod.Git, "remote", lambda self: "origin")
    return tmp_path


def test_signals_count_the_rows_of_version_zero_and_report_the_rest(main_dir: Path) -> None:
    # バージョン 0 の兆候の `version` は MVV の設定のバージョン（整数）
    old = {"at": "2026-11-14T01:00:00+00:00", "kind": md.OVERRIDE_PASS, "version": 2, "repo": str(main_dir), "pr": 1}
    path = mvv.record_path("mvv-signals")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old) + "\n[]\n", encoding="utf-8")
    mvv.record_answer(main_dir, {"verdict": md.NOT_FOLLOW, "approved": True, "pr": 2}, 2)
    warnings: list[dict] = []
    assert mvv.signals(main_dir, "develop", warnings=warnings).overrides == 2
    assert warnings == [{"kind": "broken_rows", "path": str(path), "lines": [2]}]


def test_record_answer_without_a_version_takes_the_worktree_ndf_settings(repo: Path) -> None:
    wt = repo / ".worktrees" / "w"
    rows = mvv.record_answer(wt, {"verdict": md.NOT_FOLLOW, "approved": True, "pr": 1}, None)
    assert [(r["version"], r["mvv_version"]) for r in rows] == [(1, 2)]
    assert mvv.signals(wt, "develop", warnings=[]).overrides == 1
    (wt / ".ndf" / "mvv.json").unlink()
    with pytest.raises(mvv.MvvError):
        mvv.record_answer(wt, {"verdict": md.NOT_FOLLOW, "approved": True, "pr": 2}, None)


def test_mvv_validate_sprint_reuses_the_latest_follow_and_survives_broken_rows(repo: Path) -> None:
    calls: list[int] = []

    def judge(inputs: Any) -> tuple[str, dict]:
        calls.append(1)
        return md.FOLLOW, {"usd": 0.2, "seconds": 3}

    path = mvv.record_path("mvv-validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{壊れた\n", encoding="utf-8")
    path.chmod(0o644)
    inputs = {"sprint": "本文", "project": "MVV", "principles": "原則", "prompt": "指示"}
    first, _ = mvv.mvv_validate_sprint(repo, inputs, judge)
    second, _ = mvv.mvv_validate_sprint(repo / ".worktrees" / "w", inputs, judge)
    assert len(calls) == 1 and second["reused_from"] == first["at"]
    mvv.mvv_validate_sprint(repo, {**inputs, "sprint": "変えた"}, judge)
    mvv.mvv_validate_sprint(repo, inputs, judge, reuse=False)
    assert len(calls) == 3
    rows, broken = jsonl.read_rows(path, kind="mvv-validation")
    assert len(rows) == 4 and [b.line for b in broken] == [1]
    # 判定の理由を伏せずに持つので、先に通常の権限で作られていても、追記の後は所有者だけが読める（#3959 の r1-5）
    assert path.stat().st_mode & 0o777 == 0o600


def test_mvv_validate_sprint_judges_again_after_a_row_of_the_same_inputs_that_does_not_fit(main_dir: Path) -> None:
    verdicts = [md.FOLLOW, md.NOT_FOLLOW]
    inputs = {"sprint": "本文"}

    def judge(_: Any) -> tuple[str, dict]:
        return verdicts.pop(0), {}

    first, _ = mvv.mvv_validate_sprint(main_dir, inputs, judge)
    with mvv.record_path("mvv-validation").open("a", encoding="utf-8") as f:
        f.write(json.dumps({**first, "verdict": md.NOT_FOLLOW, "usd": "0.1"}) + "\n")
    assert mvv.mvv_validate_sprint(main_dir, inputs, judge)[0]["verdict"] == md.NOT_FOLLOW
    assert verdicts == []


# 現状固定（I-011 の前）: 記録の保存先は NDF_MVV_STATE_DIR → 状態の保存先の順。状態の保存先を解けなければ IdentityError のまま
def test_record_path_prefers_the_env_dir_and_raises_without_it_and_the_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "m"))
    monkeypatch.setattr(identity, "load", _unloadable)
    assert mvv.record_path("mvv-validation") == tmp_path / "m" / "mvv-validation.jsonl"
    assert mvv.record_path("mvv-signals") == tmp_path / "m" / "mvv-signals.jsonl"
    # 環境変数も状態の保存先も無ければ IdentityError
    monkeypatch.delenv("NDF_MVV_STATE_DIR")
    with pytest.raises(identity.IdentityError):
        mvv.record_path("mvv-validation")


@pytest.mark.parametrize("env", [None, ""])
def test_record_path_falls_back_to_the_state_dir_when_the_env_is_unset_or_empty(env: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    if env is None:
        monkeypatch.delenv("NDF_MVV_STATE_DIR", raising=False)
    else:
        monkeypatch.setenv("NDF_MVV_STATE_DIR", env)
    state = identity.load().state_dir
    assert mvv.record_path("mvv-validation") == state / "mvv-validation.jsonl"
    assert mvv.record_path("mvv-signals") == state / "mvv-signals.jsonl"


def _unloadable() -> identity.PluginIdentity:
    raise identity.IdentityError("plugin.json を読めない")


def test_count_signals_counts_disputed_findings_by_their_key() -> None:
    """レビューの指摘の見送りは、指摘のキーごとに 1 件として「判定できない」に足す（id では見分けない）。"""
    row = {"repo": "/m", "mvv_version": 2, "kind": md.REVIEW_DISPUTED, "pr": 1, "key": "a", "id": "r1-1"}
    rows = [row, {**row, "id": "r2-1"}, {**row, "key": "b"}, {**row, "pr": 2}, {**row, "mvv_version": 1, "key": "c"},
            {"repo": "/m", "mvv_version": 2, "kind": md.UNKNOWN, "state": "s", "gate": "g", "pr": 1}]  # fmt: skip
    got = md.count_signals(rows, _decl(2, {"unknowns": 4}), "/m")
    assert (got.overrides, got.unknown_pass, got.unknown, got.revision_due) == (0, 0, 4, True)


# --- #3959 Task 4: 記録を 1 回だけ読む（validation_rows）・読み済みの行で判定する・比較の差分（compare_files） ---

INPUTS = {"sprint": "本文"}


def _follow(calls: list[int]) -> mvv.Judge:
    def judge(_: Any) -> tuple[str, dict]:
        calls.append(1)
        return md.FOLLOW, {}

    return judge


def test_validation_rows_reads_every_row_once_the_record_exists_and_refuses_a_non_file(repo: Path) -> None:
    assert mvv.validation_rows(repo) == ([], [])
    path = mvv.record_path("mvv-validation")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(path.parent / "どこにも無い")
    with pytest.raises(OSError, match="ファイルでない"):
        mvv.validation_rows(repo)
    path.unlink()
    path.mkdir()
    with pytest.raises(OSError, match="ファイルでない"):
        mvv.validation_rows(repo)
    # すべての種類の行と読めない行を返し、不正なバイトと新しい形式の行は投げる
    path.rmdir()
    first, _ = mvv.mvv_validate_sprint(repo, INPUTS, _follow([]))
    with path.open("a", encoding="utf-8") as f:
        f.write("{壊れた\n")
    rows, broken = mvv.validation_rows(repo)
    assert rows == [first] and [(b.line, b.before) for b in broken] == [(2, 1)]
    with path.open("ab") as f:
        f.write(b'{"\xff"}\n')
    with pytest.raises(UnicodeDecodeError):
        mvv.validation_rows(repo)
    path.write_text(json.dumps({**first, "version": 99}) + "\n", encoding="utf-8")
    with pytest.raises(InternalError):
        mvv.validation_rows(repo)


def test_mvv_validate_sprint_reuses_from_the_given_rows_without_reading_the_record(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    first, _ = mvv.mvv_validate_sprint(repo, INPUTS, _follow(calls))
    rows, broken = mvv.validation_rows(repo)
    monkeypatch.setattr(jsonl, "read_rows", lambda *a, **k: pytest.fail("渡した行があるのに記録を読み直した"))
    again, cause = mvv.mvv_validate_sprint(repo, INPUTS, _follow(calls), rows=rows, broken=broken)
    assert (len(calls), again["reused_from"], cause) == (1, first["at"], None)
    # 同じ材料の「従う」の後ろに読めない行があれば再利用しない
    mvv.mvv_validate_sprint(repo, INPUTS, _follow(calls), rows=rows, broken=[jsonl.Broken(2, "壊れた", 1, {"key": first["key"]})])
    # reuse=False は記録を読まない（読めない記録でも止まらない）
    mvv.mvv_validate_sprint(repo, INPUTS, _follow(calls), reuse=False)
    assert len(calls) == 3


def test_mvv_validate_sprint_returns_the_row_and_the_cause_when_it_cannot_append(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: Any, **_k: Any) -> dict:
        raise OSError("書けない")

    monkeypatch.setattr(jsonl, "append_locked", fail)
    row, cause = mvv.mvv_validate_sprint(repo, INPUTS, _follow([]), rows=[])
    assert (row["verdict"], cause) == (md.FOLLOW, "書けない")


class CompareHub:
    """比較の REST だけを返す GitHub の偽物。"""

    repo = "o/r"

    def __init__(self, files: list[dict[str, Any]], refused: Optional[GitHubError] = None) -> None:
        self.files, self.refused = files, refused
        self.paths: list[str] = []
        self.params: list[Any] = []

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> dict:
        self.paths.append(path)
        self.params.append(params)
        if self.refused is not None:
            raise self.refused
        return {"files": self.files}


class CountingGit:
    """手元の git の偽物。`fetch` の結果と、呼んだ手を残す（子のプロセスは起動しない）。"""

    def __init__(self, fetched: git.Completed) -> None:
        self.fetched = fetched
        self.calls: list[tuple[str, ...]] = []

    def fetch(self, remote: str, *refspecs: str) -> git.Completed:
        self.calls.append(("fetch", remote, *refspecs))
        return self.fetched

    def diff_between(self, a: str, b: str, *options: str) -> str:
        self.calls.append(("diff", a, b, *options))
        return "diff --git a/x b/x\nindex 1..2 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"

    def merge_base(self, *a: str) -> str:
        raise AssertionError("merge-base を別に起動しない")


def _files(n: int) -> list[dict[str, Any]]:
    return [{"filename": f"f{i}", "status": "modified", "patch": "+x", "changes": 1} for i in range(n)]


def test_compare_files_reads_the_comparison_once_and_git_only_at_the_cap() -> None:
    hub, g = CompareHub(_files(mvv.COMPARE_FILES_CAP - 1)), CountingGit(git.Completed(0, "", ""))
    assert mvv.compare_files(hub, g, "origin", "b" * 40, "h" * 40) == hub.files  # type: ignore[arg-type]
    assert hub.paths == [f"/repos/o/r/compare/{'b' * 40}...{'h' * 40}"] and g.calls == []
    # ページを指定し、使わないコミットの一覧を 1 件に抑える（files は 1 ページ目に比較の全体の分が載る）
    assert hub.params == [{"page": 1, "per_page": 1}]
    hub = CompareHub(_files(mvv.COMPARE_FILES_CAP))
    rows = mvv.compare_files(hub, g, "origin", "b" * 40, "h" * 40)  # type: ignore[arg-type]
    assert [r["filename"] for r in rows] == ["x"]
    assert g.calls == [("fetch", "origin", "h" * 40), ("diff", "b" * 40, "h" * 40, *mvv.LOCAL_DIFF)]
    assert {"--merge-base", "--patch", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", "--submodule=short",
            "--src-prefix=a/", "--dst-prefix=b/", "-M"} <= set(mvv.LOCAL_DIFF)  # fmt: skip


def test_compare_files_refuses_an_unfetchable_head_or_an_answer_without_files() -> None:
    g = CountingGit(git.Completed(128, "", "取得できない"))
    with pytest.raises(git.GitError, match="取得できない"):
        mvv.compare_files(CompareHub(_files(mvv.COMPARE_FILES_CAP)), g, "origin", "b" * 40, "h" * 40)  # type: ignore[arg-type]
    assert [c[0] for c in g.calls] == ["fetch"]
    # 応答に files が無ければ GitHubError
    hub = CompareHub([])
    hub.rest = lambda *a, **k: {"message": "x"}  # type: ignore[method-assign]
    with pytest.raises(GitHubError):
        mvv.compare_files(hub, CountingGit(git.Completed(0, "", "")), "origin", "b" * 40, "h" * 40)  # type: ignore[arg-type]


def _limited(kind: str) -> GitHubError:
    if kind == "quota":
        return GitHubError("上限", 403, quota="rest")
    if kind == "retry_after":
        return GitHubError("上限", 403, retry_after=60.0)
    made = GitHubError("二次の上限", 403)
    made.secondary = True
    return made


@pytest.mark.parametrize(
    ("refused", "by_git"),
    [
        (GitHubError("読めない", 403), True),
        (GitHubError("読めない", 404), True),
        (GitHubError("落ちた", 502), False),
        (_limited("quota"), False),
        (_limited("retry_after"), False),
        (_limited("secondary"), False),
    ],
)
def test_compare_files_reads_with_git_only_when_the_api_token_cannot_read_the_comparison(refused: GitHubError, by_git: bool) -> None:
    # 非公開のリポジトリで API の認証情報に Contents の読み取りが無い（403・404）: 同じ SHA の組を手元の git で読む。
    # ほかの失敗と、レート制限と識別できた 403 は拒否でないので、手元の git へ切り替えずにそのまま投げる
    hub, g = CompareHub([], refused=refused), CountingGit(git.Completed(0, "", ""))
    if not by_git:
        with pytest.raises(GitHubError):
            mvv.compare_files(hub, g, "origin", "b" * 40, "h" * 40)  # type: ignore[arg-type]
        assert g.calls == []
        return
    rows = mvv.compare_files(hub, g, "origin", "b" * 40, "h" * 40)  # type: ignore[arg-type]
    assert [r["filename"] for r in rows] == ["x"]
    assert g.calls == [("fetch", "origin", "h" * 40), ("diff", "b" * 40, "h" * 40, *mvv.LOCAL_DIFF)]


def test_compare_files_at_the_cap_reads_only_the_changes_of_the_pr_from_the_merge_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # マージ先だけが分岐の後に進んだ状態（本物の git）: マージ先だけの変更が載らず、両方で同じに変えたファイルが落ちない
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    origin, local = tmp_path / "origin", tmp_path / "local"
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(origin))

    def commit(where: Path, files: dict[str, str]) -> str:
        for name, text in files.items():
            (where / name).write_text(text, encoding="utf-8")
        git_sandbox.sh(where, "add", "-A")
        git_sandbox.sh(where, "commit", "-q", "-m", "c")
        return git_sandbox.sh(where, "rev-parse", "HEAD", strip=True)

    commit(origin, {"both.txt": "0\n", "base.txt": "0\n"})
    git_sandbox.sh(origin, "checkout", "-q", "-b", "pr")
    head = commit(origin, {"both.txt": "1\n", "pr.txt": "1\n"})
    git_sandbox.sh(origin, "checkout", "-q", "main")
    base = commit(origin, {"both.txt": "1\n", "base.txt": "1\n"})
    git_sandbox.sh(tmp_path, "clone", "-q", "--single-branch", "-b", "main", str(origin), str(local))
    rows = mvv.compare_files(CompareHub(_files(mvv.COMPARE_FILES_CAP)), git.Git(local), "origin", base, head)  # type: ignore[arg-type]
    assert sorted(r["filename"] for r in rows) == ["both.txt", "pr.txt"]
