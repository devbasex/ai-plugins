"""このプラグインだけを読む claude の起動（`bin/ndf-claude`。#3409・決定 74・S1-H4）。

偽の `claude`（受けた引数を書くだけ）で、外す設定と読む引数が `ndf launch-args` の値どおりで、利用者の設定ファイルを
書き換えないことを確かめる。実物の claude を起こす確かめ（起動の init の plugins と hook の計測の記録）は費用がかかるので、
`NDF_LAUNCH_LIVE=1` のときだけ走らせる（受け入れの確かめで打つ）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude

ROOT = identity.ROOT
SCRIPT = ROOT / "bin" / "ndf-claude"
# 実物の claude の認証は利用者の設定の置き場にある。テストの前提（conftest）が置き換える前の値を控える
REAL_CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR")
LIVE = os.environ.get("NDF_LAUNCH_LIVE") == "1"
RECORD = 'import json, os, sys\nwith open(os.environ["ARGV_OUT"], "w") as f:\n    json.dump(sys.argv[1:], f)\n'


def _fake_claude(path_dir: Path) -> None:
    path_dir.mkdir(exist_ok=True)
    fake = path_dir / "claude"
    fake.write_text(f"#!{shutil.which('python3')}\n{RECORD}", encoding="utf-8")
    fake.chmod(0o755)


def _run(script: Path, tmp_path: Path, *args: str, env: dict | None = None):
    path_dir = tmp_path / "path"
    _fake_claude(path_dir)
    extra = {"PATH": f"{path_dir}{os.pathsep}{os.environ['PATH']}", "ARGV_OUT": str(tmp_path / "argv.json"), **(env or {})}
    return subprocess.run([str(script), *args], capture_output=True, text=True, timeout=300, env={**os.environ, **extra}, check=False)


@pytest.fixture
def copied(tmp_path) -> Path:
    """起動のスクリプトだけを写し、`bin/ndf` を決めた答えを出す偽に替えたプラグインの根。"""
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "bin" / "ndf-claude")
    return root


def _fake_ndf(root: Path, stdout: str, code: int = 0) -> None:
    ndf = root / "bin" / "ndf"
    ndf.write_text(f"#!/bin/sh\ncat <<'EOF'\n{stdout}\nEOF\nexit {code}\n", encoding="utf-8")
    ndf.chmod(0o755)


def test_the_script_passes_the_launch_args_and_then_the_callers_args(copied, tmp_path):
    report = {"tool": "launch-args", "status": "ok", "items": [{"args": ["--settings", '{"a": {"b c": false}}', "--plugin-dir", "/p q"]}]}
    _fake_ndf(copied, json.dumps(report))

    done = _run(copied / "bin" / "ndf-claude", tmp_path, "-p", "two words")

    assert done.returncode == 0, done.stderr
    got = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert got == ["--settings", '{"a": {"b c": false}}', "--plugin-dir", "/p q", "-p", "two words"]


def test_the_script_stops_with_the_answer_of_launch_args_when_it_fails(copied, tmp_path):
    stopped = json.dumps({"tool": "cli", "status": "stopped", "cause": {"kind": "deps_missing"}})
    _fake_ndf(copied, stopped, code=7)

    done = _run(copied / "bin" / "ndf-claude", tmp_path)

    assert done.returncode == 7
    assert done.stdout.strip() == stopped
    assert not (tmp_path / "argv.json").exists()


def test_the_script_refuses_an_answer_it_cannot_read(copied, tmp_path):
    _fake_ndf(copied, "not json")
    done = _run(copied / "bin" / "ndf-claude", tmp_path)
    assert done.returncode == 2 and "読めない" in done.stderr
    assert not (tmp_path / "argv.json").exists()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が要る（CI は setup-uv で入れる）")
def test_the_script_disables_the_installed_ndf_and_loads_this_plugin_without_writing_user_settings(tmp_path):
    # 利用者の設定（Claude Code の設定の置き場の settings.json）は読ませるだけで、書き換えない
    settings = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    before = '{"enabledPlugins": {"ndf@ai-plugins": true}}\n'
    settings.write_text(before, encoding="utf-8")
    uv_cache = os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")

    done = _run(SCRIPT, tmp_path, "-p", "x", env={"UV_CACHE_DIR": uv_cache})

    assert done.returncode == 0, done.stderr
    got = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    assert got == [*claude.plugin_args(ROOT), "-p", "x"]
    assert got[got.index("--plugin-dir") + 1] == str(ROOT)
    assert settings.read_text(encoding="utf-8") == before


def test_launch_args_disable_every_old_plugin_but_never_this_one():
    # 入れ替え（S2）で名前が外す相手と同じになっても、自分を外さない
    argv = claude.plugin_args(ROOT)
    disabled = json.loads(argv[argv.index("--settings") + 1])["enabledPlugins"] if "--settings" in argv else {}
    own = identity.load().name
    assert all(key.split("@", 1)[0] != own for key in disabled)
    assert set(disabled) == {k for k in claude.DISABLED_PLUGINS if k.split("@", 1)[0] != own}
    assert argv[-2:] == ["--plugin-dir", str(ROOT)]


@pytest.mark.skipif(not LIVE or shutil.which("claude") is None, reason="実物の claude を起こす確かめ（NDF_LAUNCH_LIVE=1 のときだけ）")
def test_live_session_loads_only_this_plugin_and_its_hooks(tmp_path, monkeypatch):
    # 起動の init の plugins にこのプラグインがあり、外す相手が無い。計測の記録にこのプラグインの hook の行が出る（S1-H4）
    if REAL_CONFIG_DIR is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", REAL_CONFIG_DIR)
    done = subprocess.run(
        [
            str(SCRIPT),
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--max-budget-usd",
            "0.3",
            "Reply OK.",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
        check=False,
    )
    lines = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    init = next(d for d in lines if d.get("type") == "system" and d.get("subtype") == "init")
    names = {p["name"] for p in init["plugins"]}
    own = identity.load().name
    assert own in names and any(p.get("path") == str(ROOT) for p in init["plugins"])
    assert not names & {k.split("@", 1)[0] for k in claude.DISABLED_PLUGINS if k.split("@", 1)[0] != own}
    others = {k.split("@", 1)[0] for k in claude.DISABLED_PLUGINS} - {own}
    assert not [s for s in init.get("skills", []) if ":" in str(s) and str(s).split(":", 1)[0] in others]
    records = [json.loads(x) for p in (identity.load().state_dir / "hooks").glob("*.jsonl") for x in p.read_text().splitlines() if x]
    assert {r["event"] for r in records} >= {"SessionStart"}
    assert {r["runtime"] for r in records} == {"claude"}


def test_launch_args_keep_a_plugin_whose_name_is_on_the_disabled_list(tmp_path):
    # 名前を読むだけで、そのプラグインの bin/ndf は起こさない（旧い状態の置き場に触れない）
    for key in claude.DISABLED_PLUGINS:
        root = tmp_path / key.split("@", 1)[0]
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": key.split("@", 1)[0]}), encoding="utf-8")
        argv = claude.plugin_args(root)
        rest = {k: v for k, v in claude.DISABLED_PLUGINS.items() if k != key}
        assert argv == (["--settings", json.dumps({"enabledPlugins": rest}, separators=(",", ":"))] if rest else []) + [
            "--plugin-dir",
            str(root),
        ]
    unnamed = tmp_path / "unnamed"
    assert claude.plugin_args(unnamed)[:2] == ["--settings", json.dumps({"enabledPlugins": claude.DISABLED_PLUGINS}, separators=(",", ":"))]
