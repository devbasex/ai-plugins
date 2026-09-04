"""共通層の置き場所と、そこへ届く指し方（#285）。

収束ループの共通層は `cross-review` の下にあり、`cross-refactoring` が Skill の境界を
またいで読んでいた。**配る Skill を絞る配布先（agy）では、基準に無い Skill が配布した
先から消える。** 相手が消えれば相対の参照は解決できない。プラグインルート直下の
`scripts/` は配布の基準の対象ではないため、4 ランタイムすべてへ届く。

検査の対象が 2 つの Skill と配布の経路にまたがるため、リポジトリの根の `scripts/tests/`
へ置く（`test_lock_common.py` と同じ理由）。

**指し方はシェルと Python で逆になる。** どちらも「symlink を解いた側で階層を数える」
ことを求めているが、シェルの `cd` は `..` を字句で畳んで symlink の手前へ戻るため、
物理的な解決を避ける形が正しい。Python の `parents[]` は逆に `.resolve()` を求める。
届くことだけでは契約が守られているか見分けられないため、**避ける側・求める側の両方を
外した場合も測る**。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NDF = ROOT / "plugins" / "ndf"
LIB = NDF / "scripts" / "lib"
SKILLS = NDF / "skills"

# 収束ループの共通層として移した実体。プラグイン全体の 3 つ（`worktree-common.sh` /
# `projects-common.sh` / `lock-common.sh`）は移動の対象ではないため数えない。
MOVED = (
    "monitor.py",
    "metrics.py",
    "models.py",
    "launch-cli.sh",
    "assignment.py",
    "statefile.py",
    "_tmpdir.sh",
    "README.md",
)

# 共通層を指す 3 本の起動スクリプト。
LAUNCHERS = (
    SKILLS / "cross-review" / "scripts" / "wait-review.sh",
    SKILLS / "cross-review" / "scripts" / "launch-reviewer.sh",
    SKILLS / "cross-refactoring" / "scripts" / "launch-cli.sh",
)


# ---------- A1: 置き場所 ----------

@pytest.mark.parametrize("name", MOVED)
def test_the_shared_layer_sits_at_the_plugin_root(name: str) -> None:
    assert (LIB / name).is_file(), f"{name} が {LIB} にありません"


def test_the_old_place_under_cross_review_is_gone() -> None:
    """シムを残さない。残すと #285 の指す経路がそのまま使える状態が続く。"""
    assert not (SKILLS / "cross-review" / "scripts" / "lib").exists()


# ---------- A3 / A4: 4 ランタイムの配置で届くこと ----------

def _build_layouts(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """プラグインルート直下の配置と、Kiro CLI の symlink の配置を組み立てる。

    実測した 4 ランタイムのうち Claude Code / Codex / agy は同じ形（プラグインルート
    直下に `scripts/` と `skills/` が並ぶ）で、Kiro CLI だけが `.kiro/skills/<名前>` を
    symlink にする。**Kiro CLI 側には `scripts/lib` の囮を置く。** `cd` で登ると字句で
    畳んだ先が存在するため、そちらが選ばれる。
    """
    plugin = tmp_path / "plugin"
    (plugin / "scripts" / "lib").mkdir(parents=True)
    (plugin / "scripts" / "lib" / "probe.sh").write_text(
        'probe_value() { echo shared; }\n', encoding="utf-8"
    )
    scripts = plugin / "skills" / "probe" / "scripts"
    scripts.mkdir(parents=True)

    (scripts / "string-form.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\n'
        '. "$DIR/../../../scripts/lib/probe.sh"\n'
        'probe_value\n',
        encoding="utf-8",
    )
    (scripts / "cd-form.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\n'
        'L=$(cd -- "$DIR/../../../scripts/lib" && pwd)\n'
        '. "$L/probe.sh"\n'
        'probe_value\n',
        encoding="utf-8",
    )
    (scripts / "probe.py").write_text(
        "import json, pathlib\n"
        "here = pathlib.Path(__file__)\n"
        "print(json.dumps({\n"
        '    "resolved": str(here.resolve().parents[3] / "scripts" / "lib"),\n'
        '    "plain": str(here.parents[3] / "scripts" / "lib"),\n'
        "}))\n",
        encoding="utf-8",
    )

    kiro = tmp_path / "project" / ".kiro"
    (kiro / "skills").mkdir(parents=True)
    (kiro / "skills" / "probe").symlink_to(plugin / "skills" / "probe")
    # 囮。Kiro CLI が作るのは agents / prompts / skills / steering の 4 つで
    # `.kiro/scripts` は作られないが、作られた場合に `cd` が選ぶ先である。
    (kiro / "scripts" / "lib").mkdir(parents=True)
    (kiro / "scripts" / "lib" / "probe.sh").write_text(
        'probe_value() { echo decoy; }\n', encoding="utf-8"
    )
    return plugin, kiro


def _bash(script: pathlib.Path) -> str:
    return subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60, check=True
    ).stdout.strip()


def test_the_shell_form_reaches_the_shared_layer_in_both_layouts(tmp_path) -> None:
    plugin, kiro = _build_layouts(tmp_path)
    direct = plugin / "skills" / "probe" / "scripts" / "string-form.sh"
    through_symlink = kiro / "skills" / "probe" / "scripts" / "string-form.sh"
    assert _bash(direct) == "shared"
    assert _bash(through_symlink) == "shared"


def test_the_shell_form_that_cds_up_misses_the_shared_layer(tmp_path) -> None:
    """`cd` は `..` を字句で畳むため、symlink の手前の囮を選ぶ。"""
    plugin, kiro = _build_layouts(tmp_path)
    assert _bash(plugin / "skills" / "probe" / "scripts" / "cd-form.sh") == "shared"
    assert _bash(kiro / "skills" / "probe" / "scripts" / "cd-form.sh") == "decoy"


def test_the_python_form_reaches_the_shared_layer_in_both_layouts(tmp_path) -> None:
    plugin, kiro = _build_layouts(tmp_path)
    shared = plugin / "scripts" / "lib"
    for entry in (
        plugin / "skills" / "probe" / "scripts" / "probe.py",
        kiro / "skills" / "probe" / "scripts" / "probe.py",
    ):
        out = json.loads(subprocess.run(
            [sys.executable, str(entry)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout)
        assert pathlib.Path(out["resolved"]) == shared.resolve()


def test_the_python_form_without_resolve_misses_the_shared_layer(tmp_path) -> None:
    """`.resolve()` を外すと `.kiro` で止まる。"""
    plugin, kiro = _build_layouts(tmp_path)
    out = json.loads(subprocess.run(
        [sys.executable, str(kiro / "skills" / "probe" / "scripts" / "probe.py")],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout)
    assert pathlib.Path(out["plain"]) == kiro / "scripts" / "lib"
    assert pathlib.Path(out["plain"]) != (plugin / "scripts" / "lib").resolve()


# ---------- 実体が契約どおりに指していること ----------

@pytest.mark.parametrize(
    "path",
    [
        SKILLS / "cross-review" / "scripts" / "_tmpdir.sh",
        SKILLS / "cross-review" / "scripts" / "launch-reviewer.sh",
        SKILLS / "cross-refactoring" / "scripts" / "launch-cli.sh",
    ],
    ids=lambda p: p.name,
)
def test_the_shell_callers_do_not_cd_up_to_the_shared_layer(path: pathlib.Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert "../../../scripts/lib" in body
    assert 'cd -- "$SCRIPT_DIR/../../../scripts/lib"' not in body
    assert 'cd -- "$DIR/../../../scripts/lib"' not in body


@pytest.mark.parametrize(
    "path",
    [
        SKILLS / "cross-review" / "scripts" / "monitor.py",
        SKILLS / "cross-refactoring" / "scripts" / "refactor.py",
        SKILLS / "cross-refactoring" / "tests" / "conftest.py",
    ],
    ids=lambda p: f"{p.parent.parent.name}-{p.name}",
)
def test_the_python_callers_resolve_before_counting_parents(path: pathlib.Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert '"scripts" / "lib"' in body
    # symlink を解いてから階層を数える。解かないと `.kiro` で止まる。
    assert ".resolve()" in body
    # 境界をまたぐ指し方が残っていないこと。
    assert '"cross-review" / "scripts"' not in body


# ---------- A5 / A6: 2 つのシム ----------

def test_the_monitor_shim_exposes_the_implementation_namespace() -> None:
    shim = SKILLS / "cross-review" / "scripts" / "monitor.py"
    name = "shared_lib_layout_monitor_shim"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, shim)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
        assert mod._IMPL == LIB / "monitor.py"
        assert callable(mod.monitor_agent)
        assert mod.monitor_agent.__globals__ is mod.__dict__
        assert mod.monitor_agent.__globals__["_pid_alive"] is mod._pid_alive
    finally:
        sys.modules.pop(name, None)


def test_the_tmpdir_shim_still_defines_tmpdir(tmp_path) -> None:
    shim = SKILLS / "cross-review" / "scripts" / "_tmpdir.sh"
    out = subprocess.run(
        ["bash", "-c", f'. "{shim}"; type -t tmpdir; tmpdir'],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path / "tmp")},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["function", str(tmp_path / "tmp")]


# ---------- A7: 3 本の起動スクリプト ----------

def test_the_wait_wrapper_starts_the_moved_monitor() -> None:
    """`wait-review.sh` → シム → 共通層の実体、までが 1 回の起動で通ること。"""
    out = subprocess.run(
        ["bash", str(SKILLS / "cross-review" / "scripts" / "wait-review.sh"), "--help"],
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "--stem-template" in out.stdout


def test_the_agy_launcher_gets_past_the_shared_layer(tmp_path) -> None:
    """互換のために残した名前から呼んでも、委譲先が共通層まで届くこと。"""
    out = subprocess.run(
        ["bash", str(SKILLS / "cross-review" / "scripts" / "launch-agy.sh"), "1", "1"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
    )
    # 状態ファイルが無いことで止まる。共通層の読み込みでは止まらない。
    assert "state.json not found" in out.stdout + out.stderr
    assert "scripts/lib" not in out.stderr


def test_the_cross_refactoring_launcher_gets_past_the_shared_layer(tmp_path) -> None:
    out = subprocess.run(
        ["bash", str(SKILLS / "cross-refactoring" / "scripts" / "launch-cli.sh"),
         "claude", "propose", "1"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(tmp_path)},
    )
    assert "状態ファイルがありません" in out.stdout + out.stderr
    assert "scripts/lib" not in out.stderr


@pytest.mark.parametrize("path", LAUNCHERS, ids=lambda p: p.name)
def test_the_paths_written_in_the_launchers_resolve(path: pathlib.Path) -> None:
    """書かれた相対パスが、いまの木で実在すること。"""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "../../../scripts/lib" not in line:
            continue
        tail = line.split("../../../scripts/lib", 1)[1]
        name = tail.split('"')[0].split()[0].lstrip("/") if tail.strip() else ""
        target = (path.parent / "../../../scripts/lib" / name) if name else \
            (path.parent / "../../../scripts/lib")
        assert target.exists(), f"{path.name}: {line.strip()}"


# ---------- A10: 監視の引数と終了コード ----------

def _monitor(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIB / "monitor.py"), *args],
        capture_output=True, text=True, timeout=180,
    )


def test_the_monitor_still_takes_a_replaceable_naming_scheme(tmp_path) -> None:
    """収束ループの外の名前（`deploy`）でも、引数を差し替えれば監視できる。"""
    job = tmp_path / "deploy-job.sh"
    job.write_text("#!/usr/bin/env bash\nsleep 1\n", encoding="utf-8")
    proc = subprocess.Popen(["bash", str(job)])
    (tmp_path / "deploy-job99.pid").write_text(f"{proc.pid}\n", encoding="utf-8")
    try:
        out = _monitor(
            "99", "--agents", "deploy", "--tmp-dir", str(tmp_path),
            "--stem-template", "{agent}-job{id}", "--no-require-result",
            "--timeout", "60", "--poll", "2",
        )
    finally:
        proc.wait(timeout=30)
    assert out.returncode == 0, out.stdout + out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1])["status"] == "OK"


def test_the_monitor_still_reports_a_bad_pidfile_as_six(tmp_path) -> None:
    (tmp_path / "deploy-job99.pid").write_text("not-a-pid\n", encoding="utf-8")
    out = _monitor(
        "99", "--agents", "deploy", "--tmp-dir", str(tmp_path),
        "--stem-template", "{agent}-job{id}", "--no-require-result",
        "--timeout", "60", "--poll", "2",
    )
    assert out.returncode == 6, out.stdout + out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1])["status"] == "PIDFILE_BAD"


def test_the_identifier_still_has_to_be_an_integer() -> None:
    out = _monitor("10.2.0", "--agents", "deploy")
    assert out.returncode != 0
    assert "invalid int value" in out.stderr
