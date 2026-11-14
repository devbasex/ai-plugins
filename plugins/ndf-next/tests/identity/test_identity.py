from __future__ import annotations

import fcntl
import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from ndf import identity

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 20, 30, 5)


def make_plugin(base: Path, dirname: str, name: str) -> Path:
    root = base / "plugins" / dirname
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "1.0.0"}))
    return root


def test_load_reads_name_from_plugin_json_regardless_of_dirname(tmp_path, monkeypatch):
    root = make_plugin(tmp_path, "x", "renamed")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

    ident = identity.load()

    assert ident == identity.PluginIdentity(root=root, name="renamed", prefix="renamed:", state_dir=tmp_path / "state" / "renamed")


def test_load_without_env_uses_own_location():
    ident = identity.load()

    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert ident.root == PLUGIN_ROOT
    assert ident.name == manifest["name"]


def test_load_uses_home_when_xdg_state_home_is_unset(tmp_path, monkeypatch):
    root = make_plugin(tmp_path, "x", "p")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert identity.load().state_dir == tmp_path / "home" / ".local" / "state" / "p"


def test_ndf_state_home_is_the_root_before_xdg(tmp_path, monkeypatch):
    root = make_plugin(tmp_path, "x", "p")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    monkeypatch.setenv("NDF_STATE_HOME", str(tmp_path / "service"))

    # サービスが渡した根の下にプラグイン名の置き場を作り、根そのもの（旧い NDF の置き場）を退避しない
    old = tmp_path / "service" / "old.json"
    old.parent.mkdir()
    old.write_text("{}")
    ident = identity.load()
    assert ident.state_dir == tmp_path / "service" / "p"
    identity.prepare_state_dir(ident)
    assert old.exists()


@pytest.mark.parametrize("content", [None, "{", "[]", '{"version": "1"}', '{"name": ""}'])
def test_load_raises_when_plugin_json_is_unreadable(tmp_path, monkeypatch, content):
    root = tmp_path / "broken"
    (root / ".claude-plugin").mkdir(parents=True)
    if content is not None:
        (root / ".claude-plugin" / "plugin.json").write_text(content)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

    with pytest.raises(identity.IdentityError):
        identity.load()


@pytest.fixture
def ident(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(make_plugin(tmp_path, "x", "p")))
    return identity.load()


def test_prepare_creates_state_dir_with_format_file(ident):
    path = identity.prepare_state_dir(ident, now=NOW)

    assert path == ident.state_dir
    assert json.loads((path / "format.json").read_text()) == {"format": 1}


def test_prepare_retires_old_state_and_keeps_its_contents(ident):
    ident.state_dir.mkdir(parents=True)
    (ident.state_dir / "ledger.jsonl").write_text("old\n")

    identity.prepare_state_dir(ident, now=NOW)

    retired = ident.state_dir.with_name("p.v10-20261113")
    assert (retired / "ledger.jsonl").read_text() == "old\n"
    assert sorted(p.name for p in ident.state_dir.iterdir()) == ["format.json"]


def test_prepare_does_not_retire_on_second_start(ident):
    identity.prepare_state_dir(ident, now=NOW)
    (ident.state_dir / "runs.db").write_text("new")

    identity.prepare_state_dir(ident, now=NOW)

    assert (ident.state_dir / "runs.db").read_text() == "new"
    assert [p.name for p in ident.state_dir.parent.iterdir() if identity.RETIRED_SUFFIX in p.name] == []


def test_prepare_does_not_retire_empty_dir(ident):
    ident.state_dir.mkdir(parents=True)

    identity.prepare_state_dir(ident, now=NOW)

    assert [p.name for p in ident.state_dir.parent.iterdir() if identity.RETIRED_SUFFIX in p.name] == []


def test_prepare_adds_time_when_retired_name_exists(ident):
    earlier = ident.state_dir.with_name("p.v10-20261113")
    earlier.mkdir(parents=True)
    (earlier / "keep").write_text("1")
    ident.state_dir.mkdir()
    (ident.state_dir / "old").write_text("2")

    identity.prepare_state_dir(ident, now=NOW)
    (ident.state_dir / "format.json").unlink()
    (ident.state_dir / "old").write_text("3")
    identity.prepare_state_dir(ident, now=NOW)

    parent = ident.state_dir.parent
    assert (earlier / "keep").read_text() == "1"
    assert (parent / "p.v10-20261113T203005" / "old").read_text() == "2"
    assert (parent / "p.v10-20261113T203005-1" / "old").read_text() == "3"


def test_prepare_waits_for_the_state_lock(ident):
    # 判定から format.json の作成までは、同じ置き場を用意するほかの処理と重ならない
    ident.state_dir.parent.mkdir(parents=True, exist_ok=True)
    with open(identity.lock_path(ident), "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        worker = threading.Thread(target=identity.prepare_state_dir, args=(ident,), kwargs={"now": NOW})
        worker.start()
        worker.join(0.3)
        assert worker.is_alive()
        assert not (ident.state_dir / "format.json").exists()
        fcntl.flock(held, fcntl.LOCK_UN)
    worker.join(5)

    assert (ident.state_dir / "format.json").exists()
