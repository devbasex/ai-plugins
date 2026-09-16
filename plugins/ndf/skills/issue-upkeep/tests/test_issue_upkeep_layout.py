"""`issue-upkeep` の構成と配線（#331 / #712 / #713）。

**判定の値は 3 箇所（手順の表・自動で反映してよい変更の表・報告）で同じ並びを持つ。**
片方だけが増えると、反映の側が知らない判定を受け取る。
"""
from __future__ import annotations

import pathlib
import re

import pytest

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SKILLS = SKILL_DIR.parent
ROOT = SKILLS.parents[2]

SKILL = SKILL_DIR / "SKILL.md"
NO_WORK = SKILL_DIR / "references" / "no-work.md"
MILESTONES = SKILL_DIR / "references" / "milestones.md"
GROUPING = SKILL_DIR / "references" / "grouping.md"
VOCABULARY = SKILLS / "refactoring" / "references" / "vocabulary.md"

# 手順の表が持つ 8 つの判定。**この並びが基準である。**
VERDICTS = [
    "そのまま", "追記が要る", "書き直しが要る", "閉じてよい",
    "やらない", "重複", "ルートコーズ", "要判断",
]


def flat(text: str) -> str:
    """折り返しの改行を除く。日本語の文は改行の位置で語が割れるため、照合の前に繋ぐ。"""
    return text.replace("\n", "")


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。"""
    level = heading.split(" ", 1)[0]
    start = text.index(heading + "\n")
    rest = text[start + len(heading):]
    ends = [m.start() for m in re.finditer(r"^(#+) ", rest, re.MULTILINE)
            if len(m.group(1)) <= len(level)]
    return heading + (rest[:ends[0]] if ends else rest)


def table(text: str, header: str) -> list[list[str]]:
    """見出し行で始まる表の、データ行のセルを返す。太字の印は外す。"""
    block = text[text.index(header):]
    block = block[:block.index("\n\n")] if "\n\n" in block else block
    rows = [line for line in block.split("\n")[2:] if line.startswith("|")]
    return [[cell.strip().strip("*").strip() for cell in row.strip("|").split("|")]
            for row in rows]


def test_the_references_exist() -> None:
    """「やらない」とマイルストーンは参照へ分ける。

    段 1 と段 2A では、どちらの判断も起きない。手順の本体から外すと、対象を選んで調べる
    段では読み込まれない。
    """
    assert NO_WORK.is_file()
    assert MILESTONES.is_file()
    assert GROUPING.is_file()


def test_the_verdict_table_lists_eight_in_order() -> None:
    """判定は 8 つに限られ、手順の表がその並びを持つ。

    「ルートコーズ」は課題をまたいで決まるため「重複」の隣に置く。「要判断」は受け皿として末尾に残す。
    """
    body = SKILL.read_text(encoding="utf-8")
    table = body[body.index("| 判定 | 選ぶ条件 | 段 3 での対応 |"):]
    table = table[:table.index("\n\n")]
    found = [re.match(r"\| \*?\*?([^|*]+?)\*?\*?(?: \|| ).*", line).group(1).strip()
             for line in table.split("\n")[2:] if line.startswith("|")]
    assert found == VERDICTS, found


def test_no_work_is_reachable_from_the_verdict_table() -> None:
    """「やらない」の行から参照を指す。判断の基準はそちらにある。"""
    body = SKILL.read_text(encoding="utf-8")
    row = next(line for line in body.split("\n")
               if line.startswith("| **やらない**"))
    assert "references/no-work.md" in row


def test_no_work_has_two_necessary_conditions() -> None:
    """2 つの必要条件と、欠けたときの行き先が表で示されている。

    条件 2 の行き先を「重複」に固定すると、同じ修正レイヤーを指す別の課題が重複として閉じられる。
    行き先は 3 つに分け、「要判断」へ倒す文は見積りである条件 1 だけに掛ける。
    """
    body = NO_WORK.read_text(encoding="utf-8")
    assert "## 2 つの必要条件" in body
    assert "欠けたときの行き先" in body
    assert "抱える費用が、直す費用を下回る" in body
    assert "同じ原因の他の課題へ寄せられない" in body
    rows = table(body, "| # | 条件 | 確かめ方 | 欠けたときの行き先 |")
    second = next(row for row in rows if row[0] == "2")
    assert "重複とルートコーズの両方の突き合わせの結果" in flat(second[2])
    assert second[2].index("重複") < second[2].index("ルートコーズ")
    for outcome in ("重複", "ルートコーズ", "寄せ先が無く、条件は欠けていない"):
        assert outcome in second[3], outcome
    assert "どちらも見積りである" not in flat(body)
    assert "条件 1 は見積りである" in flat(body)


def test_no_work_shows_both_kinds_of_reactivation_condition() -> None:
    """再燃の条件は観測できる形で書く。観測できない書き方の例も示す。"""
    body = NO_WORK.read_text(encoding="utf-8")
    assert "**観測できる**" in body
    assert "観測できない" in body


def test_closing_and_not_planned_are_separate_verdicts() -> None:
    """「やらない」と「閉じてよい」を別の判定として区別する。"""
    body = SKILL.read_text(encoding="utf-8")
    assert "**「閉じてよい」とは\n別である。**" in NO_WORK.read_text(encoding="utf-8")
    assert "| 閉じてよい |" in body
    assert "| **やらない** |" in body


def test_milestones_reflect_only_the_earlier_direction() -> None:
    """早める方向だけを自動で反映する。"""
    body = MILESTONES.read_text(encoding="utf-8")
    assert "**早める方向だけを自動で反映する。**" in body
    assert "**要判断**" in body


def test_milestones_are_not_created_for_a_single_issue() -> None:
    """1 件しか残らないときは作らない。"""
    assert "**1 件しか残らないときは作らない。**" in MILESTONES.read_text(encoding="utf-8")


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


# ---------- 対象の範囲 ----------

def test_the_target_is_decided_only_by_the_milestone() -> None:
    """段 1 の 3 つ目の経路が、マイルストーンの有無だけで対象を決めることを明記している。

    絞ると、未設定のまま溜まる課題を誰も見ないことになる。
    """
    body = SKILL.read_text(encoding="utf-8")
    assert "マイルストーンの付いていない open の課題すべて" in body
    assert "**起票者は問わない。**" in body


def test_the_target_query_filters_only_by_milestone() -> None:
    """未設定の課題を拾う例が、マイルストーンの有無だけで絞っていること。"""
    body = SKILL.read_text(encoding="utf-8")
    block = body[body.index("**起票者は問わない。**"):]
    start = block.index("```bash")
    block = block[start:block.index("```", start + len("```bash")) + 3]
    assert "--author" not in block, "投稿者で絞る例になっている"
    assert "created:" not in block, "起票の時期で絞る例になっている"
    assert "select(.milestone == null)" in block


# ---------- ルートコーズの判定（#712） ----------

def test_cluster_is_reachable_from_the_verdict_table() -> None:
    """「ルートコーズ」の行から参照を指す。条件と書き方はそちらにある。"""
    body = SKILL.read_text(encoding="utf-8")
    row = next(line for line in body.split("\n")
               if line.startswith("| **ルートコーズ**"))
    assert "references/grouping.md" in row


def test_creating_the_parent_needs_approval() -> None:
    """親 issue の起票と子 issue への書き込みは、一括で提示して承認を得てから行う。

    どちらも外部から見える場所への書き込みである。
    """
    body = SKILL.read_text(encoding="utf-8")
    rows = table(body, "| 変更 | 自動で反映してよいか |")
    create = [row for row in rows if "親 issue を作る" in row[0]]
    link = [row for row in rows if "本文へ 1 行を足す" in row[0]]
    assert create and link
    for row in create + link:
        assert "一括で提示し、承認を得てから行う" in row[1], row


def test_upkeep_handles_four_things() -> None:
    """扱うことが 4 つになり、4 つ目が根本原因の場所で直す判断である。"""
    body = SKILL.read_text(encoding="utf-8")
    assert "## 扱う 3 つ" not in body
    part = section(body, "## 扱う 4 つ")
    rows = table(part, "| # | 扱うこと | 決めること |")
    assert [row[0] for row in rows] == ["1", "2", "3", "4"]
    assert "根本原因の場所で直す" in rows[3][2]
    assert "references/grouping.md" in part
    terms = table(body, "| 語 | この文書での意味 |")
    assert "8 つの区分" in next(row for row in terms if row[0] == "判定")[1]


def test_stage_1_picks_up_children_of_a_closed_parent() -> None:
    """閉じた親 issue の子 issue を拾う経路がある。

    子 issue は閉じないため親が閉じた後も open のまま残り、親と別のマイルストーンにいると
    ほかの 3 経路では拾えない。
    """
    part = section(SKILL.read_text(encoding="utf-8"), "### 段 1: 対象を選ぶ")
    assert "4 つの経路" in part
    rows = table(part, "| 経路 | 取り方 | 拾えるもの |")
    assert len(rows) == 4
    assert "閉じた親 issue の子 issue" in rows[3][1]
    assert "閉じない" in flat(part)


def test_stage_2a_records_the_shared_cause() -> None:
    """段 2A は現象レイヤーと修正レイヤーを控え、クラスタは決めない。"""
    part = section(SKILL.read_text(encoding="utf-8"), "### 段 2A: 課題ごとに調べる")
    text = flat(part)
    assert "確かめるのは 6 点である" in text
    assert "修正レイヤーが現象レイヤーと違うか" in text
    rows = table(part, "| 控える項目 | 何に使うか |")
    names = [row[0] for row in rows]
    assert "現象レイヤー" in names and "修正レイヤー" in names
    assert "段 2A ではクラスタを決めない" in text
    assert "他の課題の控えが見えない" in text


def test_stage_2b_matches_issues_by_shared_cause() -> None:
    """段 2B は同じ原因を持つクラスタを突き合わせ、書き換えを揃える行とは分ける。"""
    part = section(SKILL.read_text(encoding="utf-8"), "### 段 2B: 全体で突き合わせて判定を確定する")
    names = [row[0] for row in table(part, "| 突き合わせるもの | 何を決めるか |")]
    assert "同じ原因を持つクラスタ" in names
    assert "同じ前提の変化を受けた課題群" in names
    assert names.index("重複と判定された組") < names.index("同じ原因を持つクラスタ")
    assert "言い回しを揃える" in flat(part)


def test_stage_2b_note_branches_to_cluster() -> None:
    """起点が同じ組は、直した後に個別の作業が残るかで重複と親 issue へ分かれる。"""
    body = SKILL.read_text(encoding="utf-8")
    note = flat(body[body.index("**重複の判定では、起点が同じことと同じ課題であることを分ける。**"):
                     body.index("**「やらない」の候補は")])
    assert "個別の作業が残る" in note
    assert "ルートコーズ" in note and "親 issue" in note


def test_cluster_separates_itself_from_duplication() -> None:
    """重複との境目は、原因を直した後に個別の作業が残るかである。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    assert "原因を直しても各課題に個別の作業が残るならルートコーズ、残らないなら重複である" in text


def test_root_cause_has_one_condition() -> None:
    """条件は 1 つで、件数は条件ではない。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 条件 | 確かめ方 | 欠けたときの行き先 |")
    assert len(rows) == 1
    assert "修正レイヤーが現象レイヤーと違う" in rows[0][0]
    assert "既存の判定のまま" in rows[0][2]
    text = flat(body)
    assert "件数は条件ではない" in text
    assert "件数が決めるのは" in text


def test_stage_3_has_three_outcomes() -> None:
    """段 3 の対応は、件数と個別の作業の有無で 3 つに分かれ、3 行目が重複との境目である。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 同じ修正レイヤーを指す件数 | 直しても個別の作業が残るか | 対応 |")
    assert len(rows) == 3
    assert "本文へ" in rows[0][2]
    assert "親 issue" in rows[1][2]
    assert "重複" in rows[2][2]
    assert "3 行目が重複との境目である" in flat(body)


def test_cluster_keeps_the_child_issues_open() -> None:
    """子 issue を閉じない。結び付けはサブイシュー関係を既定にし、例外だけ本文の 1 行にする。"""
    body = GROUPING.read_text(encoding="utf-8")
    text = flat(body)
    assert "子 issue を閉じない" in text
    assert "固有の再現手順" in text and "個別の適用が残る" in text
    assert "サブイシュー関係" in text
    assert "sub_issue_id" in body and "データベースの ID" in text
    assert "子 1 件が持てる親は 1 件だけ" in text
    assert "サブイシューの API を持たないリポジトリ" in text


def test_cluster_shows_the_body_of_the_parent_issue() -> None:
    """親 issue に書く 4 つを挙げ、子 issue の中身は写さない。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    for item in ("修正レイヤー", "採る手", "現象レイヤーと観測", "完了条件"):
        assert item in text, item
    assert "見出しの形は決めない" in text
    assert "子 issue の中身を写さない" in text
    assert "片方だけが古くなる" in text


def test_an_issue_may_join_two_clusters() -> None:
    """1 件が複数のクラスタへ属してよく、やり直しの見分けは親 issue の側から引く。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    assert "1 件が複数のクラスタへ属してよい" in text
    assert "親 issue どうしが同じ箇所を直すなら、それは 1 つのクラスタである" in text
    assert "親 issue の側から引く" in text


def test_cluster_does_not_gate_on_size() -> None:
    """層がまたがっても判定は変わらず、採る手は判定の条件ではない。"""
    text = flat(GROUPING.read_text(encoding="utf-8"))
    assert "修正レイヤーが 2 つ以上にまたがっても、判定は変わらない" in text
    assert "マイルストーンの割り当てと実装計画" in text
    assert "手は判定の条件ではない" in text
    assert "当てはまるものを全部書く" in text


def test_parent_takes_the_highest_priority_in_the_cluster() -> None:
    """親 issue の重要度はクラスタの最高値を採り、重要度の規則を先に効かせてから最も早いものを採る。"""
    assert "クラスタの中で最も高いもの" in flat(GROUPING.read_text(encoding="utf-8"))
    assert "milestones.md" in GROUPING.read_text(encoding="utf-8")
    part = flat(section(MILESTONES.read_text(encoding="utf-8"), "## 親 issue を割り当てる"))
    assert "重要度が「高い」" in part
    assert part.index("重要度が「高い」") < part.index("最も早いもの")
    assert "子 issue のマイルストーンは動かさない" in part


def test_upkeep_files_only_the_parent_issue() -> None:
    """「手入れ」は起票を含まず、例外は親 issue 1 つだけである。"""
    body = SKILL.read_text(encoding="utf-8")
    text = flat(body)
    assert "「手入れ」は起票を含まない" in text
    assert "例外は親 issue の起票 1 つだけである" in text
    assert "発見の瞬間しか見ない" in text
    terms = table(body, "| 語 | この文書での意味 |")
    assert "親 issue" in next(row for row in terms if row[0] == "手入れ")[1]


def test_the_report_counts_the_clusters() -> None:
    """完了報告は 8 つの判定の内訳と、処理したクラスタの数を持つ。"""
    rows = table(SKILL.read_text(encoding="utf-8"), "| 項目 | 何を書くか |")
    assert "8 つの判定" in next(row for row in rows if row[0] == "判定の内訳")[1]
    assert any("処理したクラスタの数" in row[1] for row in rows)


# ---------- 判断の担い手（#713） ----------

def test_the_boundary_table_covers_both_judgements() -> None:
    """価値と構造の判断 × 発見の瞬間と溜まった課題の表を、この Skill が正本として持つ。"""
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


def test_retrospective_declines_cluster_discovery() -> None:
    """振り返りはクラスタの発見を担わない。1 回の変更を見るためクラスタが見えない。"""
    text = flat((SKILLS / "retrospective" / "SKILL.md").read_text(encoding="utf-8"))
    assert "クラスタの発見を担わない" in text
    assert "1 回の変更を見る" in text


# ---------- 用語 ----------

def test_the_terms_are_loanwords() -> None:
    """判定・クラスタ・着手の時期を一般語で呼ばない。4 語は用語の表が定める。"""
    body = SKILL.read_text(encoding="utf-8")
    names = [row[0] for row in table(body, "| 語 | この文書での意味 |")]
    for term in ("ルートコーズ", "クラスタ", "親 issue", "子 issue", "まとまり"):
        assert term in names, term
    grouping = GROUPING.read_text(encoding="utf-8")
    for text in (body, grouping):
        assert "束ねる" not in text
        assert "かたまり" not in text
        assert "群" not in text.replace("同じ前提の変化を受けた課題群", "")
    assert "まとまり" not in grouping
    for phrase in ("着手のまとまり", "どのまとまりで着手", "どのまとまりへも"):
        assert phrase not in flat(body), phrase


def test_milestones_use_one_word_for_the_timing() -> None:
    """着手の時期は「マイルストーン」で呼び、「まとまり」を使わない。"""
    assert "まとまり" not in MILESTONES.read_text(encoding="utf-8")


def test_milestones_pick_the_nearest_by_sequence() -> None:
    """直近と順序は名前の先頭の連番で決める。open のマイルストーンは版数を持たない。"""
    text = flat(MILESTONES.read_text(encoding="utf-8"))
    assert "版数が最も小さい" not in text
    assert "版数の順序" not in text
    assert "連番が最も小さい" in text
    assert "連番の順序と食い違うとき" in text
    assert "後の連番にあるなら" in text
    assert "`<2 桁の連番> <主題>`" in text
    assert "説明が連番と別の着手の順序を書いているときは、説明を採る" in text


# ---------- 上位の分解 ----------

def test_grouping_separates_the_two_layers() -> None:
    """現象レイヤーと修正レイヤーを分けて書き、さかのぼる段数を決めない。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 現象レイヤーの例 | 修正レイヤーの例 |")
    phenomena = [row[0] for row in rows]
    for example in ("各コントローラ", "各サブクラス", "移譲している側", "各スクリプト"):
        assert example in phenomena, example
    text = flat(body)
    assert "さかのぼる段数を決めない" in text
    assert "そこを直せば、現象レイヤーの各所が同じ形で直るか" in text
    assert "修正レイヤーが現象レイヤーと同じこともある" in text
    assert "名指しできない" in text


def test_grouping_lists_five_moves() -> None:
    """採る手は 5 つに限り、呼び名は `refactoring` の語彙から採る。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 手 | いつ選ぶか | 対応する手法（`refactoring` の呼び名） |")
    assert [row[0] for row in rows] == ["移動", "統合", "新設", "向きの修正", "分離"]
    names = re.findall(r"`([a-z_]+)`", " ".join(row[2] for row in rows))
    assert len(names) >= 6
    vocabulary = VOCABULARY.read_text(encoding="utf-8")
    for name in names:
        assert f"| `{name}` |" in vocabulary, name
    assert "references/vocabulary.md" in body


# ---------- 判断を人へ返さない・書式を持たない ----------

def test_cluster_never_defers_to_a_human() -> None:
    """ルートコーズの分岐は段 2A の控えだけで決まるため、「要判断」へ倒さない。"""
    body = GROUPING.read_text(encoding="utf-8")
    assert "要判断" not in body
    text = flat(body)
    for outcome in ("ルートコーズ", "既存の判定のまま", "本文へ", "親 issue を 1 件つくり", "重複として正本へ寄せる"):
        assert outcome in text, outcome


def test_the_skill_carries_only_what_it_must() -> None:
    """その場で決めれば済む書式の雛形を置かない。実測でしか分からないことは書く。"""
    body = GROUPING.read_text(encoding="utf-8")
    assert "```markdown" not in body
    assert "```text" not in body
    assert "sub_issue_id" in body


def test_grouping_rereads_the_stated_fix() -> None:
    """課題が書いている直し方をそのまま採らず、手段の語で終わる本文は何が直るのかを書き出す。"""
    body = GROUPING.read_text(encoding="utf-8")
    part = section(body, "## 課題が書いている直し方をそのまま採らない")
    assert table(part, "| 本文が書いていること | 棚卸が確かめること |")
    text = flat(part)
    assert "起票は現象を見て書かれる" in text
    for word in ("まとめる", "整理する", "統一する", "揃える"):
        assert f"「{word}」" in text, word
    assert "何が直るのか" in text
    assert "まだ原因に届いていない" in text
