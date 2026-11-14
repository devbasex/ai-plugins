"""リリース後テストの前の、メインディレクトリのベースブランチのそろえ（本物の git で確かめる）。"""

from __future__ import annotations

from pathlib import Path

from ndf.domains.release import sync_decide, verify

from ._fakes import git, init_repo


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """origin（bare）と、メインディレクトリの clone と、origin の develop を 1 つ進める別の clone。"""
    seed = init_repo(tmp_path / "seed")
    (seed / "plugin.json").write_text('{"version": "1.0.0"}\n', encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "branch", "main")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    main_dir = tmp_path / "main"
    git(tmp_path, "clone", "-q", "-b", "develop", str(origin), str(main_dir))
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", "-b", "develop", str(origin), str(other))
    for repo in (main_dir, other):
        git(repo, "config", "user.email", "t@example.invalid")
        git(repo, "config", "user.name", "t")
        git(repo, "config", "commit.gpgsign", "false")
    (other / "plugin.json").write_text('{"version": "1.0.1"}\n', encoding="utf-8")
    git(other, "commit", "-q", "-am", "bump")
    git(other, "push", "-q", "origin", "develop")
    return main_dir, other


def test_issue_2140_main_dir_on_production_branch_still_moves_base(tmp_path: Path) -> None:
    """#2140: メインディレクトリが本番ブランチにいても、取り出さずにベースブランチの参照を進める。"""
    main_dir, other = _setup(tmp_path)
    git(main_dir, "switch", "-q", "main")
    plan = verify.sync_base(main_dir, "develop")
    assert plan.action == sync_decide.UPDATE_REF
    assert git(main_dir, "rev-parse", "develop") == git(other, "rev-parse", "HEAD")
    assert git(main_dir, "branch", "--show-current").strip() == "main"


def test_issue_3108_leftover_version_file_with_landed_content_is_restored(tmp_path: Path) -> None:
    """#3108: メインディレクトリに残った版のファイルの変更が origin と同じ中身なら、戻してから取り込む。"""
    main_dir, other = _setup(tmp_path)
    (main_dir / "plugin.json").write_text('{"version": "1.0.1"}\n', encoding="utf-8")
    plan = verify.sync_base(main_dir, "develop")
    assert plan == sync_decide.Plan(sync_decide.RESTORE_THEN_FF, ("plugin.json",))
    assert git(main_dir, "rev-parse", "HEAD") == git(other, "rev-parse", "HEAD")
    assert git(main_dir, "status", "--porcelain") == ""


def test_user_change_that_differs_from_origin_stops(tmp_path: Path) -> None:
    main_dir, _other = _setup(tmp_path)
    (main_dir / "plugin.json").write_text('{"version": "9.9.9"}\n', encoding="utf-8")
    plan = verify.sync_base(main_dir, "develop")
    assert plan == sync_decide.Plan(sync_decide.STOP, ("plugin.json",))
    assert (main_dir / "plugin.json").read_text(encoding="utf-8") == '{"version": "9.9.9"}\n'
