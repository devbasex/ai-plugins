"""`ndf statusline` の導入・切り替え・復元（#3569）。"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from ndf.domains.statusline import cli, settings


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def _settings(tmp_path: Path) -> Path:
    return tmp_path / "claude-config" / "settings.json"


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _line(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8")).get("statusLine")


def test_ensure_sets_when_absent_and_keeps_user_setting(tmp_path, home, capsys, last_result):
    assert cli.main(["ensure"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["action"] == "set"
    assert _line(_settings(tmp_path))["command"] == settings.NDF_COMMAND
    body = home / ".claude" / settings.BODY_NAME
    assert body.read_bytes() == settings.BODY_SOURCE.read_bytes()
    assert cli.main(["ensure"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["action"] == "kept"
    user = {"type": "command", "command": "bash ~/mine.sh"}
    _write(_settings(tmp_path), {"statusLine": user})
    assert cli.main(["ensure"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["action"] == "kept"
    assert _line(_settings(tmp_path)) == user


def test_ensure_migrates_a_managed_copy(tmp_path, home, capsys, last_result):
    copy = home / ".claude" / "statusline-command.sh"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text("# ndf-statusline: managed\n", encoding="utf-8")
    old = {"type": "command", "command": "bash ~/.claude/statusline-command.sh"}
    _write(_settings(tmp_path), {"statusLine": old})
    assert cli.main(["ensure"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["action"] == "updated"
    assert _line(_settings(tmp_path))["command"] == settings.NDF_COMMAND
    assert json.loads((_settings(tmp_path).parent / settings.BACKUP_NAME).read_text()) == old


def test_ensure_skips_a_broken_settings_file(tmp_path, home, capsys, last_result):
    _settings(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _settings(tmp_path).write_text("{", encoding="utf-8")
    assert cli.main(["ensure"]) == 0
    out = last_result(capsys.readouterr().out)
    assert out["items"][0]["action"] == "skipped"
    assert out["warnings"]


def test_set_backs_up_and_restore_returns(tmp_path, home, capsys, last_result):
    user = {"type": "command", "command": "bash ~/mine.sh"}
    _write(_settings(tmp_path), {"statusLine": user, "theme": "dark"})
    assert cli.main(["set"]) == 0
    assert _line(_settings(tmp_path))["command"] == settings.NDF_COMMAND
    assert cli.main(["status"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["backup"] is True
    assert cli.main(["restore"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["action"] == "restored"
    data = json.loads(_settings(tmp_path).read_text())
    assert data == {"statusLine": user, "theme": "dark"}
    assert not (_settings(tmp_path).parent / settings.BACKUP_NAME).exists()


def test_results_mask_a_secret_in_the_user_command(tmp_path, home, capsys):
    user = {"type": "command", "command": "API_KEY=xxxxxxxxxxxx bash ~/mine.sh"}
    _write(_settings(tmp_path), {"statusLine": user})
    for command in ("status", "set", "restore"):
        assert cli.main([command]) == 0
        out = capsys.readouterr().out
        assert "xxxxxxxxxxxx" not in out, command
    assert _line(_settings(tmp_path)) == user


def test_switching_keeps_a_private_settings_file_private(tmp_path, home):
    path = _settings(tmp_path)
    _write(path, {"statusLine": {"type": "command", "command": "bash ~/mine.sh"}, "env": {"TOKEN": "x"}})
    path.chmod(0o600)
    assert cli.main(["set"]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((path.parent / settings.BACKUP_NAME).stat().st_mode) == 0o600


@pytest.mark.parametrize("earlier", ['{"type": "command", "command": "bash ~/first.sh"}\n', None])
def test_failed_set_puts_the_earlier_backup_back(tmp_path, home, monkeypatch, earlier):
    """settings.json を書けなければ、バックアップを操作の前の状態（前のバックアップの中身・無ければ無し）へ戻す。"""
    base = tmp_path / "cfg"
    p = settings.Paths(base / "settings.json", base / settings.BACKUP_NAME, base / settings.LOCK_NAME, home / ".claude" / "body.sh")
    _write(p.settings, {"statusLine": {"type": "command", "command": "bash ~/second.sh"}})
    if earlier is not None:
        p.backup.write_text(earlier, encoding="utf-8")
    real = settings._write

    def fail_on_settings(path, data):
        if path == p.settings:
            raise settings.StatuslineError("unknown", "書けない")
        real(path, data)

    monkeypatch.setattr(settings, "_write", fail_on_settings)
    with pytest.raises(settings.StatuslineError):
        settings.set_ndf(p)
    if earlier is None:
        assert not p.backup.exists()
    else:
        assert p.backup.read_text(encoding="utf-8") == earlier


def test_restore_without_backup_removes_only_the_ndf_line(tmp_path, home, capsys, last_result):
    user = {"type": "command", "command": "bash ~/mine.sh"}
    _write(_settings(tmp_path), {"statusLine": user})
    assert cli.main(["restore"]) == 0
    assert _line(_settings(tmp_path)) == user
    assert cli.main(["set"]) == 0
    (_settings(tmp_path).parent / settings.BACKUP_NAME).unlink()
    assert cli.main(["restore"]) == 0
    assert last_result(capsys.readouterr().out)["items"][0]["action"] == "removed"
    assert _line(_settings(tmp_path)) is None


def test_symlinked_settings_are_written_through(tmp_path, home):
    real = tmp_path / "dotfiles" / "settings.json"
    _write(real, {})
    link = _settings(tmp_path)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    assert cli.main(["set"]) == 0
    assert link.is_symlink()
    assert _line(real)["command"] == settings.NDF_COMMAND


def test_broken_settings_stop_set_and_status(tmp_path, home, capsys, last_result):
    _settings(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _settings(tmp_path).write_text("[]", encoding="utf-8")
    assert cli.main(["status"]) == 2
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert cli.main(["set"]) == 2


def test_unknown_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["fly"])
    assert exc.value.code == 2
