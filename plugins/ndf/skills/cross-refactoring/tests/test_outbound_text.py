"""外へ出す文章の規約（#436 決定 6-b / D1・D2・D3）。

**読み手は GitHub 上にいる。** 状態ファイルも内部の識別子も見えない。

| 規約 | 受け入れ条件 |
| --- | --- |
| 項目は `<ファイル>#<シンボル>` を併記する | D1 |
| 取り消した項目の内訳を書かない（件数だけ） | D2 |
| 改修計画の URL を**生の URL**で書く | D3 |
"""

from __future__ import annotations

import re
import sys

from crossref_helpers import make_state_v2, read_state

COMMENT_URL = "https://github.com/devbasex/ai-plugins/pull/130#issuecomment-999"


def _item(**over):
    base = {
        "id": "I-001",
        "rank": 1,
        "path": "src/foo.py",
        "symbol": "Foo.handle",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "tier": "high",
        "rationale": "理由",
        "plan": "手順",
        "tests": [],
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "estimated_diff_lines": 40,
        "proposed_by": ["codex"],
        "status": "verified",
        "fix_count": 0,
        "danger": [],
        "commits": {"test": None, "implement": "abc1234", "fix": []},
    }
    base.update(over)
    return base


def _state(tmp_path, **over):
    over.setdefault("plan_mode", "comment")
    over.setdefault("plan_file", "")
    over.setdefault("plan_comment", {"id": 999, "url": COMMENT_URL})
    over.setdefault("items", [_item()])
    path = make_state_v2(tmp_path, tmp_path / "work", **over)
    return path, read_state(path)


# ---------- D3: 改修計画の参照 ----------


def test_the_plan_reference_is_a_raw_url(outbound, tmp_path):
    """**Markdown のリンクにしない。** 読み手が URL を取り出せなくなる。"""
    _, state = _state(tmp_path)
    reference = outbound.plan_reference(state)
    assert reference == COMMENT_URL
    assert "[" not in reference and "](" not in reference


def test_the_plan_line_names_the_plan(outbound, tmp_path):
    _, state = _state(tmp_path)
    assert outbound.plan_line(state) == f"改修計画: {COMMENT_URL}"


def test_a_missing_comment_is_stated_instead_of_a_blank(outbound, tmp_path):
    """投稿できていないことを黙らない。空欄だと読み手が探し始める。"""
    _, state = _state(tmp_path, plan_comment=None)
    assert "作成できていません" in outbound.plan_reference(state)


def test_the_file_mode_points_at_the_file(outbound, tmp_path):
    _, state = _state(tmp_path, plan_mode="file", plan_file="issues/plan.md")
    assert outbound.plan_reference(state) == "issues/plan.md"


# ---------- D1: 項目の指し方 ----------


def test_an_item_is_named_with_its_file_and_symbol(outbound, tmp_path):
    _, state = _state(tmp_path)
    assert outbound.item_lines(state, ["I-001"]) == ["I-001 `src/foo.py#Foo.handle`"]


# ---------- D2: 取り消しは件数だけ ----------


def test_a_drop_is_reported_as_a_count(outbound, tmp_path):
    _, state = _state(tmp_path)
    line = outbound.dropped_line(state, 3)
    assert "3 件" in line and "内訳は改修計画にある" in line
    assert COMMENT_URL in line


def test_the_report_does_not_list_the_deferred_breakdown(refactor, tmp_path, env_tmp_dir, capsys):
    """D2 — 進行の報告は件数だけを述べ、内訳は改修計画へ譲る。"""
    path, _ = _state(
        tmp_path,
        deferred_items=[
            {
                "item_id": "I-002",
                "path": "src/bar.py",
                "symbol": "Bar.run",
                "smell": "duplication",
                "defer_reason": "budget",
                "detail": "想定最大時間に収まらない",
            }
        ],
    )
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    out = capsys.readouterr().out
    # 計画に入らなかった提案は見送った改善項目に数えず、見送った提案の行に出す（#1660）
    assert "見送り: 0 件" in out
    assert "- 見送った提案: 1 件（理由別: budget 1 " in out  # 理由別の件数（AC26）
    assert "src/bar.py#Bar.run" not in out, "内訳を書いている"
    assert "想定最大時間に収まらない" not in out, "内訳を書いている"
    assert COMMENT_URL in out, "改修計画の生の URL が無い"


def test_the_report_does_not_call_items_adopted_without_the_final_gate(refactor, tmp_path, env_tmp_dir, capsys):
    """AC-1399-6: 最終ゲートを経ていない実行は、残った改善項目を「採用」と数えない。"""
    path, _ = _state(tmp_path, items=[_item(id="I-001", status="verified"), _item(id="I-002", status="verified")])
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    out = capsys.readouterr().out
    assert "採用: 未確定" in out and "採用: 2 件" not in out


def test_the_report_counts_each_item_once(refactor, tmp_path, env_tmp_dir, capsys):
    """採用・取り消し・見送りの和が項目の数を超えない（3042be53 の二重計上の回帰）。

    見送った項目（`status: deferred`、`deferred_items` にもある）は見送りにだけ数え、
    取り消しに数えない。
    """
    path, _ = _state(
        tmp_path,
        items=[
            _item(id="I-001", status="verified"),
            _item(id="I-002", status="reverted", failure_reason="検証の失敗"),
            _item(id="I-003", status="deferred", failure_reason="締め切り"),
        ],
        deferred_items=[
            {
                "item_id": "I-003",
                "path": "src/foo.py",
                "symbol": "Foo.handle",
                "smell": "long_method",
                "defer_reason": "not_done",
                "detail": "締め切り",
            }
        ],
        final_gate={"fix_rounds": 0, "checks": [], "status": "passed"},
    )
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    out = capsys.readouterr().out
    assert "採用: 1 件 / 取り消し: 1 件 / 見送り: 1 件" in out
    assert "not_done 1" in out


def test_the_report_names_the_plan_in_its_header(refactor, tmp_path, env_tmp_dir, capsys):
    path, _ = _state(tmp_path)
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert f"- 改修計画: {COMMENT_URL}" in capsys.readouterr().out


def test_the_report_still_names_each_item_with_its_symbol(refactor, tmp_path, env_tmp_dir, capsys):
    """D1 — 改善項目の表は残す。**取り消しの内訳とは別のものである。**"""
    path, _ = _state(tmp_path)
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert "src/foo.py#Foo.handle" in capsys.readouterr().out


# ---------- R2-001: 状態に無い項目のフォールバック ----------


def test_an_unknown_item_id_is_returned_without_a_label(outbound, tmp_path):
    """`item_id` が `state['items']` に無ければ、ラベルを付けず ID だけを返す。

    現状固定: `by_id.get(item_id)` が `None` を返す経路。ファイル名やシンボル名の
    参照をスキップし `str(item_id)` をそのまま返すフォールバックを固定する。
    """
    _, state = _state(tmp_path, items=[])
    assert outbound.item_lines(state, ["I-099"]) == ["I-099"]


# ---------- #1660: 見送りの件数を結果 JSON・計画のコメント・完了報告で揃える ----------


def _mixed_deferred_state(tmp_path):
    """見送った改善項目 1 件（not_done）と、計画に入らなかった提案 2 件（budget・duplicate）。"""
    return _state(
        tmp_path,
        items=[_item(id="I-001", status="verified"), _item(id="I-002", status="deferred", failure_reason="時間内に終わらない")],
        deferred_items=[
            {
                "item_id": "I-002",
                "path": "src/foo.py",
                "symbol": "Foo.handle",
                "smell": "long_method",
                "defer_reason": "not_done",
                "detail": "時間内に終わらない",
            },
            {
                "item_id": "C-003",
                "path": "src/bar.py",
                "symbol": "Bar.run",
                "smell": "duplication",
                "defer_reason": "budget",
                "detail": "予算",
            },
            {
                "item_id": "C-004",
                "path": "src/baz.py",
                "symbol": "Baz.go",
                "smell": "duplication",
                "defer_reason": "duplicate",
                "detail": "重複",
            },
        ],
        final_gate={"fix_rounds": 0, "checks": [], "status": "passed"},
    )


def _report_lines(refactor, path, env_tmp_dir, capsys):
    env_tmp_dir(path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    return capsys.readouterr().out.splitlines()


def _deferred_in(line, sep):
    return int(re.match(r"\d+", line.split(f"見送り{sep}")[1]).group())


def test_the_deferred_count_agrees_across_metrics_plan_and_report(refactor, tmp_path, env_tmp_dir, capsys):
    """AC10・AC12（#1660）: metrics.deferred・計画の件数の行・完了報告の件数の行の「見送り」がどれも 1。"""
    ledger, plan = sys.modules["refactor_lib.ledger"], sys.modules["refactor_lib.plan"]
    path, state = _mixed_deferred_state(tmp_path)
    metrics = ledger.tally(state).as_metrics()
    lines = _report_lines(refactor, path, env_tmp_dir, capsys)
    counts = next(line for line in lines if line.startswith("- 採用: "))
    assert metrics["deferred"] == 1
    assert _deferred_in(plan.counts_line(state), " ") == 1
    assert _deferred_in(counts, ": ") == 1
    proposals = next(line for line in lines if line.startswith("- 見送った提案: "))
    by_reason = dict(part.rsplit(" ", 1) for part in proposals.split("理由別: ")[1].rstrip("）").split(" / "))
    assert int(by_reason["not_done"]) + int(by_reason["test_failed"]) == metrics["deferred"]


def test_the_report_states_the_deferred_proposals_on_their_own_line(refactor, tmp_path, env_tmp_dir, capsys):
    """AC11（#1660）: 見送った提案の総数 3 と理由別の件数を件数の行と別の行に出し、和が総数と等しい。"""
    DEFER_REASONS = sys.modules["refactor_lib.vocabulary"].DEFER_REASONS
    path, _ = _mixed_deferred_state(tmp_path)
    lines = _report_lines(refactor, path, env_tmp_dir, capsys)
    proposals = next(line for line in lines if line.startswith("- 見送った提案: "))
    assert not proposals.startswith("- 採用: ")
    total = int(proposals.split("- 見送った提案: ")[1].split(" 件")[0])
    by_reason = {k: int(v) for k, v in (part.rsplit(" ", 1) for part in proposals.split("理由別: ")[1].rstrip("）").split(" / "))}
    assert total == 3
    assert list(by_reason) == list(DEFER_REASONS)
    assert by_reason == {r: (1 if r in ("budget", "duplicate", "not_done") else 0) for r in DEFER_REASONS}
    assert sum(by_reason.values()) == total


def test_the_report_counts_line_does_not_follow_the_deferred_proposals(refactor, tmp_path, env_tmp_dir, capsys):
    """AC13（#1660）: 見送った提案だけを増やしても、件数の行の「見送り」は変わらない。"""
    path, state = _mixed_deferred_state(tmp_path)
    before = _deferred_in(next(line for line in _report_lines(refactor, path, env_tmp_dir, capsys) if line.startswith("- 採用: ")), ": ")
    extra = {"item_id": "C-005", "path": "src/q.py", "symbol": "Q.x", "smell": "duplication", "defer_reason": "rank", "detail": "順位"}
    path, _ = _state(tmp_path, items=state["items"], deferred_items=[*state["deferred_items"], extra], final_gate=state["final_gate"])
    after = _deferred_in(next(line for line in _report_lines(refactor, path, env_tmp_dir, capsys) if line.startswith("- 採用: ")), ": ")
    assert before == after == 1
