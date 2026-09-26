"""書き込み用の作業ディレクトリは、開発用の worktree と同じ head ブランチでも作れる（#638）。

worktree の運用では、Pull Request の head ブランチが `.worktrees/<ブランチ名>` で checkout 済みのことが多い。
git は同じブランチを 2 つの作業ツリーへ checkout できないため、書き込み用の作業ディレクトリは detach で作る。
"""
import subprocess


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo_with_dev_worktree(tmp_path):
    """origin と、head ブランチを開発用の worktree で checkout 済みのメインディレクトリ。"""
    origin = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    main = tmp_path / "main"
    _git("clone", "-q", str(origin), str(main), cwd=tmp_path)
    _git("config", "user.email", "t@e.st", cwd=main)
    _git("config", "user.name", "test", cwd=main)
    (main / "a.txt").write_text("1\n")
    _git("add", "-A", cwd=main)
    _git("commit", "-qm", "init", cwd=main)
    _git("push", "-q", "origin", "main", cwd=main)
    _git("switch", "-q", "-c", "feat/x", cwd=main)
    (main / "a.txt").write_text("2\n")
    _git("commit", "-qam", "change", cwd=main)
    _git("push", "-q", "origin", "feat/x", cwd=main)
    _git("switch", "-q", "main", cwd=main)
    dev = main / ".worktrees" / "feat" / "x"
    _git("worktree", "add", "-q", str(dev), "feat/x", cwd=main)
    return main


def test_work_worktree_is_created_detached_beside_dev_worktree(cmd_setup, tmp_path, monkeypatch):
    main = _repo_with_dev_worktree(tmp_path)
    monkeypatch.chdir(main)
    work = tmp_path / "tmp" / "rf1" / "work"

    cmd_setup._ensure_work_worktree(work, "feat/x")

    assert _git("rev-parse", "HEAD", cwd=work).stdout == _git("rev-parse", "origin/feat/x", cwd=main).stdout
    detached = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=work, capture_output=True, text=True)
    assert detached.returncode == 1


def test_work_worktree_is_reused_and_synced(cmd_setup, tmp_path, monkeypatch):
    main = _repo_with_dev_worktree(tmp_path)
    monkeypatch.chdir(main)
    work = tmp_path / "tmp" / "rf1" / "work"
    cmd_setup._ensure_work_worktree(work, "feat/x")
    dev = main / ".worktrees" / "feat" / "x"
    (dev / "a.txt").write_text("3\n")
    _git("commit", "-qam", "more", cwd=dev)
    _git("push", "-q", "origin", "feat/x", cwd=dev)

    cmd_setup._ensure_work_worktree(work, "feat/x")

    assert _git("rev-parse", "HEAD", cwd=work).stdout == _git("rev-parse", "HEAD", cwd=dev).stdout


def test_run_test_at_returns_to_detached_head(gitfacts, tmp_path, monkeypatch):
    """detach した作業ディレクトリでは、ブランチ名ではなく元のコミットへ戻る。"""
    main = _repo_with_dev_worktree(tmp_path)
    work = tmp_path / "work"
    _git("worktree", "add", "-q", "--detach", str(work), "origin/feat/x", cwd=main)
    head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    base = _git("rev-parse", "origin/main", cwd=main).stdout.strip()

    assert gitfacts.run_test_at(str(work), base, "true", "feat/x", 60) == "pass"

    assert _git("rev-parse", "HEAD", cwd=work).stdout.strip() == head
