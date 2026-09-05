"""agy へ hook を差し込む導入スクリプトの振る舞いを固定する（#305）。

**`agy plugin install` は `hooks.json` を複製するが、agy はそれを読み込まない。**
読む先は利用者の `~/.gemini/config/hooks.json` の 1 か所だけである（agy 1.1.26 で実測。
プラグイン配下とプロジェクト直下のどちらに置いても
`loaded 1 named hooks from 1 hooks.json file(s)` のまま変わらない）。
`dev.agy/install-hooks.sh` がその 1 か所へ差し込む。

ここで見るのは、**他の項目に触れないこと**・**冪等であること**・**相対の指定を
導入先の絶対パスへ直すこと**・**壊れた設定を黙って上書きしないこと**の 4 つである。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "plugins" / "ndf" / "dev.agy" / "install-hooks.sh"

# 利用者が既に持っている項目。差し込みで消えないことを見る。
OTHER = {"other-tool": {"PreInvocation": [{"type": "command", "command": "true"}]}}


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALLER), *args], capture_output=True, text=True
    )


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    """導入済みの実体を模す。`hooks.json` と、指し先のスクリプトを置く。"""
    target = tmp_path / "plugins" / "ndf"
    (target / "scripts").mkdir(parents=True)
    (target / "scripts" / "worktree-guard.sh").write_text("", encoding="utf-8")
    (target / "hooks.json").write_text(
        json.dumps(
            {
                "ndf-worktree": {
                    "PreToolUse": [
                        {
                            "matcher": "Write|write_to_file",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "bash ./scripts/worktree-guard.sh",
                                    "timeout": 5,
                                }
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return target


@pytest.fixture()
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "hooks.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(OTHER), encoding="utf-8")
    return path


def test_it_adds_the_plugin_hook_without_touching_other_entries(
    config: Path, plugin_dir: Path
) -> None:
    result = run("--config", str(config), "--plugin-dir", str(plugin_dir))

    assert result.returncode == 0, result.stderr
    written = json.loads(config.read_text(encoding="utf-8"))
    assert sorted(written) == ["ndf-worktree", "other-tool"]
    assert written["other-tool"] == OTHER["other-tool"]


def test_it_rewrites_relative_commands_to_the_installed_path(
    config: Path, plugin_dir: Path
) -> None:
    """利用者の設定へ差し込むと、実行時の現在地はプラグインの位置と揃わない。"""
    run("--config", str(config), "--plugin-dir", str(plugin_dir))

    written = json.loads(config.read_text(encoding="utf-8"))
    command = written["ndf-worktree"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == f"bash {plugin_dir}/scripts/worktree-guard.sh"
    assert "./scripts/" not in command


def test_running_it_twice_changes_nothing(config: Path, plugin_dir: Path) -> None:
    run("--config", str(config), "--plugin-dir", str(plugin_dir))
    first = config.read_text(encoding="utf-8")

    result = run("--config", str(config), "--plugin-dir", str(plugin_dir))

    assert result.returncode == 0
    assert "変更なし" in result.stdout
    assert config.read_text(encoding="utf-8") == first


def test_dry_run_does_not_write(config: Path, plugin_dir: Path) -> None:
    before = config.read_text(encoding="utf-8")

    result = run("--config", str(config), "--plugin-dir", str(plugin_dir), "--dry-run")

    assert result.returncode == 0
    assert config.read_text(encoding="utf-8") == before


def test_uninstall_removes_only_the_plugin_entries(
    config: Path, plugin_dir: Path
) -> None:
    run("--config", str(config), "--plugin-dir", str(plugin_dir))

    result = run("--config", str(config), "--plugin-dir", str(plugin_dir), "--uninstall")

    assert result.returncode == 0, result.stderr
    written = json.loads(config.read_text(encoding="utf-8"))
    assert sorted(written) == ["other-tool"]


def test_it_creates_the_config_when_absent(tmp_path: Path, plugin_dir: Path) -> None:
    missing = tmp_path / "fresh" / "hooks.json"

    result = run("--config", str(missing), "--plugin-dir", str(plugin_dir))

    assert result.returncode == 0, result.stderr
    assert sorted(json.loads(missing.read_text(encoding="utf-8"))) == ["ndf-worktree"]


def test_it_stops_on_a_broken_config(tmp_path: Path, plugin_dir: Path) -> None:
    """読めない設定を黙って上書きしない。利用者の hook がまとめて消える。"""
    broken = tmp_path / "broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")

    result = run("--config", str(broken), "--plugin-dir", str(plugin_dir))

    assert result.returncode != 0
    assert broken.read_text(encoding="utf-8") == "{ this is not json"


def test_it_falls_back_to_the_repository_definition(
    config: Path, tmp_path: Path
) -> None:
    """導入前でも中身を確かめられる。実体が無ければリポジトリの定義を読む。"""
    result = run(
        "--config", str(config), "--plugin-dir", str(tmp_path / "not-installed"),
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert "ndf-worktree" in result.stdout


def test_the_repository_definition_is_the_one_that_is_distributed() -> None:
    """差し込む定義は、配布する `hooks.json` と同じものである。"""
    source = json.loads(
        (INSTALLER.parent / "hooks.json").read_text(encoding="utf-8")
    )
    assert sorted(source) == ["ndf-worktree"]
    assert sorted(source["ndf-worktree"]) == ["PreInvocation", "PreToolUse"]
