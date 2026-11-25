"""複製 ~/.claude/ndf/relay.py と PATH のエントリポイント（#2461・#2583・#4528・要求の 346 行）。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ndf.domains.relay import install

CODE = 9


def _plugin(base: Path, version: str) -> Path:
    root = base / version
    ndf = root / "bin" / "ndf"
    ndf.parent.mkdir(parents=True)
    ndf.write_text(f'#!/bin/sh\necho "{version} $*"\n', encoding="utf-8")
    ndf.chmod(0o755)
    return root


def _env(tmp_path: Path) -> dict[str, str]:
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "XDG_BIN_HOME": str(tmp_path / "bin"), "PATH": str(tmp_path / "bin")}


def test_old_copy_and_foreign_entry_are_not_overwritten_unless_asked(tmp_path):
    env = _env(tmp_path)
    root = _plugin(tmp_path / "cache", "11.0.0")
    copy = tmp_path / "claude" / "ndf" / "relay.py"
    copy.parent.mkdir(parents=True)
    copy.write_text("# 旧い NDF の複製\n", encoding="utf-8")
    entry = tmp_path / "bin" / install.ENTRY_NAME
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    got = install.install(env, root, CODE, replace_foreign=False, version="11.0.0")
    assert (got.copy.state, got.entry.state, got.ndf.state) == ("foreign", "foreign", "ours")
    assert copy.read_text(encoding="utf-8") == "# 旧い NDF の複製\n"
    assert install.install(env, root, CODE, replace_foreign=True, version="11.0.0").copy.state == "ours"
    removed = install.uninstall(env, CODE)
    assert (removed.copy.state, removed.entry.state, removed.ndf.state) == ("absent", "foreign", "absent")
    assert entry.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_entry_outside_path_is_reported(tmp_path):
    env = {**_env(tmp_path), "PATH": "/usr/bin"}
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    assert got.on_path is False


def _version_file(env: dict[str, str]) -> Path:
    return install.config_dir(env) / install.VERSION_FILE


def test_install_records_its_own_version_when_it_writes_the_launcher(tmp_path):
    # 規則 P3: 10 系の SessionStart は relay.version より古いバージョンでは複製を置き直さない。置き換えたら自分のバージョンを書く
    env = _env(tmp_path)
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    assert got.version == "11.0.0"
    assert _version_file(env).read_text(encoding="utf-8") == "11.0.0\n"
    assert _version_file(env).stat().st_mode & 0o777 == 0o644


def test_install_leaves_relay_version_alone_when_it_keeps_an_old_copy(tmp_path):
    # マーカーの無い 10 系の複製を replace_foreign が偽で残すとき、10 系のバージョンの記録を書き換えない
    env = _env(tmp_path)
    copy = install.config_dir(env) / install.COPY_NAME
    copy.parent.mkdir(parents=True)
    copy.write_text("# 旧い NDF の複製\n", encoding="utf-8")
    _version_file(env).write_text("10.27.77\n", encoding="utf-8")
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    assert got.version is None
    assert _version_file(env).read_text(encoding="utf-8") == "10.27.77\n"
    assert install.install(env, _plugin(tmp_path / "cache2", "11.0.0"), CODE, replace_foreign=True, version="11.0.0").version == "11.0.0"
    assert _version_file(env).read_text(encoding="utf-8") == "11.0.0\n"


def test_follow_places_nothing_without_an_installed_copy(tmp_path):
    env = _env(tmp_path)
    new = _plugin(tmp_path / "cache", "11.0.1")
    assert install.follow(env, new, "11.0.1") is None
    assert not (tmp_path / "claude" / "ndf").exists()
    # 旧い NDF の複製（マーカーが無い）も向け直さない
    foreign = tmp_path / "claude" / "ndf" / "relay.py"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("# old\n", encoding="utf-8")
    assert install.follow(env, new, "11.0.1") is None
    assert not (foreign.parent / "relay.root").exists()


def _run(path: Path, *args: str) -> str:
    return subprocess.run([str(path), *args], check=True, capture_output=True, text=True).stdout


def test_ndf_entry_passes_arguments_as_they_are_and_ndf_relay_adds_relay(tmp_path):
    # #4528: 端末から `ndf` を打てる。`ndf` は relay を足さず、`ndf-relay` は relay を足してプラグインの bin/ndf を呼ぶ
    assert shutil.which("python3")
    env = _env(tmp_path)
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    assert got.ndf == install.Status(tmp_path / "bin" / install.NDF_NAME, "ours")
    assert got.ndf.path.stat().st_mode & 0o777 == 0o755
    assert _run(got.ndf.path, "check", "a b") == "11.0.0 check a b\n"
    assert _run(got.ndf.path) == "11.0.0 \n"
    assert _run(got.entry.path, "account", "list") == "11.0.0 relay account list\n"


def test_ndf_entry_follows_relay_root_when_the_plugin_is_updated(tmp_path):
    # プラグインの更新は relay.root の向け直しだけで `ndf` にも届く（置き直さない）
    env = _env(tmp_path)
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    assert install.follow(env, _plugin(tmp_path / "cache", "11.0.1"), "11.0.1") == "11.0.1"
    assert _run(got.ndf.path, "--version") == "11.0.1 --version\n"
    assert install.status(env, CODE).ndf.state == "ours"


def test_stale_ndf_entry_is_rewritten_and_uninstall_removes_it(tmp_path):
    env = _env(tmp_path)
    root = _plugin(tmp_path / "cache", "11.0.0")
    ndf = tmp_path / "bin" / install.NDF_NAME
    ndf.parent.mkdir(parents=True)
    ndf.write_text(f"#!/bin/sh\n{install.NDF_MARK}\nexec false\n", encoding="utf-8")
    assert install.status(env, CODE).ndf.state == "stale"
    assert install.install(env, root, CODE, replace_foreign=False, version="11.0.0").ndf.state == "ours"
    assert _run(ndf, "x") == "11.0.0 x\n"
    assert install.uninstall(env, CODE).ndf.state == "absent"
    assert not ndf.exists()


def test_restore_entries_puts_back_entries_lost_with_the_container(tmp_path):
    # #4535: ~/.local/bin が使い捨ての領域だと、再起動でエントリポイントだけが消える。複製があれば SessionStart が置き直す
    env = _env(tmp_path)
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    shutil.rmtree(tmp_path / "bin")
    assert install.restore_entries(env) == [got.entry.path, got.ndf.path]
    assert _run(got.entry.path, "x") == "11.0.0 relay x\n"
    assert _run(got.ndf.path, "x") == "11.0.0 x\n"
    assert install.restore_entries(env) == []


def test_restore_entries_leaves_foreign_stale_and_uninstalled_alone(tmp_path):
    env = _env(tmp_path)
    install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False, version="11.0.0")
    entry, ndf = tmp_path / "bin" / install.ENTRY_NAME, tmp_path / "bin" / install.NDF_NAME
    entry.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    # 別のアカウントの CLAUDE_CONFIG_DIR が置いたエントリポイントは中身が違う（stale）。書き換え合わない
    ndf.write_text(f"#!/bin/sh\n{install.NDF_MARK}\nexec python3 /other/relay.py --ndf \"$@\"\n", encoding="utf-8")
    assert install.restore_entries(env) == []
    assert entry.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert "/other/relay.py" in ndf.read_text(encoding="utf-8")
    install.uninstall(env, CODE)
    entry.unlink()
    assert install.restore_entries(env) == []
    assert not entry.exists() and not ndf.exists()
