"""版数の比較と一括の書き換えの包み（lib/versions.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import semver  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import versions  # noqa: E402


def test_order_puts_dev_before_rc_before_release_and_compares_numbers():
    vs = ["10.17.10", "10.17.10-rc.1", "10.17.10-dev.10", "10.17.9", "10.17.10-dev.9", "v10.17.8"]
    assert sorted(vs, key=versions.version_order) == [
        "v10.17.8", "10.17.9", "10.17.10-dev.9", "10.17.10-dev.10", "10.17.10-rc.1", "10.17.10"]
    assert versions.compare_versions("1.2.3-dev.2", "1.2.3-dev.10") < 0
    assert versions.compare_versions("1.2.3", "1.2.3") == 0


@pytest.mark.parametrize("bad", ["", None, "1.2", "1.2.3-beta.1", "1.2.3-dev", "1.2.3+b1", "x"])
def test_other_forms_are_not_versions(bad):
    assert versions.parse_version(bad) is None
    with pytest.raises(ValueError):
        versions.compare_versions("1.0.0", bad or "")


def test_next_versions_and_base():
    assert versions.next_dev("10.17.30-dev.1") == "10.17.30-dev.2"
    assert versions.next_dev("10.17.30") == "10.17.31-dev.1"
    assert versions.next_patch("10.17.31-dev.2") == "10.17.31"
    assert versions.next_patch("10.17.31") == "10.17.32"
    assert versions.release_base("1.2.3-rc.4") == "1.2.3" and versions.dev_number("1.2.3-rc.4") is None
    with pytest.raises(ValueError):
        versions.next_dev("1.2.3-rc.1")


CONFIG = r'''[tool.bumpversion]
current_version = "1.2.3-dev.1"
parse = "(?P<major>\\d+)\\.(?P<minor>\\d+)\\.(?P<patch>\\d+)(?:-(?P<pre_l>dev|rc)\\.(?P<pre_n>\\d+))?"
serialize = ["{major}.{minor}.{patch}-{pre_l}.{pre_n}", "{major}.{minor}.{patch}"]

[[tool.bumpversion.files]]
filename = "plugin.json"
search = '"version": "{current_version}"'
replace = '"version": "{new_version}"'
'''


def test_bump_replace_rewrites_the_configured_places(tmp_path: Path):
    import bumpversion  # noqa: F401  包みの外部パッケージ。無ければ落とす
    (tmp_path / "plugin.json").write_text('{"version": "1.2.3-dev.1", "other": "1.2.3-dev.1"}\n')
    (tmp_path / "bump.toml").write_text(CONFIG)
    r = versions.bump_replace(tmp_path / "bump.toml", "1.2.3-dev.1", "1.2.3-dev.2", tmp_path)
    assert r.ok, r.output
    assert (tmp_path / "plugin.json").read_text() == '{"version": "1.2.3-dev.2", "other": "1.2.3-dev.1"}\n'
    with pytest.raises(ValueError):
        versions.bump_replace(tmp_path / "bump.toml", "1.2.3-dev.2", "next", tmp_path)
