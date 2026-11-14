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


# make_plugin の manifest の既定（plugin.json を名前と版から書く）
FROM_NAME = object()


def make_plugin(base: Path, dirname: str, name: str, manifest: object = FROM_NAME) -> Path:
    """プラグインの根を作る。`manifest` が文字ならその中身で plugin.json を書き、None なら plugin.json を書かない。"""
    root = base / "plugins" / dirname
    (root / ".claude-plugin").mkdir(parents=True)
    if manifest is FROM_NAME:
        manifest = json.dumps({"name": name, "version": "1.0.0"})
    if manifest is not None:
        (root / ".claude-plugin" / "plugin.json").write_text(str(manifest))
    return root


def _retired_names(ident: identity.PluginIdentity) -> list[str]:
    """状態の置き場の親にある、退避先の名前。"""
    return [p.name for p in ident.state_dir.parent.iterdir() if identity.RETIRED_SUFFIX in p.name]


def test_load_reads_name_from_plugin_json_regardless_of_dirname(tmp_path, monkeypatch):
    root = make_plugin(tmp_path, "x", "renamed")
    monkeypatch.setattr(identity, "ROOT", root)

    ident = identity.load()

    assert ident == identity.PluginIdentity(root=root, name="renamed", prefix="renamed:", state_dir=tmp_path / "state" / "renamed")


def test_load_does_not_take_the_root_from_the_environment(tmp_path, monkeypatch):
    # 3367-4: 根は自分の位置の 1 通りで求め、ほかのプラグインの CLAUDE_PLUGIN_ROOT は違うと記すだけ
    other = make_plugin(tmp_path, "old", "ndf")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(other))

    ident = identity.load()

    assert ident.root == PLUGIN_ROOT
    assert ident.foreign_root == other


def test_load_accepts_the_environment_naming_its_own_root(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN_ROOT))

    assert identity.load().foreign_root is None


def test_load_without_env_uses_own_location():
    ident = identity.load()

    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert ident.root == PLUGIN_ROOT
    assert ident.name == manifest["name"]


def test_load_uses_home_when_xdg_state_home_is_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ROOT", make_plugin(tmp_path, "x", "p"))
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert identity.load().state_dir == tmp_path / "home" / ".local" / "state" / "p"


@pytest.mark.parametrize("content", [None, "{", "[]", '{"version": "1"}', '{"name": ""}'])
def test_load_raises_when_plugin_json_is_unreadable(tmp_path, monkeypatch, content):
    monkeypatch.setattr(identity, "ROOT", make_plugin(tmp_path, "broken", "p", manifest=content))

    with pytest.raises(identity.IdentityError):
        identity.load()


@pytest.fixture
def ident(tmp_path, monkeypatch):
    monkeypatch.setattr(identity, "ROOT", make_plugin(tmp_path, "x", "p"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return identity.load()


def test_prepare_creates_state_dir_with_format_file(ident):
    ready = identity.prepare_state_dir(ident, now=NOW)

    assert ready == identity.StateReady(ident.state_dir)
    assert ready.items() == []
    assert json.loads((ident.state_dir / "format.json").read_text()) == {"format": 1}
    assert sorted(p.name for p in ident.state_dir.iterdir()) == ["format.json"]


def test_prepare_retires_old_state_and_keeps_its_contents(ident):
    ident.state_dir.mkdir(parents=True)
    (ident.state_dir / "ledger.jsonl").write_text("old\n")

    ready = identity.prepare_state_dir(ident, now=NOW)

    retired = ident.state_dir.with_name("p.v10-20261113")
    assert (retired / "ledger.jsonl").read_text() == "old\n"
    assert sorted(p.name for p in ident.state_dir.iterdir()) == ["format.json"]
    # 3394-8: 退避した元と先を返す
    assert ready.retired == (identity.Retired(ident.state_dir, retired),)
    assert ready.items() == [{"retired_from": str(ident.state_dir), "retired_to": str(retired)}]


def test_prepare_retires_every_old_place_by_the_same_rule(ident, tmp_path):
    # 3341-3: 旧い NDF の既定の状態の置き場（<設定の置き場>/<名前>/state）も同じ規則で退避し、作り直さない
    ident.state_dir.mkdir(parents=True)
    (ident.state_dir / "a").write_text("1")
    config_state = tmp_path / "config" / "p" / "state"
    config_state.mkdir(parents=True)
    (config_state / "ledger.jsonl").write_text("2")

    ready = identity.prepare_state_dir(ident, now=NOW)

    moved = config_state.with_name("state.v10-20261113")
    assert (moved / "ledger.jsonl").read_text() == "2"
    assert not config_state.exists()
    assert [r.source for r in ready.retired] == [ident.state_dir, config_state]
    assert ready.retired[1].target == moved


def test_old_state_places_are_the_state_home_and_the_config_state_in_order(ident, tmp_path):
    # 現状固定: このモジュールで退避するのは state-home と config-state の 2 つで、表の順に並ぶ
    assert identity.old_state_places(ident) == [ident.state_dir, tmp_path / "config" / "p" / "state"]


def test_every_old_place_has_a_decision_and_a_reason():
    # 3341-3: 旧い NDF の状態の置き場の全行が、退避するか・その理由を持つ
    keys = [place.key for place in identity.OLD_STATE_PLACES]
    assert len(keys) == len(set(keys))
    assert {"state-home", "config-state", "checkout-state", "config-accounts", "config-relay", "config-locks", "cache"} == set(keys)
    for place in identity.OLD_STATE_PLACES:
        assert isinstance(place.handling, identity.Handling), place.key
        assert place.reason.strip(), place.key
        # このモジュールで退避する行だけがパスを持つ（ほかの行にこのモジュールは触らない）
        assert (place.handling is identity.Handling.RETIRE_HERE) == (place.path is not None), place.key


def test_prepare_leaves_places_marked_not_to_retire(ident, tmp_path, monkeypatch):
    # 3341-3: 退避しない行（accounts・relay.py・locks・~/.cache/<名前>）は中身ごとそのまま残す
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = tmp_path / "config" / "p"
    kept = [config / "accounts", config / "locks", tmp_path / "home" / ".cache" / "p"]
    for place in kept:
        place.mkdir(parents=True)
        (place / "x").write_text("keep")
    (config / "relay.py").write_text("relay")

    ready = identity.prepare_state_dir(ident, now=NOW)

    assert ready.retired == ()
    assert all((place / "x").read_text() == "keep" for place in kept)
    assert (config / "relay.py").read_text() == "relay"
    assert sorted(p.name for p in config.iterdir()) == ["accounts", "locks", "relay.py"]


def test_prepare_does_not_retire_when_the_environment_names_another_root(ident, tmp_path, monkeypatch):
    # 3367-4: ほかのプラグインの hook の子として動いたときは、旧い置き場に触れずに止まる
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(make_plugin(tmp_path, "old", "p")))
    foreign = identity.load()
    foreign.state_dir.mkdir(parents=True)
    (foreign.state_dir / "ledger.jsonl").write_text("old")

    with pytest.raises(identity.IdentityError, match="CLAUDE_PLUGIN_ROOT"):
        identity.prepare_state_dir(foreign, now=NOW)

    assert (foreign.state_dir / "ledger.jsonl").read_text() == "old"
    assert not (foreign.state_dir / "format.json").exists()


def test_prepare_with_another_root_in_the_environment_uses_a_ready_place(ident, tmp_path, monkeypatch):
    identity.prepare_state_dir(ident, now=NOW)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(make_plugin(tmp_path, "old", "p")))

    assert identity.prepare_state_dir(identity.load(), now=NOW).path == ident.state_dir


def test_prepare_leaves_no_temporary_format_file(ident):
    # 3377-5: format.json は一時の名前で書いてから置き換える
    identity.prepare_state_dir(ident, now=NOW)

    assert [p.name for p in ident.state_dir.iterdir()] == ["format.json"]


def test_prepare_does_not_retire_on_second_start(ident):
    identity.prepare_state_dir(ident, now=NOW)
    (ident.state_dir / "runs.db").write_text("new")

    identity.prepare_state_dir(ident, now=NOW)

    assert (ident.state_dir / "runs.db").read_text() == "new"
    assert _retired_names(ident) == []


def test_prepare_does_not_retire_empty_dir(ident):
    ident.state_dir.mkdir(parents=True)

    identity.prepare_state_dir(ident, now=NOW)

    assert _retired_names(ident) == []


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
