from __future__ import annotations

import fcntl
import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from lock_probe import taken_by_another

from ndf import identity

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 20, 30, 5, tzinfo=UTC)


# make_plugin の manifest の既定（plugin.json を名前とバージョンから書く）
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
    """状態の保存先の親にある、退避先の名前。"""
    return [p.name for p in ident.state_dir.parent.iterdir() if identity.RETIRED_SUFFIX in p.name]


@pytest.fixture
def use_plugin(tmp_path, monkeypatch):
    """`plugin.json` の name が `name` のプラグインを作り、自分の根として指す関数。作った根を返す。"""

    def use(name: str) -> Path:
        root = make_plugin(tmp_path, "x", name)
        monkeypatch.setattr(identity, "ROOT", root)
        return root

    return use


def test_load_reads_name_from_plugin_json_regardless_of_dirname(tmp_path, use_plugin):
    root = use_plugin("renamed")

    ident = identity.load()

    assert ident == identity.PluginIdentity(root=root, name="renamed", prefix="renamed:", state_dir=tmp_path / "state" / "renamed")


def test_load_does_not_take_the_root_from_the_environment(tmp_path, monkeypatch):
    # 3367-4: 根は自分の位置の 1 通りで求め、ほかのプラグインの CLAUDE_PLUGIN_ROOT は違うと記すだけ
    other = make_plugin(tmp_path, "old", "ndf")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(other))

    ident = identity.load()

    assert ident.root == PLUGIN_ROOT
    assert ident.foreign_root == other


def test_load_checks_the_callers_root_that_the_entrypoint_kept(monkeypatch, tmp_path):
    # bin/ndf は CLAUDE_PLUGIN_ROOT を自分の根で上書きする。写した呼び出し元の値（NDF_CALLER_PLUGIN_ROOT）で確かめる
    other = make_plugin(tmp_path, "old", "ndf")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN_ROOT))
    monkeypatch.setenv("NDF_CALLER_PLUGIN_ROOT", str(other))

    assert identity.load().foreign_root == other

    monkeypatch.setenv("NDF_CALLER_PLUGIN_ROOT", "")
    assert identity.load().foreign_root is None


def test_load_accepts_the_environment_naming_its_own_root(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN_ROOT))

    assert identity.load().foreign_root is None


def test_load_without_env_uses_own_location():
    ident = identity.load()

    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert ident.root == PLUGIN_ROOT
    assert ident.name == manifest["name"]


def test_load_uses_home_when_xdg_state_home_is_unset(tmp_path, monkeypatch, use_plugin):
    use_plugin("p")
    monkeypatch.delenv("XDG_STATE_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert identity.load().state_dir == tmp_path / "home" / ".local" / "state" / "p"


def test_ndf_state_home_is_the_root_before_xdg(tmp_path, monkeypatch, use_plugin):
    use_plugin("p")
    monkeypatch.setenv("NDF_STATE_HOME", str(tmp_path / "service"))

    # サービスが渡した根の下にプラグイン名の保存先を作り、根そのもの（旧い NDF の保存先）を退避しない
    old = tmp_path / "service" / "old.json"
    old.parent.mkdir()
    old.write_text("{}")
    ident = identity.load()
    assert ident.state_dir == tmp_path / "service" / "p"
    identity.prepare_state_dir(ident)
    assert old.exists()


@pytest.mark.parametrize("content", [None, "{", "[]", '{"version": "1"}', '{"name": ""}'])
def test_load_raises_when_plugin_json_is_unreadable(tmp_path, monkeypatch, content):
    monkeypatch.setattr(identity, "ROOT", make_plugin(tmp_path, "broken", "p", manifest=content))

    with pytest.raises(identity.IdentityError):
        identity.load()


@pytest.fixture
def ident(tmp_path, monkeypatch, use_plugin):
    use_plugin("p")
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
    (ident.state_dir / "journal.jsonl").write_text("old\n")

    ready = identity.prepare_state_dir(ident, now=NOW)

    retired = ident.state_dir.with_name("p.v10-20261113")
    assert (retired / "journal.jsonl").read_text() == "old\n"
    assert sorted(p.name for p in ident.state_dir.iterdir()) == ["format.json"]
    # 3394-8: 退避した元と先を返す
    assert ready.retired == (identity.Retired(ident.state_dir, retired),)
    assert ready.items() == [{"retired_from": str(ident.state_dir), "retired_to": str(retired)}]


def test_prepare_does_not_retire_the_old_config_state(ident, tmp_path, monkeypatch):
    # 規則 P1 の適用範囲: 旧い NDF のジャーナル（$HOME/.claude/<名前>/state）は新しい NDF と重ならず、退避しない
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config_state = tmp_path / "home" / ".claude" / "p" / "state"
    config_state.mkdir(parents=True)
    (config_state / "ask.jsonl").write_text("2")

    ready = identity.prepare_state_dir(ident, now=NOW)

    assert ready.retired == ()
    assert (config_state / "ask.jsonl").read_text() == "2"
    assert sorted(p.name for p in config_state.parent.iterdir()) == ["state"]


def test_prepare_carries_nothing_when_the_rename_fails(ident, monkeypatch):
    # 名前を変えられなければ config_broken で止まり、退避した保存先は無い
    ident.state_dir.mkdir(parents=True)
    (ident.state_dir / "a").write_text("1")

    def fail(self, target):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "rename", fail)

    with pytest.raises(identity.IdentityError, match="read-only") as caught:
        identity.prepare_state_dir(ident, now=NOW)

    assert caught.value.kind == "config_broken"
    assert caught.value.retired == ()
    assert caught.value.items() == []
    assert (ident.state_dir / "a").read_text() == "1"


def test_prepare_refuses_to_move_an_outer_location_holding_locations_marked_not_to_retire(tmp_path, monkeypatch):
    # XDG_STATE_HOME と CLAUDE_CONFIG_DIR が同じだと state-home が accounts・locks を含む。外側ごと動かさず止める
    monkeypatch.setattr(identity, "ROOT", make_plugin(tmp_path, "x", "p"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "same"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "same"))
    ident = identity.load()
    (ident.state_dir / "state").mkdir(parents=True)
    (ident.state_dir / "state" / "journal.jsonl").write_text("old")
    (ident.state_dir / "locks").mkdir()
    (ident.state_dir / "locks" / "x.lock").write_text("held")

    with pytest.raises(identity.IdentityError, match="退避しない保存先"):
        identity.prepare_state_dir(ident, now=NOW)

    assert (ident.state_dir / "locks" / "x.lock").read_text() == "held"
    assert (ident.state_dir / "state" / "journal.jsonl").read_text() == "old"
    assert not (ident.state_dir / identity.FORMAT_FILE).exists()
    assert _retired_names(ident) == []


@pytest.mark.parametrize("var, sub", [("NDF_ACCOUNTS_DIR", "accounts"), ("NDF_LOCK_DIR", "issue-locks")])
def test_prepare_refuses_when_an_overridden_kept_location_is_inside_the_state_home(tmp_path, monkeypatch, var, sub):
    # 旧い NDF と同じ規則で、環境変数が指す退避しない保存先も解決する。状態のホームの中にあれば外側ごと動かさず止める
    monkeypatch.setattr(identity, "ROOT", make_plugin(tmp_path, "x", "p"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    ident = identity.load()
    monkeypatch.setenv(var, str(ident.state_dir / sub))
    (ident.state_dir / sub).mkdir(parents=True)
    (ident.state_dir / sub / "x").write_text("keep")

    with pytest.raises(identity.IdentityError, match="退避しない保存先"):
        identity.prepare_state_dir(ident, now=NOW)

    assert (ident.state_dir / sub / "x").read_text() == "keep"
    assert _retired_names(ident) == []


def test_old_state_locations_are_only_the_state_home(ident):
    # このモジュールで退避するのは状態のホームだけ（チェックアウトの保存先は durable が退避する）
    assert identity.old_state_locations(ident) == [ident.state_dir]


def test_only_the_state_home_and_the_checkout_store_are_retired():
    # 不変条件 I5: 退避の対象は O1（状態のホーム）と O3（チェックアウトの保存先）の 2 つだけ
    retired = {location.key for location in identity.OLD_STATE_LOCATIONS if location.handling is not identity.Handling.KEEP}
    assert retired == {"state-home", "checkout-state"}


def test_every_old_location_has_a_decision_and_a_reason():
    # 3341-3: 旧い NDF の状態の保存先の全行が、退避するか・その理由を持つ
    keys = [location.key for location in identity.OLD_STATE_LOCATIONS]
    assert len(keys) == len(set(keys))
    assert {
        "state-home",
        "config-state",
        "checkout-state",
        "config-accounts",
        "config-relay",
        "config-locks",
        "issue-locks",
        "cache",
    } == set(keys)
    for location in identity.OLD_STATE_LOCATIONS:
        assert isinstance(location.handling, identity.Handling), location.key
        assert location.reason.strip(), location.key
        # ほかのモジュールが退避する行だけがパスを持たない（退避しない行のパスは中に入っていないかの確かめだけに使う）
        assert (location.handling is identity.Handling.RETIRE_ELSEWHERE) == (location.path is None), location.key


def test_prepare_leaves_locations_marked_not_to_retire(ident, tmp_path, monkeypatch):
    # 3341-3: 退避しない行（ジャーナル・accounts・relay.py・locks・~/.cache/<名前>）は中身ごとそのまま残す
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = tmp_path / "config" / "p"
    kept = [config / "accounts", config / "locks", tmp_path / "home" / ".cache" / "p", tmp_path / "home" / ".claude" / "p" / "state"]
    for location in kept:
        location.mkdir(parents=True)
        (location / "x").write_text("keep")
    (config / "relay.py").write_text("relay")

    ready = identity.prepare_state_dir(ident, now=NOW)

    assert ready.retired == ()
    assert all((location / "x").read_text() == "keep" for location in kept)
    assert (config / "relay.py").read_text() == "relay"
    assert sorted(p.name for p in config.iterdir()) == ["accounts", "locks", "relay.py"]


def test_prepare_does_not_retire_when_the_environment_names_another_root(ident, tmp_path, monkeypatch):
    # 3367-4: ほかのプラグインの hook の子として動いたときは、旧い保存先に触れずに止まる
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(make_plugin(tmp_path, "old", "p")))
    foreign = identity.load()
    foreign.state_dir.mkdir(parents=True)
    (foreign.state_dir / "journal.jsonl").write_text("old")

    with pytest.raises(identity.IdentityError, match="CLAUDE_PLUGIN_ROOT"):
        identity.prepare_state_dir(foreign, now=NOW)

    assert (foreign.state_dir / "journal.jsonl").read_text() == "old"
    assert not (foreign.state_dir / "format.json").exists()


def test_prepare_with_another_root_in_the_environment_uses_a_ready_location(ident, tmp_path, monkeypatch):
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


def test_prepare_stops_without_changes_when_the_retired_name_exists(ident):
    # 規則 P2: 退避先が既にあれば、名前を足して続けずに何も変えずに止まる
    earlier = ident.state_dir.with_name("p.v10-20261113")
    earlier.mkdir(parents=True)
    (earlier / "keep").write_text("1")
    ident.state_dir.mkdir()
    (ident.state_dir / "old").write_text("2")

    with pytest.raises(identity.IdentityError) as caught:
        identity.prepare_state_dir(ident, now=NOW)

    assert (caught.value.kind, caught.value.reason, caught.value.paths) == ("precondition", "retire_target_exists", (earlier,))
    assert caught.value.items() == [{"reason": "retire_target_exists", "path": str(earlier)}]
    assert (earlier / "keep").read_text() == "1"
    assert sorted(p.name for p in ident.state_dir.iterdir()) == ["old"]
    assert _retired_names(ident) == ["p.v10-20261113"]


def test_retired_name_uses_the_form_and_the_utc_date(tmp_path):
    place = tmp_path / "ndf"
    assert identity.retired_name(place, None, datetime(2026, 11, 17, 3, 0, tzinfo=UTC)) == tmp_path / "ndf.v10-20261117"
    assert identity.retired_name(place, 1, datetime(2026, 11, 17, 3, 0, tzinfo=UTC)) == tmp_path / "ndf.v1-20261117"
    # JST の 11-18 00:30 は UTC の 11-17
    jst = timezone(timedelta(hours=9))
    assert identity.retired_name(place, None, datetime(2026, 11, 18, 0, 30, tzinfo=jst)) == tmp_path / "ndf.v10-20261117"


def _lock_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def test_lock_held_is_false_for_a_free_or_missing_lock_and_true_while_held(tmp_path):
    lock = _lock_file(tmp_path / "relay.lock")
    assert identity.lock_held(lock) is False
    assert identity.lock_held(tmp_path / "missing.lock") is False
    assert not (tmp_path / "missing.lock").exists()
    with open(lock) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert identity.lock_held(lock) is True


def test_hold_legacy_locks_keeps_every_lock_until_the_block_ends(tmp_path):
    a = _lock_file(tmp_path / "relay" / "w1" / "relay.lock")
    hub = _lock_file(tmp_path / "relay" / "hub" / "hub.lock")

    with identity.hold_legacy_locks(tmp_path, identity.LEGACY_LOCKS) as busy:
        assert busy == []
        assert taken_by_another(a) and taken_by_another(hub)

    assert not taken_by_another(a) and not taken_by_another(hub)


def test_hold_legacy_locks_creates_no_lock_file(tmp_path):
    with identity.hold_legacy_locks(tmp_path, identity.LEGACY_LOCKS) as busy:
        assert busy == []
    assert list(tmp_path.iterdir()) == []


def test_old_locks_open_writable_without_truncating_and_fall_back_when_read_only(tmp_path, monkeypatch):
    # Linux の NFS は読むだけで開いたファイルに排他を取れないため、書ける形で開く。中身は切り詰めない
    a = _lock_file(tmp_path / "relay" / "w1" / "relay.lock")
    a.write_text("12345\n")
    ro = _lock_file(tmp_path / "relay" / "hub" / "hub.lock")
    ro.chmod(0o444)
    real_open = identity.os.open
    flags: dict[str, int] = {}

    def recording_open(path, flag, *args):
        fd = real_open(path, flag, *args)
        # os.open の差し替えはテストの補助の開き方にも効くため、最初に開いた形（hold_legacy_locks）だけを残す
        flags.setdefault(Path(path).name, flag)
        return fd

    monkeypatch.setattr(identity.os, "open", recording_open)
    with identity.hold_legacy_locks(tmp_path, identity.LEGACY_LOCKS) as busy:
        assert busy == []
        assert taken_by_another(a) and taken_by_another(ro)
    assert identity.lock_held(a) is False

    assert flags["relay.lock"] & identity.os.O_ACCMODE == identity.os.O_RDWR
    assert not flags["relay.lock"] & (identity.os.O_CREAT | identity.os.O_TRUNC)
    assert a.read_text() == "12345\n"


@pytest.mark.parametrize("held_rel", ["relay/w1/relay.lock", "relay/hub/hub.lock"])
def test_hold_legacy_locks_names_a_held_lock_and_releases_the_others(tmp_path, held_rel):
    held_path = _lock_file(tmp_path / held_rel)
    other = _lock_file(tmp_path / "relay" / "w0" / "relay.lock")
    with open(held_path) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with identity.hold_legacy_locks(tmp_path, identity.LEGACY_LOCKS) as busy:
            assert busy == [held_path]
            # 取れた錠は with の中で既に放している
            assert not taken_by_another(other)


def test_prepare_stops_without_changes_while_an_old_wrapper_runs(ident):
    # 規則 P6: 旧いラッパーの錠が取れなければ legacy_running で止まり、何も変えない
    lock = _lock_file(ident.state_dir / "relay" / "20261113T1" / "relay.lock")
    with open(lock) as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(identity.IdentityError) as caught:
            identity.prepare_state_dir(ident, now=NOW)

    assert (caught.value.kind, caught.value.reason, caught.value.paths) == ("store_in_use", "legacy_running", (lock,))
    assert caught.value.items() == [{"reason": "legacy_running", "path": str(lock)}]
    assert lock.exists() and not (ident.state_dir / identity.FORMAT_FILE).exists()
    assert _retired_names(ident) == []


def test_prepare_holds_the_old_locks_through_the_rename_and_the_format_file(ident, monkeypatch):
    # 不変条件 I3b: 確かめから名前の変更と format.json の書き込みが終わるまで、旧い錠を持ち続ける
    old_lock = _lock_file(ident.state_dir / "relay" / "20261113T1" / "relay.lock")
    hub = _lock_file(ident.state_dir / "relay" / "hub" / "hub.lock")
    retired = ident.state_dir.with_name("p.v10-20261113")
    seen: dict[str, bool] = {}
    rename = Path.rename
    write_format = identity._write_format

    def probe_rename(self, target):
        if self == ident.state_dir:
            seen["rename"] = taken_by_another(old_lock) and taken_by_another(hub)
        return rename(self, target)

    def probe_format(state_dir):
        moved = retired / old_lock.relative_to(ident.state_dir)
        seen["format"] = taken_by_another(moved) and taken_by_another(retired / hub.relative_to(ident.state_dir))
        write_format(state_dir)

    monkeypatch.setattr(Path, "rename", probe_rename)
    monkeypatch.setattr(identity, "_write_format", probe_format)

    ready = identity.prepare_state_dir(ident, now=NOW)

    assert ready.retired == (identity.Retired(ident.state_dir, retired),)
    assert seen == {"rename": True, "format": True}
    assert not taken_by_another(retired / old_lock.relative_to(ident.state_dir))


def test_prepare_waits_for_the_state_lock(ident):
    # 判定から format.json の作成までは、同じ保存先を用意するほかの処理と重ならない
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
