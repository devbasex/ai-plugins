"""現状固定: 工程の担当へ渡す指示文（`prompt_decide`。drive から移す前の文面と一字一句同じ）。

触る関数はファイルごとに 1 行で、名前は昇順。ファイル単位で扱うファイル（whole_files）は、その後にファイル全体として並ぶ。
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


WHOLE = "（ファイル全体。関数の範囲を読めないため、ファイルの中のどの関数・クラスも提案できる）"


def test_propose_prompt_keeps_a_file_without_names_and_lists_whole_files_after_it():
    touched = Touched({"lib/cli.py": frozenset()}, ("web/app.ts",))
    got = _prompt(3, touched)
    assert got == HEAD.format(pr=3) + "- lib/cli.py: \n- web/app.ts: " + WHOLE + "\n" + TAIL


def test_propose_prompt_for_a_pr_without_python_lists_its_files_as_whole():
    # Python 以外の変更はすべて whole_files に入る。対象が空の指示文にしない
    got = _prompt(5, Touched({}, ("web/app.ts", "cmd/main.go")))
    assert got == HEAD.format(pr=5) + "- web/app.ts: " + WHOLE + "\n- cmd/main.go: " + WHOLE + "\n" + TAIL


ITEM = {"id": "I-002", "path": "src/b.py", "symbol": "g", "title": "t2"}
COMMIT = '結果は {"commit": "<最後のコミット>"}。'


def test_plan_prompt_lists_proposals_with_tier_technique_and_tests():
    proposals = [
        {"path": "src/a.py", "symbol": "f", "title": "t", "reason": "r"},
        {"path": "src/b.py", "symbol": "g", "title": "t2", "reason": "r2"},
    ]
    assert prompt_decide.plan(7, proposals) == (
        "PR #7 のリファクタリングの提案から、採る項目を選んで並べる（重なるものは 1 つにする）。\n"
        "- src/a.py f: t（r）\n- src/b.py g: t2（r2）\n"
        "項目ごとにグレード（tier: high・medium・low）と、手法（technique: extract_function などの snake_case の識別子）と、"
        "直す前に振る舞いを固定するテストを足すか（tests: true / false）を付ける。所要の分は書かない（配分の表から見積もる）。\n"
        '最後の行に JSON {"items": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>",'
        ' "tier": "<グレード>", "technique": "<手法>", "tests": <真偽>}]} を出す。'
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
        "キーごとに 1 コミットにしてトレーラー `Flaky-Fix: <キー>` を付ける（push しない）。\n"
    )
    pending = {"keys": ["pytest:a", "pytest:b"], "sendback": [{"commit": "c1", "detail": "d1"}]}
    assert prompt_decide.flaky(pending) == head + "- pytest:a\n- pytest:b\n前回の違反: c1 d1\n" + COMMIT
    assert prompt_decide.flaky({"keys": ["pytest:a"], "sendback": []}) == head + "- pytest:a\n" + COMMIT


def test_final_fix_prompt_names_the_scope_and_the_failures():
    assert prompt_decide.final_fix(7, ("src", "lib"), ["tests/a.py::t", "pytest"]) == (
        "PR #7 の CI で次のテストが落ちた。リファクタリング計画の対象範囲（src, lib）の中だけを変えて直し、"
        "コミットする（push しない）。\n- tests/a.py::t\n- pytest\n" + COMMIT
    )


CANDIDATES = "- #12: `invoke.py` の補いの並び（3 つの単位が同じ関数へ足す）\n"


def test_propose_prompt_with_candidates_adds_the_plan_section_before_the_result_line():
    touched = Touched({"src/geo.py": frozenset({"area"})})
    got = prompt_decide.propose(7, touched, CANDIDATES)
    section = "実装計画が挙げた候補（採るかは他の提案と同じ基準で判断する）:\n" + CANDIDATES
    assert got == HEAD.format(pr=7) + "- src/geo.py: area\n" + section + TAIL


def test_propose_prompt_keeps_the_result_line_apart_from_candidates_without_a_trailing_newline():
    touched = Touched({"src/geo.py": frozenset({"area"})})
    assert prompt_decide.propose(7, touched, CANDIDATES.rstrip("\n")) == prompt_decide.propose(7, touched, CANDIDATES)


def test_propose_prompt_with_empty_candidates_is_the_same_as_without_them():
    touched = Touched({"src/geo.py": frozenset({"area"})})
    assert prompt_decide.propose(7, touched, "") == prompt_decide.propose(7, touched)


def test_final_fix_local_switches_from_ci_and_carries_the_excerpts():
    # #4566: 手元で落ちた抜粋をそのまま載せる（文言は比べない）
    ci = prompt_decide.final_fix(7, ("src",), ["- #1 unit（終了コード 1）"])
    local = prompt_decide.final_fix(7, ("src",), ["- #1 unit（終了コード 1）"], local=True)
    assert ci != local
    assert "- #1 unit（終了コード 1）" in local


def test_final_fix_local_stays_under_the_byte_limit_and_counts_the_dropped():
    blocks = [f"- #{i} 単体（終了コード 1）\n" + "あ" * 7_000 for i in range(1, 11)]
    got = prompt_decide.final_fix(7, ("src",), blocks, local=True)
    assert len(got.encode("utf-8")) <= prompt_decide.PROMPT_BYTES
    kept = sum(1 for b in blocks if b in got)
    assert 0 < kept < len(blocks)
    assert f"ほかに {len(blocks) - kept} 件" in got
