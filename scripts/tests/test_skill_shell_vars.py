"""手順書の bash が参照する変数の出所を検査する仕組みを確かめる（#518-1）。

**繰り返しの中で使う値を、繰り返しの外の 1 回だけが返す構造は表に出ない。**
`init` から順に実行すれば定義されるため、手元では再現しない。骨組みを抜粋して
写した経路と、状態ファイルから再開する経路で未定義になる。

検査そのものは `scripts/check-skill-shell-vars.py` にある。実物の Skill は書き換えず、
一時ディレクトリへ最小の木を作ってそこを検査させる（実物を読むのは最後の 1 件だけ）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts/check-skill-shell-vars.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args], capture_output=True, text=True)


def make_skill(root: Path, body: str, emits: str = "") -> Path:
    """最小の Skill の木を作る。`body` が「実行」節の bash になる。"""
    skill = root / "skills" / "sample"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "# sample\n\n## 実行\n\n```bash\n" + body + "\n```\n", encoding="utf-8")
    (skill / "scripts" / "run.py").write_text(
        "import statefile\n\n\n" + (emits or "def cmd_noop(args):\n    pass\n"),
        encoding="utf-8")
    return skill


# ---------- 出所の突き合わせ ----------

def test_a_variable_assigned_in_the_block_is_a_source(tmp_path):
    skill = make_skill(tmp_path, 'NAME=x\necho "$NAME"')
    assert run("--skill-dir", str(skill)).returncode == 0


def test_a_variable_bound_by_for_is_a_source(tmp_path):
    skill = make_skill(tmp_path, 'ITEMS="a b"\nfor a in $ITEMS; do echo "$a"; done')
    assert run("--skill-dir", str(skill)).returncode == 0


def test_an_undefined_variable_is_reported(tmp_path):
    skill = make_skill(tmp_path, 'echo "$NOWHERE"')
    result = run("--skill-dir", str(skill))
    assert result.returncode != 0
    assert "NOWHERE" in result.stdout + result.stderr


def test_a_value_emitted_by_a_subcommand_is_a_source(tmp_path):
    """`eval` する副コマンドが返す値は出所になる。"""
    emits = (
        "def cmd_start_round(args):\n"
        "    statefile.emit(ROUND=1, RUNTIMES='a b')\n"
    )
    skill = make_skill(
        tmp_path,
        'rf_eval start-round "$ID"\nfor a in $RUNTIMES; do echo "$ROUND $a"; done',
        emits=emits,
    )
    # ID は骨組みの外から渡る値として宣言しておく
    result = run("--skill-dir", str(skill), "--external", "ID")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_value_emitted_only_later_is_reported(tmp_path):
    """その行より前のコマンドが返していなければ出所ではない。"""
    emits = (
        "def cmd_start_round(args):\n"
        "    statefile.emit(ROUND=1)\n"
    )
    skill = make_skill(
        tmp_path,
        'for a in $RUNTIMES; do echo "$a"; done\nrf_eval start-round "$ID"',
        emits=emits,
    )
    result = run("--skill-dir", str(skill), "--external", "ID")
    assert result.returncode != 0
    assert "RUNTIMES" in result.stdout + result.stderr


def test_a_value_emitted_through_a_helper_is_a_source(tmp_path):
    """`cmd_init` が `_emit_init` を呼ぶ形も 1 段だけたどる。"""
    emits = (
        "def _emit_init(state):\n"
        "    statefile.emit(WORK='w')\n"
        "\n\n"
        "def cmd_init(args):\n"
        "    _emit_init({})\n"
    )
    skill = make_skill(tmp_path, 'rf_eval init "$ID"\necho "$WORK"', emits=emits)
    result = run("--skill-dir", str(skill), "--external", "ID")
    assert result.returncode == 0, result.stdout + result.stderr


def test_shell_builtins_are_not_reported(tmp_path):
    skill = make_skill(tmp_path, 'echo "$HOME/$PWD" >&2\necho "$?" "$1" "$@"')
    assert run("--skill-dir", str(skill)).returncode == 0


def test_a_default_value_makes_the_reference_safe(tmp_path):
    """`${X:-既定}` は未定義でも読める。"""
    skill = make_skill(tmp_path, 'echo "${MAYBE:-fallback}"')
    assert run("--skill-dir", str(skill)).returncode == 0


# ---------- 実物 ----------

def test_the_real_skeleton_passes():
    """現行の `cross-refactoring` の骨組みが通ること。"""
    skill = REPO_ROOT / "plugins/ndf/skills/cross-refactoring"
    result = run("--skill-dir", str(skill), "--external", "PR,SCOPE,BASELINE,HOST,"
                 "MAX_TEST,MAX_OUTER,MAX_FIX,MAX_ITEMS,CI_CHECK,WORKFLOW_STEP,"
                 "MODEL_ARGS,SYNC_COMMAND,PLAN_FILE")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_real_skeleton_gets_the_pool_from_start_round():
    """母集合の出所が `start-round` にあること（#518-1 の直しそのもの）。"""
    skill = REPO_ROOT / "plugins/ndf/skills/cross-refactoring"
    result = run("--skill-dir", str(skill), "--show-sources", "RUNTIMES")
    assert "start-round" in result.stdout, result.stdout + result.stderr
