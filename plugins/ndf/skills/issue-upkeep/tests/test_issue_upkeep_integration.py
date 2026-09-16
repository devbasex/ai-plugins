"""`issue-upkeep` と周辺 Skill・manifest の配線（Skill 間契約）。

issue-upkeep 本体のレイアウト検査とは変更理由が異なる。外部 Skill（out-of-scope /
problem-solving / retrospective / release）と 4 ランタイムの manifest を読むテスト、
判断の担い手の境界表、補助関数の番犬をここへ集める。
"""
from __future__ import annotations

import pytest

from _helpers import (
    GROUPING,
    MILESTONES,
    NO_WORK,
    OUT_OF_SCOPE,
    PROBLEM_SOLVING,
    RETROSPECTIVE,
    RETROSPECTIVE_TARGETS,
    ROOT,
    SKILLS,
    flat,
    section,
    table,
)


def test_the_references_exist() -> None:
    """「やらない」とマイルストーンは参照へ分ける。

    段 1 と段 2A では、どちらの判断も起きない。手順の本体から外すと、対象を選んで調べる
    段では読み込まれない。
    """
    assert NO_WORK.is_file()
    assert MILESTONES.is_file()
    assert GROUPING.is_file()


@pytest.mark.parametrize("caller,marker", [
    ("retrospective", "issue-upkeep"),
    ("release", "issue-upkeep"),
    ("out-of-scope", "issue-upkeep"),
    ("problem-solving", "issue-upkeep"),
])
def test_the_callers_point_here(caller: str, marker: str) -> None:
    """振り返り・配布・起票・根本原因の調査の 4 つがこの Skill を指す。

    構造の判断の担い手はこの Skill であり、どこから読み始めてもここへたどれるようにする。
    """
    body = (SKILLS / caller / "SKILL.md").read_text(encoding="utf-8")
    assert marker in body, f"{caller} が {marker} を指していない"


@pytest.mark.parametrize("runtime", ["claude", "codex", "kiro", "agy"])
def test_the_skill_is_distributed(runtime: str) -> None:
    """4 つの manifest すべてに載る。"""
    manifest = ROOT / "plugins" / "ndf" / "manifests" / f"{runtime}-skills.txt"
    names = [line.split("#", 1)[0].strip()
             for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert "issue-upkeep" in names


def test_the_boundary_table_covers_both_judgements() -> None:
    """価値と構造の判断 × 発見の瞬間と溜まった課題の表を、この Skill が正本として持つ。"""
    from _helpers import SKILL
    body = SKILL.read_text(encoding="utf-8")
    rows = table(body, "| 判断 | 発見の瞬間 | 溜まった課題 |")
    value = next(row for row in rows if row[0].startswith("価値"))
    structure = next(row for row in rows if row[0].startswith("構造"))
    assert "「やらない」" in value[2]
    assert "「ルートコーズ」" in structure[2]
    part = flat(section((SKILLS / "out-of-scope" / "SKILL.md").read_text(encoding="utf-8"),
                        "## 蓄積した課題との境界"))
    assert "`issue-upkeep` の「やらない」" in part
    assert "`issue-upkeep` の「ルートコーズ」" in part


def test_problem_solving_separates_upstream_from_cluster() -> None:
    """「上流で直す」と棚卸の「ルートコーズ」の違いを、件数ではなく見る対象で書く。"""
    body = (SKILLS / "problem-solving" / "SKILL.md").read_text(encoding="utf-8")
    paragraph = next(p for p in body.split("\n\n") if "ルートコーズ" in p)
    assert "../issue-upkeep/SKILL.md" in paragraph
    assert "件数" not in paragraph and "複数" not in paragraph


def test_problem_solving_keeps_its_own_contract() -> None:
    """problem-solving 固有の契約を残す。

    原因特定後に再現テストへ進むこと、再現できないときは 1〜3 へ戻ること、
    自動化できないときは手動の再現手順と確認結果を報告に明記すること、
    既存コードでは現状固定を先行することの 4 つを固定する。
    """
    body = PROBLEM_SOLVING.read_text(encoding="utf-8")
    flow = body[body.index("### 判断フロー"):body.index("### 再現テストを先に書く")]
    # 判断フローの 4 番で、原因特定（3 番）の後に再現テストへ進む
    assert "4. 再現テストを追加する" in flow
    text = flat(body)
    assert "修正の前に、不具合を再現する失敗テストを追加する" in text
    assert "再現テストが書けないときは、再現条件がまだ特定できていない" in text
    assert "修正へ進まず 1〜3 に戻る" in text
    assert "手動の再現手順と確認結果を" in text and "報告に明記する" in text
    assert "再現テストを書く前に" in text and "現状の振る舞いを固定するテスト" in text


def test_problem_solving_defers_details_to_the_canonical_skills() -> None:
    """再現テストの詳細と現状固定テストの手順を、写しを持たず正本へ集約する。

    再現テストの RED/GREEN は tdd-cycle、現状固定テストの手順は refactoring が正本で、
    problem-solving はそれを指すだけにする。
    """
    body = PROBLEM_SOLVING.read_text(encoding="utf-8")
    repro = body[body.index("### 再現テストを先に書く"):
                 body.index("### テストが困難な既存コード")]
    charac = body[body.index("### テストが困難な既存コード"):
                  body.index("### 典型的な見逃しパターン")]
    # 再現テストの節は tdd-cycle を正本として指し、確認項目の写しの表を持たない
    assert "`tdd-cycle`" in repro
    assert "RED/GREEN" in flat(repro)
    assert "| 確認すること | 目的 |" not in repro
    # 現状固定テストの節は refactoring を正本として指し、5 段階の写しを持たない
    assert "`refactoring`" in charac
    assert "現状固定テストの手順は `refactoring`" in flat(charac)
    assert "1. 変更対象の入口と副作用を洗い出す" not in charac


def test_retrospective_declines_cluster_discovery() -> None:
    """振り返りはクラスタの発見を担わない。1 回の変更を見るためクラスタが見えない。"""
    text = flat((SKILLS / "retrospective" / "SKILL.md").read_text(encoding="utf-8"))
    assert "クラスタの発見を担わない" in text
    assert "1 回の変更を見る" in text


def test_retrospective_gathers_the_posting_branch_into_one_table() -> None:
    """起点ごとの投稿先・実行コマンド・辿る経路が 1 つの対応表に集約されていること。

    投稿先・コマンド・経路の分岐が「投稿先を決める」「投稿する」「辿る経路を作る」の
    複数箇所に散らないよう、起点の種類だけが 3 つすべてを決める形に固定する。
    """
    body = RETROSPECTIVE.read_text(encoding="utf-8")
    rows = table(body, "| 起点 | 記録の本体を置く場所 | 実行コマンド | 辿る経路 |")
    got = [(row[0], row[1], row[2], row[3]) for row in rows]
    assert len(got) == len(RETROSPECTIVE_TARGETS), got
    for origin, place, command, trail in RETROSPECTIVE_TARGETS:
        row = next(r for r in got if r[0] == origin)
        assert row[1] == place, row
        assert command in row[2], row
        assert flat(trail) in flat(row[3]), row


def test_retrospective_posting_and_trail_defer_to_the_table() -> None:
    """「投稿する」と「辿る経路を作る」が分岐を書き直さず対応表を指すこと。"""
    body = RETROSPECTIVE.read_text(encoding="utf-8")

    def between(start: str, end: str) -> str:
        s = body.index(start)
        e = body.index(end, s + len(start))
        return body[s:e]

    post = between("#### 投稿する", "#### 辿る経路を作る")
    assert "「起点ごとの投稿先を決める」表の「実行コマンド」列" in flat(post)
    trail = between("#### 辿る経路を作る", "## 書かないこと")
    assert "「起点ごとの投稿先を決める」表の「辿る経路」列" in flat(trail)


def test_out_of_scope_stage_2_offers_exactly_three_choices() -> None:
    """段 2 の判断が {起票する, 範囲内へ入れる, 起票しない} の 3 分岐だけであることを固定する。

    表示文字列の完全一致ではなく、判断の列を集合として比較する。この 3 分岐は発見の瞬間の
    判断の中心で、後の構造改善で表を触る対象になる。
    """
    part = section(OUT_OF_SCOPE.read_text(encoding="utf-8"), "### 2. 3 択で決める")
    rows = table(part, "| 判断 | 選ぶ条件 | 残すもの |")
    assert {row[0] for row in rows} == {"起票する", "範囲内へ入れる", "起票しない"}


def test_out_of_scope_issue_target_needed_only_when_filing() -> None:
    """「起票先を決める」段が『起票する』を選んだときだけ要ることを固定する。

    起票先が要る分岐は 3 択のうち 1 つだけである。
    """
    text = flat(section(OUT_OF_SCOPE.read_text(encoding="utf-8"), "### 3. 起票先を決める"))
    assert "「起票する」を選んだときだけ行う" in text
    assert "残る 2 つの判断には起票先が要らない" in text


def test_table_helper_does_not_pass_over_a_missing_heading() -> None:
    """見出し（表）が無い本文では table 補助が投げ、素通りしないことを確かめる。

    段 2 の表を消したときに固定テストが黙って通ってしまわないための番犬。
    """
    with pytest.raises(ValueError):
        table("見出しの無い本文\n", "| 判断 | 選ぶ条件 | 残すもの |")
