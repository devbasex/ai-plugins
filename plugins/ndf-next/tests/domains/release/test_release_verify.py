"""リリース後テストの前の、メインディレクトリのベースブランチのそろえ（本物の git で確かめる）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.release import sync_decide, verify

from ._fakes import clone, git, init_repo


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """origin（bare）と、メインディレクトリの clone と、origin の develop を 1 つ進める別の clone。"""
    seed = init_repo(tmp_path / "seed")
    (seed / "plugin.json").write_text('{"version": "1.0.0"}\n', encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "branch", "main")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    main_dir = clone(tmp_path, origin, "main")
    other = clone(tmp_path, origin, "other")
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


def _config_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """利用者の設定ディレクトリ（Claude Code・Codex）を tmp_path の下へ向ける。"""
    claude, codex = tmp_path / "claude", tmp_path / "codex"
    (claude / "plugins").mkdir(parents=True)
    for version in ("1.0.0", "0.9.0"):
        (codex / "plugins" / "cache" / "m" / "kit" / version).mkdir(parents=True)
    (codex / "plugins" / "cache" / "m" / "kit" / "note.txt").write_text("x", encoding="utf-8")
    (codex / "plugins" / "cache" / "m" / "loose.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv(verify.CLAUDE_DIR_ENV, str(claude))
    monkeypatch.setenv(verify.CODEX_DIR_ENV, str(codex))
    return claude, codex


def test_snapshot_reads_config_dirs_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """現状固定（snapshot）: 環境変数の設定ディレクトリから 4 つの置き場を読み、鍵の順に並べる。"""
    claude, codex = _config_dirs(tmp_path, monkeypatch)
    installed = {"plugins": {"kit@m": [{"scope": "user", "version": "1.0.0"}]}}
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps(installed), encoding="utf-8")
    known = {"m": {"source": {"source": "github", "repo": "o/r"}}}
    (claude / "plugins" / "known_marketplaces.json").write_text(json.dumps(known), encoding="utf-8")
    toml = '[marketplaces.m]\nsource_type="git"\nsource="https://example.invalid/r.git"\n[plugins."kit@m"]\nenabled=true\n'
    (codex / "config.toml").write_text(toml, encoding="utf-8")
    got = verify.snapshot()
    assert got == verify.Snapshot(
        {
            "claude:marketplace:m": '{"repo": "o/r", "source": "github"}',
            "claude:plugin:kit@m": "user:1.0.0",
            "codex:marketplace:m": '{"ref": null, "source": "https://example.invalid/r.git", "source_type": "git"}',
            "codex:plugin:kit@m": "enabled=true versions=0.9.0,1.0.0",
        },
        (),
    )
    assert list(got.items) == sorted(got.items)


def test_snapshot_of_missing_dirs_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """現状固定（snapshot）: 設定ディレクトリが無ければ控えも読めなかったものも空。"""
    monkeypatch.setenv(verify.CLAUDE_DIR_ENV, str(tmp_path / "none1"))
    monkeypatch.setenv(verify.CODEX_DIR_ENV, str(tmp_path / "none2"))
    assert verify.snapshot() == verify.Snapshot({}, ())


def test_snapshot_at_reads_given_dirs_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """snapshot_at: 渡したディレクトリだけを読み、環境変数の設定ディレクトリを読まない。"""
    claude, codex = _config_dirs(tmp_path, monkeypatch)
    monkeypatch.setenv(verify.CLAUDE_DIR_ENV, str(tmp_path / "none1"))
    monkeypatch.setenv(verify.CODEX_DIR_ENV, str(tmp_path / "none2"))
    installed = {"plugins": {"kit@m": [{"scope": "user", "version": "1.0.0"}]}}
    (claude / "plugins" / "installed_plugins.json").write_text(json.dumps(installed), encoding="utf-8")
    assert verify.snapshot_at(claude, codex) == verify.Snapshot(
        {"claude:plugin:kit@m": "user:1.0.0", "codex:plugin:kit@m": "enabled=- versions=0.9.0,1.0.0"}, ()
    )
    assert verify.snapshot() == verify.Snapshot({}, ())


def test_config_dirs_fall_back_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """現状固定（config_dirs）: 環境変数が無いか空なら、ホームの .claude と .codex。"""
    monkeypatch.delenv(verify.CLAUDE_DIR_ENV, raising=False)
    monkeypatch.delenv(verify.CODEX_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert verify.config_dirs() == (tmp_path / ".claude", tmp_path / ".codex")
    monkeypatch.setenv(verify.CLAUDE_DIR_ENV, "")
    assert verify.config_dirs() == (tmp_path / ".claude", tmp_path / ".codex")


def test_snapshot_records_unreadable_files_in_read_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """現状固定（snapshot）: 読めない・形の違うファイルは控えに入れず、読んだ順に `<パス>: <理由>` で残す。"""
    claude, codex = _config_dirs(tmp_path, monkeypatch)
    installed, known, config = (
        claude / "plugins" / "installed_plugins.json",
        claude / "plugins" / "known_marketplaces.json",
        codex / "config.toml",
    )
    installed.write_text("{bad", encoding="utf-8")
    known.write_text("[]", encoding="utf-8")
    config.write_text("= bad", encoding="utf-8")
    got = verify.snapshot()
    assert got.items == {"codex:plugin:kit@m": "enabled=- versions=0.9.0,1.0.0"}
    assert len(got.unreadable) == 3
    assert got.unreadable[0].startswith(f"{installed}: Expecting property name")
    assert got.unreadable[1] == f"{known}: known_marketplaces.json が表でない"
    assert got.unreadable[2].startswith(f"{config}: ")
    installed.write_text(json.dumps({"plugins": []}), encoding="utf-8")
    known.write_text("{}", encoding="utf-8")
    config.write_text("", encoding="utf-8")
    assert verify.snapshot() == verify.Snapshot(
        {"codex:plugin:kit@m": "enabled=- versions=0.9.0,1.0.0"}, (f"{installed}: installed_plugins.json の plugins の形が違う",)
    )
