"""最終ゲート（Step 7）の修正の取り込みのテスト（#436 B5 / PR #447 レビュー指摘）。

**最終ゲートの修正は適用ラウンドの修正と別物である。** 落ちているのは全体のテストで、
どの改善項目にも提案ラウンドにも属さない。`merge-fix` を流用すると 3 つの壊れ方をする。

| 流用したときに起きること | このファイルで固定するテスト |
| --- | --- |
| 「起点 None」で止まり、修正を 1 件も取り込めない | `test_the_gate_records_the_fix_base_before_it_asks_for_a_fix` |
| 古い起点のせいで正常なコミットまで取り消される | `test_the_gate_does_not_reuse_the_apply_round_fix_base` |
| `Item-Id` を要求して全件が不正になる | `test_the_final_fix_commit_does_not_need_an_item_id` |
"""
from __future__ import annotations

import pytest

from crossref_helpers import make_state, read_state, write_result


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


def _gate_state(tmp_path, **over):
    return make_state(tmp_path, phase="final", outer_round=1,
                      workflow_step=True, **over)


@pytest.fixture
def gate_spy(refactor, monkeypatch):
    """テストの実行・git・push を差し替える。"""
    seen: dict[str, list] = {"tests": [], "pushed": []}

    def fake_run(command, cwd, timeout, grace=5.0):
        seen["tests"].append(command)
        return seen.get("test_code", 0), False

    monkeypatch.setattr(refactor, "_run_with_timeout", fake_run)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "HEADSHA")
    monkeypatch.setattr(refactor, "_push_head",
                        lambda state: seen["pushed"].append(state["head_branch"]))
    return seen


# ---------- 起点と担当を記録して返す ----------

def test_the_gate_records_the_fix_base_before_it_asks_for_a_fix(
    refactor, tmp_path, env_tmp_dir, gate_spy
):
    """**起点を記録しないと、取り込み側が範囲を確定できない。**"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())

    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["fix_base_sha"] == "HEADSHA"
    assert gate["impl"] in ("claude", "codex", "agy", "kiro")


def test_the_gate_emits_the_fix_impl_and_round(
    refactor, tmp_path, env_tmp_dir, gate_spy, capsys
):
    """呼び出し側は担当を**出力から**受け取る。控えを読み直させない。"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        refactor.cmd_final_gate(_args())

    out = capsys.readouterr().out
    impl = read_state(state_path)["final_gate"]["impl"]
    assert "FINAL_GATE=failing" in out
    assert f"FINAL_FIX_IMPL={impl}" in out
    assert "FINAL_FIX_ROUND=1" in out


def test_the_gate_does_not_reuse_the_apply_round_fix_base(
    refactor, tmp_path, env_tmp_dir, gate_spy
):
    """**適用ラウンドの起点は流用しない。**

    あれは最後の群の検証が落ちた地点である。そこから HEAD までには検証を通った
    正常なコミットが並ぶため、範囲に含めるとその全部が未申告として取り消される。
    """
    rounds = [{
        "round": 1, "kind": "structure", "impl": "codex", "reviewers": [],
        "impl_model": {}, "reviewer_models": {}, "proposed": {}, "merged": 0,
        "adopted": 0, "deferred": 0, "items": [], "apply_rounds": [],
        "apply_round": 0, "fix_rounds": 1, "durations": {}, "reviews": [],
        "fix_base_sha": "OLDBASE",
    }]
    state_path = _gate_state(tmp_path, rounds=rounds)
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        refactor.cmd_final_gate(_args())

    state = read_state(state_path)
    assert state["final_gate"]["fix_base_sha"] == "HEADSHA"
    assert state["rounds"][0]["fix_base_sha"] == "OLDBASE", "適用側の控えは触らない"


def test_the_same_runtime_keeps_fixing_across_fix_rounds(
    refactor, tmp_path, env_tmp_dir, gate_spy
):
    """**担当は最初に落ちたときだけ決める。** 直しかけの文脈を持つ者が続ける。"""
    state_path = _gate_state(
        tmp_path, final_gate={"fix_rounds": 1, "checks": [], "impl": "kiro"})
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        refactor.cmd_final_gate(_args())

    state = read_state(state_path)
    assert state["final_gate"]["impl"] == "kiro"
    assert state.get("apply_seq", 0) == 0, "輪番は 2 回目以降で進めない"


def test_a_passing_gate_records_no_fix_impl(
    refactor, tmp_path, env_tmp_dir, gate_spy
):
    """通ったときは担当を決めない。輪番も進めない。"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_final_gate(_args())

    state = read_state(state_path)
    assert "impl" not in state["final_gate"]
    assert state.get("apply_seq", 0) == 0


# ---------- 取り込みは専用の経路 ----------

def _failing_gate_state(tmp_path, **over):
    gate = {"fix_rounds": 1, "checks": [], "status": "failing",
            "impl": "codex", "fix_base_sha": "BASE"}
    gate.update(over.pop("final_gate", {}))
    return _gate_state(tmp_path, final_gate=gate, **over)


@pytest.fixture
def merge_spy(refactor, monkeypatch):
    """`merge-final-fix` が触る git を差し替える。"""
    seen: dict[str, list] = {"pushed": [], "reverted": []}

    monkeypatch.setattr(refactor, "_discard_impl_leftovers", lambda state, work: None)
    monkeypatch.setattr(refactor, "_push_head",
                        lambda state: seen["pushed"].append("push"))
    monkeypatch.setattr(
        refactor, "_revert_item_commits",
        lambda state, item, dry_run=False: seen["reverted"].append(item) or 1)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: (
        "HEADSHA" if args[:2] == ["rev-parse", "HEAD"] else "C1FULL"))
    monkeypatch.setattr(
        refactor, "commits_in_range",
        lambda work, base, head: None if not base else ["C1FULL"])
    monkeypatch.setattr(refactor, "collect_commit_facts", lambda *a, **k: [
        {"sha": "C1FULL", "exists": True, "files": ["src/foo.py"],
         "trailers": seen.get("trailers", {"Impl-Runtime": "codex",
                                           "Impl-Model": "gpt-5.5"}),
         "diff_lines": 10, "touches_tests": False, "test_status": "skipped"},
    ])
    return seen


def test_a_clean_final_fix_is_taken_in_and_published(
    refactor, tmp_path, env_tmp_dir, merge_spy
):
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-final-fix",
                 {"elapsed_seconds": 42, "commits": [{"sha": "C1FULL"}]})

    refactor.cmd_merge_final_fix(_args())

    gate = read_state(state_path)["final_gate"]
    assert merge_spy["reverted"] == [], "問題が無ければ取り消さない"
    assert merge_spy["pushed"] == ["push"], "取り込んだら公開する"
    assert gate["fix_base_sha"] == "HEADSHA", "起点は取り込んだ地点まで進む"
    assert gate["fix_commits"] == ["C1FULL"]
    assert gate["durations"]["fix"] == 42


def test_the_final_fix_commit_does_not_need_an_item_id(
    refactor, tmp_path, env_tmp_dir, merge_spy
):
    """**`Item-Id` と `Round` は求めない。**

    最終ゲートの修正は改善項目にも提案ラウンドにも属さない。求めると、実在しない
    番号を実装担当が作ることになる。
    """
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    merge_spy["trailers"] = {"Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"}
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    refactor.cmd_merge_final_fix(_args())

    assert merge_spy["reverted"] == []


def test_a_missing_impl_trailer_reverts_the_range(
    refactor, tmp_path, env_tmp_dir, merge_spy
):
    """誰が直したかは残す。欠けていれば取り込まない。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    merge_spy["trailers"] = {"Impl-Runtime": "codex"}
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    refactor.cmd_merge_final_fix(_args())

    assert len(merge_spy["reverted"]) == 1
    assert merge_spy["pushed"] == ["push"], "取り消しも公開する"


def test_an_out_of_scope_final_fix_reverts_the_range(
    refactor, tmp_path, env_tmp_dir, merge_spy, monkeypatch
):
    """**最終ゲートでも `--scope` の外を触ってよい理由は無い。**"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "collect_commit_facts", lambda *a, **k: [
        {"sha": "C1FULL", "exists": True, "files": ["docs/other.md"],
         "trailers": {"Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
         "diff_lines": 10, "touches_tests": False, "test_status": "skipped"},
    ])
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    refactor.cmd_merge_final_fix(_args())

    assert len(merge_spy["reverted"]) == 1


def test_an_unreported_commit_reverts_the_range(
    refactor, tmp_path, env_tmp_dir, merge_spy, monkeypatch
):
    """申告から漏れたコミットは検証を受けていない。範囲ごと取り消す。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_unassigned_fix_commits",
                        lambda work, reported, ordered: ["C2FULL"])
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    refactor.cmd_merge_final_fix(_args())

    assert len(merge_spy["reverted"]) == 1


def test_the_commit_test_status_is_not_checked(
    refactor, tmp_path, env_tmp_dir, merge_spy, monkeypatch
):
    """**コミットごとのテストは走らせない**（決定 11 の排他を破らないため）。

    合否は直後の `final-gate` が採った側で 1 度だけ見る。
    """
    seen: list = []
    state_path = _failing_gate_state(tmp_path, ci_check="tests")
    env_tmp_dir(state_path)

    def spy_facts(work, shas, in_range, test_command, head_branch, **k):
        seen.append(test_command)
        return [{"sha": "C1FULL", "exists": True, "files": ["src/foo.py"],
                 "trailers": {"Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
                 "diff_lines": 10, "touches_tests": False,
                 "test_status": "skipped"}]

    monkeypatch.setattr(refactor, "collect_commit_facts", spy_facts)
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    refactor.cmd_merge_final_fix(_args())

    assert seen == [""], "テストコマンドを渡さない"
    assert merge_spy["reverted"] == [], "`skipped` を失敗として扱わない"


def test_a_range_that_cannot_be_determined_does_not_take_anything_in(
    refactor, tmp_path, env_tmp_dir, merge_spy
):
    """起点が無ければ取り込まない。**空の範囲と混同しない。**"""
    state_path = _failing_gate_state(tmp_path, final_gate={"fix_base_sha": None})
    env_tmp_dir(state_path)
    write_result(state_path, "codex-final-fix", {"commits": []})

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_final_fix(_args())

    assert e.value.code == 2
    assert merge_spy["pushed"] == []


def test_the_take_in_needs_the_gate_to_run_first(
    refactor, tmp_path, env_tmp_dir, merge_spy
):
    """担当が無いまま呼ばれたら**進行ごと止める**（終了コード 4）。"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_final_fix(_args())

    assert e.value.code == 4


# ---------- 名前の取り決め ----------

def test_the_final_fix_result_file_has_no_round_number(refactor):
    """**最終ゲートは提案ラウンドの外にある。** 番号を名前に入れない。

    `launch-cli.sh` の `--stem-template "{agent}-final-fix"` と揃える。
    """
    assert refactor.stem_for("codex", "final-fix", 130) == "codex-final-fix"
    assert refactor.stem_for("codex", "fix", 130, 2) == "codex-fix-r2"


# ---------- 起動（launch-cli.sh） ----------

def _launch_final_fix(tmp_path):
    """`launch-cli.sh` に final-fix のプロンプトを組み立てさせて中身を返す。

    ラウンド番号を渡さずに起動できることも、ここで固定する。
    """
    import os
    import pathlib
    import subprocess

    launch = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "launch-cli.sh")
    state_path = make_state(tmp_path)
    for name in ("work", "codex"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    subprocess.run(
        [str(launch), "codex", "final-fix", "130"],
        env={**os.environ,
             "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
        check=True, capture_output=True, text=True,
    )
    return (state_path.parent / "codex-final-fix-prompt.md").read_text(
        encoding="utf-8")


def test_the_final_fix_phase_launches_without_a_round(tmp_path):
    """**ラウンド番号を要求しない。** Step 7 は提案ラウンドの外にある。"""
    text = _launch_final_fix(tmp_path)
    assert "最終ゲート" in text
    assert "RF_" not in text, "雛形の変数が生のまま残らない"


def test_the_final_fix_prompt_does_not_ask_for_an_item_id(tmp_path):
    """項目に属さない修正なので、`Item-Id` を書かせない。"""
    text = _launch_final_fix(tmp_path)
    assert "Impl-Runtime:" in text
    assert "Item-Id:" not in text
