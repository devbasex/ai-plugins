"""外部パッケージの require()（lib/deps.py・#1142 の決定 17・不足 h）。

uv は一時の PATH に置いた偽物で差し替える。偽物は受けた引数と環境を記録して終わる（`os.execve` の後は偽物の終了コード）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = LIB.parents[1]
sys.path.insert(0, str(LIB))
import deps  # noqa: E402

FAKE_UV = """#!/bin/sh
{ printf '%s\\n' "$@"; echo "REEXEC=$NDF_DEPS_REEXEC"; echo "VENV=$UV_PROJECT_ENVIRONMENT"; } > "$FAKE_UV_LOG"
exit 7
"""


def entry(tmp_path: Path, modules: list[str], more: dict[str, list[str]] | None = None) -> Path:
    groups = {"t": modules, **(more or {})}
    script = tmp_path / "entry.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(LIB)!r})\nimport deps\n"
        f"deps.GROUPS.update({groups!r})\ndeps.require(*{list(groups)!r})\nprint('after', sys.argv[1:])\n"
    )
    return script


def run_entry(
    tmp_path: Path, modules: list[str], *, uv: bool, more: dict[str, list[str]] | None = None, **env: str
) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if uv:
        (bindir / "uv").write_text(FAKE_UV)
        (bindir / "uv").chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k not in ("NDF_DEPS_REEXEC", "UV_PROJECT_ENVIRONMENT")}
    base.update(
        PATH=str(bindir), HOME=str(tmp_path / "home"), FAKE_UV_LOG=str(tmp_path / "uv.log"), NDF_DEPS_VENV=str(tmp_path / "venv"), **env
    )
    return subprocess.run([sys.executable, str(entry(tmp_path, modules, more)), "a1", "--x"], capture_output=True, text=True, env=base)


def test_an_importable_group_returns_without_reexec(tmp_path):
    p = run_entry(tmp_path, ["json"], uv=True)
    assert p.returncode == 0 and "after ['a1', '--x']" in p.stdout
    assert not (tmp_path / "uv.log").exists()


def test_reexec_through_uv_with_the_lock_and_the_venv_outside_the_plugin(tmp_path):
    p = run_entry(tmp_path, ["ndf_no_such_module"], uv=True)
    assert p.returncode == 7, p.stderr
    log = (tmp_path / "uv.log").read_text().splitlines()
    assert log[:9] == [
        "run",
        "--quiet",
        "--frozen",
        "--project",
        str(PLUGIN),
        "--extra",
        "t",
        "python",
        str((tmp_path / "entry.py").resolve()),
    ]
    assert log[9:11] == ["a1", "--x"]
    assert f"REEXEC={(tmp_path / 'entry.py').resolve()}" in log and f"VENV={tmp_path / 'venv'}" in log


def test_several_groups_reexec_once_with_every_extra(tmp_path):
    """決定 23: 2 つ以上のグループは 1 回の起動し直しで入る。import できるグループも並べる。"""
    p = run_entry(tmp_path, ["json"], uv=True, more={"u": ["ndf_no_such_module"], "w": ["ndf_no_such_module_2"]})
    assert p.returncode == 7, p.stderr
    log = (tmp_path / "uv.log").read_text().splitlines()
    assert log[:11] == ["run", "--quiet", "--frozen", "--project", str(PLUGIN), "--extra", "t", "--extra", "u", "--extra", "w"]
    assert log[11:15] == ["python", str((tmp_path / "entry.py").resolve()), "a1", "--x"]


def test_several_importable_groups_return_without_reexec(tmp_path):
    p = run_entry(tmp_path, ["json"], uv=True, more={"u": ["os"]})
    assert p.returncode == 0 and "after ['a1', '--x']" in p.stdout
    assert not (tmp_path / "uv.log").exists()


def test_after_reexec_a_missing_group_among_several_stops_as_deps_missing(tmp_path):
    p = run_entry(tmp_path, ["json"], uv=True, more={"u": ["ndf_no_such_module"]}, NDF_DEPS_REEXEC=str((tmp_path / "entry.py").resolve()))
    assert p.returncode == deps.EXIT_DEPS_MISSING and "after" not in p.stdout
    assert "u のパッケージ（ndf_no_such_module）" in p.stderr


def test_a_project_root_reexecs_with_its_own_lock_and_venv(tmp_path):
    """根の scripts/ は根の宣言と lock で解決し、環境は根の .venv に置く（決定 19・22）。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\n")
    (root / "uv.lock").write_text("")
    script = tmp_path / "entry.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(LIB)!r})\nimport deps\n"
        f"deps.GROUPS.update({{'t': ['ndf_no_such_module']}})\ndeps.require('t', project={str(root)!r})\n"
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "uv").write_text(FAKE_UV)
    (bindir / "uv").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("NDF_DEPS_REEXEC", "UV_PROJECT_ENVIRONMENT", "NDF_DEPS_VENV")}
    env.update(PATH=str(bindir), HOME=str(tmp_path / "home"), FAKE_UV_LOG=str(tmp_path / "uv.log"))
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env)
    assert p.returncode == 7, p.stderr
    log = (tmp_path / "uv.log").read_text().splitlines()
    assert log[:7] == ["run", "--quiet", "--frozen", "--project", str(root.resolve()), "--extra", "t"]
    assert f"VENV={root.resolve() / '.venv'}" in log


def test_reexec_argv_keeps_the_single_group_form():
    assert deps.reexec_argv("uv", "md", "s.py", ["a"], Path("/p")) == deps.reexec_argv("uv", ["md"], "s.py", ["a"], Path("/p"))


def test_after_reexec_a_missing_package_stops_as_deps_missing(tmp_path):
    p = run_entry(tmp_path, ["ndf_no_such_module"], uv=True, NDF_DEPS_REEXEC=str((tmp_path / "entry.py").resolve()))
    assert p.returncode == deps.EXIT_DEPS_MISSING and "after" not in p.stdout
    assert "import できない" in p.stderr
    assert not (tmp_path / "uv.log").exists()


def test_reexec_mark_is_not_inherited_by_children(monkeypatch):
    """起動し直した親（supervise.py）の子が別のグループを要るとき、印を継いで止まらない（v10.17.31-dev.1 の bump）。"""
    monkeypatch.setenv("NDF_DEPS_REEXEC", "1")
    monkeypatch.setitem(deps.GROUPS, "t", ["json"])
    deps.require("t")
    assert "NDF_DEPS_REEXEC" not in os.environ


def test_a_mark_inherited_from_another_script_reexecs(tmp_path):
    """印を外す前の版の親（`1` を付けたまま子を走らせる）から継いでも、子は起動し直す（v10.17.31-dev.1 の bump）。"""
    p = run_entry(tmp_path, ["ndf_no_such_module"], uv=True, NDF_DEPS_REEXEC="1")
    assert p.returncode == 7, p.stderr
    assert f"REEXEC={(tmp_path / 'entry.py').resolve()}" in (tmp_path / "uv.log").read_text().splitlines()


def test_uv_is_installed_when_missing_then_reexec(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "find_uv", lambda: None)
    monkeypatch.setattr(deps, "install_uv", lambda: "/opt/uv")
    monkeypatch.delenv("NDF_DEPS_REEXEC", raising=False)
    monkeypatch.setitem(deps.GROUPS, "t", ["ndf_no_such_module"])
    calls = []

    def fake_execve(path, argv, env):
        calls.append((path, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(deps.os, "execve", fake_execve)
    with pytest.raises(SystemExit):
        deps.require("t")
    path, argv, env = calls[0]
    assert path == "/opt/uv" and argv[:3] == ["/opt/uv", "run", "--quiet"]
    assert env["NDF_DEPS_REEXEC"] == str(Path(sys.argv[0]).resolve())


def test_uv_that_cannot_be_installed_stops_with_the_manual_command(monkeypatch, capsys):
    monkeypatch.setattr(deps, "find_uv", lambda: None)
    monkeypatch.setattr(deps, "install_uv", lambda: None)
    monkeypatch.delenv("NDF_DEPS_REEXEC", raising=False)
    monkeypatch.setitem(deps.GROUPS, "t", ["ndf_no_such_module"])
    with pytest.raises(SystemExit) as e:
        deps.require("t")
    assert e.value.code == deps.EXIT_DEPS_MISSING
    assert deps.INSTALL_HINT in capsys.readouterr().err


def test_a_project_without_its_declaration_stops_as_deps_missing(monkeypatch, tmp_path, capsys):
    """pyproject.toml と uv.lock が無い根は、uv を探さずに依存の欠けで止まる（#1654）。"""
    monkeypatch.delenv("NDF_DEPS_REEXEC", raising=False)
    monkeypatch.setitem(deps.GROUPS, "t", ["ndf_no_such_module"])
    monkeypatch.setattr(deps, "find_uv", lambda: pytest.fail("宣言が無いのに uv を探した"))
    with pytest.raises(SystemExit) as e:
        deps.require("t", project=tmp_path)
    assert e.value.code == deps.EXIT_DEPS_MISSING
    assert capsys.readouterr().err.startswith("❌ [ndf deps] 外部パッケージの宣言が無い")


def test_deps_missing_does_not_overlap_the_common_codes():
    """依存の欠けは 0〜3（3 は飛ばしてよい）と 10〜29（承認ゲート・判断待ち）のどれとも重ならない（#1654 の I2）。"""
    assert deps.EXIT_DEPS_MISSING not in (*range(0, 4), *range(10, 30))


def test_refactor_assess_without_its_packages_stops_as_deps_missing(tmp_path):
    """依存の欠けた python（-S で site-packages を外す）で起動し直した後の assess は、飛ばしてよい（3）でなく
    依存の欠けで終わる（#1654 の受け入れ条件 1）。"""
    script = PLUGIN / "skills" / "cross-refactoring" / "scripts" / "refactor.py"
    env = dict(os.environ, NDF_DEPS_REEXEC=str(script.resolve()))
    p = subprocess.run(
        [sys.executable, "-S", str(script), "assess", "--base", "HEAD"], capture_output=True, text=True, env=env, cwd=tmp_path
    )
    assert p.returncode == deps.EXIT_DEPS_MISSING, p.stderr
    assert p.stderr.startswith("❌ [ndf deps]") and p.stdout == ""


def test_install_uv_uses_pip_without_curl(monkeypatch):
    calls = []
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    monkeypatch.setattr(deps.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw["env"])))
    monkeypatch.setattr(deps, "find_uv", lambda: None)
    assert deps.install_uv() is None
    cmd, env = calls[0]
    assert cmd[1:] == ["-m", "pip", "install", "--user", "--quiet", f"uv=={deps.UV_VERSION}"]
    assert env["UV_NO_MODIFY_PATH"] == "1"


def test_install_uv_uses_the_pinned_installer_with_curl(monkeypatch):
    calls = []
    monkeypatch.setattr(deps.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None)
    monkeypatch.setattr(deps.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(deps, "find_uv", lambda: "/home/u/.local/bin/uv")
    assert deps.install_uv() == "/home/u/.local/bin/uv"
    assert calls[0] == ["sh", "-c", f"curl -LsSf https://astral.sh/uv/{deps.UV_VERSION}/install.sh | sh"]


def test_find_uv_looks_in_local_bin(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert deps.find_uv() is None
    uv = tmp_path / ".cargo" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    assert deps.find_uv() == str(uv)


def test_an_unknown_group_is_a_programming_error():
    with pytest.raises(ValueError):
        deps.require("aws-not-declared")
    with pytest.raises(ValueError):
        deps.require("md", "aws-not-declared")


def test_the_venv_is_named_by_the_plugin_version(monkeypatch, tmp_path):
    monkeypatch.delenv("NDF_DEPS_VENV", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert deps.venv_dir() == str(tmp_path / ".cache" / "ndf" / "venv" / version)
    assert deps.venv_version(tmp_path) == "dev"


def test_groups_match_the_declaration_and_the_lock():
    """require() のグループは pyproject.toml の extra と同じで、版は uv.lock が固定する。"""
    text = (PLUGIN / "pyproject.toml").read_text()
    section = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    declared = dict(re.findall(r"^(\w[\w-]*)\s*=\s*\[(.*?)\]", section, re.M))
    assert set(declared) == set(deps.GROUPS)
    lock = (PLUGIN / "uv.lock").read_text()
    for pkgs in declared.values():
        for name, ver in re.findall(r'"([\w-]+)==([\w.]+)"', pkgs):
            assert f'name = "{name}"\nversion = "{ver}"' in lock


def test_root_declaration_pins_the_same_versions_as_the_plugin():
    """根の pyproject.toml（全体テストの環境・決定 22）は plugins/ndf の全グループを同じ版で持ち、根の uv.lock が固定する。"""

    def groups(path: Path) -> dict[str, str]:
        section = path.read_text().split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
        return dict(re.findall(r"^(\w[\w-]*)\s*=\s*\[(.*?)\]", section, re.M))

    repo = PLUGIN.parents[1]
    plugin, root = groups(PLUGIN / "pyproject.toml"), groups(repo / "pyproject.toml")
    assert root == plugin
    lock = (repo / "uv.lock").read_text()
    for pins in root.values():
        for name, ver in re.findall(r'"([\w-]+)==([\w.]+)"', pins):
            assert f'name = "{name}"\nversion = "{ver}"' in lock


# ---------------------------------------------------------------- 子プロセスへ UV_PROJECT_ENVIRONMENT を漏らさない（#2509 の I5〜I7）

REEXEC_UV = """#!/bin/sh
while [ "$1" != python ]; do shift; done
shift
PYTHONPATH="$FAKE_SITE" exec "$FAKE_PY" "$@"
"""


def run_reexec_child(tmp_path: Path, **env: str) -> dict[str, str]:
    """起動し直した先で import が通る偽の uv（`PYTHONPATH` で印のパッケージを足して本物の python を起動する）で
    require を通し、スクリプト自身と子プロセスから見た `UV_PROJECT_ENVIRONMENT` を返す。"""
    bindir, site = tmp_path / "bin", tmp_path / "site"
    bindir.mkdir(exist_ok=True)
    site.mkdir(exist_ok=True)
    (site / "ndf_reexec_mark.py").write_text("")
    (bindir / "uv").write_text(REEXEC_UV)
    (bindir / "uv").chmod(0o755)
    script = tmp_path / "entry.py"
    script.write_text(
        f"import os, subprocess, sys\nsys.path.insert(0, {str(LIB)!r})\nimport deps\n"
        "deps.GROUPS['t'] = ['ndf_reexec_mark']\ndeps.require('t')\n"
        "print('SELF=' + repr(os.environ.get('UV_PROJECT_ENVIRONMENT')))\n"
        "print('SAVED=' + repr(os.environ.get('NDF_DEPS_SAVED_UV_PROJECT_ENVIRONMENT')))\n"
        "c = [sys.executable, '-c', \"import os; print('CHILD=' + repr(os.environ.get('UV_PROJECT_ENVIRONMENT')))\"]\n"
        "sys.stdout.write(subprocess.run(c, capture_output=True, text=True).stdout)\n"
    )
    base = {k: v for k, v in os.environ.items() if k not in ("NDF_DEPS_REEXEC", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH")}
    base.update(
        PATH=str(bindir), HOME=str(tmp_path / "home"), NDF_DEPS_VENV=str(tmp_path / "venv"),
        FAKE_SITE=str(site), FAKE_PY=sys.executable, **env,
    )  # fmt: skip
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=base)
    assert p.returncode == 0, p.stderr
    return dict(ln.split("=", 1) for ln in p.stdout.splitlines() if "=" in ln)


def test_a_reexeced_script_does_not_pass_the_shared_venv_to_children(tmp_path):
    """受け入れ条件 2・I5・I6: 起動し直したスクリプトの子に、NDF が設定した共有の環境が渡らない。"""
    got = run_reexec_child(tmp_path)
    assert got == {"SELF": "None", "SAVED": "None", "CHILD": "None"}


def test_a_reexeced_script_gives_back_the_users_own_value(tmp_path):
    """I7: 利用者が自分で設定した値は、起動し直した後に戻って子へ渡る。"""
    mine = str(tmp_path / "my-project-venv")
    got = run_reexec_child(tmp_path, UV_PROJECT_ENVIRONMENT=mine)
    assert got == {"SELF": repr(mine), "SAVED": "None", "CHILD": repr(mine)}


def test_a_shared_venv_inherited_from_an_old_parent_is_dropped(tmp_path):
    """I6・決定 3: 古い版の親から共有の環境を指す値を継いでも、起動し直した後の子へ渡さない。"""
    got = run_reexec_child(tmp_path, UV_PROJECT_ENVIRONMENT=str(tmp_path / "venv"))
    assert got["CHILD"] == "None"


def test_an_importable_require_drops_an_inherited_shared_venv(monkeypatch, tmp_path):
    """I6: 起動し直さずに戻る回も、継いだ共有の環境（既定の置き場の下）を外し、利用者の値は残す。"""
    monkeypatch.setitem(deps.GROUPS, "t", ["json"])
    monkeypatch.delenv("NDF_DEPS_VENV", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / ".cache" / "ndf" / "venv" / "9.9.9"))
    deps.require("t")
    assert "UV_PROJECT_ENVIRONMENT" not in os.environ
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "mine"))
    deps.require("t")
    assert os.environ["UV_PROJECT_ENVIRONMENT"] == str(tmp_path / "mine")


def minor_of(python: str) -> tuple[int, int] | None:
    """`python` のマイナー版。起動できなければ None。"""
    p = subprocess.run([python, "-c", "import sys; print(*sys.version_info[:2])"], capture_output=True, text=True)
    return tuple(int(x) for x in p.stdout.split()) if p.returncode == 0 else None  # type: ignore[return-value]


def test_a_child_uv_run_of_another_project_leaves_the_shared_venv_alone(tmp_path):
    """受け入れ条件 6: 本物の uv で、起動し直したスクリプトの子が打つ別のプロジェクトの `uv run` が共有の環境を作り直さない。

    ネットワークを使わないよう、Python は手元の 2 つを使い、psutil は uv のキャッシュから入れる。そろわなければ skip する。"""
    uv = shutil.which("uv")
    first, second = getattr(sys, "_base_executable", sys.executable), "/usr/bin/python3"
    if uv is None or not Path(second).exists():
        pytest.skip("uv か /usr/bin/python3 が無い")
    a, b = minor_of(first), minor_of(second)
    if a is None or b is None or a == b:
        pytest.skip(f"マイナー版の違う Python が 2 つそろわない（{a} と {b}）")
    drop = ("NDF_DEPS_REEXEC", "NDF_DEPS_VENV", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "PYTHONPATH")
    base = {k: v for k, v in os.environ.items() if k not in drop}
    base.update(UV_PYTHON_DOWNLOADS="never", UV_OFFLINE="1")
    shared, bare = tmp_path / "shared", tmp_path / "bare"
    for venv, python in ((shared, first), (bare, first)):
        if (
            p := subprocess.run([uv, "venv", "--quiet", "--python", python, str(venv)], capture_output=True, text=True, env=base)
        ).returncode:
            pytest.skip(f"環境を作れない: {p.stderr.strip()}")
    sync = [uv, "sync", "--quiet", "--frozen", "--offline", "--project", str(PLUGIN), "--extra", "procs"]
    p = subprocess.run(sync, capture_output=True, text=True, env={**base, "UV_PROJECT_ENVIRONMENT": str(shared)})
    if p.returncode:
        pytest.skip(f"uv のキャッシュから psutil を入れられない: {p.stderr.strip()}")
    other = tmp_path / "other"
    other.mkdir()
    (other / "pyproject.toml").write_text(
        f'[project]\nname = "other"\nversion = "0"\nrequires-python = ">={b[0]}.{b[1]},<{b[0]}.{b[1] + 1}"\n'
    )
    script = tmp_path / "entry.py"
    script.write_text(
        f"import subprocess, sys\nsys.path.insert(0, {str(LIB)!r})\nimport deps\ndeps.require('procs')\n"
        f"c = [{uv!r}, 'run', '--quiet', '--project', {str(other)!r}, '--python', {second!r}, 'python', '-c', 'pass']\n"
        "sys.exit(subprocess.run(c, cwd=sys.argv[1]).returncode)\n"
    )
    cfg = shared / "pyvenv.cfg"
    before = (cfg.read_text(), cfg.stat().st_mtime_ns)
    # psutil の無い環境の python で打ち、require に共有の環境へ起動し直させる
    p = subprocess.run(
        [str(bare / "bin" / "python"), str(script), str(other)], capture_output=True, text=True, env={**base, "NDF_DEPS_VENV": str(shared)}
    )
    assert p.returncode == 0, p.stderr
    assert (cfg.read_text(), cfg.stat().st_mtime_ns) == before
    assert (other / ".venv").is_dir()  # 別のプロジェクトは自分の環境で動く
    assert subprocess.run([str(shared / "bin" / "python"), "-c", "import psutil"], capture_output=True).returncode == 0


def stop_entry(tmp_path: Path, lib: Path) -> subprocess.CompletedProcess:
    """依存の確認だけを読み込んだ（核と結果を出す層より先の）エントリポイントを、起動し直した後の欠けで止める。"""
    script = tmp_path / "entry.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(lib)!r})\nimport deps\n"
        "assert 'run_core' not in sys.modules and 'step_result' not in sys.modules\n"
        "deps.GROUPS['t'] = ['ndf_no_such_module']\ndeps.require('t')\nprint('after')\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "UV_PROJECT_ENVIRONMENT"}
    env.update(NDF_DEPS_REEXEC=str(script.resolve()), NDF_RESULT_FILE=str(tmp_path / "result.json"))
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, cwd=tmp_path)


def test_deps_read_before_the_core_stops_with_the_table_code_and_a_deps_gap(tmp_path):
    """#2988 の I2〜I4: 依存の確認が核より先に読み込まれても、終了コードの表の 69 と stopped・deps の欠けで止まる。"""
    import exit_codes

    p = stop_entry(tmp_path, LIB)
    assert p.returncode == exit_codes.EXIT_DEPS_MISSING == 69, p.stderr
    assert "Traceback" not in p.stderr and "after" not in p.stdout
    r = json.loads((tmp_path / "result.json").read_text())
    assert r["status"] == "stopped" and [g["kind"] for g in r["gaps"]] == ["deps"] and r["gaps"][0]["name"] == "t"


def test_deps_without_the_core_stops_with_69_and_no_result(tmp_path):
    """#2988 の I5: 核を写さない置き場（ラッパーのバージョンディレクトリ）では、結果を出さずに 69 で終える。"""
    lib = tmp_path / "lib"
    lib.mkdir()
    for f in ("deps.py", "exit_codes.py"):
        shutil.copy(LIB / f, lib / f)
    p = stop_entry(tmp_path, lib)
    assert p.returncode == 69 and "Traceback" not in p.stderr, p.stderr
    assert p.stdout == "" and not (tmp_path / "result.json").exists()
