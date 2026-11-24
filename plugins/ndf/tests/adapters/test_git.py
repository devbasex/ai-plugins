"""git のアダプター（`ndf.adapters.git`）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import git_sandbox
import pytest

from ndf import identity
from ndf.adapters import git

FULL = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"


def _fake(returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[list[str]] = []

    def runner(args: list[str], cwd):
        calls.append(args)
        return git.Completed(returncode, stdout, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


@pytest.mark.parametrize(
    ("expected", "matches"),
    [(FULL, True), ("2bb66a6c1", True), ("2BB66A6C1", True), ("2bb66a6c2", False), ("deadbeef", False)],
)
def test_sha_matches_accepts_short_and_full(expected: str, matches: bool) -> None:
    # #2041: 同じ HEAD を指す短い SHA は一致として読む
    assert git.sha_matches(expected, FULL) is matches


@pytest.mark.parametrize(("expected", "actual"), [("2bb66", FULL), ("main", FULL), ("2bb66a6c1", "2bb66a6c1")])
def test_sha_matches_refuses_values_that_are_not_shas(expected: str, actual: str) -> None:
    with pytest.raises(git.GitError):
        git.sha_matches(expected, actual)


def test_rev_parse_returns_full_sha() -> None:
    runner = _fake(stdout=FULL + "\n")
    assert git.Git(runner=runner).rev_parse("2bb66a6c1") == FULL
    assert runner.calls == [["rev-parse", "--verify", "--quiet", "--end-of-options", "2bb66a6c1^{commit}"]]


def test_run_raises_with_exit_code_and_stderr() -> None:
    with pytest.raises(git.GitError, match="終了コード 128.*bad ref"):
        git.Git(runner=_fake(128, stderr="bad ref")).run("rev-parse", "x")


@pytest.mark.parametrize(("code", "expected"), [(0, True), (1, False)])
def test_is_ancestor_reads_exit_code(code: int, expected: bool) -> None:
    assert git.Git(runner=_fake(code)).is_ancestor("a", "b") is expected


def test_is_ancestor_raises_on_git_failure() -> None:
    with pytest.raises(git.GitError):
        git.Git(runner=_fake(128, stderr="not a commit")).is_ancestor("a", "b")


@pytest.mark.parametrize(
    ("url", "repo"),
    [
        ("https://github.com/devbasex/ai-plugins.git", "devbasex/ai-plugins"),
        ("git@github.com:devbasex/ai-plugins.git", "devbasex/ai-plugins"),
        ("https://github.com/devbasex/ai-plugins", "devbasex/ai-plugins"),
        ("https://gitlab.com/x/y.git", None),
    ],
)
def test_repo_of_url(url: str, repo: str | None) -> None:
    assert git.repo_of_url(url) == repo


def test_remote_repo_refuses_non_github_remote() -> None:
    with pytest.raises(git.GitError, match="GitHub"):
        git.Git(runner=_fake(stdout="https://gitlab.com/x/y.git\n")).remote_repo("origin")


def test_parse_shortstat_sums_insertions_and_deletions() -> None:
    assert git.parse_shortstat(" 7 files changed, 671 insertions(+), 10 deletions(-)") == 681
    assert git.parse_shortstat(" 1 file changed, 3 deletions(-)") == 3
    assert git.parse_shortstat("") == 0


def _init_repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    """main に `files` を書いた base のコミットを 1 つ持つリポジトリを作る。リポジトリのパスと base の SHA を返す。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_sandbox.sh(repo, "init", "-q", "-b", "main", identity=True, strip=True)
    for name, text in files.items():
        (repo / name).write_text(text)
    git_sandbox.sh(repo, "add", "-A", identity=True, strip=True)
    git_sandbox.sh(repo, "commit", "-q", "-m", "base", identity=True, strip=True)
    return repo, git_sandbox.sh(repo, "rev-parse", "HEAD", identity=True, strip=True)


def _merge_branch(repo: Path, branch: str, files: dict[str, str]) -> str:
    """`branch` を作ってファイルを書き、main へ --no-ff でマージする。マージのコミットの SHA を返す。"""
    git_sandbox.sh(repo, "checkout", "-q", "-b", branch, "main", identity=True, strip=True)
    for name, text in files.items():
        (repo / name).write_text(text)
    git_sandbox.sh(repo, "add", "-A", identity=True, strip=True)
    git_sandbox.sh(repo, "commit", "-q", "-m", branch, identity=True, strip=True)
    git_sandbox.sh(repo, "checkout", "-q", "main", identity=True, strip=True)
    git_sandbox.sh(repo, "merge", "-q", "--no-ff", "-m", f"merge {branch}", branch, identity=True, strip=True)
    return git_sandbox.sh(repo, "rev-parse", "HEAD", identity=True, strip=True)


@pytest.mark.parametrize(
    "call",
    [
        lambda g, v: g.merge_base(v, "main"),
        lambda g, v: g.merge_base("main", v),
        lambda g, v: g.is_ancestor(v, "main"),
        lambda g, v: g.rev_parse(v),
        lambda g, v: g.changed_files(v, "main"),
        lambda g, v: g.changed_lines(v, "main"),
        lambda g, v: g.remote_repo(v),
        lambda g, v: g.log_of(v, "-1", "--format=%ct"),
        lambda g, v: g.log_of(f"{v}..HEAD", "--format=%s"),
        lambda g, v: g.diff_between(v, "HEAD", "--no-renames", "-U0"),
    ],
)
def test_ref_that_starts_with_a_dash_is_not_read_as_an_option(tmp_path: Path, call) -> None:
    # 3364-7: git へ渡す ref は `--` か `--end-of-options` の後に置く。`--output=<パス>` は option なら書き込みが起きる
    repo, _ = _init_repo(tmp_path, {"a.txt": "a\n"})
    planted = tmp_path / "planted"
    with pytest.raises(git.GitError):
        call(git.Git(cwd=repo), f"--output={planted}")
    assert not planted.exists()


def test_checkout_detached_does_not_read_a_dash_ref_as_an_option(tmp_path: Path) -> None:
    # 3364-7: `--force` は option なら手元の変更を捨てて HEAD を外す。ref として読めば失敗し、変更も HEAD も残る
    repo, _ = _init_repo(tmp_path, {"a.txt": "a\n"})
    (repo / "a.txt").write_text("手元の変更\n")
    assert git.Git(cwd=repo).checkout_detached("--force").returncode != 0
    assert (repo / "a.txt").read_text() == "手元の変更\n"
    assert git_sandbox.sh(repo, "symbolic-ref", "HEAD", identity=True, strip=True) == "refs/heads/main"


def test_checkout_detached_passes_only_the_resolved_sha(tmp_path: Path) -> None:
    # 3364-7: checkout はバージョンによって --end-of-options を ref の側へ残すので、正しい ref は SHA へ解決して渡す
    repo, base = _init_repo(tmp_path, {"a.txt": "a\n"})
    done = git.Git(cwd=repo).checkout_detached("main")
    assert done.returncode == 0, done.stderr
    assert git_sandbox.sh(repo, "rev-parse", "HEAD", identity=True, strip=True) == base


def test_merge_ff_puts_the_ref_after_end_of_options() -> None:
    runner = _fake()
    git.Git(runner=runner).merge_ff("origin/develop")
    assert runner.calls == [["merge", "--ff-only", "-q", "--end-of-options", "origin/develop"]]


def test_has_ref_and_fetch_do_not_read_a_dash_value_as_an_option(tmp_path: Path) -> None:
    # 3364-7: `--head` は option なら HEAD を見せて真になる。`--upload-pack` は option ならコマンドを打つ
    repo, _ = _init_repo(tmp_path, {"a.txt": "a\n"})
    planted = tmp_path / "planted"
    g = git.Git(cwd=repo)
    assert g.has_ref("--head") is False
    assert g.fetch(f"--upload-pack=touch {planted}", "x").returncode != 0
    assert not planted.exists()


def test_changed_lines_does_not_subtract_overlapping_release_changes_twice(tmp_path: Path) -> None:
    # #3087: リリースの PR 2 本が同じバージョンのファイルを順に書き換え、範囲の間に機能の PR（19 行）が入る
    repo, base = _init_repo(tmp_path, {"VERSION": "1.0.0\n"})
    first = _merge_branch(repo, "release-1", {"VERSION": "1.0.1\n"})
    _merge_branch(repo, "feature", {"feature.txt": "".join(f"{i}\n" for i in range(19))})
    second = _merge_branch(repo, "release-2", {"VERSION": "1.0.2\n"})

    g = git.Git(cwd=repo)
    assert g.changed_lines(base, "main", exclude=[first, second[:9]]) == 19
    assert g.changed_lines(base, "main") == 19 + 2 + 2


def test_changed_files_returns_real_names_of_quoted_paths(tmp_path: Path) -> None:
    # 既定の core.quotePath では日本語・タブ・引用符を含むパスが引用されるが、実際のファイル名を返す
    repo, base = _init_repo(tmp_path, {"base.txt": "base\n"})
    names = ["設計.md", "a\tb.txt", 'q"uote.txt']
    _merge_branch(repo, "feature", {name: "x\n" for name in names})

    changed = git.Git(cwd=repo).changed_files(base, "main")
    assert sorted(changed) == sorted(names)
    assert all((repo / name).exists() for name in changed)


# 現状固定（I-002）: `Git` の各操作が git へ渡す引数・作業場所と、失敗の扱い。期待値は今の実装の出力。
def _fake_cwd(returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[tuple[list[str], Path | None]] = []

    def runner(args: list[str], cwd):
        calls.append((args, cwd))
        return git.Completed(returncode, stdout, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_returns_stdout_as_is() -> None:
    runner = _fake_cwd(stdout="x\n")
    assert git.Git(Path("/w"), runner).run("status") == "x\n"
    assert runner.calls == [(["status"], Path("/w"))]


def test_run_failure_message_names_command_and_stderr() -> None:
    with pytest.raises(git.GitError) as exc:
        git.Git(runner=_fake_cwd(128, stderr="bad\n")).run("status", "-s")
    assert str(exc.value) == "git status -s が失敗（終了コード 128）: bad"


def test_rev_parse_failures() -> None:
    with pytest.raises(git.GitError) as exc:
        git.Git(runner=_fake_cwd(1)).rev_parse("x")
    assert str(exc.value) == "git rev-parse --verify --quiet --end-of-options x^{commit} が失敗（終了コード 1）: "
    with pytest.raises(git.GitError) as exc:
        git.Git(runner=_fake_cwd(stdout="abc\n")).rev_parse("x")
    assert str(exc.value) == "x をコミットへ解決できない: 'abc'"


def test_is_ancestor_passes_args_and_cwd_and_failure_message() -> None:
    runner = _fake_cwd(0)
    assert git.Git(Path("/w"), runner).is_ancestor("a", "b") is True
    assert runner.calls == [(["merge-base", "--is-ancestor", "--", "a", "b"], Path("/w"))]
    with pytest.raises(git.GitError) as exc:
        git.Git(runner=_fake_cwd(128, stderr="oops\n")).is_ancestor("a", "b")
    assert str(exc.value) == "祖先を確かめられない（終了コード 128）: oops"


def test_merge_base_strips_output() -> None:
    runner = _fake_cwd(stdout=" abc \n")
    assert git.Git(runner=runner).merge_base("a", "b") == "abc"
    assert runner.calls == [(["merge-base", "--", "a", "b"], None)]


def test_changed_files_splits_on_nul() -> None:
    runner = _fake_cwd(stdout="a\0b c\0\0")
    assert git.Git(Path("/w"), runner).changed_files("b", "h") == ["a", "b c"]
    assert runner.calls == [(["diff", "--name-only", "-z", "--end-of-options", "b...h"], Path("/w"))]


def test_changed_lines_with_fake_log_excludes_short_sha_case_insensitively() -> None:
    log = f"@{'a' * 40}\n\n 1 file changed, 2 insertions(+)\n@{'b' * 40}\n\n 1 file changed, 3 deletions(-)\n"
    runner = _fake_cwd(stdout=log)
    assert git.Git(runner=runner).changed_lines("b", "h", exclude=["BBBBBBB"]) == 2
    assert runner.calls == [
        (["log", "--first-parent", "--diff-merges=first-parent", "--shortstat", "--format=@%H", "--end-of-options", "b..h"], None)
    ]
    assert git.Git(runner=_fake_cwd(stdout=log)).changed_lines("b", "h") == 5


def test_worktrees_reads_porcelain_z() -> None:
    text = f"worktree /m\0HEAD {'a' * 40}\0branch refs/heads/develop\0\0worktree /m/.worktrees/x\0HEAD {'b' * 40}\0detached\0prunable gone\0locked\0\0"
    runner = _fake_cwd(stdout=text)
    assert git.Git(runner=runner).worktrees() == [
        git.WorktreeEntry(Path("/m"), "a" * 40, "develop", False, False),
        git.WorktreeEntry(Path("/m/.worktrees/x"), "b" * 40, None, True, True),
    ]
    assert runner.calls == [(["worktree", "list", "--porcelain", "-z"], None)]


def test_fetch_returns_completed_without_raising() -> None:
    runner = _fake_cwd(1, stderr="no remote")
    assert git.Git(Path("/w"), runner).fetch("origin", "a:b") == git.Completed(1, "", "no remote")
    assert runner.calls == [(["fetch", "-q", "--no-tags", "--", "origin", "a:b"], Path("/w"))]


def test_remote_repo_passes_remote_name_and_propagates_failure() -> None:
    runner = _fake_cwd(stdout="git@github.com:o/n.git\n")
    assert git.Git(runner=runner).remote_repo("up") == "o/n"
    assert runner.calls == [(["remote", "get-url", "--", "up"], None)]
    with pytest.raises(git.GitError) as exc:
        git.Git(runner=_fake_cwd(2, stderr="no such remote")).remote_repo("up")
    assert str(exc.value) == "git remote get-url -- up が失敗（終了コード 2）: no such remote"


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("https://h/o/r.git?access_token=dummy&x=1", "https://h/o/r.git?***"),
        ("https://user:dummy@h/o/r.git", "https://***@h/o/r.git"),
        ("https://dummy@h/o/r.git?t=dummy@x", "https://***@h/o/r.git?***"),
        ("https://h/o/r.git", "https://h/o/r.git"),
    ],
)
@pytest.mark.parametrize("call", [lambda g: g.fetch_branch_or_raise("origin", "main"), lambda g: g.run("cat-file", "blob", "x")])
def test_failure_message_masks_credentials_and_query_of_urls(call, url: str, shown: str) -> None:
    with pytest.raises(git.GitError) as exc:
        call(git.Git(runner=_fake(128, stderr=f"fatal: unable to access {url} : Could not resolve host")))
    assert str(exc.value).endswith(f"fatal: unable to access {shown} : Could not resolve host")


def test_run_git_disables_terminal_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 3, "o", "e")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert git.run_git(["status"], Path("/w")) == git.Completed(3, "o", "e")
    assert seen["cmd"] == ["git", "status"] and seen["cwd"] == Path("/w") and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_run_git_raises_when_git_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("no git")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(git.GitError, match="git を起動できない"):
        git.run_git(["status"], None)


def test_run_bytes_returns_stdout_without_newline_conversion(tmp_path: Path) -> None:
    path, _ = _init_repo(tmp_path, {"a": "a\n"})
    raw = b"\xef\xbb\xbf1\r\n2\xff\r\n"
    (path / "raw").write_bytes(raw)
    oid = git_sandbox.sh(path, "hash-object", "-w", "--no-filters", "raw", strip=True)
    assert git.Git(path).run_bytes("cat-file", "blob", oid) == raw
    with pytest.raises(git.GitError, match="git cat-file blob nope が失敗（終了コード 128）: fatal"):
        git.Git(path).run_bytes("cat-file", "blob", "nope")


def test_run_bytes_disables_terminal_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, b"o", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert git.Git(Path("/w")).run_bytes("status") == b"o"
    assert seen["cmd"] == ["git", "status"] and seen["cwd"] == Path("/w") and seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_common_dir_is_real_main_git_dir_from_main_and_linked_worktree(tmp_path: Path) -> None:
    path, _ = _init_repo(tmp_path, {"a": "a\n"})
    git_sandbox.sh(path, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"), identity=True, strip=True)
    link = tmp_path / "link"
    link.symlink_to(path)
    want = Path(os.path.realpath(path / ".git"))
    assert git.Git(cwd=path).common_dir() == want
    assert git.Git(cwd=tmp_path / "side").common_dir() == want
    assert git.Git(cwd=link).common_dir() == want


def test_common_dir_propagates_git_failure() -> None:
    with pytest.raises(git.GitError):
        git.Git(runner=_fake(128, stderr="fatal: not a git repository\n")).common_dir()


# 現状固定（I-009）: `localenv.changed_paths` が `git status --porcelain -z` を読む形。期待値は今の実装の出力。
def test_changed_paths_reads_status_with_renames_and_untracked(tmp_path: Path) -> None:
    from ndf.domains.worktree import localenv

    repo, _ = _init_repo(tmp_path, {"keep.txt": "a\n", "mod.txt": "x\n", "old.txt": "old\n" * 20, "del.txt": "d\n"})
    (repo / "mod.txt").write_text("x\ny\n")
    git_sandbox.sh(repo, "mv", "old.txt", "新 名.txt", identity=True, strip=True)
    (repo / "del.txt").unlink()
    (repo / "u" / "v").mkdir(parents=True)
    (repo / "u" / "v" / "n.txt").write_text("n\n")
    (repo / "staged.txt").write_text("s\n")
    git_sandbox.sh(repo, "add", "staged.txt", identity=True, strip=True)

    # 改名は新しいパスの後に変更前のパス。追跡していないディレクトリは中のファイルまで
    assert localenv.changed_paths(repo) == ["del.txt", "mod.txt", "staged.txt", "新 名.txt", "old.txt", "u/v/n.txt"]
    (tmp_path / "clean").mkdir()
    assert localenv.changed_paths(_init_repo(tmp_path / "clean", {"a": "a\n"})[0]) == []


@pytest.mark.parametrize(
    ("stdout", "want"),
    [
        ("C  dst\0src\0M  b\0", ["dst", "src", "b"]),  # 複製も変更前のパスを含める
        ("R  new\0old\0", ["new", "old"]),
        ("R  new\0", ["new", ""]),  # NOTE: 現状固定。末尾の空の項目を変更前のパスとして読む
        ("ab\0 M c\0\0", ["c"]),  # 4 文字に満たない項目は飛ばす
        ("", []),
    ],
)
def test_changed_paths_parses_porcelain_z(monkeypatch: pytest.MonkeyPatch, stdout: str, want: list[str]) -> None:
    from ndf.domains.worktree import localenv

    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert localenv.changed_paths(Path("/w")) == want
    assert seen == [["git", "status", "--porcelain", "-z", "--untracked-files=all"]]


@pytest.mark.parametrize(
    ("stdout", "want"),
    [
        ("C  dst\0src\0M  b\0", ["dst", "src", "b"]),
        ("R  new\0old\0", ["new", "old"]),
        ("R  new\0", ["new", ""]),  # NOTE: 現状固定。末尾の空の項目を変更前のパスとして読む
        ("ab\0 M c\0\0", ["c"]),
        (" M a b\0?? 改\n行\0", ["a b", "改\n行"]),  # 空白・改行を含む名前もそのまま
        ("", []),
    ],
)
def test_status_paths_parses_porcelain_z(stdout: str, want: list[str]) -> None:
    runner = _fake_cwd(stdout=stdout)
    assert git.Git(Path("/w"), runner).status_paths() == want
    assert runner.calls == [(["status", "--porcelain", "-z", "--untracked-files=all"], Path("/w"))]


# 現状固定（I-021）: `Git.has_ref`（元は `repo.has_ref`）の終了コードの読み方。期待値は今の実装の出力。
@pytest.mark.parametrize(("code", "expected"), [(0, True), (1, False)])
def test_has_ref_reads_exit_code(code: int, expected: bool) -> None:
    calls: list = []

    def runner(args: list[str], cwd):
        calls.append((args, cwd))
        return git.Completed(code, "", "")

    assert git.Git(Path("/w"), runner).has_ref("refs/heads/x") is expected
    assert calls == [(["show-ref", "--verify", "--quiet", "--", "refs/heads/x"], Path("/w"))]


def test_has_ref_raises_on_git_failure() -> None:
    with pytest.raises(git.GitError) as exc:
        git.Git(runner=_fake(128, stderr="fatal: bad\n")).has_ref("refs/heads/x")
    assert str(exc.value) == "参照を確かめられない（終了コード 128）: fatal: bad"


def test_has_ref_with_real_repository(tmp_path: Path) -> None:
    path, _ = _init_repo(tmp_path, {"a": "a\n"})
    g = git.Git(cwd=path)
    assert g.has_ref("refs/heads/main") is True
    assert g.has_ref("refs/heads/none") is False
    assert g.has_ref("main") is False  # 完全な参照の名前だけを見る


def _declare(main: Path, decl: object) -> None:
    (main / ".ndf").mkdir(exist_ok=True)
    (main / ".ndf" / "worktree.json").write_text(json.dumps(decl), encoding="utf-8")


def test_default_remote_comes_from_the_schema_default() -> None:
    """既定のリモート名はスキーマの `default` の 1 か所から読む（#3617 の I7a）。"""
    rel, pointer = git.REMOTE_SCHEMA
    node = json.loads((identity.ROOT / rel).read_text(encoding="utf-8"))
    for key in pointer:
        node = node[key]
    assert git.default_remote() == node


def test_remote_of_reads_the_ndf_settings_then_the_default(tmp_path: Path) -> None:
    """設定の `remote` を返し、設定かキーが無ければ既定（受け入れ条件 8・9）。"""
    assert git.remote_of(tmp_path) == git.default_remote()
    _declare(tmp_path, {"version": 1})
    assert git.remote_of(tmp_path) == git.default_remote()
    _declare(tmp_path, {"version": 1, "remote": "upstream"})
    assert git.remote_of(tmp_path) == "upstream"


@pytest.mark.parametrize("value", ["", "-x", 3])
def test_remote_of_refuses_broken_values(tmp_path: Path, value: object) -> None:
    _declare(tmp_path, {"version": 1, "remote": value})
    with pytest.raises(git.RemoteError):
        git.remote_of(tmp_path)


def test_unknown_module_attribute_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError):
        git.NO_SUCH_NAME  # noqa: B018


def test_remote_reads_the_main_directory_from_a_linked_worktree(tmp_path: Path) -> None:
    """linked worktree からでも、メインディレクトリの設定のリモート名を読む。"""
    path, _ = _init_repo(tmp_path, {"a": "a\n"})
    _declare(path, {"version": 1, "remote": "upstream"})
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(path), "worktree", "add", "-q", "--detach", str(linked)], check=True)
    assert git.Git(linked).remote() == "upstream"


def test_remote_repo_uses_the_declared_remote(tmp_path: Path) -> None:
    path, _ = _init_repo(tmp_path, {"a": "a\n"})
    subprocess.run(["git", "-C", str(path), "remote", "add", "upstream", "https://github.com/o/up.git"], check=True)
    _declare(path, {"version": 1, "remote": "upstream"})
    assert git.Git(path).remote_repo() == "o/up"
    with pytest.raises(git.GitError):
        git.Git(path).remote_repo("origin")
