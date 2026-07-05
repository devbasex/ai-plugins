"""statusline-switch.sh `ensure` のバージョンアップ追従ロジックを検証する。

NDF が過去に配置した statusline コピー (マーカー付き / レガシー
statusline-command.sh) を settings.json が指している場合に、正規パス
(~/.claude/ndf-statusline.sh) 参照へ自動移行することを確認する。
ユーザー独自の statusline は決して上書きしないこと (誤検出ガード) も検証する。

既存 cross-review テストの規約に倣い、隔離 HOME 上で bash スクリプトを
subprocess 実行して settings.json の最終状態を観測する。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# bash / jq が無ければモジュールごと skip
for _cmd in ("bash", "jq"):
    if shutil.which(_cmd) is None:
        pytest.skip(f"{_cmd} not available", allow_module_level=True)

# plugins/ndf/skills/statusline/tests/ -> plugins/ndf/scripts/statusline-switch.sh
SWITCH = Path(__file__).resolve().parents[3] / "scripts" / "statusline-switch.sh"
NDF_COMMAND = "bash ~/.claude/ndf-statusline.sh"

# NDF statusline コピーとみなされる最小内容 (レガシー判定用: ctx ラベル + コンテナ名取得)
LEGACY_COPY = (
    "#!/bin/bash\n"
    "container_name=$(hostname)\n"
    'printf "[ctx: 1k / 2k tokens (5%%)]"\n'
)
# マーカー付きコピー (将来の全コピーが該当)
MARKED_COPY = (
    "#!/bin/bash\n"
    "# ndf-statusline: managed (do not edit; auto-updated by ndf:statusline)\n"
    'echo "hi"\n'
)
# NDF と無関係なユーザー独自 statusline
CUSTOM = '#!/bin/bash\necho "my custom bar"\n'


def _run_ensure(home: Path) -> subprocess.CompletedProcess:
    """隔離 HOME で `statusline-switch.sh ensure` を実行する。"""
    env = os.environ.copy()
    env["HOME"] = str(home)
    return subprocess.run(
        ["bash", str(SWITCH), "ensure"],
        capture_output=True,
        text=True,
        env=env,
    )


def _settings(home: Path) -> dict:
    return json.loads((home / ".claude" / "settings.json").read_text())


def _write_settings(home: Path, command: str) -> None:
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": command}})
    )


def _claude(home: Path) -> Path:
    d = home / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_legacy_copy_is_migrated(tmp_path: Path) -> None:
    """マーカー無しの既知レガシー statusline-command.sh は正規パスへ移行される。"""
    claude = _claude(tmp_path)
    (claude / "statusline-command.sh").write_text(LEGACY_COPY)
    _write_settings(tmp_path, "bash ~/.claude/statusline-command.sh")

    result = _run_ensure(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _settings(tmp_path)["statusLine"]["command"] == NDF_COMMAND
    # 既存設定がバックアップされていること
    assert (claude / ".ndf-statusline-backup.json").is_file()


def test_marked_copy_is_migrated(tmp_path: Path) -> None:
    """マーカー付きコピー (任意のファイル名) は正規パスへ移行される。"""
    claude = _claude(tmp_path)
    (claude / "my-status.sh").write_text(MARKED_COPY)
    _write_settings(tmp_path, "bash ~/.claude/my-status.sh")

    result = _run_ensure(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _settings(tmp_path)["statusLine"]["command"] == NDF_COMMAND


def test_official_ndf_path_unchanged(tmp_path: Path) -> None:
    """既に正規パスを指している場合は何もしない (deploy_script で本体追従済み)。"""
    _claude(tmp_path)
    _write_settings(tmp_path, NDF_COMMAND)

    result = _run_ensure(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _settings(tmp_path)["statusLine"]["command"] == NDF_COMMAND
    # 正規パスの場合はバックアップを作らない
    assert not (tmp_path / ".claude" / ".ndf-statusline-backup.json").exists()


def test_user_custom_is_respected(tmp_path: Path) -> None:
    """ユーザー独自 statusline (マーカー無し・別名) は尊重し上書きしない。"""
    claude = _claude(tmp_path)
    (claude / "mybar.sh").write_text(CUSTOM)
    _write_settings(tmp_path, "bash ~/.claude/mybar.sh")

    result = _run_ensure(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _settings(tmp_path)["statusLine"]["command"] == "bash ~/.claude/mybar.sh"
    assert not (claude / ".ndf-statusline-backup.json").exists()


def test_same_name_but_non_ndf_content_is_guarded(tmp_path: Path) -> None:
    """statusline-command.sh でも NDF 特徴を含まなければ移行しない (誤検出ガード)。"""
    claude = _claude(tmp_path)
    (claude / "statusline-command.sh").write_text(CUSTOM)
    _write_settings(tmp_path, "bash ~/.claude/statusline-command.sh")

    result = _run_ensure(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (
        _settings(tmp_path)["statusLine"]["command"]
        == "bash ~/.claude/statusline-command.sh"
    )
    assert not (claude / ".ndf-statusline-backup.json").exists()


def test_unset_statusline_gets_ndf_default(tmp_path: Path) -> None:
    """statusLine 未設定なら NDF 標準を新規設定する。"""
    claude = _claude(tmp_path)
    (claude / "settings.json").write_text("{}")

    result = _run_ensure(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _settings(tmp_path)["statusLine"]["command"] == NDF_COMMAND
