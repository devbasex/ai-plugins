"""touch_estimate.py: 触る場所の見込みの出し方と、見込みが重ならない課題を先に選ぶ並べ替え（#2997）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import touch_estimate as te  # noqa: E402

FILES = [
    "plugins/ndf/scripts/lib/sprint_loop_select.py",
    "plugins/ndf/scripts/lib/parallel_plan.py",
    "plugins/ndf/scripts/parallel.py",
    "plugins/ndf/scripts/lib/NOTES.md",
    "docs/a.md",
    "docs/b.md",
]


def est(*paths: str) -> te.Estimate:
    return te.Estimate(list(paths), te.BODY if paths else te.NONE)


# ---------------------------------------------------------------- 見込みの出し方


def test_the_body_yields_files_only_from_the_named_sections():
    body = (
        "## 目的\n\n`sprint_loop_select.py` を直す。`plugins/ndf/scripts/` の下。\n\n"
        "## 対象範囲\n\n含む:\n- `docs/*.md`\n\n含まない:\n- `parallel.py`\n\n"
        "## 関連\n\n`plugins/ndf/scripts/lib/parallel_plan.py`\n"
    )
    assert te.from_body(body, FILES) == ["plugins/ndf/scripts/lib/sprint_loop_select.py", "docs/a.md", "docs/b.md"]


def test_the_body_without_the_named_sections_is_read_whole_and_ambiguous_tails_are_dropped():
    body = "`NOTES.md` と `lib/parallel_plan.py` と `scripts/parallel.py`"
    assert te.from_body(body, FILES + ["NOTES.md"]) == [
        "NOTES.md",
        "plugins/ndf/scripts/lib/parallel_plan.py",
        "plugins/ndf/scripts/parallel.py",
    ]
    assert te.from_body("`a.md`", ["x/a.md", "y/a.md"]) == []  # 末尾が 2 つのファイルに当たる語は取らない


def test_the_body_paths_do_not_assume_the_ndf_layout():
    """受け入れ条件 9・I11: ほかの形のリポジトリでも本文のパスから見込みが出る。"""
    files = ["src/app/models/user.rb", "app/controllers/users_controller.rb"]
    body = "## 目的\n\n`src/app/models/user.rb` と `users_controller.rb` を直す\n"
    assert te.from_body(body, files) == files


def test_the_group_table_reads_files_and_directories_for_the_issue():
    desc = (
        "説明\n\n### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n| --- | --- | --- | --- |\n"
        "| 1 | #10 #11 | `plugins/ndf/scripts/lib/` / `merged` | なし |\n| 2 | #12 | `docs/a.md` | なし |\n"
    )
    assert te.from_group_table(desc, 11, FILES) == ["plugins/ndf/scripts/lib"]  # Skill の名前は追跡しているファイルに当たらず捨てる
    assert te.from_group_table(desc, 12, FILES) == ["docs/a.md"]
    assert te.from_group_table(desc, 13, FILES) == []
    assert te.from_group_table("表なし", 11, FILES) == []


def test_the_design_result_gives_the_implement_rows_files():
    doc = (
        "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n"
        "| #5 | 実装する | — | `a/x.py`、`a/y.py` |\n| #6 | 閉じる | — | — |\n"
    )
    assert te.from_design(doc, 5) == ["a/x.py", "a/y.py"]
    assert te.from_design(doc, 6) == []
    assert te.from_design("節なし", 5) == []


def test_the_first_source_with_paths_wins_and_later_sources_are_not_read():
    called = []

    def body():
        called.append("body")
        return ["docs/a.md"]

    got = te.estimate([(te.DESIGN, lambda: []), (te.GROUP, lambda: ["x"]), (te.BODY, body)])
    assert got.to_dict() == {"paths": ["x"], "source": te.GROUP, "note": ""}
    assert called == []
    assert te.estimate([(te.BODY, lambda: [])], "本文を読めない").to_dict() == {"paths": [], "source": te.NONE, "note": "本文を読めない"}


# ---------------------------------------------------------------- 並べ替え


def test_overlapping_pairs_yield_to_a_separate_one_within_the_window():
    """受け入れ条件 3・I4: 重なる 2 件と重ならない 1 件・上限 2 で、重ならない組を選び、逆転の組を残す。"""
    e = {1: est("docs/a.md"), 2: est("docs/a.md"), 3: est("docs/b.md")}
    got = te.spread([1, 2, 3], set(), e, slots=2, width=1)
    assert got.take == [1, 3]
    assert got.reorders == [{"later": 2, "earlier": 3, "with": [1]}]
    assert got.overlap == {1: {"overlap": "なし", "overlap_with": []}, 3: {"overlap": "なし", "overlap_with": []}}


def test_candidates_outside_the_window_do_not_jump_ahead():
    e = {1: est("docs/a.md"), 2: est("docs/a.md"), 3: est("docs/a.md"), 4: est("docs/b.md")}
    got = te.spread([1, 2, 3, 4], set(), e, slots=2, width=1)
    assert got.take == [1, 2]
    assert got.reorders == []


def test_all_overlapping_candidates_keep_the_rank_order_and_the_count():
    """受け入れ条件 4・I5: すべて重なれば今の選び方と同じ組で、数は減らない。"""
    e = {n: est("docs/a.md") for n in (1, 2, 3)}
    got = te.spread([1, 2, 3], set(), e, slots=2, width=5)
    assert got.take == te.spread([1, 2, 3], set(), e, slots=2, width=0).take == [1, 2]
    assert got.overlap[2] == {"overlap": "あり", "overlap_with": [1]}


def test_width_zero_keeps_the_current_order():
    """I6: 逆転の幅 0 は枠の順の先頭のまま。"""
    e = {1: est("docs/a.md"), 2: est("docs/a.md"), 3: est("docs/b.md")}
    got = te.spread([1, 2, 3], set(), e, slots=2, width=0)
    assert got.take == [1, 2] and got.reorders == []


def test_fixed_issues_are_taken_even_when_they_overlap():
    """受け入れ条件 5・I3: 先頭に選ぶ課題と品質の枠は重なりを見ずに取る。"""
    e = {1: est("docs/a.md"), 2: est("docs/a.md"), 3: est("docs/b.md")}
    got = te.spread([1, 2, 3], {1, 2}, e, slots=2, width=3)
    assert got.take == [1, 2]
    assert got.overlap[2]["overlap"] == "あり"


def test_unknown_estimates_are_taken_and_marked_unknown():
    """受け入れ条件 2・I2: 見込みなしの相手との組は 不明。"""
    e = {1: est(), 2: est("docs/a.md"), 3: est("docs/a.md")}
    got = te.spread([1, 2, 3], set(), e, slots=3, width=1)
    assert got.overlap[1] == {"overlap": "なし", "overlap_with": []}
    assert got.overlap[2] == {"overlap": "不明", "overlap_with": [1]}
    assert got.overlap[3] == {"overlap": "あり", "overlap_with": [2]}


def test_the_shared_list_counts_as_an_overlap():
    """I10: 共有の一覧に当たる 2 件は実装のキューと同じく あり。"""
    shared = [{"path": "plugins/ndf/scripts/lib/NOTES.md", "touched_by": ["plugins/ndf/scripts/lib/"]}]
    e = {1: est("plugins/ndf/scripts/lib/a.py"), 2: est("plugins/ndf/scripts/lib/b.py"), 3: est("docs/b.md")}
    assert te.spread([1, 2, 3], set(), e, slots=2, width=1, shared=shared).take == [1, 3]
    assert te.spread([1, 2, 3], set(), e, slots=2, width=1).take == [1, 2]


# ---------------------------------------------------------------- 模擬（実験版の selection-replay.py）


def _replay():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "selection_replay", Path(__file__).resolve().parents[1] / "experimental" / "selection-replay.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_replay_average_is_busy_time_over_elapsed_time():
    """受け入れ条件 8: 重なる組は直列、離れた組は並列に数え、平均は 所要の和 ÷ 経過。"""
    rp = _replay()
    seconds = {1: 10.0, 2: 10.0, 3: 10.0}
    files = {1: ["a.py"], 2: ["a.py"], 3: ["b.py"]}
    est = {n: te.Estimate(files[n], te.DESIGN) for n in files}
    assert rp.sprint_elapsed([1, 2, 3], seconds, files, [], 3) == 20.0
    got = rp.replay([1, 2, 3], seconds, files, est, [], issues=2, slots=3, width=1)
    assert (got["current"]["sprints"], got["current"]["average"]) == ([[1, 2], [3]], 1.0)
    assert (got["new"]["sprints"], got["new"]["average"]) == ([[1, 3], [2]], 1.5)
