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
WORKTREE_SKILL = PLUGIN_ROOT / "skills" / "worktree" / "SKILL.md"
COMMON_LIB = SCRIPTS_DIR / "lib" / "worktree-common.sh"
WORKFLOW_GUARD = SKILL_DIR / "scripts" / "workflow-guard.sh"

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


def table_row(code: str) -> str:
    """手順 0 の表から、1 列目が終了コード `code` で始まる行を返す。"""
    for line in step0().splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 2 and cells[1].startswith(code):
            return line
    raise AssertionError(f"手順 0 の表に終了コード {code} の行が無い")


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


# --- 条件 4: 手順 1 より前にあり、2 のときは worktree の手順 0 を通す ---------


def test_step0_comes_before_step1() -> None:
    body = skill()
    judge = body.index(JUDGE_HEADING)
    step0_at = body.index(STEP0_HEADING, judge)
    assert judge < step0_at < body.index(STEP1_HEADING, judge)
    assert "worktree-setup.sh\" check" in step0_bash()


def test_received_values_declare_scripts() -> None:
    """`$SCRIPTS` を使う以上、決め方を「この文書が受け取る値」で示す。"""
    body = skill()
    head = body.index("## この文書が受け取る値")
    assert head < body.index(JUDGE_HEADING)
    section = body[head: body.index(JUDGE_HEADING)]
    assert "`$SCRIPTS`" in section
    assert "references/scripts-lookup.md" in section


def test_missing_declaration_goes_through_worktree_step0() -> None:
    row = table_row("2")
    assert "worktree" in row and "0. 宣言ファイルを用意する" in row
    assert "0 を返してから" in row


def test_the_output_example_carries_the_declaration_line() -> None:
    body = skill()
    output = body[body.index("### 3. 判定結果を出力する"):]
    example = re.search(r"^```text\n(.*?)^```$", output, re.S | re.M)
    assert example is not None
    lines = example.group(1).splitlines()
    assert lines[0].startswith("mode:") and lines[1].startswith("根拠:")
    assert lines[2].startswith("宣言: "), lines[:3]


def test_the_stage_order_line_is_unchanged() -> None:
    """手順 0 は工程ではない。工程の並びへ書き足さない（実装で決めた B）。"""
    body = skill()
    order = next(line for line in body.splitlines() if "要求と受け入れ条件 → モード判定" in line)
    assert "宣言" not in order, order


# --- 条件 9: 拒否しない理由 ------------------------------------------------------


def test_step0_does_not_refuse_and_says_why() -> None:
    section = step0()
    assert "拒否しない" in section
    assert "引き金" in section, "起動の時点に止める引き金が無いこと"
    assert "4 ランタイム" in section
    assert "#565" in section


# --- 条件 10: 判定の実体は 1 か所 --------------------------------------------------


def test_step0_and_workflow_guard_do_not_inspect_the_file() -> None:
    assert "worktree.json" not in step0()
    assert "worktree.json" not in WORKFLOW_GUARD.read_text(encoding="utf-8")


def test_unreadable_is_classified_only_in_the_common_lib() -> None:
    hits = sorted(
        p.relative_to(PLUGIN_ROOT).as_posix()
        for p in SCRIPTS_DIR.rglob("*.sh")
        if re.search(r"printf '?unreadable", p.read_text(encoding="utf-8"))
    )
    assert hits == [COMMON_LIB.relative_to(PLUGIN_ROOT).as_posix()], hits


# --- 条件 11: 読めない宣言では進まない ---------------------------------------------


def test_unreadable_declaration_stops_without_force() -> None:
    row = table_row("3")
    assert "進まない" in row
    assert "--force" not in row.replace("`init --force` を実行せず", "")
    assert "実行せず" in row


# --- 条件 12: 判定できないときは止めない -------------------------------------------


def test_undecidable_does_not_stop_and_is_reported() -> None:
    row = table_row("1")
    assert "止めずに" in row
    assert "宣言: 判定できない" in row
    commands = "\n".join(
        line for line in step0_bash().splitlines() if not line.lstrip().startswith("#")
    )
    assert not re.search(r"(^|[;&|{]\s*|\bthen\s+|\belse\s+)exit\b", commands, re.M), (
        "手順 0 は exit しない"
    )


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


# --- worktree から check へ辿れる --------------------------------------------------


def test_worktree_step0_points_to_check() -> None:
    body = WORKTREE_SKILL.read_text(encoding="utf-8")
    section = body[body.index("## 0. 宣言ファイルを用意する"): body.index("## 1. 現在地を確かめる")]
    assert "worktree-setup.sh check" in section
