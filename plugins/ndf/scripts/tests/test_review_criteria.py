"""指摘の基準の正本とレビューの重点の宣言（lib/review_criteria.py・#1287）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import review_criteria as rc  # noqa: E402

# このリポジトリの目的の語。重点の宣言が無いときに既定の文へ出てはいけない（受け入れ条件 2）
REPO_GOAL_WORDS = ("トークン", "所要時間", "処理の回数")


def _declare(root: Path, text: str) -> Path:
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    (root / ".ndf" / "review.json").write_text(text, encoding="utf-8")
    return root


def _criterion_numbers(block: str) -> list[str]:
    return [ln.split(".", 1)[0] for ln in block.splitlines() if ln[:2] in ("1.", "2.", "3.", "4.")]


def test_without_declaration_has_no_criterion_3_and_no_repo_goal(tmp_path):
    focus = rc.load_focus(tmp_path)
    assert focus == rc.Focus("none")
    block = rc.reviewer_block(focus)
    assert _criterion_numbers(block) == ["1", "2", "4"]
    for text in [block, rc.fixer_block(focus)] + [rc.waiver_reply(k) for k in rc.WAIVE_KINDS]:
        assert not any(w in text for w in REPO_GOAL_WORDS), text


def test_waiver_reply_without_focus_is_template_and_kind_name_only():
    for kind, name in rc.WAIVE_KINDS.items():
        assert rc.waiver_reply(kind) == rc.REPLY.format(inner=name)
        assert rc.waiver_reply(kind, []) == rc.waiver_reply(kind)


def test_empty_focus_list_is_no_declaration(tmp_path):
    _declare(tmp_path, json.dumps({"version": 1, "focus": []}))
    assert rc.load_focus(tmp_path).status == "none"


def test_declared_focus_appears_by_name_in_criterion_3(tmp_path):
    _declare(tmp_path, json.dumps({"version": 1, "focus": ["起動の回数を増やす変更", "外部 API の費用"], "x": 1}))
    focus = rc.load_focus(tmp_path)
    assert focus == rc.Focus("declared", ("起動の回数を増やす変更", "外部 API の費用"))
    block = rc.reviewer_block(focus)
    assert _criterion_numbers(block) == ["1", "2", "3", "4"]
    line3 = next(ln for ln in block.splitlines() if ln.startswith("3."))
    assert "起動の回数を増やす変更 / 外部 API の費用" in line3
    reply = rc.waiver_reply("wording", focus.names)
    assert "起動の回数を増やす変更 / 外部 API の費用" in reply and rc.WAIVE_KINDS["wording"] in reply


@pytest.mark.parametrize("text", ["{not json", json.dumps({"version": 1, "focus": "文字列"}),
                                  json.dumps({"version": 2, "focus": ["x"]}), json.dumps(["x"]),
                                  json.dumps({"version": 1, "focus": ["", "x"]})])
def test_unreadable_declaration_falls_back_to_criteria_1_2_4(tmp_path, text):
    _declare(tmp_path, text)
    focus = rc.load_focus(tmp_path)
    assert focus.status == "unreadable" and focus.names == () and focus.error
    assert _criterion_numbers(rc.reviewer_block(focus)) == ["1", "2", "4"]


def test_unreadable_when_path_is_a_directory(tmp_path):
    (tmp_path / ".ndf" / "review.json").mkdir(parents=True)
    focus = rc.load_focus(tmp_path)
    assert focus.status == "unreadable" and focus.error


def test_unknown_waive_kind_raises():
    with pytest.raises(ValueError):
        rc.waiver_reply("style")


def test_focus_from_state_values():
    assert rc.focus_from("declared", ["a"]) == rc.Focus("declared", ("a",))
    assert rc.focus_from("declared", []) == rc.NO_FOCUS
    assert rc.focus_from("unreadable", [], "壊れた") == rc.Focus("unreadable", (), "壊れた")
    assert rc.focus_from(None, None) == rc.NO_FOCUS


def test_as_state_carries_reviewer_block(tmp_path):
    _declare(tmp_path, json.dumps({"version": 1, "focus": ["重点 A"]}))
    st = rc.as_state(rc.load_focus(tmp_path))
    assert st["status"] == "declared" and st["focus"] == ["重点 A"] and st["error"] is None
    assert "重点 A" in st["reviewer_block"]


def test_cli_unreadable_reports_on_stderr_and_exits_zero(tmp_path):
    _declare(tmp_path, "{broken")
    p = subprocess.run([sys.executable, str(LIB / "review_criteria.py"), "reviewer", "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert p.returncode == 0 and "読めない" in p.stderr
    assert _criterion_numbers(p.stdout) == ["1", "2", "4"]


def test_cli_without_root_ignores_declaration(tmp_path):
    p = subprocess.run([sys.executable, str(LIB / "review_criteria.py"), "fixer"], capture_output=True, text=True,
                       cwd=tmp_path)
    assert p.returncode == 0 and "waive_kind" in p.stdout and not p.stderr
