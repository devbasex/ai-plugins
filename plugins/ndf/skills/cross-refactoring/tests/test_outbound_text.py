"""外へ出す文章の規約（#436 決定 6-b / D1・D2・D3）。

**読み手は GitHub 上にいる。** 状態ファイルも内部の識別子も見えない。

| 規約 | 受け入れ条件 |
| --- | --- |
| 項目は `<ファイル>#<シンボル>` を併記する | D1 |
| 取り消した項目の内訳を書かない（件数だけ） | D2 |
| 改修計画の URL を**生の URL**で書く | D3 |
"""
from __future__ import annotations

import pytest

from crossref_helpers import make_state, read_state

COMMENT_URL = "https://github.com/devbasex/ai-plugins/pull/130#issuecomment-999"


def _item(**over):
    base = {
        "item_id": "R1-001", "round": 1, "path": "src/foo.py",
        "symbol": "Foo.handle", "smell": "long_method",
        "technique": "extract_method", "severity": "major",
        "rationale": "理由", "plan": "手順", "test_gap": False,
        "estimated_diff_lines": 40, "proposed_by": ["codex"],
        "status": "done", "commits": ["abc1234"],
    }
    base.update(over)
    return base


def _state(tmp_path, **over):
    over.setdefault("plan_mode", "comment")
    over.setdefault("plan_file", "")
    over.setdefault("plan_comment", {"id": 999, "url": COMMENT_URL})
    over.setdefault("items", [_item()])
    path = make_state(tmp_path, **over)
    return path, read_state(path)


# ---------- D3: 改修計画の参照 ----------

def test_the_plan_reference_is_a_raw_url(refactor, tmp_path):
    """**Markdown のリンクにしない。** 読み手が URL を取り出せなくなる。"""
    _, state = _state(tmp_path)
    reference = refactor.plan_reference(state)
    assert reference == COMMENT_URL
    assert "[" not in reference and "](" not in reference


def test_the_plan_line_names_the_plan(refactor, tmp_path):
    _, state = _state(tmp_path)
    assert refactor.plan_line(state) == f"改修計画: {COMMENT_URL}"


def test_a_missing_comment_is_stated_instead_of_a_blank(refactor, tmp_path):
    """投稿できていないことを黙らない。空欄だと読み手が探し始める。"""
    _, state = _state(tmp_path, plan_comment=None)
    assert "作成できていません" in refactor.plan_reference(state)


def test_the_file_mode_points_at_the_file(refactor, tmp_path):
    _, state = _state(tmp_path, plan_mode="file", plan_file="issues/plan.md")
    assert refactor.plan_reference(state) == "issues/plan.md"


# ---------- D1: 項目の指し方 ----------

def test_an_item_is_named_with_its_file_and_symbol(refactor, tmp_path):
    _, state = _state(tmp_path)
    assert refactor.item_lines(state, ["R1-001"]) == [
        "R1-001 `src/foo.py#Foo.handle`"
    ]


def test_a_test_item_is_named_with_its_target(refactor, tmp_path):
    """テスト項目の `path` はテストを足す先。指すのは固定する入口である。"""
    _, state = _state(tmp_path, items=[{
        "item_id": "R1-001", "round": 1, "kind": "test",
        "path": "tests/test_foo.py", "target": "src/foo.py#Foo.handle",
        "case": "branch", "level": "unit", "proposed_by": ["codex"],
        "status": "done", "commits": [],
    }])
    assert refactor.item_lines(state, ["R1-001"]) == [
        "R1-001 `src/foo.py#Foo.handle`"
    ]


# ---------- D2: 取り消しは件数だけ ----------

def test_a_drop_is_reported_as_a_count(refactor, tmp_path):
    _, state = _state(tmp_path)
    line = refactor.dropped_line(state, 3)
    assert "3 件" in line and "内訳は改修計画にある" in line
    assert COMMENT_URL in line


def test_the_report_does_not_list_the_deferred_breakdown(refactor, tmp_path,
                                                         env_tmp_dir, capsys):
    """D2 — 進行の報告は件数だけを述べ、内訳は改修計画へ譲る。"""
    path, _ = _state(tmp_path, deferred_items=[{
        "item_id": "R1-002", "round": 1, "path": "src/bar.py",
        "symbol": "Bar.run", "smell": "duplication",
        "defer_reason": "差分予算を超えた",
    }])
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    out = capsys.readouterr().out
    assert "件数: 1 件" in out
    assert "差分予算を超えた" not in out, "内訳を書いている"
    assert COMMENT_URL in out, "改修計画の生の URL が無い"


def test_the_report_names_the_plan_in_its_header(refactor, tmp_path,
                                                 env_tmp_dir, capsys):
    path, _ = _state(tmp_path)
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert f"- 改修計画: {COMMENT_URL}" in capsys.readouterr().out


def test_the_report_still_names_each_item_with_its_symbol(refactor, tmp_path,
                                                          env_tmp_dir, capsys):
    """D1 — 改善項目の表は残す。**取り消しの内訳とは別のものである。**"""
    path, _ = _state(tmp_path)
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert "src/foo.py#Foo.handle" in capsys.readouterr().out
