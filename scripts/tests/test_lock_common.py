"""排他の実装を 1 箇所へ寄せた結果を固定する（#293）。

**この束が受け持つのは、寄せ方そのものである。** 関門が持ち主を 1 つに決めることと、
陳腐化の判定は `worktree/tests/test_registry.py` と `worktree/tests/test_testenv.py` に
あり、そちらは書き換えない。ここでは共通ファイルが 1 つであること・4 つの配布先
ランタイムの配置で読み込めること・読み込めないときに書き込みへ進まないことを見る。

`scripts/tests/` へ置くのは、検査の対象が 2 つの Skill と配布の経路にまたがるためである。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NDF = ROOT / "plugins" / "ndf"
LOCK_LIB = NDF / "scripts" / "lib" / "lock-common.sh"
WT_LIB = NDF / "scripts" / "lib" / "worktree-common.sh"
WF_SKILL = NDF / "skills" / "development-workflow"
WF_LIB = WF_SKILL / "scripts" / "lib" / "workflow-common.sh"


def run_lib(lib: Path, snippet: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """`lib` を読み込んだうえで `snippet` を bash で実行する。"""
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n. "{lib}"\n{snippet}\n'],
        env=env, capture_output=True, text=True,
    )


def _shell_files() -> list[Path]:
    """配布物の実体にあるシェルスクリプトを返す。

    `os.walk` は symlink のディレクトリへ降りない。`dev.agy/scripts` は実体への
    symlink であるため、同じファイルを 2 度数えずに済む。
    """
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(NDF):
        found.extend(Path(dirpath) / n for n in filenames if n.endswith(".sh"))
    return found


# --- A1: 取得の手順が 1 箇所だけにある --------------------------------------

def test_the_two_stage_gate_lives_in_one_file() -> None:
    """A1: 1 段目の `mkdir` と 2 段目の `set -C` を両方持つファイルは 1 つだけである。"""
    holders = sorted(
        p.relative_to(ROOT).as_posix()
        for p in _shell_files()
        if 'mkdir "$dir"' in (text := p.read_text(encoding="utf-8")) and "set -C" in text
    )

    assert holders == ["plugins/ndf/scripts/lib/lock-common.sh"], holders


def test_no_other_file_holds_the_gate_of_the_second_stage() -> None:
    """A1: 握りの印を作る関門も、共通ファイルの外に写しを持たない。"""
    holders = sorted(
        p.relative_to(ROOT).as_posix()
        for p in _shell_files()
        if "/held" in p.read_text(encoding="utf-8")
    )

    assert holders == ["plugins/ndf/scripts/lib/lock-common.sh"], holders


# --- A2 / A3: 既存の名前で呼べる --------------------------------------------

@pytest.mark.parametrize(
    ("lib", "names"),
    [
        pytest.param(WT_LIB, ("wt_lock_acquire", "wt_lock_release", "wt_lock_is_held"), id="worktree"),
        pytest.param(WF_LIB, ("wf_lock_acquire", "wf_lock_release"), id="workflow"),
    ],
)
def test_the_existing_names_stay_callable(lib: Path, names: tuple[str, ...]) -> None:
    """A2 / A3: 読み込む側は、寄せる前と同じ名前で呼べる。"""
    got = run_lib(lib, "\n".join(f'declare -F {n} >/dev/null && echo have={n}' for n in names))

    for name in names:
        assert f"have={name}" in got.stdout, got


@pytest.mark.parametrize(
    ("lib", "acquire", "release"),
    [
        pytest.param(WT_LIB, "wt_lock_acquire", "wt_lock_release", id="worktree"),
        pytest.param(WF_LIB, "wf_lock_acquire", "wf_lock_release", id="workflow"),
    ],
)
def test_the_existing_names_take_and_release_the_lock(
    tmp_path: Path, lib: Path, acquire: str, release: str
) -> None:
    """A2 / A3: 名前だけでなく、取得と解放の結果も寄せる前と変わらない。"""
    lock = tmp_path / "a.lock"
    got = run_lib(
        lib,
        f'{acquire} "{lock}" 1; echo first=$?\n'
        f'{acquire} "{lock}" 1; echo second=$?\n'
        f'{release} "{lock}"; {acquire} "{lock}" 1; echo again=$?',
    )

    assert "first=0" in got.stdout, got
    assert "second=1" in got.stdout, got
    assert "again=0" in got.stdout, got


# --- A4: 待ちの上限の上書きは工程の控えの側だけに効く -----------------------

def _held_lock(path: Path) -> Path:
    """生きている持ち主がいるロックを作る。陳腐化と読まれないため、上限まで待つ。"""
    path.mkdir()
    (path / "held").touch()
    (path / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (path / "token").write_text("held\n", encoding="utf-8")
    return path


def test_the_timeout_override_reaches_only_the_stage_state(tmp_path: Path) -> None:
    """A4: `NDF_STAGE_LOCK_TIMEOUT` は控えの側だけを縮め、台帳の既定は 5 秒のまま。"""
    env = os.environ.copy()
    env["NDF_STAGE_LOCK_TIMEOUT"] = "1"

    short = _held_lock(tmp_path / "short.lock")
    started = time.monotonic()
    got = run_lib(WF_LIB, f'wf_lock_acquire "{short}"; echo rc=$?', env=env)
    waited_by_the_stage_state = time.monotonic() - started

    assert "rc=1" in got.stdout, got
    assert waited_by_the_stage_state < 3, waited_by_the_stage_state

    long = _held_lock(tmp_path / "long.lock")
    started = time.monotonic()
    got = run_lib(WT_LIB, f'wt_lock_acquire "{long}"; echo rc=$?', env=env)
    waited_by_the_registry = time.monotonic() - started

    assert "rc=1" in got.stdout, got
    assert waited_by_the_registry >= 4, waited_by_the_registry


# --- A5: 4 つの配布先ランタイムの配置で読み込める ---------------------------

def _flat_layout(base: Path) -> Path:
    """プラグインルート直下に `scripts/` と `skills/` が並ぶ配置。

    Claude Code / Codex / agy の 3 つが同じ形になる（#293 の実測）。
    """
    root = base / "plugin-root"
    (root / "skills").mkdir(parents=True)
    shutil.copytree(NDF / "scripts", root / "scripts", symlinks=True)
    shutil.copytree(WF_SKILL, root / "skills" / "development-workflow", symlinks=True)
    return root / "skills" / "development-workflow"


def _kiro_layout(base: Path) -> Path:
    """`.kiro/skills/<Skill 名>` を、配布物の Skill への symlink にする配置。

    Kiro CLI だけが Skill を複製する。`..` はカーネルが物理的に解決するため、
    symlink を経由した経路からもプラグインルートへ戻れる。
    """
    skill = _flat_layout(base)
    project = base / "project" / ".kiro" / "skills"
    project.mkdir(parents=True)
    (project / "development-workflow").symlink_to(skill)
    return project / "development-workflow"


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param(_flat_layout, id="claude-codex-agy"),
        pytest.param(_kiro_layout, id="kiro"),
    ],
)
def test_the_common_file_is_reached_from_every_layout(tmp_path: Path, layout) -> None:
    """A5: 配布した後の配置から、相対で共通ファイルへ届く。

    `ndf_lock_is_held` は共通ファイルだけが定義する。読み込めなかったときに置く
    定義には含まれないため、**実際に届いたことの見分けになる。**
    """
    skill = layout(tmp_path)
    lock = tmp_path / "layout.lock"

    got = run_lib(
        skill / "scripts" / "lib" / "workflow-common.sh",
        'declare -F ndf_lock_is_held >/dev/null && echo reached=yes\n'
        f'wf_lock_acquire "{lock}" 1; echo rc=$?',
    )

    assert "reached=yes" in got.stdout, got
    assert "rc=0" in got.stdout, got
    assert (lock / "held").is_file(), "握りの印が作られていない"


def test_the_libraries_do_not_locate_the_common_file_with_cd(tmp_path: Path) -> None:
    """決定 6: `cd` で戻ってから `pwd` を取る形を使わない。

    `cd` は `..` を論理パスに対して字句で畳むため、Kiro CLI の配置では symlink の
    手前へ戻り、プラグインルートを外す。**この形は配置を変えずに壊れる**ため、
    テストで固定する。
    """
    for lib in (WT_LIB, WF_LIB):
        line = next(
            l for l in lib.read_text(encoding="utf-8").splitlines()
            if "lock-common.sh" in l and '. "' in l
        )
        assert '$(dirname "${BASH_SOURCE[0]}")' in line, (lib, line)
        assert "pwd" not in line, (lib, line)


# --- A6: 共通ファイルを読み込めないとき -------------------------------------

def test_a_missing_common_file_stops_the_write_but_not_the_step(tmp_path: Path) -> None:
    """A6: 共通ファイルが無くても工程は続き、控えへは書かない。

    **排他を取れないことと、排他なしで書くことは別である。** 取得できないときの
    分岐は寄せる前から控えの側が持っており、そこへ合流させる。
    """
    skill = _flat_layout(tmp_path)
    (skill.parents[1] / "scripts" / "lib" / "lock-common.sh").unlink()
    state = tmp_path / "state"
    state.mkdir()
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(state)

    got = run_lib(
        skill / "scripts" / "lib" / "workflow-common.sh",
        'echo sourced=ok\n'
        'wf_record "devbasex/ai-plugins" 293 stage "構造改善"; echo rc=$?',
        env=env,
    )

    assert "sourced=ok" in got.stdout, got
    assert "rc=0" in got.stdout, got
    assert list((state / "stages").glob("*.json")) == [], "排他を取れないまま控えへ書いた"


# --- A7: 同時に走らせても持ち主は 1 つ --------------------------------------

LOCK_WORKER = """#!/usr/bin/env bash
# $1 共通ファイル / $2 置き場所
set -uo pipefail
. "$1"
while [ ! -e "$2/go" ]; do :; done
if ndf_lock_acquire "$2/lock" 6; then
  : >"$2/in.$$"
  if [ "$(ls "$2"/in.* 2>/dev/null | wc -l)" -gt 1 ]; then : >"$2/over.$$"; fi
  sleep 0.02
  rm -f "$2/in.$$"
  ndf_lock_release "$2/lock"
else
  : >"$2/miss.$$"
fi
exit 0
"""


def test_six_at_once_leave_one_owner(tmp_path: Path) -> None:
    """A7: 6 つが同時に取りに行っても、臨界区間は重ならず取りこぼしも出ない。

    `mkdir` コマンドは同じ名前の作成に複数を通す。1 回の実行では取りこぼしがあっても
    通ることがあるため、繰り返したうえで件数で見る。
    """
    worker = tmp_path / "worker.sh"
    worker.write_text(LOCK_WORKER, encoding="utf-8")
    base = tmp_path / "race"
    base.mkdir()
    overlap = miss = 0

    for _ in range(7):
        for stray in base.iterdir():
            shutil.rmtree(stray, ignore_errors=True) if stray.is_dir() else stray.unlink()
        procs = [
            subprocess.Popen(
                ["bash", str(worker), str(LOCK_LIB), str(base)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(6)
        ]
        (base / "go").touch()
        for proc in procs:
            proc.wait()
        overlap += 1 if any(base.glob("over.*")) else 0
        miss += len(list(base.glob("miss.*")))

    assert overlap == 0, f"臨界区間が重なった試行 {overlap} 件"
    assert miss == 0, f"上限に達して取れなかった回数 {miss} 回"


# --- A8: 呼び出し側のシェルの状態を変えない ---------------------------------

def test_the_common_file_leaves_no_noclobber_on_the_caller(tmp_path: Path) -> None:
    """A8: 取得の成否のどちらでも、呼び出し側の `$-` に `C` を残さない。

    `set -C` を裸で書くと実行の後も残り、呼び出し元の後続の上書きの向き先が変わる。
    部分シェルの中だけで張ることを、共通ファイルの契約として固定する。
    """
    lock = tmp_path / "flag.lock"
    got = run_lib(
        LOCK_LIB,
        f'ndf_lock_acquire "{lock}" 1; echo taken=$-\n'
        f'ndf_lock_acquire "{lock}" 1; echo missed=$-',
    )

    reported = [l for l in got.stdout.split() if "=" in l]
    assert len(reported) == 2, got
    for line in reported:
        assert "C" not in line.split("=", 1)[1], f"{line} に noclobber が残った"
