"""`issue-upkeep` の構成と配線（#331 / #712 / #713）。

**判定の値は 3 箇所（手順の表・自動で反映してよい変更の表・報告）で同じ並びを持つ。**
片方だけが増えると、反映の側が知らない判定を受け取る。

検査は本文の語順ではなく、**表の行・並び・リンク先・コマンドの引数**から読む。言い換えや節の
再配置で落ちないようにするためで、文言そのものが契約になる箇所（人へ返す安全弁と、
書き戻し・写しを禁じる規則）だけ、文を残す。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

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
ROUTE_HEADER = "| 経路 | 取り方 | 拾えるもの |"
RECORDED_HEADER = "| 控える項目 | 何に使うか |"
DISPOSITION_HEADER = "| 変更 | 自動で反映してよいか |"
REPORT_HEADER = "| 項目 | 何を書くか |"
TERM_HEADER = "| 語 | この文書での意味 |"
FALLBACK_HEADER = "| 無いもの | 振る舞い |"
CRITERIA_HEADER = "| 変えるもの | 基準 | 材料の在り処 |"

# 変更操作の 3 区分（自動で反映してよいか）。
AUTOMATIC = "よい"
AFTER_APPROVAL = "一括で提示し、承認を得てから行う"
RETURNED = "返す"

# grouping.md の表。
CONDITION_HEADER = "| 条件 | 確かめ方 | 欠けたときの行き先 |"
STAGE_3_HEADER = "| 同じ修正レイヤーを指す件数 | 直しても個別の作業が残るか | 対応 |"
MOVES_HEADER = "| 手 | いつ選ぶか | 対応する手法（`refactoring` の呼び名） |"


# ---------- Markdown の構造を読む補助 ----------

def flat(text: str) -> str:
    """折り返しの改行を除く。日本語の文は改行の位置で語が割れるため、照合の前に繋ぐ。"""
    return text.replace("\n", "")


def plain(text: str) -> str:
    """折り返しの改行に加えて強調の印を除く。太字の付け外しで同じ契約が落ちないようにする。"""
    return text.replace("\n", "").replace("**", "")


def links_to(text: str, target: str, base: pathlib.Path = SKILL_DIR) -> bool:
    """本文のリンクが `target` を指し、その先のファイルが実在するか。"""
    found = [link.split("#", 1)[0] for link in re.findall(r"\]\(([^)]+)\)", text)]
    return target in found and (base / target).is_file()


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。コードブロックの中の `#` は見出しと数えない。"""
    level = heading.split(" ", 1)[0]
    lines = text[text.index(heading + "\n"):].split("\n")
    kept, fenced = [lines[0]], False
    for line in lines[1:]:
        fenced ^= line.startswith("```")
        found = re.match(r"(#+) ", line)
        if not fenced and found and len(found.group(1)) <= len(level):
            break
        kept.append(line)
    return "\n".join(kept)


def table(text: str, header: str) -> list[list[str]]:
    """見出し行で始まる表の、データ行のセルを返す。太字の印は外す。"""
    block = text[text.index(header):]
    block = block[:block.index("\n\n")] if "\n\n" in block else block
    rows = [line for line in block.split("\n")[2:] if line.startswith("|")]
    return [[plain(cell).strip() for cell in row.strip("|").split("|")]
            for row in rows]


def column(rows: list[list[str]], index: int) -> list[str]:
    """表の 1 列を、行の順に返す。並びの契約はこれで読む。"""
    return [row[index] for row in rows]


def mapping(rows: list[list[str]]) -> dict[str, list[str]]:
    """先頭のセルを鍵に、残りのセルを引く。判定・状況・区分ごとの遷移先はこれで読む。"""
    return {row[0]: row[1:] for row in rows}


def pairs(rows: list[list[str]]) -> dict[str, str]:
    """2 列の表を辞書にする。"""
    return {row[0]: row[1] for row in rows}


def sentence_with(text: str, fragment: str) -> str:
    """`fragment` を含む最初の文。人へ返す・要判断へ倒すといった遷移が、どの条件に付いて
    いるかを読む。語順ではなく、同じ文に条件と遷移先が並ぶことだけを見る。"""
    for sentence in plain(text).split("。"):
        if fragment in sentence:
            return sentence
    raise ValueError(f"{fragment!r} を含む文が無い")


def bold_claims(text: str) -> list[str]:
    """太字で書かれた主張を、出てくる順に返す。"""
    return [flat(claim) for claim in re.findall(r"\*\*(.+?)\*\*", text, re.DOTALL)]


def slash_list(cell: str) -> list[str]:
    """`a / b / c` の形で並べた項目を分ける。折り返しで `/` の前後の空白が落ちても分ける。"""
    return [item.strip("。 ") for item in re.split(r"\s*/\s*", plain(cell)) if item.strip()]


def bash_blocks(text: str) -> list[str]:
    """本文の bash の例を、出てくる順に返す。"""
    return re.findall(r"```bash\n(.*?)```", text, re.DOTALL)


def jq_of(command: str) -> str:
    """コマンドが `--jq` に渡している式。"""
    return re.search(r"--jq '([^']*)'", command).group(1)


def run_jq(expression: str, data: object) -> list[str]:
    """jq の式を実際に流し、出力の行を返す。"""
    done = subprocess.run(["jq", "-r", expression], input=json.dumps(data),
                          capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def frontmatter(text: str) -> dict[str, str]:
    """先頭の frontmatter のうち、1 行で値を持つ項目。"""
    head = text.split("---\n")[1]
    return dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)


# ---------- 文書ごとの取り出し ----------

def skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def verdicts() -> dict[str, list[str]]:
    """手順の表の判定。値は [選ぶ条件, 段 3 での対応]。"""
    return mapping(table(skill(), VERDICT_HEADER))


def dispositions() -> dict[str, str]:
    """変更操作ごとの、自動で反映してよいか（3 区分のどれか）。"""
    return pairs(table(skill(), DISPOSITION_HEADER))


def report_items() -> dict[str, str]:
    return pairs(table(skill(), REPORT_HEADER))


def terms() -> dict[str, str]:
    return pairs(table(skill(), TERM_HEADER))


def fallbacks() -> dict[str, str]:
    """他のリポジトリで無いものごとの振る舞い。"""
    return {name.replace("`", ""): behavior
            for name, behavior in pairs(table(skill(), FALLBACK_HEADER)).items()}


def stage_1() -> str:
    return section(skill(), "### 段 1: 対象を選ぶ")


def stage_1_query() -> str:
    """段 1 がマイルストーンの付いていない課題を拾うコマンド。"""
    return next(block for block in bash_blocks(stage_1()) if "gh issue list" in block)


def unassigned_reasons(text: str) -> list[str]:
    """未設定のまま残った理由の区分。報告の側と決め方の側で同じ並びを持つ。"""
    return slash_list(re.search(r"理由（([^）]+)）", plain(text)).group(1))


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
    assert list(verdicts()) == VERDICTS


def test_no_work_is_reachable_from_the_verdict_table() -> None:
    """「やらない」の行から参照を指す。判断の基準はそちらにある。"""
    condition, _ = verdicts()["やらない"]
    assert links_to(condition, "references/no-work.md"), condition


def test_no_work_has_two_necessary_conditions() -> None:
    """2 つの必要条件と、欠けたときの行き先が表で示されている。

    条件 2 の行き先を「重複」に固定すると、同じ修正レイヤーを指す別の課題が重複として閉じられる。
    行き先は 3 つに分け、「要判断」へ倒す文は見積りである条件 1 だけに掛ける。
    """
    body = NO_WORK.read_text(encoding="utf-8")
    rows = table(section(body, "## 2 つの必要条件"),
                 "| # | 条件 | 確かめ方 | 欠けたときの行き先 |")
    assert column(rows, 1) == ["抱える費用が、直す費用を下回る", "同じ原因の他の課題へ寄せられない"]
    _, _, check, destination = rows[1]
    assert check.index("重複") < check.index("ルートコーズ")
    for outcome in ("重複", "ルートコーズ", "寄せ先が無く、条件は欠けていない"):
        assert outcome in destination, outcome
    assert links_to(destination, "grouping.md", NO_WORK.parent)
    # 要判断へ倒す文は見積りである条件 1 だけに掛かる。安全弁なので文言を残す。
    assert "どちらも見積りである" not in flat(body)
    assert "条件 1 は見積りである" in plain(body)


def test_no_work_shows_both_kinds_of_reactivation_condition() -> None:
    """再燃の条件は観測できる形で書く。観測できない書き方の例も示す。"""
    rows = table(section(NO_WORK.read_text(encoding="utf-8"), "## 再燃の条件は、観測できる形で書く"),
                 "| 書き方 | 例 |")
    assert column(rows, 0) == ["観測できる", "観測できない"]
    assert all(slash_list(examples) for examples in column(rows, 1))


def test_closing_and_not_planned_are_separate_verdicts() -> None:
    """「やらない」と「閉じてよい」を別の判定として区別する。閉じ方が違う。"""
    found = verdicts()
    assert "wontfix" in found["やらない"][1]
    assert "wontfix" not in found["閉じてよい"][1]
    assert "「閉じてよい」" in plain(NO_WORK.read_text(encoding="utf-8"))


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
    assert mapping(rows)[priority] == [holding_cost, candidate]


def test_no_work_high_priority_requires_frequency_measurement_or_needs_judgement() -> None:
    """重要度「高い」を候補にするには頻度の実測が必要で、示せなければ要判断へ倒す規則を固定する。"""
    part = section(NO_WORK.read_text(encoding="utf-8"),
                   "## 重要度が、抱える費用の目安になる")
    rows = table(part, "| 重要度 | 抱える費用 | 候補になるか |")
    assert "実測で示す" in mapping(rows)["高い（実害・安全機構の欠落）"][1]
    assert "示せなければ" in sentence_with(part, "要判断")


def test_milestones_reflect_only_the_earlier_direction() -> None:
    """早める方向だけを自動で反映する。自動で動かす状況は 1 つで、残りは要判断か何もしない。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 既に設定されているものを振り直す")
    responses = pairs(table(part, "| 状況 | 対応 |"))
    automatic = [situation for situation, response in responses.items()
                 if response != "何もしない" and not response.startswith("要判断")]
    deferred = [situation for situation, response in responses.items()
                if response.startswith("要判断")]
    assert automatic == ["重要度が高いのに、直近でないマイルストーンにある"]
    assert deferred == ["重要度が低いのに、直近のマイルストーンにある"]


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
    assert pairs(table(part, "| 状況 | 対応 |"))[situation] == response


def test_existing_milestones_explain_why_only_earlier_moves_are_automatic() -> None:
    """遅らせる判断は価値の判断なので自動化せず、「やらない」の必要条件へ委ねて参照で指す。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 既に設定されているものを振り直す")
    assert "「やらない」" in sentence_with(part, "遅らせる判断")
    assert links_to(part, "no-work.md", MILESTONES.parent)


def test_milestones_are_not_created_for_a_single_issue() -> None:
    """1 件しか残らないときは作らず未設定に残す。その理由は決め方と報告で同じ区分を持つ。"""
    in_milestones = unassigned_reasons(
        section(MILESTONES.read_text(encoding="utf-8"), "## 報告へ残す"))
    assert "1 件しか残らない" in in_milestones
    assert unassigned_reasons(report_items()["未設定のまま残った課題"]) == in_milestones


def test_new_milestone_is_appended_with_the_next_number() -> None:
    """通常の追加は既存の順序を動かさず、過去最大の着手順序の次を採る。例がその形を持つ。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 新しく作るとき")
    name = mapping(table(part, "| 決めること | 決め方 |"))["名前"][0]
    before, after = re.search(r"`(\d+)` があれば `(\d+)`", name).groups()
    assert int(after) == int(before) + 1


def test_inserting_a_new_milestone_between_existing_ones_needs_judgement() -> None:
    """既存の間への差し込みは他の課題の着手順序を変えるため、要判断へ倒す。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 新しく作るとき")
    assert "差し込む" in sentence_with(part, "要判断")


def test_new_milestone_number_is_not_reused_after_closing() -> None:
    """閉じたマイルストーンを含む過去最大の着手順序から、再利用しない連番を決める。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 新しく作るとき")
    name = mapping(table(part, "| 決めること | 決め方 |"))["名前"][0]
    assert "これまでに付けた最大の次" in name
    assert "再利用しない" in name
    assert "閉じたマイルストーンは版数へ改名される" in name
    assert "着手の順序: N 番目" in name


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
    assert mapping(rows)[priority][1] == when_no_matching_subject


def test_unassigned_issue_with_multiple_matching_subjects_needs_judgement() -> None:
    """主題が複数に当てはまるときは、自動で割り当てず要判断へ倒す。"""
    part = section(MILESTONES.read_text(encoding="utf-8"),
                   "## 設定されていない課題を割り当てる")
    assert "主題が複数に当てはまる" in sentence_with(part, "要判断")


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
    procedure = section(skill(), "## 手順")
    assert column(table(procedure, ROUTE_HEADER), 0) == [
        "機械の候補", "担当が足す", "マイルストーン", "親 issue",
    ]
    assert "飛ばす" in sentence_with(procedure, "0 件")
    assert "0 件" in report_items()["対象"]


def test_the_target_is_decided_only_by_the_milestone() -> None:
    """段 1 の 3 つ目の経路が、マイルストーンの有無だけで対象を決める。

    絞ると、未設定のまま溜まる課題を誰も見ないことになる。
    """
    how, _ = mapping(table(stage_1(), ROUTE_HEADER))["マイルストーン"]
    assert "マイルストーンの付いていない open の課題すべて" in how


def test_the_target_query_filters_only_by_milestone() -> None:
    """未設定の課題を拾う例が、マイルストーンの有無だけで絞っていること。"""
    command = stage_1_query()
    assert "--author" not in command, "投稿者で絞る例になっている"
    assert "created:" not in command, "起票の時期で絞る例になっている"
    assert "select(.milestone == null)" in jq_of(command)


def test_the_target_query_keeps_only_null_milestone_issues() -> None:
    """段 1 の jq を実際に流し、milestone が null の課題だけを残すことを固定する。

    現状固定: 期待値の根拠は仕様ではなく、抽出手順が返す jq 式の振る舞いである。
    文字列の存在（別の現状固定テスト）ではなく、分岐そのものを流して固定する。
    """
    issues = [
        {"number": 10, "title": "foo", "milestone": None},
        {"number": 20, "title": "bar", "milestone": {"title": "m1"}},
        {"number": 30, "title": "baz baz", "milestone": None},
    ]
    lines = run_jq(jq_of(stage_1_query()), issues)

    # branch: milestone が null の 2 件だけが残り、milestone を持つ課題は落ちる。
    assert lines == ["#10 foo", "#30 baz baz"]
    # 整形の形: 各行は "#<number> <title>" である（表示文言ではなく形として見る）。
    for line, issue in zip(lines, [issues[0], issues[2]]):
        assert line.startswith(f"#{issue['number']} ")
        assert line == f"#{issue['number']} {issue['title']}"


def test_stage_1_expands_to_all_open_issues_on_pervasive_changes() -> None:
    """全体に影響する変更における open 全件への拡張（--all）の 4 条件を固定する。

    ディレクトリの移動・統合、対応する実行環境の増減、ブランチ戦略の変更、
    識別子の一括改名のいずれかに当たるときは、対象を open 全件（--all）へ広げる。
    """
    part = stage_1()
    assert column(table(part, "| 全体に影響する変更 | 例 |"), 0) == [
        "ディレクトリの移動・統合",
        "対応する実行環境の増減",
        "ブランチ戦略の変更",
        "識別子の一括改名",
    ]
    assert "全件" in sentence_with(part, "`--all`")
    assert "--all" in frontmatter(skill())["argument-hint"]


# ---------- ルートコーズの判定（#712） ----------

def test_cluster_is_reachable_from_the_verdict_table() -> None:
    """「ルートコーズ」の行から参照を指す。条件と書き方はそちらにある。"""
    condition, _ = verdicts()["ルートコーズ"]
    assert links_to(condition, "references/grouping.md"), condition


def test_creating_the_parent_needs_approval() -> None:
    """親 issue の起票と子 issue への書き込みは、一括で提示して承認を得てから行う。

    どちらも外部から見える場所への書き込みである。
    """
    found = dispositions()
    create = [change for change in found if "親 issue を作る" in change]
    link = [change for change in found if "本文へ 1 行を足す" in change]
    assert create and link
    for change in create + link:
        assert found[change] == AFTER_APPROVAL, change


def test_every_upkeep_change_has_one_of_the_three_dispositions() -> None:
    """変更操作は、自動反映・承認後に反映・人へ返す、の 3 区分だけを持つ。"""
    assert set(dispositions().values()) == {AUTOMATIC, AFTER_APPROVAL, RETURNED}


@pytest.mark.parametrize("change", [
    "起票の意図が現在も要るかを判断できない",
    "正しい読み方が複数ある",
])
def test_upkeep_returns_ambiguous_changes_to_a_human(change: str) -> None:
    """本文の外に判断材料が要る 2 つの安全弁は、自動反映せず人へ返す。"""
    assert dispositions()[change] == RETURNED


def test_upkeep_handles_four_things() -> None:
    """扱うことが 4 つになり、4 つ目が根本原因の場所で直す判断である。"""
    body = skill()
    assert "## 扱う 3 つ" not in body
    part = section(body, "## 扱う 4 つ")
    rows = table(part, "| # | 扱うこと | 決めること |")
    assert column(rows, 0) == ["1", "2", "3", "4"]
    assert "根本原因の場所で直す" in rows[3][2]
    assert links_to(part, "references/grouping.md")
    assert f"{len(verdicts())} つの区分" in terms()["判定"]


def test_stage_1_picks_up_children_of_a_closed_parent() -> None:
    """閉じた親 issue の子 issue を拾う経路がある。

    子 issue は閉じないため親が閉じた後も open のまま残り、親と別のマイルストーンにいると
    ほかの 3 経路では拾えない。
    """
    routes = mapping(table(stage_1(), ROUTE_HEADER))
    assert len(routes) == 4
    how, what = routes["親 issue"]
    assert "/sub_issues" in how and "本文で親 issue を指す" in how
    assert "open のまま残る子 issue" in what


def test_stage_2a_records_the_shared_cause() -> None:
    """段 2A は現象レイヤーと修正レイヤーを控え、クラスタは決めない。"""
    part = section(skill(), "### 段 2A: 課題ごとに調べる")
    checks = slash_list(next(claim for claim in bold_claims(part) if "/" in claim))
    assert len(checks) == 6
    assert "修正レイヤーが現象レイヤーと違うか" in checks
    recorded = column(table(part, RECORDED_HEADER), 0)
    assert {"現象レイヤー", "修正レイヤー"} <= set(recorded)
    assert not any("クラスタ" in item for item in recorded)


def test_stage_2b_matches_issues_by_shared_cause() -> None:
    """段 2B は同じ原因を持つクラスタを突き合わせ、書き換えを揃える行とは分ける。"""
    part = section(skill(), "### 段 2B: 全体で突き合わせて判定を確定する")
    decisions = pairs(table(part, "| 突き合わせるもの | 何を決めるか |"))
    names = list(decisions)
    assert names.index("重複と判定された組") < names.index("同じ原因を持つクラスタ")
    assert "親 issue" in decisions["同じ原因を持つクラスタ"]
    assert "言い回し" in decisions["同じ前提の変化を受けた課題群"]


def test_stage_2b_note_branches_to_cluster() -> None:
    """起点が同じ組は、直した後に個別の作業が残るかで重複と親 issue へ分かれる。"""
    found = verdicts()
    assert "段 2B" in found["重複"][1]
    assert "2 件以上なら親 issue" in found["ルートコーズ"][1]
    part = section(skill(), "### 段 2B: 全体で突き合わせて判定を確定する")
    assert "個別の作業が残る" in sentence_with(part, "重複ではない")


def test_cluster_separates_itself_from_duplication() -> None:
    """重複との境目は、原因を直した後に個別の作業が残るかである。"""
    rows = table(GROUPING.read_text(encoding="utf-8"), STAGE_3_HEADER)
    outcomes = {(count, remains): outcome for count, remains, outcome in rows}
    assert "親 issue" in outcomes[("2 件以上", "残る")]
    assert "重複" in outcomes[("2 件以上", "残らない")]


def test_root_cause_has_one_condition() -> None:
    """条件は 1 つで、件数は条件ではない。件数で分かれるのは段 3 の対応だけである。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, CONDITION_HEADER)
    assert len(rows) == 1
    condition, _, fallback = rows[0]
    assert "修正レイヤーが現象レイヤーと違う" in condition
    assert "件数" not in condition
    assert "既存の判定のまま" in fallback
    assert table(body, STAGE_3_HEADER)


def test_stage_3_has_three_outcomes() -> None:
    """段 3 の対応は、件数と個別の作業の有無で 3 つに分かれ、3 行目が重複との境目である。"""
    rows = table(GROUPING.read_text(encoding="utf-8"), STAGE_3_HEADER)
    assert [(count, remains) for count, remains, _ in rows] == [
        ("1 件", "—"), ("2 件以上", "残る"), ("2 件以上", "残らない"),
    ]
    for (_, _, outcome), key in zip(rows, ("本文へ", "親 issue", "重複")):
        assert key in outcome, outcome


def test_cluster_keeps_the_child_issues_open() -> None:
    """子 issue を閉じない。結び付けはサブイシュー関係を既定にし、番号ではなく ID を渡す。"""
    body = GROUPING.read_text(encoding="utf-8")
    assert "閉じない" in terms()["子 issue"]
    assert not any("閉じ" in outcome for outcome in column(table(body, STAGE_3_HEADER), 2))
    command = bash_blocks(section(body, "### 結び付け方"))[0]
    assert jq_of(command) == ".id"
    assert "/sub_issues" in command and "sub_issue_id=" in command


def test_cluster_shows_the_body_of_the_parent_issue() -> None:
    """親 issue に書く 4 つを挙げ、子 issue の中身は写さない。"""
    text = plain(GROUPING.read_text(encoding="utf-8"))
    for item in ("修正レイヤー", "採る手", "現象レイヤーと観測", "完了条件"):
        assert item in text, item
    assert "見出しの形は決めない" in text
    assert "子 issue の中身を写さない" in text


def test_an_issue_may_join_two_clusters() -> None:
    """1 件が複数のクラスタへ属してよく、親 issue どうしが同じ箇所を直すなら 1 つにする。"""
    part = section(GROUPING.read_text(encoding="utf-8"), "### 1 件が複数のクラスタへ属してよい")
    assert "1 つのクラスタ" in sentence_with(part, "同じ箇所を直す")


def test_cluster_does_not_gate_on_size() -> None:
    """層がまたがっても判定は変わらず、採る手は判定の条件ではない。"""
    body = GROUPING.read_text(encoding="utf-8")
    condition = table(body, CONDITION_HEADER)[0][0]
    moves = column(table(body, MOVES_HEADER), 0)
    assert not any(move in condition for move in moves)
    assert "判定は変わらない" in sentence_with(body, "2 つ以上にまたがっても")
    assert "全部書く" in sentence_with(section(body, "## 採る手"), "1 つに決まらないとき")


def test_parent_takes_the_highest_priority_in_the_cluster() -> None:
    """親 issue の重要度はクラスタの最高値を採り、重要度の規則を先に効かせてから最も早いものを採る。"""
    grouping = GROUPING.read_text(encoding="utf-8")
    assert "クラスタの中で最も高いもの" in plain(grouping)
    assert links_to(grouping, "milestones.md", GROUPING.parent)
    part = plain(section(MILESTONES.read_text(encoding="utf-8"), "## 親 issue を割り当てる"))
    assert "重要度が「高い」" in part
    assert part.index("重要度が「高い」") < part.index("最も早いもの")
    assert "子 issue のマイルストーンは動かさない" in part


def test_upkeep_files_only_the_parent_issue() -> None:
    """「手入れ」は起票を含まず、例外は親 issue 1 つだけである。起票は承認を経る。"""
    assert "起票は親 issue に限って含む" in terms()["手入れ"]
    assert dispositions()["親 issue を作る"] == AFTER_APPROVAL


def test_the_report_counts_the_clusters() -> None:
    """完了報告は 8 つの判定の内訳と、処理したクラスタの数を持つ。"""
    report = report_items()
    assert f"{len(VERDICTS)} つの判定" in report["判定の内訳"]
    assert "処理したクラスタの数" in report["クラスタ"]


# ---------- 判断の担い手（#713） ----------

def test_the_boundary_table_covers_both_judgements() -> None:
    """価値と構造の判断 × 発見の瞬間と溜まった課題の表を、この Skill が正本として持つ。"""
    rows = table(skill(), "| 判断 | 発見の瞬間 | 溜まった課題 |")
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
    body = skill()
    names = list(terms())
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
    body = MILESTONES.read_text(encoding="utf-8")
    text = plain(body)
    assert "版数が最も小さい" not in text
    assert "版数の順序" not in text
    name = mapping(table(section(body, "## 新しく作るとき"), "| 決めること | 決め方 |"))["名前"][0]
    assert name.startswith("`<2 桁の連番> <主題>`")
    assigning = section(body, "## 設定されていない課題を割り当てる")
    assert "連番が最も小さい" in sentence_with(assigning, "直近のマイルストーンは")
    assert "説明を採る" in sentence_with(assigning, "説明が連番と別の")
    reordering = section(body, "## 順序を直すとき")
    assert "連番の順序と食い違うとき" in plain(reordering)
    assert "後の連番にあるなら" in plain(reordering)


# ---------- 上位の分解 ----------

def test_grouping_separates_the_two_layers() -> None:
    """現象レイヤーと修正レイヤーを分けて書き、さかのぼる段数を決めない。"""
    body = GROUPING.read_text(encoding="utf-8")
    part = section(body, "## 修正レイヤーの決め方")
    phenomena = column(table(part, "| 現象レイヤーの例 | 修正レイヤーの例 |"), 0)
    for example in ("各コントローラ", "各サブクラス", "移譲している側", "各スクリプト"):
        assert example in phenomena, example
    text = plain(part)
    assert "さかのぼる段数を決めない" in text
    assert "そこを直せば、現象レイヤーの各所が同じ形で直るか" in text
    assert "修正レイヤーが現象レイヤーと同じこともある" in text
    assert "既存の判定のまま" in sentence_with(part, "名指しできない")


def test_grouping_lists_five_moves() -> None:
    """採る手は 5 つに限り、呼び名は `refactoring` の語彙から採る。"""
    body = GROUPING.read_text(encoding="utf-8")
    rows = table(body, MOVES_HEADER)
    assert column(rows, 0) == ["移動", "統合", "新設", "向きの修正", "分離"]
    names = re.findall(r"`([a-z_]+)`", " ".join(column(rows, 2)))
    assert len(names) >= 6
    vocabulary = VOCABULARY.read_text(encoding="utf-8")
    for name in names:
        assert f"| `{name}` |" in vocabulary, name
    assert links_to(body, "../../refactoring/references/vocabulary.md", GROUPING.parent)


# ---------- 判断を人へ返さない・書式を持たない ----------

def test_cluster_never_defers_to_a_human() -> None:
    """ルートコーズの分岐は段 2A の控えだけで決まるため、「要判断」へ倒さない。"""
    body = GROUPING.read_text(encoding="utf-8")
    assert "要判断" not in body
    assert "既存の判定のまま" in table(body, CONDITION_HEADER)[0][2]
    outcomes = column(table(body, STAGE_3_HEADER), 2)
    for outcome, key in zip(outcomes, ("本文へ", "親 issue を 1 件つくり", "重複として正本へ寄せる")):
        assert key in outcome, outcome


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
    他の担当の変更を消さないための安全弁なので、順序と文言を残す。
    """
    part = plain(section(skill(), "### 段 3: 反映する"))
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
    part = section(skill(), "## 外部への書き込みの制限")
    assert pairs(table(part, "| 状況 | 待ち方 |"))[situation] == handling


def test_rate_limit_takes_the_wait_from_the_response() -> None:
    """待ち方の表に固定の秒数が無く、止める行以外はどれも応答から取った長さで待つ。"""
    part = section(skill(), "## 外部への書き込みの制限")
    waits = pairs(table(part, "| 状況 | 待ち方 |"))
    assert not any(re.search(r"\d+ ?秒", wait) for wait in waits.values())
    assert all("待つ" in wait for wait in list(waits.values())[:-1])


def test_rate_limit_reports_the_wait_count() -> None:
    """完了報告に、制限へ当たった回数と待った長さを残す行がある。"""
    row = report_items()["待った回数"]
    assert "外部への書き込みの制限に当たった回数" in row
    assert "待った長さ" in row


# ---------- 他のリポジトリで動くこと ----------


def test_other_repositories_define_fallback_behaviors() -> None:
    """マイルストーン・サブイシュー API・重要度ラベル・wontfix ラベル欠落時の振る舞いを固定する。

    前提にしてよいのは gh と issue だけであり、4 つの機能欠落時における
    フォールバック動作の分岐が定義されていることを検証する。
    """
    found = fallbacks()
    assert list(found) == ["マイルストーン", "サブイシューの API", "重要度ラベル", "wontfix にあたるラベル"]
    assert "段 1 の 3 つ目の経路と、扱うこと 2 を飛ばす" in found["マイルストーン"]
    assert "子 issue の結び付けを、すべて子 issue の本文の 1 行で行う" in found["サブイシューの API"]
    assert "gh label list" in found["重要度ラベル"]
    assert "説明が無ければ付け替えない" in found["重要度ラベル"]
    assert "ラベルを付けずに閉じる" in found["wontfix にあたるラベル"]
    assert "理由と再燃の条件は本文に残る" in found["wontfix にあたるラベル"]


# ---------- 重要度と問いの形を変えるときの基準 ----------


def test_priority_change_branches_by_harm_observation() -> None:
    """重要度変更の 3 経路（実害観測追記で上げる／実害なし判明で下げる／説明なしラベルは維持）を固定する。

    重要度はラベルの説明の区分へ当てはめ、実害観測が本文へ追記されたら上げ、
    実害が起きない経路だと分かったら下げ、説明のないラベルへは付け替えない。
    """
    part = section(skill(), "### 重要度と問いの形を変えるときの基準")
    basis, source = mapping(table(part, CRITERIA_HEADER))["重要度"]
    assert "実害の観測" in basis
    assert "ラベルの説明" in source and "gh label list" in source
    assert "上げ" in sentence_with(part, "実害の観測が本文へ追記された")
    assert "下げ" in sentence_with(part, "実害が起きない経路")
    assert "付け替えない" in sentence_with(part, "説明の無いラベル")


def test_question_restructuring_branches_by_premise_and_gist() -> None:
    """問いの形変更の 2 経路（前提反転時のみ立て直す／主旨変更時は人へ返す）を固定する。

    問いを立て直すのは前提反転時のみで主旨や困りごとは変えず、
    主旨まで変わる場合は新起票判断として人へ返す。
    """
    part = section(skill(), "### 重要度と問いの形を変えるときの基準")
    basis, source = mapping(table(part, CRITERIA_HEADER))["問いの形"]
    assert "前提が反転" in basis
    assert "実物の状態" in source
    assert "前提が反転したときに限る" in sentence_with(part, "問いを立て直すのは")
    assert "主旨ではない" in sentence_with(part, "立て直すのは問いの形")
    assert "起票" in sentence_with(part, RETURNED)


# ---------- 発見の瞬間の 3 択（out-of-scope の段 2） ----------

OUT_OF_SCOPE = SKILLS / "out-of-scope" / "SKILL.md"


def test_out_of_scope_stage_2_offers_exactly_three_choices() -> None:
    """段 2 の判断が {起票する, 範囲内へ入れる, 起票しない} の 3 分岐だけであることを固定する。

    表示文字列の完全一致ではなく、判断の列を集合として比較する。この 3 分岐は発見の瞬間の
    判断の中心で、後の構造改善で表を触る対象になる。
    """
    part = section(OUT_OF_SCOPE.read_text(encoding="utf-8"), "### 2. 3 択で決める")
    rows = table(part, "| 判断 | 選ぶ条件 | 残すもの |")
    assert set(column(rows, 0)) == {"起票する", "範囲内へ入れる", "起票しない"}


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
GROUP_COLUMN_HEADER = "| 列 | 値 | 写す元 |"
GROUP_TIMING_HEADER = "| 時点 | 行うこと |"


def group_section() -> str:
    return section(MILESTONES.read_text(encoding="utf-8"), GROUP_SECTION)


def group_columns() -> dict[str, list[str]]:
    """組の表の列ごとの [値, 写す元]。"""
    return mapping(table(group_section(), GROUP_COLUMN_HEADER))


def group_timings() -> dict[str, str]:
    """組を書く時点ごとの行うこと。"""
    return pairs(table(group_section(), GROUP_TIMING_HEADER))


def test_milestones_have_a_section_for_the_parallel_groups() -> None:
    """AC20: マイルストーンへ課題を入れるときに、その課題の組を説明へ書く。"""
    part = group_section()
    assert "### 並列の組（見込み）" in part, "説明へ置く見出しの形が無い"
    assert GROUP_TABLE_HEADER in part
    assert any("マイルストーンへ入れる" in moment for moment in group_timings())


def test_the_group_row_is_written_when_an_issue_enters_a_milestone() -> None:
    """AC20: 新しく作る・既存へ足す・直近へ移すの 3 つが同じ時点として扱われる。"""
    moments = list(group_timings())
    assert any("入れる" in m and "新しく作る" in m and "足す" in m and "移す" in m
               for m in moments), moments
    assert any("別のマイルストーンへ移す" in m for m in moments), moments
    assert any("閉じた" in m for m in moments), moments


def test_the_group_is_copied_from_what_stage_2a_already_records() -> None:
    """AC21: 触る場所は段 2A の修正レイヤーから、依存は依存する課題の番号から写す。

    写す元に挙がる項目は、段 2A の控える項目の表にあるものだけである。
    """
    sources = {name: source for name, (_, source) in group_columns().items()}
    assert "修正レイヤー" in sources["課題"]
    assert "依存する課題の番号" in sources["依存"]
    recorded = column(table(skill(), RECORDED_HEADER), 0)
    for source in sources.values():
        for name in re.findall(r"「(.+?)」", source):
            assert name in recorded, name


def test_stage_2a_records_the_same_six_items() -> None:
    """AC21: 段 2A の控える項目は増えない。

    組のために段 2A へ項目を足すと、棚卸の 1 課題あたりの費用が上がる。
    """
    recorded = column(table(skill(), RECORDED_HEADER), 0)
    assert len(recorded) == 6, recorded
    assert recorded[-1] == "修正レイヤー"


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
    assert "既存のマイルストーンを分けたり束ね直したりしない" in plain(group_section())
    assert "組の番号は詰めない" in group_timings()["課題を別のマイルストーンへ移す"]


def test_an_empty_group_row_is_removed_without_renumbering_the_remaining_groups() -> None:
    """現状固定: 移動で 0 件になった組は消すが、残る組の番号は詰めない。"""
    assert group_columns()["組"][0] == "1 から始まる連番。説明の中で一意"
    assert group_timings()["課題を別のマイルストーンへ移す"] == (
        "元の説明の表から番号を消す。課題が 0 件になった組は行を消し、"
        "組の番号は詰めない"
    )


def test_milestones_without_a_group_table_are_not_rewritten_at_once() -> None:
    """決定 11: 既存の説明を一括で書き直さず、足した課題の行だけを載せる。"""
    assert "一括で書き直さない" in plain(group_section())
    entering = next(action for moment, action in group_timings().items()
                    if "マイルストーンへ入れる" in moment)
    assert "表が無ければ見出しと表を作る" in entering


def test_group_partition_rule_by_fix_layer() -> None:
    """現状固定: 同じ修正レイヤーの課題は同じ組、違えば別組にする規則を固定する。"""
    assert group_columns()["課題"][0] == "同じ修正レイヤーを持つ課題の番号"
    entering = next(action for moment, action in group_timings().items()
                    if "マイルストーンへ入れる" in moment)
    assert "違えば組を 1 つ足す" in entering


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
    action = next(action for moment, action in group_timings().items()
                  if timing_pattern in moment)
    for expected in expected_actions:
        assert expected in action, f"{timing_pattern} の行うことに '{expected}' が含まれていない"


# ---------- やり直しで 2 度行わない（grouping.md） ----------

REDO_SECTION = "## やり直しで 2 度行わない"


def redo_section() -> str:
    return section(GROUPING.read_text(encoding="utf-8"), REDO_SECTION)


def redo_commands() -> list[str]:
    """やり直しの節が持つ判定材料の取り方（bash の例）を、出てくる順に返す。"""
    return bash_blocks(redo_section())


def test_redo_takes_its_materials_from_two_lookups() -> None:
    """現状固定: 起票と結び付けを分けて見分け、材料は親の側と子の側の 2 つの取り方で引く。

    まとめて扱うと、書き込みの制限で途中で止まったときに、起票済みを理由として残りの
    結び付けが飛ぶ。親の側からは子の一覧を、子の側からは親の番号を引く。
    """
    assert "起票と結び付けを分けて見分ける" in plain(redo_section())

    from_parent, from_child = redo_commands()
    assert "/issues/<親の番号>/sub_issues" in from_parent
    assert jq_of(from_parent) == ".[].number"
    assert "gh api graphql" in from_child
    assert "issue(number:<子の番号>){parent{number}}" in from_child


@pytest.mark.parametrize(("decision", "material"), [
    ("起票の要否", "この修正レイヤーを指す親 issue を探して起票の要否を決め"),
    ("結び付けの要否", "その親 issue の子の一覧で結び付けの要否を決める"),
])
def test_redo_decides_filing_and_linking_from_the_parent_side(
        decision: str, material: str) -> None:
    """現状固定: 起票の要否は親 issue の有無で、結び付けの要否は親の子の一覧で決める。

    親が作成済みなら起票を飛ばし、子の一覧に無い子だけを結び付ける経路になる。
    子の本文の 1 行だけでは決めない。複数のクラスタへ属する課題では別の親を指す。
    """
    part = redo_section()
    assert "親 issue の側から引く" in plain(part)
    assert material in sentence_with(part, decision), decision
    assert "決めない" in sentence_with(part, "子の本文の 1 行だけ")


def test_the_parent_side_lookup_lists_the_linked_children() -> None:
    """現状固定: 親の側の jq を実際に流し、結び付け済みの子の番号だけが並ぶことを固定する。

    この一覧に無い子が、やり直しで結び付ける残りである。番号以外の項目は落ちる。
    """
    from_parent, _ = redo_commands()
    expression = jq_of(from_parent)

    sub_issues = [
        {"id": 1001, "number": 12, "title": "child a"},
        {"id": 1002, "number": 34, "title": "child b"},
    ]
    assert run_jq(expression, sub_issues) == ["12", "34"]

    # branch: 子が 1 件も結び付いていない親では、一覧が空で返る。
    assert run_jq(expression, []) == []


def test_a_child_with_another_parent_is_linked_by_a_body_line() -> None:
    """現状固定: 子が既に別の親を持つかは子の側から引き、値があれば本文の 1 行で指す。

    親は単数で返るため、2 つ目のクラスタはサブイシュー関係では結べない。
    結び付け方の節が、その例外を本文の 1 行として定める。
    """
    _, from_child = redo_commands()
    assert "parent{number}" in from_child
    assert "本文の 1 行で指す" in sentence_with(redo_section(), "値があれば")

    linking = section(GROUPING.read_text(encoding="utf-8"), "### 結び付け方")
    assert "本文へ親 issue を指す 1 行を足す" in sentence_with(linking, "サブイシューの API を持たない")
