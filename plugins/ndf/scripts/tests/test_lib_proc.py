"""子プロセスと git の起動（lib/proc.py）とリポジトリの識別（lib/repo.py）（#1142 の L0）。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import branch_strategy  # noqa: E402
import proc  # noqa: E402
import repo  # noqa: E402
import step_result  # noqa: E402
import strategy_fixture  # noqa: E402


def git_init(path: Path, origin: str | None = None) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin], check=True)
    return path


def test_run_raises_step_error_with_code_1():
    with pytest.raises(proc.StepError) as e:
        proc.run([sys.executable, "-c", "import sys; sys.stderr.write('ng'); sys.exit(4)"])
    assert e.value.code == 1 and "終了コード 4" in str(e.value) and "ng" in str(e.value)
    assert proc.run([sys.executable, "-c", "print('ok')"]).stdout == "ok\n"
    assert proc.run([sys.executable, "-c", "raise SystemExit(3)"], check=False).returncode == 3


def test_step_result_reexports_the_same_error_and_runners():
    assert step_result.StepError is proc.StepError
    assert step_result.git is proc.git and step_result.run is proc.run and step_result.git_root is proc.git_root


def test_git_and_git_out(tmp_path):
    root = git_init(tmp_path / "r")
    assert proc.git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    assert proc.git_out(root, "rev-parse", "--is-inside-work-tree") == "true"
    assert proc.git_out(tmp_path / "missing", "status") is None


def test_git_root_outside_a_work_tree_is_code_2(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(proc.StepError) as e:
        proc.git_root(None)
    assert e.value.code == 2
    assert proc.git_root(str(tmp_path)) == tmp_path.resolve()


def test_die_and_info(capsys):
    proc.info("途中")
    with pytest.raises(SystemExit) as e:
        proc.die("止まる", 3)
    assert e.value.code == 3
    err = capsys.readouterr().err
    assert "途中" in err and "❌ 止まる" in err


@pytest.mark.parametrize(
    "url", ["https://github.com/o/n.git", "git@github.com:o/n.git", "https://github.com/o/n", "ssh://git@github.com/o/n.git/"]
)
def test_owner_repo_from_url(url):
    assert repo.owner_repo_from_url(url) == "o/n"


def test_owner_repo_and_slug(tmp_path):
    root = git_init(tmp_path / "r", "git@github.com:devbasex/ai-plugins.git")
    assert repo.owner_repo(root) == "devbasex/ai-plugins"
    assert repo.slug(repo.owner_repo(root)) == "devbasex--ai-plugins"
    assert repo.owner_repo(git_init(tmp_path / "bare")) is None
    assert repo.slug(None) is None


def test_main_dir_from_a_linked_worktree(tmp_path):
    root = git_init(tmp_path / "main")
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "i"], check=True
    )
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", str(tmp_path / "wt")], check=True)
    assert repo.main_dir(tmp_path / "wt") == root.resolve()
    assert repo.main_dir(root) == root.resolve()
    assert repo.main_dir(tmp_path / "nothing") is None


def test_base_branch_reads_the_strategy_and_never_falls_back(tmp_path):
    """#3672 の I3: ベースブランチは宣言の `branches.base` だけから読み、宣言が無い・壊れているときは止まる。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(branch_strategy.StrategyError):
        repo.base_branch(tmp_path)
    strategy_fixture.declare(tmp_path, "develop", "main")
    assert repo.base_branch(tmp_path) == "develop" and repo.production_branch(tmp_path) == "main"
    (tmp_path / ".ndf" / "project.json").write_text("{")
    with pytest.raises(branch_strategy.StrategyError):
        repo.base_branch(tmp_path)
