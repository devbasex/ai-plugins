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
VERDICT_HEADER = "| 判定 | 選ぶ条件 | 段 3 での対応 |"


def flat(text: str) -> str:
    """折り返しの改行を除く。日本語の文は改行の位置で語が割れるため、照合の前に繋ぐ。"""
    return text.replace("\n", "")


def plain(text: str) -> str:
    """折り返しの改行に加えて強調の印を除く。太字の付け外しで同じ契約が落ちないようにする。"""
    return text.replace("\n", "").replace("**", "")


def contains(text: str, fragment: str) -> bool:
    """強調の印と改行の位置によらず、文が本文にあるか。"""
    return plain(fragment) in plain(text)


def locate(text: str, fragment: str) -> int:
    """強調の印を除いた文の、本文での位置。切り出しの起点に使う。"""
    return text.index(plain(fragment))


def row_starting(text: str, header: str, first_cell: str) -> str:
    """見出し行で始まる表から、先頭のセルで行を探す。先頭のセルの太字の有無は問わない。

    探すのは見出し行より後だけである。同じ語を先頭に持つ別の表（用語の表など）の行を拾わない。
    """
    return next(line for line in text[text.index(header):].split("\n")
                if plain(line).startswith(plain(first_cell)))


def links_to(text: str, target: str, base: pathlib.Path = SKILL_DIR) -> bool:
    """本文のリンクが `target` を指し、その先のファイルが実在するか。"""
    found = [link.split("#", 1)[0] for link in re.findall(r"\]\(([^)]+)\)", text)]
    return target in found and (base / target).is_file()


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
    row = row_starting(body, VERDICT_HEADER, "| **やらない**")
    assert links_to(row, "references/no-work.md"), row


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
    assert "重複とルートコーズの両方の突き合わせの結果" in plain(second[2])
    assert second[2].index("重複") < second[2].index("ルートコーズ")
    for outcome in ("重複", "ルートコーズ", "寄せ先が無く、条件は欠けていない"):
        assert outcome in second[3], outcome
    assert "どちらも見積りである" not in flat(body)
    assert "条件 1 は見積りである" in plain(body)


def test_no_work_shows_both_kinds_of_reactivation_condition() -> None:
    """再燃の条件は観測できる形で書く。観測できない書き方の例も示す。"""
    body = NO_WORK.read_text(encoding="utf-8")
    assert contains(body, "**観測できる**")
    assert "観測できない" in body


def test_closing_and_not_planned_are_separate_verdicts() -> None:
    """「やらない」と「閉じてよい」を別の判定として区別する。"""
    body = SKILL.read_text(encoding="utf-8")
    assert contains(NO_WORK.read_text(encoding="utf-8"), "**「閉じてよい」とは\n別である。**")
    assert contains(body, "| 閉じてよい |")
    assert contains(body, "| **やらない** |")


@pytest.mark.parametrize(("priority", "holding_cost", "candidate"), [
    ("高い（実害・安全機構の欠落）", "大きい", "原則ならない。 候補にするなら、実害が稀であることを実測で示す"),
    ("中くらい（保守性・設計一貫性）", "中くらい", "直す費用が大きければ候補になる"),
    ("低い（余力があれば）", "小さい", "候補になりやすい"),
])
def test_no_work_branches_by_priority(
        priority: str, holding_cost: str, candidate: str) -> None:
    """重要度の 3 区分に応じた「やらない」候補判定の分岐を固定する。"""
    part = section(NO_WORK.read_text(encoding="utf-8"),
                   "## 重要度が、抱える費用の目安になる")
    rows = table(part, "| 重要度 | 抱える費用 | 候補になるか |")
    normalized = [[plain(cell) for cell in row] for row in rows]
    row = next(r for r in normalized if r[0] == priority)
    assert row[1] == holding_cost
    assert row[2] == candidate


def test_no_work_high_priority_requires_frequency_measurement_or_needs_judgement() -> None:
    """重要度「高い」を候補にするには頻度の実測が必要で、示せなければ要判断へ倒す規則を固定する。"""
    part = plain(section(NO_WORK.read_text(encoding="utf-8"),
                         "## 重要度が、抱える費用の目安になる"))
    assert "高いものを候補にするときは、頻度を実測で示す" in part
    assert "示せなければ要判断へ倒す" in part


def test_milestones_reflect_only_the_earlier_direction() -> None:
    """早める方向だけを自動で反映する。"""
    body = MILESTONES.read_text(encoding="utf-8")
    assert contains(body, "**早める方向だけを自動で反映する。**")
    assert contains(body, "**要判断**")


@pytest.mark.parametrize(("situation", "response"), [
    ("重要度が高いのに、直近でないマイルストーンにある", "直近へ移す"),
    ("重要度が低いのに、直近のマイルストーンにある", "要判断。 後ろへ移すと着手が遅れる"),
    ("重要度と位置が合っている", "何もしない"),
])
def test_existing_milestones_branch_by_priority_and_position(
        situation: str, response: str) -> None:
    """重要度と現在位置の食い違いに応じた 3 つの対応を固定する。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 既に設定されているものを振り直す")
    rows = table(part, "| 状況 | 対応 |")
    row = next(row for row in rows if row[0] == situation)
    assert plain(row[1]) == response


def test_existing_milestones_explain_why_only_earlier_moves_are_automatic() -> None:
    """早める判断には実害の根拠があり、遅らせる判断は価値判断なので自動化しない。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 既に設定されているものを振り直す")
    assert contains(part, "早める判断は実害の記述が根拠になる")
    assert contains(part, "遅らせる判断は\n「今やらなくてよい」という価値の判断")


def test_milestones_are_not_created_for_a_single_issue() -> None:
    """1 件しか残らないときは作らない。"""
    assert contains(MILESTONES.read_text(encoding="utf-8"), "**1 件しか残らないときは作らない。**")


@pytest.mark.parametrize(("priority", "when_no_matching_subject"), [
    ("高い（実害・安全機構の欠落）", "直近のマイルストーンへ、主題によらず入れる"),
    ("中くらい（保守性・設計一貫性）", "末尾に新しく作る（下記）"),
    ("低い（余力があれば）", "未設定のまま残す"),
])
def test_unassigned_issues_branch_by_priority_when_no_subject_matches(
        priority: str, when_no_matching_subject: str) -> None:
    """主題の合うものが無いときの割り当て先を、重要度の 3 区分ごとに固定する。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 設定されていない課題を割り当てる")
    rows = table(part, "| 重要度 | 入れる先 | 主題の合うものが無いとき |")
    normalized = [[cell.replace("**", "") for cell in row] for row in rows]
    row = next(row for row in normalized if row[0] == priority)
    assert row[2] == when_no_matching_subject


def test_unassigned_issue_with_multiple_matching_subjects_needs_judgement() -> None:
    """主題が複数に当てはまるときは、自動で割り当てず要判断へ倒す。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 設定されていない課題を割り当てる")
    assert contains(part, "主題が複数に当てはまるときは**要判断**へ倒す")


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

def test_zero_targets_skip_to_reporting_with_the_reason() -> None:
    """4 経路で集めた対象が 0 件なら、判定・反映をせず報告へ理由を残す。"""
    body = SKILL.read_text(encoding="utf-8")
    procedure = section(body, "## 手順")
    target_rows = table(procedure, "| 経路 | 取り方 | 拾えるもの |")
    report_rows = table(body, "| 項目 | 何を書くか |")

    assert [row[0] for row in target_rows] == [
        "機械の候補", "担当が足す", "マイルストーン", "親 issue",
    ]
    assert "段 1 で対象が 0 件なら、そこで飛ばす" in plain(procedure)
    target_report = next(row for row in report_rows if row[0] == "対象")
    assert "0 件なら飛ばしたことと理由" in target_report[1]


def test_the_target_is_decided_only_by_the_milestone() -> None:
    """段 1 の 3 つ目の経路が、マイルストーンの有無だけで対象を決めることを明記している。

    絞ると、未設定のまま溜まる課題を誰も見ないことになる。
    """
    body = SKILL.read_text(encoding="utf-8")
    assert contains(body, "マイルストーンの付いていない open の課題すべて")
    assert contains(body, "**起票者は問わない。**")


def test_the_target_query_filters_only_by_milestone() -> None:
    """未設定の課題を拾う例が、マイルストーンの有無だけで絞っていること。"""
    body = SKILL.read_text(encoding="utf-8")
    block = body[locate(body, "**起票者は問わない。**"):]
    start = block.index("```bash")
    block = block[start:block.index("```", start + len("```bash")) + 3]
    assert "--author" not in block, "投稿者で絞る例になっている"
    assert "created:" not in block, "起票の時期で絞る例になっている"
    assert "select(.milestone == null)" in block


def test_stage_1_expands_to_all_open_issues_on_pervasive_changes() -> None:
    """全体に影響する変更における open 全件への拡張（--all）の 4 条件を固定する。

    ディレクトリの移動・統合、対応する実行環境の増減、ブランチ戦略の変更、
    識別子の一括改名のいずれかに当たるときは、対象を open 全件（--all）へ広げる。
    """
    body = SKILL.read_text(encoding="utf-8")
    part = body[body.index("### 段 1: 対象を選ぶ"):body.index("### 段 2A: 課題ごとに調べる")]
    rows = table(part, "| 全体に影響する変更 | 例 |")

    assert [row[0] for row in rows] == [
        "ディレクトリの移動・統合",
        "対応する実行環境の増減",
        "ブランチ戦略の変更",
        "識別子の一括改名",
    ]
    text = plain(part)
    assert "全体に影響する変更では、触った領域では足りない" in text
    assert "次のいずれかに当たるときは対象をopen の全件へ広げる（`--all`）" in text


# ---------- ルートコーズの判定（#712） ----------

def test_cluster_is_reachable_from_the_verdict_table() -> None:
    """「ルートコーズ」の行から参照を指す。条件と書き方はそちらにある。"""
    body = SKILL.read_text(encoding="utf-8")
    row = row_starting(body, VERDICT_HEADER, "| **ルートコーズ**")
    assert links_to(row, "references/grouping.md"), row


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


def test_every_upkeep_change_has_one_of_the_three_dispositions() -> None:
    """変更操作は、自動反映・承認後に反映・人へ返す、の 3 区分だけを持つ。"""
    part = section(SKILL.read_text(encoding="utf-8"),
                   "## 自動で反映してよい変更と、返す変更")
    rows = table(part, "| 変更 | 自動で反映してよいか |")
    dispositions = {row[1] for row in rows}
    assert dispositions == {
        "よい",
        "一括で提示し、承認を得てから行う",
        "返す",
    }


@pytest.mark.parametrize("change", [
    "起票の意図が現在も要るかを判断できない",
    "正しい読み方が複数ある",
])
def test_upkeep_returns_ambiguous_changes_to_a_human(change: str) -> None:
    """本文の外に判断材料が要る 2 つの安全弁は、自動反映せず人へ返す。"""
    part = section(SKILL.read_text(encoding="utf-8"),
                   "## 自動で反映してよい変更と、返す変更")
    rows = table(part, "| 変更 | 自動で反映してよいか |")
    row = next(row for row in rows if row[0] == change)
    assert row[1] == "返す"
    assert "判断の材料が本文の外にあるかどうか" in plain(part)
    assert "決まらないものだけを返す" in plain(part)


def test_upkeep_handles_four_things() -> None:
    """扱うことが 4 つになり、4 つ目が根本原因の場所で直す判断である。"""
    body = SKILL.read_text(encoding="utf-8")
    assert "## 扱う 3 つ" not in body
    part = section(body, "## 扱う 4 つ")
    rows = table(part, "| # | 扱うこと | 決めること |")
    assert [row[0] for row in rows] == ["1", "2", "3", "4"]
    assert "根本原因の場所で直す" in rows[3][2]
    assert links_to(part, "references/grouping.md")
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
    assert "閉じない" in plain(part)


def test_stage_2a_records_the_shared_cause() -> None:
    """段 2A は現象レイヤーと修正レイヤーを控え、クラスタは決めない。"""
    part = section(SKILL.read_text(encoding="utf-8"), "### 段 2A: 課題ごとに調べる")
    text = plain(part)
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
    assert "言い回しを揃える" in plain(part)


def test_stage_2b_note_branches_to_cluster() -> None:
    """起点が同じ組は、直した後に個別の作業が残るかで重複と親 issue へ分かれる。"""
    body = SKILL.read_text(encoding="utf-8")
    note = plain(body[locate(body, "**重複の判定では、起点が同じことと同じ課題であることを分ける。**"):
                      locate(body, "**「やらない」の候補は")])
    assert "個別の作業が残る" in note
    assert "ルートコーズ" in note and "親 issue" in note


def test_cluster_separates_itself_from_duplication() -> None:
    """重複との境目は、原因を直した後に個別の作業が残るかである。"""
    text = plain(GROUPING.read_text(encoding="utf-8"))
    assert "原因を直しても各課題に個別の作業が残るならルートコーズ、残らないなら重複である" in text


def test_root_cause_has_one_condition() -> None:
    """条件は 1 つで、件数は条件ではない。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, "| 条件 | 確かめ方 | 欠けたときの行き先 |")
    assert len(rows) == 1
    assert "修正レイヤーが現象レイヤーと違う" in rows[0][0]
    assert "既存の判定のまま" in rows[0][2]
    text = plain(body)
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
    assert "3 行目が重複との境目である" in plain(body)


def test_cluster_keeps_the_child_issues_open() -> None:
    """子 issue を閉じない。結び付けはサブイシュー関係を既定にし、例外だけ本文の 1 行にする。"""
    body = GROUPING.read_text(encoding="utf-8")
    text = plain(body)
    assert "子 issue を閉じない" in text
    assert "固有の再現手順" in text and "個別の適用が残る" in text
    assert "サブイシュー関係" in text
    assert "sub_issue_id" in body and "データベースの ID" in text
    assert "子 1 件が持てる親は 1 件だけ" in text
    assert "サブイシューの API を持たないリポジトリ" in text


def test_cluster_shows_the_body_of_the_parent_issue() -> None:
    """親 issue に書く 4 つを挙げ、子 issue の中身は写さない。"""
    text = plain(GROUPING.read_text(encoding="utf-8"))
    for item in ("修正レイヤー", "採る手", "現象レイヤーと観測", "完了条件"):
        assert item in text, item
    assert "見出しの形は決めない" in text
    assert "子 issue の中身を写さない" in text
    assert "片方だけが古くなる" in text


def test_an_issue_may_join_two_clusters() -> None:
    """1 件が複数のクラスタへ属してよく、やり直しの見分けは親 issue の側から引く。"""
    text = plain(GROUPING.read_text(encoding="utf-8"))
    assert "1 件が複数のクラスタへ属してよい" in text
    assert "親 issue どうしが同じ箇所を直すなら、それは 1 つのクラスタである" in text
    assert "親 issue の側から引く" in text


def test_cluster_does_not_gate_on_size() -> None:
    """層がまたがっても判定は変わらず、採る手は判定の条件ではない。"""
    text = plain(GROUPING.read_text(encoding="utf-8"))
    assert "修正レイヤーが 2 つ以上にまたがっても、判定は変わらない" in text
    assert "マイルストーンの割り当てと実装計画" in text
    assert "手は判定の条件ではない" in text
    assert "当てはまるものを全部書く" in text


def test_parent_takes_the_highest_priority_in_the_cluster() -> None:
    """親 issue の重要度はクラスタの最高値を採り、重要度の規則を先に効かせてから最も早いものを採る。"""
    assert "クラスタの中で最も高いもの" in plain(GROUPING.read_text(encoding="utf-8"))
    assert links_to(GROUPING.read_text(encoding="utf-8"), "milestones.md", GROUPING.parent)
    part = plain(section(MILESTONES.read_text(encoding="utf-8"), "## 親 issue を割り当てる"))
    assert "重要度が「高い」" in part
    assert part.index("重要度が「高い」") < part.index("最も早いもの")
    assert "子 issue のマイルストーンは動かさない" in part


def test_upkeep_files_only_the_parent_issue() -> None:
    """「手入れ」は起票を含まず、例外は親 issue 1 つだけである。"""
    body = SKILL.read_text(encoding="utf-8")
    text = plain(body)
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
    part = plain(section((SKILLS / "out-of-scope" / "SKILL.md").read_text(encoding="utf-8"),
                         "## 蓄積した課題との境界"))
    assert "`issue-upkeep` の「やらない」" in part
    assert "`issue-upkeep` の「ルートコーズ」" in part


def test_problem_solving_separates_upstream_from_cluster() -> None:
    """「上流で直す」と棚卸の「ルートコーズ」の違いを、件数ではなく見る対象で書く。"""
    body = (SKILLS / "problem-solving" / "SKILL.md").read_text(encoding="utf-8")
    paragraph = next(p for p in body.split("\n\n") if "ルートコーズ" in p)
    assert links_to(paragraph, "../issue-upkeep/SKILL.md", SKILLS / "problem-solving"), paragraph
    assert "件数" not in paragraph and "複数" not in paragraph


def test_retrospective_declines_cluster_discovery() -> None:
    """振り返りはクラスタの発見を担わない。1 回の変更を見るためクラスタが見えない。"""
    text = plain((SKILLS / "retrospective" / "SKILL.md").read_text(encoding="utf-8"))
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
    text = plain(MILESTONES.read_text(encoding="utf-8"))
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
    text = plain(body)
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
    text = plain(body)
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
    text = plain(part)
    assert "起票は現象を見て書かれる" in text
    for word in ("まとめる", "整理する", "統一する", "揃える"):
        assert f"「{word}」" in text, word
    assert "何が直るのか" in text
    assert "まだ原因に届いていない" in text


# ---------- 段 3 の反映直前の照合 ----------

STAGE_3_GATE = "**`updated_at` が変わっていれば本文の要約値を見る。**"


@pytest.mark.parametrize(("path", "outcome"), [
    ("updated_at が不変", STAGE_3_GATE),
    ("updated_at は変わったが要約値は同じ", "同じならそのまま反映する"),
    ("要約値も変わった", "要約値も変わっていればその課題だけを段 2A へ戻し"),
])
def test_stage_3_reconciles_right_before_reflecting(path: str, outcome: str) -> None:
    """反映直前の照合を、`updated_at` と本文の要約値の 3 経路ごとに固定する。

    要約値を見るのは `updated_at` が変わったときだけで、不変ならそのまま反映へ進む。
    要約値が同じなら反映を続け、変わっていればその課題だけを段 2A へ戻す。
    """
    part = plain(section(SKILL.read_text(encoding="utf-8"), "### 段 3: 反映する"))
    assert "段 3 は反映の直前に照合する" in part
    gate = part.index(plain(STAGE_3_GATE))
    assert "要約値" not in part[:gate], "updated_at より先に要約値を見ている"
    assert plain(outcome) in part, path
    assert part.index(plain(outcome)) >= gate, path


# ---------- 外部への書き込みの制限 ----------


@pytest.mark.parametrize(("situation", "handling"), [
    ("応答が `Retry-After` を返す", "その秒数だけ待つ"),
    ("上限の回復時刻を返す", "その時刻まで待つ"),
    ("どちらも無い（作成の二次的な制限）", "間隔を倍にしながら待つ"),
    ("一定の回数を超えた",
     "部分的に終わった状態として止め、何が済んだかを報告へ残す"),
])
def test_rate_limit_branches_by_response(situation: str, handling: str) -> None:
    """GitHub のレート制限に当たったときの 4 つの状況ごとの待ち方・停止を固定する。

    待つ長さは応答から取り、固定の間隔で待たない。`Retry-After` は秒数、回復時刻は時刻、
    どちらも無ければ倍にしながら待ち、一定の回数を超えたら部分終了として止める。
    """
    part = section(SKILL.read_text(encoding="utf-8"), "## 外部への書き込みの制限")
    rows = table(part, "| 状況 | 待ち方 |")
    mapping = {flat(row[0]): flat(row[1]) for row in rows}
    assert flat(situation) in mapping, situation
    assert flat(handling) == mapping[flat(situation)]


def test_rate_limit_takes_the_wait_from_the_response() -> None:
    """待つ長さは応答から取り、固定の間隔で待たないことを明記している。"""
    part = plain(section(SKILL.read_text(encoding="utf-8"), "## 外部への書き込みの制限"))
    assert "待つ長さは応答から取る" in part
    assert "固定の間隔で待たない" in part


def test_rate_limit_reports_the_wait_count() -> None:
    """完了報告に、制限へ当たった回数と待った長さを残す行がある。"""
    rows = table(SKILL.read_text(encoding="utf-8"), "| 項目 | 何を書くか |")
    row = next(row for row in rows if row[0] == "待った回数")
    assert "外部への書き込みの制限に当たった回数" in row[1]
    assert "待った長さ" in row[1]


# ---------- 他のリポジトリで動くこと ----------


def test_other_repositories_define_fallback_behaviors() -> None:
    """マイルストーン・サブイシュー API・重要度ラベル・wontfix ラベル欠落時の振る舞いを固定する。

    前提にしてよいのは gh と issue だけであり、4 つの機能欠落時における
    フォールバック動作の分岐が定義されていることを検証する。
    """
    body = SKILL.read_text(encoding="utf-8")
    part = section(body, "## 他のリポジトリで動くこと")
    rows = table(part, "| 無いもの | 振る舞い |")
    mapping = {row[0].replace("`", ""): row[1] for row in rows}

    assert "マイルストーン" in mapping
    assert "段 1 の 3 つ目の経路と、扱うこと 2 を飛ばす" in mapping["マイルストーン"]

    assert "サブイシューの API" in mapping
    assert "子 issue の結び付けを、すべて子 issue の本文の 1 行で行う" in mapping["サブイシューの API"]

    assert "重要度ラベル" in mapping
    assert "gh label list" in mapping["重要度ラベル"]
    assert "説明が無ければ付け替えない" in mapping["重要度ラベル"]

    assert "wontfix にあたるラベル" in mapping
    assert "ラベルを付けずに閉じる" in mapping["wontfix にあたるラベル"]
    assert "理由と再燃の条件は本文に残る" in mapping["wontfix にあたるラベル"]


# ---------- 重要度と問いの形を変えるときの基準 ----------


def test_priority_change_branches_by_harm_observation() -> None:
    """重要度変更の 3 経路（実害観測追記で上げる／実害なし判明で下げる／説明なしラベルは維持）を固定する。

    重要度はラベルの説明の区分へ当てはめ、実害観測が本文へ追記されたら上げ、
    実害が起きない経路だと分かったら下げ、説明のないラベルへは付け替えない。
    """
    part = section(SKILL.read_text(encoding="utf-8"),
                   "### 重要度と問いの形を変えるときの基準")
    rows = table(part, "| 変えるもの | 基準 | 材料の在り処 |")
    row = next(row for row in rows if row[0] == "重要度")
    assert "実害の観測が変わったか" in row[1]
    assert "ラベルの説明" in row[2]

    text = plain(part)
    assert "重要度は、ラベルの説明が持つ区分へ当てはめる" in text
    assert "実害の観測が本文へ追記された課題は上げ" in text
    assert "実害が起きない経路だと分かった課題は下げ" in text
    assert "説明の無いラベルへは付け替えない" in text


def test_question_restructuring_branches_by_premise_and_gist() -> None:
    """問いの形変更の 2 経路（前提反転時のみ立て直す／主旨変更時は人へ返す）を固定する。

    問いを立て直すのは前提反転時のみで主旨や困りごとは変えず、
    主旨まで変わる場合は新起票判断として人へ返す。
    """
    part = section(SKILL.read_text(encoding="utf-8"),
                   "### 重要度と問いの形を変えるときの基準")
    rows = table(part, "| 変えるもの | 基準 | 材料の在り処 |")
    row = next(row for row in rows if row[0] == "問いの形")
    assert "前提が反転したか" in row[1]
    assert "本文が指す実物の状態" in row[2]

    text = plain(part)
    assert "問いを立て直すのは、前提が反転したときに限る" in text
    assert "立て直すのは問いの形であって、主旨ではない" in text
    assert "何が困るかは変えない" in text
    assert "主旨まで変わるなら、それは別の課題である" in text
    assert "元の課題を閉じて新しく起票する判断になるため、返す" in text


# ---------- 発見の瞬間の 3 択（out-of-scope の段 2） ----------

OUT_OF_SCOPE = SKILLS / "out-of-scope" / "SKILL.md"


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
    text = plain(section(OUT_OF_SCOPE.read_text(encoding="utf-8"), "### 3. 起票先を決める"))
    assert "「起票する」を選んだときだけ行う" in text
    assert "残る 2 つの判断には起票先が要らない" in text


def test_table_helper_does_not_pass_over_a_missing_heading() -> None:
    """見出し（表）が無い本文では table 補助が投げ、素通りしないことを確かめる。

    段 2 の表を消したときに固定テストが黙って通ってしまわないための番犬。
    """
    with pytest.raises(ValueError):
        table("見出しの無い本文\n", "| 判断 | 選ぶ条件 | 残すもの |")


# --- 並列の組（#541） --------------------------------------------------------
#
# **組は、マイルストーンへ課題を入れる時点で書く見込みである。** 確定した触る場所は
# 実行計画（`issue-plan-strategy`）が持ち、説明へ書き戻さない（設計の決定 10）。

GROUP_SECTION = "## 並列の組を説明へ書く"
GROUP_TABLE_HEADER = "| 組 | 課題 | 触る場所の見込み | 依存 |"


def group_section() -> str:
    return section(MILESTONES.read_text(encoding="utf-8"), GROUP_SECTION)


def test_milestones_have_a_section_for_the_parallel_groups() -> None:
    """AC20: マイルストーンへ課題を入れるときに、その課題の組を説明へ書く。"""
    part = group_section()
    assert contains(part, "課題をマイルストーンへ入れるときに、その課題の組を説明へ書く")
    assert "### 並列の組（見込み）" in part, "説明へ置く見出しの形が無い"
    assert GROUP_TABLE_HEADER in part


def test_the_group_row_is_written_when_an_issue_enters_a_milestone() -> None:
    """AC20: 新しく作る・既存へ足す・直近へ移すの 3 つが同じ時点として扱われる。"""
    rows = table(group_section(), "| 時点 | 行うこと |")
    moments = [row[0] for row in rows]
    assert any("入れる" in m and "新しく作る" in m and "足す" in m and "移す" in m
               for m in moments), moments
    assert any("別のマイルストーンへ移す" in m for m in moments), moments
    assert any("閉じた" in m for m in moments), moments


def test_the_group_is_copied_from_what_stage_2a_already_records() -> None:
    """AC21: 触る場所は段 2A の修正レイヤーから、依存は依存する課題の番号から写す。"""
    part = plain(group_section())
    assert "修正レイヤー" in part
    assert "依存する課題の番号" in part
    assert "新しく調べる項目を増やさない" in part


def test_stage_2a_records_the_same_six_items() -> None:
    """AC21: 段 2A の控える項目は増えない。

    組のために段 2A へ項目を足すと、棚卸の 1 課題あたりの費用が上がる。
    """
    rows = table(SKILL.read_text(encoding="utf-8"), "| 控える項目 | 何に使うか |")
    assert len(rows) == 6, [row[0] for row in rows]
    assert rows[-1][0] == "修正レイヤー"


def test_groups_are_parallel_between_and_sequential_within() -> None:
    """AC22: 組の間は並列、組の中は 1 本へ束ねるか順に進める見込みである。"""
    part = plain(group_section())
    assert "組の間は並列にできる見込み" in part
    assert "組の中は同じ Pull Request へ束ねるか順に進める見込み" in part


def test_the_group_is_only_an_estimate() -> None:
    """AC22: 確定は設計の後に実行計画が持ち、説明へ書き戻さない。"""
    part = plain(group_section())
    assert "見込みであり、確定は設計の後に実行計画が持つ" in part
    assert "書き戻さない" in part


def test_the_group_does_not_change_the_milestone_structure() -> None:
    """AC24: 組のために既存のマイルストーンを分けたり束ね直したりしない。"""
    part = plain(group_section())
    assert "既存のマイルストーンを分けたり束ね直したりしない" in part
    assert "組の番号は詰めない" in part


def test_an_empty_group_row_is_removed_without_renumbering_the_remaining_groups() -> None:
    """現状固定: 移動で 0 件になった組は消すが、残る組の番号は詰めない。"""
    part = group_section()
    column_rows = table(part, "| 列 | 値 | 写す元 |")
    group_column = next(row for row in column_rows if row[0] == "組")
    assert group_column[1] == "1 から始まる連番。説明の中で一意"

    timing_rows = table(part, "| 時点 | 行うこと |")
    moving = next(row for row in timing_rows if row[0] == "課題を別のマイルストーンへ移す")
    assert plain(moving[1]) == (
        "元の説明の表から番号を消す。課題が 0 件になった組は行を消し、"
        "組の番号は詰めない"
    )


def test_milestones_without_a_group_table_are_not_rewritten_at_once() -> None:
    """決定 11: 既存の説明を一括で書き直さず、足した課題の行だけを載せる。"""
    part = plain(group_section())
    assert "一括で書き直さない" in part
    assert "表が無ければ見出しと表を作る" in part


def test_group_partition_rule_by_fix_layer() -> None:
    """現状固定: 同じ修正レイヤーの課題は同じ組、違えば別組にする規則を固定する。"""
    part = plain(group_section())
    assert "同じ修正レイヤーの課題は同じ組、違えば別の組にする" in part


@pytest.mark.parametrize(("timing_pattern", "expected_actions"), [
    ("課題をマイルストーンへ入れる", [
        "修正レイヤーが既存の組と同じなら、その組の「課題」へ番号を足す",
        "違えば組を 1 つ足す",
        "表が無ければ見出しと表を作る",
    ]),
    ("別のマイルストーンへ移す", [
        "元の説明の表から番号を消す",
        "課題が 0 件になった組は行を消し",
        "組の番号は詰めない",
    ]),
    ("課題が閉じた", [
        "何もしない",
        "実行計画が状態を持つ",
    ]),
])
def test_group_timing_branches_and_actions(
        timing_pattern: str, expected_actions: list[str]) -> None:
    """現状固定: マイルストーンへの追加（一致・不一致）、移動、完了の各分岐における動作対応を固定する。"""
    rows = table(group_section(), "| 時点 | 行うこと |")
    row = next(r for r in rows if timing_pattern in r[0])
    action = plain(row[1])
    for expected in expected_actions:
        assert expected in action, f"{timing_pattern} の行うことに '{expected}' が含まれていない"
