"""実行計画の規約（`issue-plan-strategy`、#540）。

**実行計画は進行側が持つ 1 つのファイルで、開いている間はコミットしない。** 会話が
失われても同じ内容を読み直せることが、置き場所を決めた理由である（設計の決定 5）。

**契約の列名と状態の値は、書く側（この文書）と読む側（テスト）が同じ表から写す。**
列名が片方だけ変わると、実行計画を読む進行側が知らない列を受け取る。
"""
from __future__ import annotations

import pathlib

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
PLAN = SKILL_DIR / "references" / "execution-plan.md"


def skill() -> str:
    return SKILL.read_text(encoding="utf-8")


def plan() -> str:
    return PLAN.read_text(encoding="utf-8")


def flat(text: str) -> str:
    """折り返しの改行と強調の印を除く。日本語の文は改行の位置で語が割れる。"""
    return text.replace("\n", "").replace("**", "")


# --- AC1: 作る時点と、作らないとき ------------------------------------------


def test_the_plan_is_written_before_the_first_lane_starts() -> None:
    """長い待ちを契機にしないため、起動の前に依存と本数を置いておく。"""
    body = flat(plan())
    assert "複数の課題" in body
    assert "最初の担当を起動する前に" in body


def test_a_single_pull_request_needs_no_plan() -> None:
    """依存も重なりも本数も、比べる相手がいない。"""
    assert "課題が 1 件で、Pull Request も 1 本" in flat(plan())
    assert "書かない" in flat(plan())


# --- AC2: 行の形 -------------------------------------------------------------

ROW_COLUMNS = [
    "行", "束", "種類", "課題", "モード", "触るファイルと節", "確度",
    "設計の依存", "実装の依存", "状態", "Pull Request",
]
CONFIDENCE = ["見込み", "確定"]
STATES = ["待ち", "着手できる", "動いている", "承認待ち", "止まった", "マージ済み"]


def test_the_row_table_has_every_column() -> None:
    assert ROW_COLUMNS in _tables(plan()), _tables(plan())


def test_the_row_table_distinguishes_a_guess_from_a_fixed_value() -> None:
    """触るファイルと節は、設計のレビューが収束するまで見込みである。"""
    body = flat(plan())
    for value in CONFIDENCE:
        assert f"`{value}`" in body, value
    assert "設計のレビューが収束した見直しで、その束の行を `確定` にする" in body


def test_the_row_table_has_six_states() -> None:
    body = flat(plan())
    for state in STATES:
        assert f"`{state}`" in body, state


def test_only_the_running_rows_are_counted() -> None:
    """承認待ちの担当は報告を返して終わっており、CLI もテストも動かしていない。"""
    assert "本数に数えるのは `動いている` だけである" in flat(plan())


def test_the_dependency_is_a_pair_of_stages() -> None:
    """設計の行に実装の依存を書くと、実装の順序が次の設計を止める（決定 2）。"""
    body = flat(plan())
    assert "`A-設計:収束`" in body
    assert "`A-実装:マージ`" in body
    assert "B の設計の行に、B の実装の依存を書かない" in body


# --- AC3: 置き場所 -----------------------------------------------------------


def test_the_plan_has_one_place_and_survives_a_restart() -> None:
    body = flat(plan())
    assert "issues/execution-plan-" in body
    assert "主ディレクトリ" in body
    assert "本体が再起動しても" in body


def test_the_plan_is_not_committed_while_it_is_open() -> None:
    """見直しのたびに Pull Request を通すと、計画の更新がマージを待つ。"""
    assert "開いている間はコミットしない" in flat(plan())


# --- AC4 / AC37: 測った値 ----------------------------------------------------

MEASURE_COLUMNS = [
    "時刻", "空き（MiB）", "cgroup の残り（MiB）", "スワップの空き（MiB）",
    "oom_kill", "動いている本数", "起動してよい本数", "決めた条件", "起動した行",
]


def test_the_measured_table_has_every_column() -> None:
    assert MEASURE_COLUMNS in _tables(plan()), _tables(plan())


def test_the_capacity_is_measured_before_every_launch() -> None:
    body = flat(plan())
    assert "担当を起動する前に、毎回これを実行し" in body
    assert "parallel-measure.py" in body
    assert "capacity" in body


def test_an_unmeasurable_host_runs_one_lane() -> None:
    """終了コード 3 のときは、上限ではなく 1 本で進める。"""
    body = flat(plan())
    assert "測れない" in body
    assert "起動してよい本数を 1 として扱う" in body


def test_the_plan_does_not_copy_the_defaults() -> None:
    """予備・1 本の見込み・上限・スワップの閾値は `parallel-measure.py` だけが持つ。"""
    body = plan()
    for value in ("2048", "25%"):
        assert value not in body, f"初期値 {value} は parallel-measure.py だけが持つ"


def test_only_one_lane_runs_container_checks() -> None:
    assert "コンテナを起動する検査を持つ行" in flat(plan())


# --- AC9 / AC10 / AC11: 見直し ----------------------------------------------

TRIGGERS = [
    "担当の報告を受け取った",
    "Pull Request がマージされた",
    "担当が止まった",
    "OOM Killer の回数",
    "進行側が再起動して",
]
REVIEW_COLUMNS = ["時刻", "契機", "変えたこと", "理由"]


def test_the_five_triggers_are_listed() -> None:
    body = flat(plan())
    assert "契機は 5 つである" in body
    for trigger in TRIGGERS:
        assert trigger in body, trigger


def test_a_row_starts_when_its_dependencies_are_done_and_there_is_room() -> None:
    """supervisor の中の待ちは進行側から見えないため、待ちを契機にしない。"""
    body = flat(plan())
    assert "着手できるかは依存と本数だけで決まる" in body
    assert "長い待ち" in body
    assert "知らなくても" in body


def test_the_review_table_records_the_reason() -> None:
    assert REVIEW_COLUMNS in _tables(plan()), _tables(plan())
    assert "1 行足して理由を残す" in flat(plan())


def test_an_increased_oom_kill_stops_the_launch_and_moves_the_baseline() -> None:
    """起点を更新しないと、以後の見直しがすべて「増えた」になる（決定 7）。"""
    body = flat(plan())
    assert "oom_kill_increased=yes" in body
    assert "その見直しでは新しい担当を起動しない" in body
    assert "いま測った値へ更新する" in body


# --- AC23: 組を束の初期値として読む ------------------------------------------


def test_the_plan_reads_the_milestone_groups() -> None:
    body = flat(plan())
    assert "### 並列の組（見込み）" in body
    assert "束の初期値として読む" in body


def test_there_is_a_way_to_start_without_groups() -> None:
    """マイルストーンを使わないリポジトリでも実行計画を作れる。"""
    body = flat(plan())
    assert "組が無いとき" in body
    assert "マイルストーンを使わないリポジトリ" in body
    assert "書き戻さない" in body


# --- AC7: 競合の解き方 --------------------------------------------------------


def test_the_later_merge_resolves_and_reconverges() -> None:
    body = flat(plan())
    assert "後からマージする側が解く" in body
    assert "解いた後の head で実装レビューを収束させてからマージする" in body


# --- AC41: 閉じる -------------------------------------------------------------

CLOSING_COLUMNS = [
    "対象の Pull Request", "期間（分）", "重なり（分）", "並行度", "最大同時",
    "oom_kill の増分",
]


def test_the_closing_table_has_every_column() -> None:
    assert CLOSING_COLUMNS in _tables(plan()), _tables(plan())


def test_closing_measures_posts_and_deletes() -> None:
    body = flat(plan())
    assert "すべての行が `マージ済み` になったら閉じる" in body
    assert "concurrency" in body
    assert "gh pr comment" in body
    assert "ファイルを消す" in body


# --- 配線: `SKILL.md` から辿れる ---------------------------------------------


def test_the_skill_points_at_the_reference() -> None:
    assert "references/execution-plan.md" in skill()


def test_the_skill_has_a_section_for_the_execution_plan() -> None:
    assert "## 実行計画" in skill()


def _tables(text: str) -> list[list[str]]:
    """表の見出し行だけを列の一覧として返す。"""
    headers: list[list[str]] = []
    lines = text.splitlines()
    for i, line in enumerate(lines[:-1]):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        following = lines[i + 1].strip()
        if not following.startswith("|"):
            continue
        cells = [c.strip() for c in following.strip("|").split("|")]
        if not cells or set("".join(cells)) > set("-: "):
            continue
        headers.append([c.strip() for c in stripped.strip("|").split("|")])
    return headers
