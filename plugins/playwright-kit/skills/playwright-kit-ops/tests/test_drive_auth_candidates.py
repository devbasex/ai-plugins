"""`google-auth` の探索先と案内が、配布後の前提と揃っていることを固定する（#417 の 7）。

**`google-auth` は 4 つの manifest すべてに載っている。** 「どの公開セットにも同梱して
いない」と書いたままの案内は、読んだ利用者を要らない設定へ誘導する。あわせて agy の
導入先が候補から抜けていた。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
MODULE = SCRIPTS / "_drive_auth.py"
REPO = Path(__file__).resolve().parents[5]


@pytest.fixture(scope="module")
def drive_auth():
    spec = importlib.util.spec_from_file_location("_drive_auth_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_every_runtime_has_a_candidate(drive_auth) -> None:
    """4 ランタイムのうち、agy の導入先だけが候補から抜けていた。"""
    candidates = [str(p) for p in drive_auth._CANDIDATES]
    for marker in (".claude", ".codex", ".kiro", ".gemini"):
        assert any(marker in path for path in candidates), f"{marker} の候補が無い"


def test_the_message_does_not_claim_it_is_undistributed(
    drive_auth, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配布前の前提のまま残ると、読んだ利用者を要らない設定へ誘導する。"""
    monkeypatch.setattr(
        drive_auth, "_CANDIDATES", (Path("/nonexistent/google-auth/scripts"),)
    )
    with pytest.raises(RuntimeError) as raised:
        drive_auth._ensure_google_auth_on_path()

    message = str(raised.value)
    assert "同梱していない" not in message
    assert "GOOGLE_AUTH_SCRIPTS" in message


def test_google_auth_is_distributed_to_every_runtime() -> None:
    """案内の前提そのもの。載っていなければ、案内を書き戻すことになる。"""
    manifests = sorted((REPO / "plugins" / "ndf" / "manifests").glob("*-skills.txt"))
    assert manifests
    for manifest in manifests:
        names = manifest.read_text(encoding="utf-8").split()
        assert "google-auth" in names, f"{manifest.name} に google-auth が無い"


def test_the_module_docstring_matches_the_distribution(drive_auth) -> None:
    assert drive_auth.__doc__ is not None
    assert "同梱していない" not in drive_auth.__doc__


def test_claude_code_plugin_cache_is_searched_at_the_latest_version(
    drive_auth, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code は Skill を `~/.claude/plugins/cache/<取得元>/ndf/<版>/` に置く。版は数で比べる。"""
    for ver in ("10.9.1", "10.17.8", "10.17.7"):
        d = tmp_path / ".claude/plugins/cache/ai-plugins/ndf" / ver / "skills/google-auth/scripts"
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    found = drive_auth._claude_plugin_cache()
    assert found is not None and Path(found).parents[2].name == "10.17.8"


@pytest.mark.parametrize(
    ("versions", "latest"),
    [
        (("10.17.30-dev.1", "10.17.30", "10.17.29"), "10.17.30"),
        (("10.17.30-rc.1", "10.17.30-dev.2", "10.17.29"), "10.17.30-rc.1"),
        (("10.17.30-dev.10", "10.17.30-dev.9"), "10.17.30-dev.10"),
        (("10.17.31-dev.1", "10.17.30"), "10.17.31-dev.1"),
    ],
)
def test_claude_code_plugin_cache_orders_prereleases_by_semver(
    drive_auth, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, versions, latest
) -> None:
    """同じ基底では正式版が prerelease より新しい。prerelease 同士は識別子ごとに比べる。"""
    for ver in versions:
        d = tmp_path / ".claude/plugins/cache/ai-plugins/ndf" / ver / "skills/google-auth/scripts"
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    found = drive_auth._claude_plugin_cache()
    assert found is not None and Path(found).parents[2].name == latest


def test_claude_code_plugin_cache_absent(
    drive_auth, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert drive_auth._claude_plugin_cache() is None
