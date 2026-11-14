"""複製 ~/.claude/ndf/relay.py と PATH のエントリポイント（#2461・#2583・要求の 346 行）。"""

from __future__ import annotations

import subprocess
import sys
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


def _launch(copy: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(copy), *args], capture_output=True, text=True, timeout=30, check=False)


def test_one_install_places_a_copy_that_calls_the_plugin(tmp_path):
    env = _env(tmp_path)
    root = _plugin(tmp_path / "cache", "11.0.0")
    got = install.install(env, root, CODE, replace_foreign=False)
    assert (got.copy.state, got.entry.state, got.on_path) == ("ours", "ours", True)
    assert got.copy.path == tmp_path / "claude" / "ndf" / "relay.py"
    assert _launch(got.copy.path, "ls").stdout.strip() == "11.0.0 relay ls"
    # PATH のエントリポイントはどこからでも複製を呼ぶ
    entry = subprocess.run([str(got.entry.path), "ls"], capture_output=True, text=True, timeout=30, check=False, cwd=tmp_path)
    assert entry.stdout.strip() == "11.0.0 relay ls"
    again = install.install(env, root, CODE, replace_foreign=False)
    assert install.status(env, CODE).copy.state == "ours" and again.entry.state == "ours"


def test_copy_follows_a_plugin_update_that_removed_the_old_version(tmp_path):
    # プラグインの更新で版の置き場が消えても、同じ親の新しい版へ向け直す（#2583）
    env = _env(tmp_path)
    old = _plugin(tmp_path / "cache", "11.0.0")
    got = install.install(env, old, CODE, replace_foreign=False)
    _plugin(tmp_path / "cache", "11.0.1")
    subprocess.run(["rm", "-rf", str(old)], check=True)
    assert _launch(got.copy.path, "ls").stdout.strip() == "11.0.1 relay ls"
    assert (got.copy.path.parent / install.ROOT_FILE).read_text(encoding="utf-8").strip().endswith("11.0.1")


def test_copy_without_a_plugin_says_why_and_how(tmp_path):
    env = _env(tmp_path)
    root = _plugin(tmp_path / "cache", "11.0.0")
    got = install.install(env, root, CODE, replace_foreign=False)
    subprocess.run(["rm", "-rf", str(tmp_path / "cache")], check=True)
    done = _launch(got.copy.path, "ls")
    assert done.returncode == CODE and "relay install" in done.stderr


def test_old_copy_and_foreign_entry_are_not_overwritten_unless_asked(tmp_path):
    env = _env(tmp_path)
    root = _plugin(tmp_path / "cache", "11.0.0")
    copy = tmp_path / "claude" / "ndf" / "relay.py"
    copy.parent.mkdir(parents=True)
    copy.write_text("# 旧い NDF の複製\n", encoding="utf-8")
    entry = tmp_path / "bin" / install.ENTRY_NAME
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    got = install.install(env, root, CODE, replace_foreign=False)
    assert (got.copy.state, got.entry.state) == ("foreign", "foreign")
    assert copy.read_text(encoding="utf-8") == "# 旧い NDF の複製\n"
    assert install.install(env, root, CODE, replace_foreign=True).copy.state == "ours"
    removed = install.uninstall(env, CODE)
    assert (removed.copy.state, removed.entry.state) == ("absent", "foreign")
    assert entry.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_entry_outside_path_is_reported(tmp_path):
    env = {**_env(tmp_path), "PATH": "/usr/bin"}
    got = install.install(env, _plugin(tmp_path / "cache", "11.0.0"), CODE, replace_foreign=False)
    assert got.on_path is False
