"""指摘の基準の正本とレビューの宣言（重点とプロファイル）の読み取り（lib/review_criteria.py・#1287・#1910）。"""

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


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps({"version": 1, "focus": "文字列"}),
        json.dumps({"version": 4, "focus": ["x"]}),
        json.dumps({"version": True, "focus": ["x"]}),
        json.dumps({"version": 2, "focus": "x"}),
        json.dumps(["x"]),
        json.dumps({"version": 1, "focus": ["", "x"]}),
    ],
)
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
    p = subprocess.run(
        [sys.executable, str(LIB / "review_criteria.py"), "reviewer", "--root", str(tmp_path)], capture_output=True, text=True
    )
    assert p.returncode == 0 and "読めない" in p.stderr
    assert _criterion_numbers(p.stdout) == ["1", "2", "4"]


def test_cli_without_root_ignores_declaration(tmp_path):
    p = subprocess.run([sys.executable, str(LIB / "review_criteria.py"), "fixer"], capture_output=True, text=True, cwd=tmp_path)
    assert p.returncode == 0 and "waive_kind" in p.stdout and not p.stderr


# --- レビューのプロファイル（#1910） -------------------------------------------------

NOVEL = {
    "criteria": {"1": "設定と本文が食い違う", "4": "文が日本語として不自然", "5": "人物の口調が設定とずれる"},
    "not_written": ["好みの文体", "設定に無い新しい案"],
    "waive_kinds": {"preference": "好みの文体", "out_of_scope": "設定を変える提案"},
    "reply": "直しません。{inner}に当たるためです。",
    "categories": {"manuscript": {"paths": ["works/*/chapters/*/text.md"], "instructions": "## 原稿の観点"}},
    "replace_auto_categories": True,
}


def _profile(root: Path, profile: dict, **extra) -> tuple[rc.Focus, rc.Profile]:
    _declare(root, json.dumps({"version": 2, "profile": profile, **extra}, ensure_ascii=False))
    decl = rc.load_review_decl(root)
    assert decl.profiles is None
    return decl.focus, decl.profile


def _cli(block: str, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LIB / "review_criteria.py"), block, "--root", str(root)], capture_output=True, text=True)


@pytest.mark.parametrize("decl", [None, {"version": 1, "focus": ["重点 A"]}, {"version": 2, "focus": ["重点 A"]}, {"version": 2}])
def test_without_profile_output_is_unchanged(tmp_path, decl):
    """受け入れ条件 1・I1: プロファイルが無いときは、既定の節・返信と同じで、状態にプロファイルの項目を置かない。"""
    if decl is not None:
        _declare(tmp_path, json.dumps(decl, ensure_ascii=False))
    decl = rc.load_review_decl(tmp_path)
    focus, profile = decl.focus, decl.profile
    assert profile == rc.NO_PROFILE and decl.profiles is None
    assert rc.reviewer_block(focus, None, profile) == rc.reviewer_block(focus)
    assert rc.fixer_block(focus, None, profile) == rc.fixer_block(focus)
    assert "コードを変えずに" in rc.fixer_block(focus)
    assert rc.criterion_numbers(profile) == rc.CRITERION_NUMBERS
    assert "profile_status" not in rc.as_state(focus, None, profile)
    for kind in rc.WAIVE_KINDS:
        assert rc.waiver_reply(kind, focus.names, None, profile) == rc.waiver_reply(kind, focus.names)


def test_profile_criteria_and_not_written_replace_defaults_but_keep_criterion_2(tmp_path):
    """受け入れ条件 2・I2: プロファイルの基準が番号つきで並び、外せない基準 2 が残り、既定の 1・4 と「字句や言い回し」が出ない。"""
    _declare(tmp_path, json.dumps({"version": 2, "profile": NOVEL}, ensure_ascii=False))
    p = _cli("reviewer", tmp_path)
    assert p.returncode == 0 and not p.stderr
    lines = p.stdout.splitlines()
    assert "1. 設定と本文が食い違う" in lines and "4. 文が日本語として不自然" in lines and "5. 人物の口調が設定とずれる" in lines
    assert f"2. {rc.CRITERIA[2]}" in lines
    assert rc.CRITERIA[1] not in p.stdout and rc.CRITERIA[4] not in p.stdout and "字句や言い回し" not in p.stdout
    assert "書かないもの: 好みの文体・設定に無い新しい案" in lines


def test_profile_criteria_merge_with_focus_in_order(tmp_path):
    focus, profile = _profile(tmp_path, {"criteria": {"5": "口調"}}, focus=["重点 A"])
    assert _criterion_numbers(rc.reviewer_block(focus, None, profile)) == ["2", "3"]
    assert [ln.split(".")[0] for ln in rc.reviewer_block(focus, None, profile).splitlines() if ln[:1].isdigit()] == ["2", "3", "5"]
    assert rc.criterion_numbers(profile) == (2, 3, 5)


def test_profile_keys_take_effect_individually(tmp_path):
    """I3: criteria だけを書けば、書かないもの・見送りの種類・返信は既定のまま。"""
    focus, profile = _profile(tmp_path, {"criteria": {"1": "設定と本文が食い違う"}})
    assert profile.status == "declared"
    assert f"書かないもの: {rc.NOT_WRITTEN}" in rc.reviewer_block(focus, None, profile)
    assert rc.waive_kinds(profile) == rc.WAIVE_KINDS
    assert rc.waiver_reply("wording", (), None, profile) == rc.waiver_reply("wording")


def test_empty_not_written_drops_the_line(tmp_path):
    focus, profile = _profile(tmp_path, {"not_written": []})
    assert "書かないもの" not in rc.reviewer_block(focus, None, profile)
    assert "書かないもの" not in rc.fixer_block(focus, None, profile)


def test_profile_reply_uses_kind_name_and_drops_focus_phrase(tmp_path):
    """受け入れ条件 3・I8: 返信はプロファイルの雛形の {inner} に種類の名前を入れた文で、重点の句を足さない。"""
    focus, profile = _profile(tmp_path, NOVEL, focus=["重点 A"])
    assert rc.waiver_reply("preference", focus.names, None, profile) == "直しません。好みの文体に当たるためです。"
    assert rc.waiver_reply("preference", focus.names, "根拠: X", profile) == "直しません。好みの文体に当たるためです。根拠: X"
    assert "重点 A" in rc.waiver_reply("wording", focus.names)
    with pytest.raises(ValueError):
        rc.waiver_reply("wording", (), None, profile)


def test_reply_keeps_other_braces(tmp_path):
    _, profile = _profile(tmp_path, {"reply": "直しません（{x}）。{inner}"})
    assert rc.waiver_reply("wording", (), None, profile) == f"直しません（{{x}}）。{rc.WAIVE_KINDS['wording']}"


def test_fixer_block_with_profile_speaks_of_target_files(tmp_path):
    """受け入れ条件 7: プロファイルがあるとき、修正担当の節は「コードを変え」と書かず、例の返信もプロファイルの種類で組む。"""
    focus, profile = _profile(tmp_path, NOVEL)
    block = _cli("fixer", tmp_path).stdout
    assert "コードを変え" not in block and "対象のファイルを変えずに" in block
    assert "- `preference`: 好みの文体" in block and "`wording`" not in block
    assert "> 直しません。好みの文体に当たるためです。" in block
    assert block.rstrip("\n").startswith(rc.fixer_block(focus, None, profile))


@pytest.mark.parametrize(
    "profile",
    [
        {**NOVEL, "reply": "直しません。"},
        {**NOVEL, "reply": "{inner}{inner}"},
        {"criteria": {"2": "上書き"}},
        {"criteria": {"3": "重点と重なる"}},
        {"criteria": {"0": "x"}},
        {"criteria": {}},
        {"criteria": {"1": ""}},
        {"not_written": "文字列"},
        {"waive_kinds": {"Bad": "x"}},
        {"waive_kinds": {}},
        {"categories": {"m": {"paths": [], "instructions": "x"}}},
        {"categories": {"m": {"paths": ["a"], "instructions": ""}}},
        {"replace_auto_categories": "yes"},
        {"critera": {"1": "綴りの誤り"}},
        ["x"],
    ],
)
def test_broken_profile_falls_back_to_defaults_and_keeps_focus(tmp_path, profile):
    """受け入れ条件 8・I4: 壊れたプロファイルは丸ごと捨て、終了コード 0 で既定の節を出し、理由を標準エラーへ出す。重点は効く。"""
    focus, prof = _profile(tmp_path, profile, focus=["重点 A"])
    assert prof.status == "unreadable" and prof.error and str(tmp_path) in prof.error
    assert focus == rc.Focus("declared", ("重点 A",))
    p = _cli("reviewer", tmp_path)
    assert p.returncode == 0 and "レビューのプロファイルを読めない" in p.stderr
    assert _criterion_numbers(p.stdout) == ["1", "2", "3", "4"] and "字句や言い回し" in p.stdout
    st = rc.as_state(focus, None, prof)
    assert st["profile_status"] == "unreadable" and st["profile_error"] == prof.error and "profile" not in st
    assert st["reviewer_block"] == rc.reviewer_block(focus)


def test_profile_record_roundtrips_and_state_carries_it(tmp_path):
    focus, profile = _profile(tmp_path, NOVEL)
    assert rc.profile_from(profile.record()) == profile
    st = rc.as_state(focus, None, profile)
    assert st["profile_status"] == "declared" and st["profile"] == NOVEL
    assert rc.state_profile(st) == profile
    assert rc.state_profile({"profile_status": "unreadable"}) == rc.NO_PROFILE
    assert rc.profile_from({"reply": "壊れた"}) == rc.NO_PROFILE and rc.profile_from(None) == rc.NO_PROFILE


def test_profile_categories_match_like_pace_globs(tmp_path):
    """受け入れ条件 5・決定 2: `*` は `/` をまたがない。"""
    _, profile = _profile(tmp_path, NOVEL)
    assert rc.profile_categories(profile, ["works/novel-a/chapters/011/text.md"]) == [("manuscript", "## 原稿の観点")]
    assert rc.profile_categories(profile, ["works/novel-a/x/chapters/011/text.md", "README.md"]) == []
    assert rc.profile_categories(rc.NO_PROFILE, ["works/novel-a/chapters/011/text.md"]) == []
