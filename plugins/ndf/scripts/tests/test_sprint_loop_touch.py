"""開発ループの選定の触る場所の見込み（#2997）: 選定の行の見込み・重なり、窓の内の並べ替え、古い記録の読み方。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sprint_loop as base  # noqa: E402
from test_sprint_loop import DECL, rec, sl, sl_select, start  # noqa: E402
import touch_estimate as te  # noqa: E402

env, mvv = base.env, base.mvv  # フィクスチャを受け継ぐ

FILES = ["docs/a.md", "docs/b.md", "src/x.py"]


def with_files(env, width=None):
    """git ls-files を差し替え、進め方の宣言に逆転の幅を書く。"""
    run = env.run

    def fake(cmd, cwd=None, stdin=None):
        if cmd[:2] == ["git", "ls-files"]:
            return 0, "\n".join(FILES) + "\n", ""
        return run(cmd, cwd, stdin)

    env.run = fake
    if width is not None:
        decl = {**DECL, "loop": {**DECL["loop"], "reorder_width": width}}
        (env.root / ".ndf" / "pace.json").write_text(json.dumps(decl))
    env.bodies.update({11: "## 目的\n\n`docs/a.md`\n", 12: "## 目的\n\n`a.md` を直す\n", 13: "## 目的\n\n`docs/b.md`\n"})
    return env


def rows_of(out):
    return {r["number"]: r for r in out["items"] if "decision" in r}


def test_the_selection_records_estimates_overlaps_and_reorders(env):
    """受け入れ条件 1〜3: 重なる 2 件と重ならない 1 件・上限 2・幅 1 で、重ならない組を選び、行と逆転の組を記録する。"""
    with_files(env, width=1)
    start(env)
    assert rec(env)["reorder_width"] == 1
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["selected"] == [11, 13]
    rows = rows_of(out)
    assert rows[11]["touch"] == {"paths": ["docs/a.md"], "source": "課題の本文", "note": ""}
    assert rows[12]["touch"]["paths"] == ["docs/a.md"] and rows[12]["decision"] == "外した"
    assert (rows[11]["overlap"], rows[11]["overlap_with"]) == ("なし", [])
    assert (rows[13]["overlap"], rows[13]["overlap_with"]) == ("なし", [])
    assert "overlap" not in rows[12]
    assert out["metrics"]["reorders"] == [{"later": 12, "earlier": 13, "with": [11]}]
    assert sl.current_lap(rec(env))["reorders"] == [{"later": 12, "earlier": 13, "with": [11]}]


def test_an_issue_without_paths_is_unknown(env):
    """受け入れ条件 1・2: 本文にパスの無い課題は 見込みなし、その相手との組は 不明。"""
    with_files(env, width=1)
    env.bodies[11] = "本文\n"
    start(env)
    rows = rows_of(sl_select.cmd_select(env, "l1"))
    assert rows[11]["touch"] == {"paths": [], "source": "見込みなし", "note": ""}
    assert (rows[12]["overlap"], rows[12]["overlap_with"]) == ("不明", [11])


def test_the_design_result_and_the_group_table_come_before_the_body(env):
    with_files(env, width=0)
    (env.root / "issues").mkdir()
    (env.root / "issues" / "issue-11-design.md").write_text(
        "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n| #11 | 実装する | — | `src/x.py` |\n"
    )
    env.descs = {"01 近い": "### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n| --- | --- | --- | --- |\n| 1 | #12 | `docs/` | なし |\n"}
    start(env)
    rows = rows_of(sl_select.cmd_select(env, "l1"))
    assert rows[11]["touch"] == {"paths": ["src/x.py"], "source": "設計の結果", "note": ""}
    assert rows[12]["touch"] == {"paths": ["docs"], "source": "課題グループの表", "note": ""}


def test_width_zero_keeps_the_current_choice(env):
    """I6: 逆転の幅 0（既定）なら、重なっても順位の順に選ぶ。"""
    with_files(env)
    start(env)
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["selected"] == [11, 12] and "reorders" not in out["metrics"]
    assert rows_of(out)[12]["overlap"] == "あり"
    assert rows_of(out)[13]["touch"] == {"paths": [], "source": "見込みなし", "note": "切り出しの境界の外"}  # 境界の外は本文を読まない


def test_an_old_record_without_the_width_still_selects(env):
    """受け入れ条件 10・I7: reorder_width の無い記録は宣言の値で読み、選定は止まらない。"""
    with_files(env, width=1)
    start(env)
    p = sl.loop_record_path(env, "l1")
    data = json.loads(p.read_text())
    data.pop("reorder_width")
    p.write_text(json.dumps(data))
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"] == [11, 13]


def test_fixed_and_quality_slot_issues_are_chosen_despite_overlaps():
    """受け入れ条件 5・I3: 先頭に選ぶ課題と品質の枠の課題は重なっても選ばれる。"""
    cands = [{"rank": i, "number": n} for i, n in enumerate((10, 11, 12), 1)]
    cands[1]["quality_slot"] = True
    same = te.Estimate(["docs/a.md"], te.BODY)
    touch = sl_select.TouchPick({10: same, 11: same, 12: te.Estimate(["docs/b.md"], te.BODY)}, width=3)
    picked, here = sl_select._select_candidate_rows(cands, "M", 2, lambda r: None, first={10}, touch=touch)
    assert picked == [10, 11]
    assert [r["reason"] for r in here if r["decision"] == "選んだ"] == ["直しの先頭", "品質の枠"]
    assert touch.reorders == []


def test_without_estimates_the_rows_keep_their_keys():
    """受け入れ条件 10: 見込みを渡さなければ選定の行の既存のキーの形は変わらない。"""
    cands = [{"rank": 1, "number": 10}]
    _, here = sl_select._select_candidate_rows(cands, "M", 1, lambda r: None)
    assert set(here[0]) == {"number", "milestone", "rank", "decision", "reason"}
