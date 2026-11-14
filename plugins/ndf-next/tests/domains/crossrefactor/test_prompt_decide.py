"""現状固定: 工程の担当へ渡す指示文（`prompt_decide`。drive から移す前の文面と一字一句同じ）。

触る関数はファイルごとに 1 行で、名前は昇順。ファイル単位で扱うファイル（whole_files）は指示文に入らない。
"""

from __future__ import annotations

from ndf.domains.crossrefactor import prompt_decide
from ndf.domains.crossrefactor.symbols_decide import Touched

HEAD = "PR #{pr} が触る次の関数・クラスについて、振る舞いを変えないリファクタリングを提案する。ほかの関数は提案しない。\n"
TAIL = '最後の行に JSON {"proposals": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>"}]} を出す。'


def _prompt(pr: int, touched: Touched) -> str:
    # drive は状態ファイルの touched（as_json の形。ファイルは名前の順）から組み直して渡す
    state = {"touched": touched.as_json()}
    return prompt_decide.propose(pr, Touched.from_json(state["touched"]))


def test_propose_prompt_lists_each_file_with_sorted_names():
    touched = Touched({"src/geo.py": frozenset({"area", "Shape.size"}), "lib/a.py": frozenset({"f"})})
    got = _prompt(7, touched)
    assert got == HEAD.format(pr=7) + "- lib/a.py: f\n- src/geo.py: Shape.size, area\n" + TAIL


def test_propose_prompt_with_no_symbols_leaves_an_empty_line():
    assert _prompt(12, Touched({})) == HEAD.format(pr=12) + "\n" + TAIL


def test_propose_prompt_keeps_a_file_without_names_and_ignores_whole_files():
    touched = Touched({"lib/cli.py": frozenset()}, ("web/app.ts",))
    got = _prompt(3, touched)
    assert got == HEAD.format(pr=3) + "- lib/cli.py: \n" + TAIL
    assert "web/app.ts" not in got


ITEM = {"id": "I-002", "path": "src/b.py", "symbol": "g", "title": "t2"}
COMMIT = '結果は {"commit": "<最後のコミット>"}。'


def test_plan_prompt_lists_proposals_with_tier_and_estimate():
    proposals = [
        {"path": "src/a.py", "symbol": "f", "title": "t", "reason": "r"},
        {"path": "src/b.py", "symbol": "g", "title": "t2", "reason": "r2"},
    ]
    assert prompt_decide.plan(7, proposals) == (
        "PR #7 のリファクタリングの提案から、採る項目を選んで並べる（重なるものは 1 つにする）。\n"
        "- src/a.py f: t（r）\n- src/b.py g: t2（r2）\n"
        "項目ごとにグレード（tier: high・medium・low）と見積り（estimate: テストの追加と実装の分）を付ける。\n"
        '最後の行に JSON {"items": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>",'
        ' "tier": "<グレード>", "estimate": {"test": <分>, "implement": <分>}}]} を出す。'
    )


def test_add_tests_and_implement_prompts():
    assert prompt_decide.add_tests(ITEM) == (
        "I-002（src/b.py の g）: t2 を直す前に、今の振る舞いを固定するテストを足してコミットする（push しない）。"
        ' 結果は {"tests": ["<足したテストのファイル>"]}。'
    )
    assert prompt_decide.implement(ITEM) == (
        "I-002（src/b.py の g）: t2 を、振る舞いを変えずに直してコミットする（push しない）。ほかの関数は変えない。 " + COMMIT
    )


def test_fix_prompt_sorts_failures_and_adds_only_feasible_judgements():
    judged = [
        {"by": "codex", "reason": "r1", "evidence": "e1", "feasible": True},
        {"by": "kiro", "reason": "r2", "evidence": "e2", "feasible": False},
    ]
    head = "の変更で、次の検査かテストが落ちた（<suite>:<ファイル>）。この項目の変更の中で直してコミットする。\n"
    got = prompt_decide.fix({**ITEM, "feasibility": judged}, frozenset({"pytest:b", "pytest:a"}))
    assert got == "I-002（src/b.py の g）: t2 " + head + "- pytest:a\n- pytest:b\n成立の判定（codex）: r1 e1\n" + COMMIT
    assert prompt_decide.fix(ITEM, frozenset({"pytest:a"})) == "I-002（src/b.py の g）: t2 " + head + "- pytest:a\n" + COMMIT


def test_judge_prompt_names_the_item_in_the_answer_form():
    assert prompt_decide.judge(ITEM, frozenset({"pytest:b", "pytest:a"})) == (
        "I-002（src/b.py の g）: t2 の修正が停滞か振動した。次の失敗を、項目の変更（振る舞いを変えないリファクタリング）の中で"
        "直せるかを判定する。\n- pytest:a\n- pytest:b\n"
        '最後の行に JSON {"items": [{"item": "I-002", "feasible": <true|false>, "reason": "<理由>", "evidence": "<根拠>"}]} を出す。'
    )


def test_flaky_prompt_lists_keys_and_previous_violations():
    head = (
        "次のテストは打ち直すと通るフレーキーなテストである（<suite>:<ファイル>）。テストのファイルだけを直し、"
        "鍵ごとに 1 コミットにしてトレーラー `Flaky-Fix: <鍵>` を付ける（push しない）。\n"
    )
    pending = {"keys": ["pytest:a", "pytest:b"], "sendback": [{"commit": "c1", "detail": "d1"}]}
    assert prompt_decide.flaky(pending) == head + "- pytest:a\n- pytest:b\n前回の違反: c1 d1\n" + COMMIT
    assert prompt_decide.flaky({"keys": ["pytest:a"], "sendback": []}) == head + "- pytest:a\n" + COMMIT


def test_final_fix_prompt_names_the_scope_and_the_failures():
    assert prompt_decide.final_fix(7, ("src", "lib"), ["tests/a.py::t", "pytest"]) == (
        "PR #7 の CI で次のテストが落ちた。リファクタリング計画の対象範囲（src, lib）の中だけを変えて直し、"
        "コミットする（push しない）。\n- tests/a.py::t\n- pytest\n" + COMMIT
    )
