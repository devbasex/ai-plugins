"""Claude Code の設定の保存先の規則（`CLAUDE_CONFIG_DIR`、空か無ければ `~/.claude`）の振る舞いを固定する。

`claude.same_cache_rows` のキャッシュの根と `signals.claude_config_root` は同じ規則で、どちらも渡された環境の
`CLAUDE_CONFIG_DIR` を読み、プロセスの `CLAUDE_CONFIG_DIR` は読まない。渡された環境で未設定か空のときは
`Path.home()` がプロセスの `HOME` を参照するため、テストは `HOME` を一時ディレクトリへ差し替える。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.adapters import claude
from ndf.domains.relay import signals


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HOME を一時ディレクトリへ向け、プロセスの `CLAUDE_CONFIG_DIR` には関係の無い値を入れる。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "process-env"))
    return tmp_path / "home"


def _row(path: Path, market: str = "ai-plugins") -> dict:
    return {"id": f"ndf@{market}", "version": path.name, "scope": "user", "enabled": True, "installPath": str(path)}


def _environs(tmp_path: Path) -> list[tuple[dict, Path]]:
    """（環境, 期待する設定の保存先）の組。"""
    home = tmp_path / "home"
    return [
        ({"CLAUDE_CONFIG_DIR": str(tmp_path / "conf")}, tmp_path / "conf"),
        ({}, home / ".claude"),
        ({"CLAUDE_CONFIG_DIR": ""}, home / ".claude"),
    ]


@pytest.mark.parametrize("case", range(3))
def test_claude_config_root_follows_the_given_environ(home, tmp_path, case):
    environ, expected = _environs(tmp_path)[case]
    assert signals.claude_config_root(environ) == expected


@pytest.mark.parametrize("case", range(3))
def test_same_cache_rows_uses_plugins_cache_under_the_config_root(home, tmp_path, case):
    environ, base = _environs(tmp_path)[case]
    parent = base / "plugins" / "cache" / "ai-plugins" / "ndf"
    moved = _row(parent / "11.0.10")
    assert claude.same_cache_rows([moved], parent / "11.0.7", "11.0.7", environ) == [moved]


@pytest.mark.parametrize("case", range(3))
def test_same_cache_rows_and_claude_config_root_agree(home, tmp_path, case):
    environ, _ = _environs(tmp_path)[case]
    parent = signals.claude_config_root(environ) / "plugins" / "cache" / "ai-plugins" / "ndf"
    moved = _row(parent / "11.0.10")
    assert claude.same_cache_rows([moved], parent / "11.0.7", "11.0.7", environ) == [moved]


def test_same_cache_rows_ignores_the_process_environ(home, tmp_path):
    # プロセスの CLAUDE_CONFIG_DIR の下の行は、渡した環境が ~/.claude を指すとき拾わない
    parent = tmp_path / "process-env" / "plugins" / "cache" / "ai-plugins" / "ndf"
    assert claude.same_cache_rows([_row(parent / "11.0.10")], parent / "11.0.7", "11.0.7", {}) == []


def test_claude_config_root_ignores_the_process_environ(home):
    assert signals.claude_config_root({}) == home / ".claude"


def test_same_cache_rows_finds_rows_through_a_symlinked_config_root(home, tmp_path):
    # キャッシュの根は実体へ解決して比べる
    real = tmp_path / "real"
    (real / "plugins" / "cache" / "ai-plugins" / "ndf").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    parent = real / "plugins" / "cache" / "ai-plugins" / "ndf"
    moved = _row(parent / "11.0.10")
    assert claude.same_cache_rows([moved], parent / "11.0.7", "11.0.7", {"CLAUDE_CONFIG_DIR": str(link)}) == [moved]


def test_claude_config_root_does_not_resolve_or_expand(home):
    # 値はそのままのパスで返す（解決もしない・`~` も展開しない）
    assert signals.claude_config_root({"CLAUDE_CONFIG_DIR": "~/x"}) == Path("~/x")
    assert signals.claude_config_root({"CLAUDE_CONFIG_DIR": "rel/conf"}) == Path("rel/conf")
