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


def test_the_defaults_match_the_implementation(cmd_setup, vocabulary, skill):
    """既定値は 1 か所（新規の初期化が置き換える表）が持ち、手順書の表はそれを写す。"""
    assert "| `--max-test-rounds N` | " in skill
    for cap in CAPS:
        default = cmd_setup.NEW_RUN_DEFAULTS[cap[2:].replace("-", "_")]
        row = next(l for l in skill.splitlines() if l.startswith(f"| `{cap} N`"))
        assert f"`{default}`" in row, f"{cap} の既定が表と実装で食い違う"
    assert vocabulary.DEFAULT_MAX_TEST_ROUNDS == 2


# ---------- 参加者と担当の決め方（#727 の AC42 / AC43） ----------

PARTICIPANT_ARGS = ("--exclude", "--include", "--require-all")
DOC01 = SKILL.parent / "docs" / "01-state-and-propose.md"
CLAUDE_MD = SKILL.parents[4] / "CLAUDE.md"


def test_the_participant_arguments_are_documented(skill):
    """AC43 — 引数の表と `argument-hint` に 3 つの引数がある。"""
    hint = next(l for l in skill.splitlines() if l.startswith("argument-hint:"))
    rows = [l for l in skill.splitlines() if l.startswith("| `--")]
    for arg in PARTICIPANT_ARGS:
        assert arg in hint, f"argument-hint に {arg} が無い"
        assert any(r.startswith(f"| `{arg}") for r in rows), f"引数の表に {arg} が無い"


def test_the_assignment_section_has_one_cohort(skill):
    """AC43 — 担当の決め方は母集合を 1 つの表で書き、適用専用の母集合を持たない。"""
    lines = skill.splitlines()
    start = lines.index("## 担当の決め方")
    end = next(i for i, l in enumerate(lines[start + 1:], start + 1) if l.startswith("## "))
    section = "\n".join(lines[start:end])
    assert "impl_capable" not in section
    assert sum(1 for l in lines[start:end] if l.startswith("| ---")) == 1
    assert "codex / kiro" in section and "--include agy" in section


def test_the_prerequisites_no_longer_demand_every_cli(skill):
    """AC43 — 前提から「すべてログイン済み」が消え、要る CLI は codex / kiro-cli になる。"""
    lines = skill.splitlines()
    start = lines.index("## 前提")
    end = next(i for i, l in enumerate(lines[start + 1:], start + 1) if l.startswith("## "))
    section = "\n".join(lines[start:end])
    assert "すべてログイン済み" not in section
    assert "| Claude Code | `codex` / `kiro-cli` |" in section


def test_init_variables_do_not_list_the_implementation_cohort():
    """AC43 — `init` が返す変数の表に適用専用の母集合が無い。"""
    assert "IMPL_POOL" not in DOC01.read_text(encoding="utf-8")


def test_claude_md_describes_the_participants_and_the_rotation():
    """AC42 — 指示書の cross-refactoring の節が新しい母集合と輪番を書く（#736 を含む）。"""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = lines.index("## cross-refactoring")
    end = next(i for i, l in enumerate(lines[start + 1:], start + 1) if l.startswith("## "))
    section = "\n".join(lines[start:end])
    assert "codex / kiro とホスト（ホストが codex / kiro なら 2 者）" in section
    assert "適用担当は参加者の数のラウンドで 1 周する" in section
    for stale in ("ホストを除く 3 者", "参加する 4 者", "codex / agy の両方",
                  "既定が 4"):
        assert stale not in text, f"CLAUDE.md に {stale} が残っている"


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


# ---------- 結果なしと無進捗の許容（#728 / #647 / #553） ----------

DOCS = SKILL.parent / "docs"


def test_the_apply_round_row_states_the_attempt_cap(skill):
    """AC43: 適用ラウンドの行が、同じ群を開き直す上限（2 回）を書く。"""
    row = next(r for r in _terms_table(skill) if "適用ラウンド" in r)

    assert "2 回" in row


def test_the_single_cap_paragraph_is_gone(skill):
    """AC43: 上限を 1 つに保つとしていた段落が残っていないこと。"""
    assert "別の上限を置かない" not in skill
    assert "別に置かない" not in skill


def test_every_implementer_phase_passes_the_stall_timeout(skill):
    """AC41: 適用・修正・最終ゲートの修正の監視が、無進捗の許容だけを受け取る。"""
    for phase in ("apply", "fix", "final-fix"):
        block = skill.split(f"--phase {phase}", 1)[1].split("\n\n", 1)[0]
        assert '--stall-timeout "$IMPL_STALL_TIMEOUT"' in block, phase
        assert "--timeout " not in block, phase


def test_the_apply_document_describes_a_missing_result(skill):
    """AC44: 適用の説明が、結果なしのときの取り消し・記録・終了コードを書く。"""
    text = (DOCS / "02-apply-and-review.md").read_text(encoding="utf-8")

    assert "実装担当が結果を残さなかったとき" in text
    assert "failed_attempts" in text
    assert "終了コード" in text
    assert '--stall-timeout "$IMPL_STALL_TIMEOUT"' in text


def test_the_fix_document_describes_a_missing_result(skill):
    """AC44: 修正と最終ゲートの説明が、同じ 2 つを書く。"""
    text = (DOCS / "04-fix-and-report.md").read_text(encoding="utf-8")

    assert text.count('--stall-timeout "$IMPL_STALL_TIMEOUT"') == 2
    assert "修正の担当が結果を残さなかったときも、修正ラウンドは進める" in text
    assert "修正の担当が結果を残さなかったときも同じ手順を通る" in text


def test_the_trailer_section_states_both_ways_of_reading(skill):
    """AC45: 記名の節が、人の集計と進行側の検証の 2 つの読み方を書く。"""
    text = (DOCS / "02-apply-and-review.md").read_text(encoding="utf-8")
    section = text.split("### コミットトレーラーの形式", 1)[1].split("\n### ", 1)[0]

    assert "最後の段落だけ" in section
    assert "git interpret-trailers --parse" in section
