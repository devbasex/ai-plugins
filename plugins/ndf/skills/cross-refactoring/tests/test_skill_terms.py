"""`SKILL.md` の用語表と引数の表が 4 つのラウンドを並べること（A1 / A6）。

**階層はすべて「ラウンド」で表す**（#436）。4 つは同じ形を持つため読み手が覚える
形は 1 つで済むが、**何の単位か・どの上限が掛かるか**は 4 つとも違う。書いていないと、
上限の名前からどのラウンドが切られるのかを読み解けない。
"""
from __future__ import annotations

import pathlib

import pytest

SKILL = pathlib.Path(__file__).resolve().parent.parent / "SKILL.md"

ROUNDS = ("テスト整備ラウンド", "提案ラウンド", "適用ラウンド", "修正ラウンド")
CAPS = ("--max-test-rounds", "--max-outer-rounds", "--max-fix-rounds",
        "--max-items-per-round")


@pytest.fixture(scope="module")
def skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def _terms_table(text: str) -> list[str]:
    """用語表の行だけを取り出す。"""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("## この Skill で使う語"))
    rows = []
    for line in lines[start:]:
        if line.startswith("## ") and rows:
            break
        if line.startswith("| "):
            rows.append(line)
    return rows


def test_the_four_rounds_are_listed_in_order(skill):
    rows = "\n".join(_terms_table(skill))
    positions = [rows.find(name) for name in ROUNDS]
    assert all(p >= 0 for p in positions), f"用語表に無いラウンドがある: {positions}"
    assert positions == sorted(positions), "並びは実行の順にする"


def test_each_round_states_its_unit_and_cap(skill):
    """それぞれ何の単位かと、上限を決めるものは何かを書く。"""
    rows = _terms_table(skill)
    header = rows[0]
    assert "何の単位か" in header and "上限" in header
    for name in ROUNDS:
        row = next(r for r in rows if name in r)
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert all(cells), f"{name} の行に空の欄がある: {row}"


def test_every_cap_appears_in_the_argument_table(skill):
    for cap in CAPS:
        assert f"`{cap} N`" in skill, f"引数の表に {cap} が無い"


def test_the_defaults_match_the_implementation(refactor, skill):
    """既定値は 1 か所（`refactor.py`）が持ち、表はそれを写す。"""
    assert "| `--max-test-rounds N` | " in skill
    for cap, default in (("--max-test-rounds", 2), ("--max-outer-rounds", 3),
                         ("--max-fix-rounds", 3), ("--max-items-per-round", 5)):
        row = next(l for l in skill.splitlines() if l.startswith(f"| `{cap} N`"))
        assert f"`{default}`" in row, f"{cap} の既定が表と実装で食い違う"
    assert refactor.DEFAULT_MAX_TEST_ROUNDS == 2


# ---------- 実行のコマンド列（A1 / B6） ----------

def _run_block(text: str) -> str:
    """「## 実行」の節にある bash のコード塊を返す。"""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("## 実行"))
    end = next((i for i, l in enumerate(lines[start + 1:], start + 1)
                if l.startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def test_the_command_sequence_passes_the_propose_phase(skill):
    """テスト整備ラウンドは `propose-tests` を起動する。

    `propose` を直に書くと、テスト整備ラウンドでも構造改善のプロンプトが渡り、
    ラウンドの種類が実行に反映されない。
    """
    block = _run_block(skill)
    assert '"$PROPOSE_PHASE"' in block
    assert 'launch-cli.sh" "$a" propose ' not in block


def test_the_command_sequence_runs_the_final_gate(skill):
    """B5 / B6 — Step 7 の分岐は `final-gate` が決める。"""
    block = _run_block(skill)
    assert "final-gate" in block
    assert "FINAL_GATE" in block


def test_the_command_sequence_passes_the_new_caps(skill):
    block = _run_block(skill)
    for flag in ("--max-test-rounds", "--ci-check", "--workflow-step"):
        assert flag in block, f"実行のコマンド列に {flag} が無い"


def test_the_command_sequence_does_not_launch_reviewers(skill):
    """Step 5 はテストで判定する。**レビュー CLI は起動しない。**"""
    block = _run_block(skill)
    assert " review " not in block
    assert "verify-round" in block
