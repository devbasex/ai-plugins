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


def test_a_relative_plugin_dir_is_resolved_to_an_absolute_path(
    config: Path, plugin_dir: Path, tmp_path: Path
) -> None:
    """agy はリポジトリの外でも起動する。相対のまま保存すると hook が落ちる（#417 の 3）。

    `plugins/ndf/README.md` の案内は clone からの相対パスで書いてあるため、書かれた
    とおりに実行すると踏む。
    """
    result = subprocess.run(
        [
            "bash", str(INSTALLER),
            "--config", str(config),
            "--plugin-dir", str(plugin_dir.relative_to(tmp_path)),
        ],
        cwd=str(tmp_path), capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    written = json.loads(config.read_text(encoding="utf-8"))
    command = written["ndf-worktree"]["PreToolUse"][0]["hooks"][0]["command"]
    assert command == f"bash {plugin_dir}/scripts/worktree-guard.sh"


@pytest.fixture()
def spaced_plugin_dir(tmp_path: Path) -> Path:
    """空白とシェルの特殊文字を含む導入先。"""
    target = tmp_path / "plugin space" / "ndf$x"
    (target / "scripts").mkdir(parents=True)
    (target / "scripts" / "worktree-guard.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (target / "hooks.json").write_text(
        json.dumps(
            {
                "ndf-worktree": {
                    "PreInvocation": [
                        {
                            "type": "command",
                            "command": "bash ./scripts/worktree-guard.sh",
                            "timeout": 5,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return target


def test_a_command_with_a_spaced_path_is_runnable(
    config: Path, spaced_plugin_dir: Path
) -> None:
    """文字列の一致だけでは、シェルの語として壊れていることを捕まえられない（#417 の 4）。

    **正常終了するのに hook が効かない**形になるため、利用者から見て失敗が見えない。
    """
    result = run("--config", str(config), "--plugin-dir", str(spaced_plugin_dir))
    assert result.returncode == 0, result.stderr

    written = json.loads(config.read_text(encoding="utf-8"))
    command = written["ndf-worktree"]["PreInvocation"][0]["command"]
    executed = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert executed.returncode == 0, f"{command}\n{executed.stderr}"


def test_uninstall_keeps_entries_the_plugin_does_not_distribute(
    config: Path, plugin_dir: Path
) -> None:
    """`ndf-` で始まる利用者の項目まで消さない（#417 の 8）。"""
    mine = {"PreInvocation": [{"type": "command", "command": "true"}]}
    current = json.loads(config.read_text(encoding="utf-8"))
    current["ndf-mine"] = mine
    config.write_text(json.dumps(current), encoding="utf-8")
    run("--config", str(config), "--plugin-dir", str(plugin_dir))

    result = run("--config", str(config), "--plugin-dir", str(plugin_dir), "--uninstall")

    assert result.returncode == 0, result.stderr
    written = json.loads(config.read_text(encoding="utf-8"))
    assert sorted(written) == ["ndf-mine", "other-tool"]
    assert written["ndf-mine"] == mine


def test_the_write_leaves_no_temporary_file_behind(
    config: Path, plugin_dir: Path
) -> None:
    """置き換えで書く。中断で壊れた設定を残さない（#417 の 8）。"""
    result = run("--config", str(config), "--plugin-dir", str(plugin_dir))

    assert result.returncode == 0, result.stderr
    leftovers = sorted(
        p.name for p in config.parent.iterdir()
        if p.name not in {"hooks.json", "hooks.json.bak"}
    )
    assert leftovers == []
    assert sorted(json.loads(config.read_text(encoding="utf-8"))) == [
        "ndf-worktree", "other-tool"
    ]


def test_it_reports_a_command_it_could_not_rewrite(
    config: Path, tmp_path: Path
) -> None:
    """黙って相対パスのまま保存すると、正常終了して効かない状態になる（決定 6）。"""
    target = tmp_path / "odd"
    (target / "scripts").mkdir(parents=True)
    (target / "hooks.json").write_text(
        json.dumps(
            {
                "ndf-odd": {
                    "PreInvocation": [
                        {"type": "command", "command": "sh -c './scripts/x.sh'"}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    result = run("--config", str(config), "--plugin-dir", str(target))

    assert result.returncode == 0, result.stderr
    assert "書き換えられなかった" in result.stdout + result.stderr
