"""`issue-upkeep` 本体（`SKILL.md`）の構成と配線（#331 / #712 / #713）。

**判定の値は 3 箇所（手順の表・自動で反映してよい変更の表・報告）で同じ並びを持つ。**
片方だけが増えると、反映の側が知らない判定を受け取る。

参照（no-work / milestones / grouping）と Skill 間契約・manifest のテストは、変更理由が
異なるため別モジュールへ分けた（`test_no_work.py` / `test_milestones.py` /
`test_grouping.py` / `test_issue_upkeep_integration.py`）。共有の補助とパスは `_helpers.py`。
"""
from __future__ import annotations

import re

import pytest

from _helpers import (
    GROUPING,
    SKILL,
    STAGE_3_GATE,
    VERDICTS,
    flat,
    section,
    table,
)


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


def test_closing_and_not_planned_are_separate_verdicts() -> None:
    """「やらない」と「閉じてよい」を別の判定として区別する。"""
    from _helpers import NO_WORK
    body = SKILL.read_text(encoding="utf-8")
    assert "**「閉じてよい」とは\n別である。**" in NO_WORK.read_text(encoding="utf-8")
    assert "| 閉じてよい |" in body
    assert "| **やらない** |" in body


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
    assert "段 1 で対象が 0 件なら、そこで飛ばす" in flat(procedure)
    target_report = next(row for row in report_rows if row[0] == "対象")
    assert "0 件なら飛ばしたことと理由" in target_report[1]


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
    text = flat(part)
    assert "全体に影響する変更では、触った領域では足りない" in text
    assert "次のいずれかに当たるときは対象をopen の全件へ広げる（`--all`）" in text


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
    assert "判断の材料が本文の外にあるかどうか" in flat(part)
    assert "決まらないものだけを返す" in flat(part)


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


# ---------- 段 3 の反映直前の照合 ----------


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
    part = flat(section(SKILL.read_text(encoding="utf-8"), "### 段 3: 反映する"))
    assert "段 3 は反映の直前に照合する" in part
    gate = part.index(STAGE_3_GATE)
    assert "要約値" not in part[:gate], "updated_at より先に要約値を見ている"
    assert outcome in part, path
    assert part.index(outcome) >= gate, path


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
    part = flat(section(SKILL.read_text(encoding="utf-8"), "## 外部への書き込みの制限"))
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

    text = flat(part)
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

    text = flat(part)
    assert "問いを立て直すのは、前提が反転したときに限る" in text
    assert "立て直すのは問いの形であって、主旨ではない" in text
    assert "何が困るかは変えない" in text
    assert "主旨まで変わるなら、それは別の課題である" in text
    assert "元の課題を閉じて新しく起票する判断になるため、返す" in text
