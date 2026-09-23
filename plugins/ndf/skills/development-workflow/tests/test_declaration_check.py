"""起動時に作業ツリーの宣言を確かめる手順 0 を固定する（#527）。

**`development-workflow` を起動したことを、作業ツリー運用を使う意思表示として扱う。**
宣言の無いリポジトリで何もしない `worktree` の性質は保ち、確かめるのはこの本文を
読み込んだ会話だけである。

**本文は終了コードで分岐するだけで、宣言ファイルを自分で調べない。** 状態を分ける
基準は `worktree-common.sh` の `wt_declaration_state` の 1 か所にある。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from workflow_helpers import SKILL_DIR, init_repo

SKILL = SKILL_DIR / "SKILL.md"
PLUGIN_ROOT = SKILL_DIR.parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
COMMON_LIB = SCRIPTS_DIR / "lib" / "worktree-common.sh"

JUDGE_HEADING = "## 判定の手順"
STEP0_HEADING = "### 0. "
STEP1_HEADING = "### 1. 変更対象を確認する"


def skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def step0() -> str:
    """「判定の手順」の手順 0 の節（手順 1 の見出しの手前まで）を返す。"""
    body = skill()
    judge = body.index(JUDGE_HEADING)
    head = body.index(STEP0_HEADING, judge)
    return body[head: body.index(STEP1_HEADING, head)]


def step0_bash() -> str:
    block = re.search(r"^```bash\n(.*?)^```$", step0(), re.S | re.M)
    assert block is not None, "手順 0 に bash のブロックが無い"
    return block.group(1)


def run_step0(cwd: Path, scripts: str | None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LC_ALL"] = "C.UTF-8"
    env.pop("SCRIPTS", None)
    if scripts is not None:
        env["SCRIPTS"] = scripts
    return subprocess.run(
        ["bash", "-c", f"set -uo pipefail\n{step0_bash()}"],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )


# --- 条件 10: 判定の実体は 1 か所 --------------------------------------------------


def test_unreadable_is_classified_only_in_the_common_lib() -> None:
    hits = sorted(
        p.relative_to(PLUGIN_ROOT).as_posix()
        for p in SCRIPTS_DIR.rglob("*.sh")
        if re.search(r"printf '?unreadable", p.read_text(encoding="utf-8"))
    )
    assert hits == [COMMON_LIB.relative_to(PLUGIN_ROOT).as_posix()], hits


# --- 条件 12: 判定できないときは止めない -------------------------------------------


def test_step0_without_scripts_reports_undecidable(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "main")
    got = run_step0(repo, scripts=None)
    assert got.returncode == 0, got.stderr
    assert "判定できない" in got.stdout, got.stdout


def test_step0_reports_the_exit_code_of_check(tmp_path: Path) -> None:
    """本文の bash をそのまま実行し、宣言の無いリポジトリで 2 を読めること。"""
    repo = init_repo(tmp_path / "main")
    got = run_step0(repo, scripts=str(SCRIPTS_DIR))
    assert got.returncode == 0, got.stderr
    assert got.stdout.splitlines()[-1] == "exit=2", got.stdout
    assert not (repo / ".ndf").exists(), "確認は宣言を作らない"
