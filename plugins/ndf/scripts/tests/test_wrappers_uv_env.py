"""汎用の処理の包み（lib/ の 12 本・#1142 の決定 19）のテストを、宣言と lock の環境で流す。

包みのテスト（`test_lib_<包み>.py`）は、包みが使う外部パッケージを import できる環境でだけ中身を流す。
全体のテストの環境（playwright-kit の venv）にはそのパッケージが無いため、ここで `uv run --frozen --extra <グループ>`
の環境に pytest を足して、そのファイルを流し直す。パッケージを import できる環境（uv の環境の中）では、
ファイルがその場で流れるので、ここは何もしない。

uv が無いときは落とす（読み飛ばすと、包みのテストが 1 件も流れないまま通る）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
LIB = TESTS.parent / "lib"
PLUGIN = LIB.parents[1]
REPO = PLUGIN.parents[1]
sys.path.insert(0, str(LIB))
import deps  # noqa: E402

WRAPPERS = ("md", "mdtable", "schema", "procs", "locks", "shparse", "versions", "pathmatch", "textparse",
            "yamlio", "waits", "notify")
EXTRA_GROUPS = {"versions": ("versions", "bump")}


@pytest.mark.parametrize("wrapper", WRAPPERS)
def test_wrapper_tests_pass_in_the_locked_environment(wrapper: str):
    groups = EXTRA_GROUPS.get(wrapper, (wrapper,))
    if all(deps.importable(g) for g in groups):
        pytest.skip("この環境で import できるので、test_lib_%s.py がその場で流れる" % wrapper)
    uv = deps.find_uv()
    assert uv, "uv が無い。包みのテストは uv の環境で流す（https://docs.astral.sh/uv/）"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "VIRTUAL_ENV"))}
    env["UV_PROJECT_ENVIRONMENT"] = deps.venv_dir()
    cmd = [uv, "run", "--quiet", "--frozen", "--project", str(PLUGIN)]
    for g in groups:
        cmd += ["--extra", g]
    cmd += ["--with", "pytest", "python", "-m", "pytest", str(TESTS / f"test_lib_{wrapper}.py"),
            "-q", "-p", "no:cacheprovider", "-p", "no:randomly"]
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    tail = (p.stdout + p.stderr)[-3000:]
    assert p.returncode == 0, tail
    assert " passed" in p.stdout and "skipped" not in p.stdout.splitlines()[-1], tail


def test_every_wrapper_has_a_test_file_and_a_group():
    for w in WRAPPERS:
        assert (LIB / f"{w}.py").is_file()
        assert (TESTS / f"test_lib_{w}.py").is_file()
        assert all(g in deps.GROUPS for g in EXTRA_GROUPS.get(w, (w,)))


def test_root_declaration_pins_the_same_versions_as_the_plugin():
    """根の pyproject.toml のグループは、plugins/ndf の同じ名前のグループと同じ版を持つ。"""
    import re

    def groups(path: Path) -> dict[str, str]:
        section = path.read_text().split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
        return dict(re.findall(r'^(\w[\w-]*)\s*=\s*\[(.*?)\]', section, re.M))

    plugin, root = groups(PLUGIN / "pyproject.toml"), groups(REPO / "pyproject.toml")
    assert root and set(root) <= set(plugin)
    for name, pins in root.items():
        assert pins == plugin[name], name
    lock = (REPO / "uv.lock").read_text()
    for pins in root.values():
        for name, ver in re.findall(r'"([\w-]+)==([\w.]+)"', pins):
            assert f'name = "{name}"\nversion = "{ver}"' in lock
