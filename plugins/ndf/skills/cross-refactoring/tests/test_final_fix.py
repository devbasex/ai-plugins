"""最終ゲート（Step 7）の修正の取り込みのテスト（#436 B5 / PR #447 レビュー指摘）。

**最終ゲートの修正は検証の中の修正と別物である。** 落ちているのは全体のテストで、
どの改善項目にも属さない。`merge-fix` を流用すると 3 つの壊れ方をする。
修正担当は実装担当が担う（#933 の決定 1。輪番は無い）。

| 流用したときに起きること | このファイルで固定するテスト |
| --- | --- |
| 「起点 None」で止まり、修正を 1 件も取り込めない | `test_the_gate_records_the_fix_base_before_it_asks_for_a_fix` |
| 古い起点のせいで正常なコミットまで取り消される | `test_the_gate_does_not_reuse_the_fix_base_of_the_verify_loop` |
| `Item-Id` を要求して全件が不正になる | `test_the_final_fix_commit_does_not_need_an_item_id` |
"""
from __future__ import annotations

import sys
import pytest

from crossref_helpers import make_state_v2, read_state, write_result


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


def _gate_state(tmp_path, **over):
    over.setdefault("baseline_test", {"command": "pytest -q", "status": "green",
                                      "checked_at": "2026-09-24T10:00:00", "seconds": 6.0})
    return make_state_v2(tmp_path, tmp_path / "work", phase="final", workflow_step=True, **over)


@pytest.fixture
def gate_spy(patch_lib, refactor, monkeypatch):
    """テストの実行・git・push を差し替える。"""
    seen: dict[str, list] = {"tests": [], "pushed": []}

    def fake_run(command, cwd, timeout, grace=5.0):
        seen["tests"].append(command)
        return seen.get("test_code", 0), False

    patch_lib("run_with_timeout", fake_run)
    patch_lib("git_out", lambda work, args, **k: "HEADSHA")
    patch_lib("push_head",
                        lambda state: seen["pushed"].append(state["head_branch"]))
    return seen


# ---------- 起点と担当を記録して返す ----------

def test_the_gate_records_the_fix_base_before_it_asks_for_a_fix(
    refactor, cmd_gate, tmp_path, env_tmp_dir, gate_spy
):
    """**起点を記録しないと、取り込み側が範囲を確定できない。**"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())

    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["fix_base_sha"] == "HEADSHA"
    assert gate["impl"] == "claude", "修正担当は実装担当"


def test_the_gate_emits_the_fix_impl_and_round(
    refactor, cmd_gate, tmp_path, env_tmp_dir, gate_spy, capsys
):
    """呼び出し側は担当を**出力から**受け取る。控えを読み直させない。"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        cmd_gate.cmd_final_gate(_args())

    out = capsys.readouterr().out
    impl = read_state(state_path)["final_gate"]["impl"]
    assert "FINAL_GATE=failing" in out
    assert f"FINAL_FIX_IMPL={impl}" in out
    assert "FINAL_FIX_ROUND=1" in out


def test_the_gate_does_not_reuse_the_fix_base_of_the_verify_loop(
    refactor, cmd_gate, tmp_path, env_tmp_dir, gate_spy
):
    """**検証の中の修正の起点は流用しない。**

    あれは項目の検証が落ちた地点である。そこから HEAD までには検証を通った
    正常なコミットが並ぶため、範囲に含めるとその全部が未申告として取り消される。
    """
    state_path = _gate_state(tmp_path, phases={"fix": {"base_sha": "OLDBASE"}},
                             fix={"items": [], "base_sha": "OLDBASE"})
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        cmd_gate.cmd_final_gate(_args())

    state = read_state(state_path)
    assert state["final_gate"]["fix_base_sha"] == "HEADSHA"
    assert state["phases"]["fix"]["base_sha"] == "OLDBASE", "検証の側の控えは触らない"
    assert state["fix"]["base_sha"] == "OLDBASE"


def test_the_same_runtime_keeps_fixing_across_fix_rounds(
    refactor, cmd_gate, tmp_path, env_tmp_dir, gate_spy
):
    """**担当は最初に落ちたときだけ決める。** 直しかけの文脈を持つ者が続ける。"""
    state_path = _gate_state(
        tmp_path, final_gate={"fix_rounds": 1, "checks": [], "impl": "kiro"})
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        cmd_gate.cmd_final_gate(_args())

    state = read_state(state_path)
    assert state["final_gate"]["impl"] == "kiro"


def test_a_passing_gate_records_no_fix_impl(
    refactor, cmd_gate, tmp_path, env_tmp_dir, gate_spy
):
    """通ったときは担当を決めない。"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    state = read_state(state_path)
    assert "impl" not in state["final_gate"]


# ---------- 取り込みは専用の経路 ----------

def _failing_gate_state(tmp_path, **over):
    gate = {"fix_rounds": 1, "checks": [], "status": "failing",
            "impl": "codex", "fix_base_sha": "BASE"}
    gate.update(over.pop("final_gate", {}))
    return _gate_state(tmp_path, final_gate=gate, **over)


@pytest.fixture
def merge_spy(patch_lib, refactor, monkeypatch):
    """`merge-final-fix` が触る git を差し替える。"""
    seen: dict[str, list] = {"pushed": [], "reverted": []}

    patch_lib("discard_impl_leftovers", lambda state, work: None)
    patch_lib("push_head",
                        lambda state: seen["pushed"].append("push"))
    patch_lib("revert_item_commits",
        lambda state, item, dry_run=False: seen["reverted"].append(item) or 1)
    patch_lib("git_out", lambda work, args, **k: (
        "HEADSHA" if args[:2] == ["rev-parse", "HEAD"] else "C1FULL"))
    patch_lib("commits_in_range",
        lambda work, base, head: None if not base else ["C1FULL"])
    patch_lib("collect_commit_facts", lambda *a, **k: [
        {"sha": "C1FULL", "exists": True, "files": ["src/foo.py"],
         "trailers": seen.get("trailers", {"Impl-Runtime": "codex",
                                           "Impl-Model": "gpt-5.5"}),
         "diff_lines": 10, "touches_tests": False, "test_status": "skipped"},
    ])
    return seen


def test_a_clean_final_fix_is_taken_in_and_published(
    refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-final-fix",
                 {"elapsed_seconds": 42, "commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    gate = read_state(state_path)["final_gate"]
    assert merge_spy["reverted"] == [], "問題が無ければ取り消さない"
    assert merge_spy["pushed"] == ["push"], "取り込んだら公開する"
    assert gate["fix_base_sha"] == "HEADSHA", "起点は取り込んだ地点まで進む"
    assert gate["fix_commits"] == ["C1FULL"]
    assert gate["durations"]["fix"] == 42


def test_the_final_fix_commit_does_not_need_an_item_id(
    refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """**`Item-Id` は求めない。**

    最終ゲートの修正は改善項目に属さない。求めると、実在しない番号を実装担当が
    作ることになる。
    """
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    merge_spy["trailers"] = {"Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"}
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    assert merge_spy["reverted"] == []


def test_a_missing_impl_trailer_reverts_the_range(
    refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """誰が直したかは残す。欠けていれば取り込まない。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    merge_spy["trailers"] = {"Impl-Runtime": "codex"}
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    assert len(merge_spy["reverted"]) == 1
    assert merge_spy["pushed"] == ["push"], "取り消しも公開する"


def test_an_out_of_scope_final_fix_reverts_the_range(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy, monkeypatch):
    """**最終ゲートでも `--scope` の外を触ってよい理由は無い。**"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    patch_lib("collect_commit_facts", lambda *a, **k: [
        {"sha": "C1FULL", "exists": True, "files": ["docs/other.md"],
         "trailers": {"Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
         "diff_lines": 10, "touches_tests": False, "test_status": "skipped"},
    ])
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    assert len(merge_spy["reverted"]) == 1


def test_an_unreported_commit_reverts_the_range(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy, monkeypatch):
    """申告から漏れたコミットは検証を受けていない。範囲ごと取り消す。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    patch_lib("unassigned_fix_commits",
                        lambda work, reported, ordered: ["C2FULL"])
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    assert len(merge_spy["reverted"]) == 1


def test_the_commit_test_status_is_not_checked(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy, monkeypatch):
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

    patch_lib("collect_commit_facts", spy_facts)
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    assert seen == [""], "テストコマンドを渡さない"
    assert merge_spy["reverted"] == [], "`skipped` を失敗として扱わない"


def test_a_range_that_cannot_be_determined_does_not_take_anything_in(
    refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """起点が無ければ取り込まない。**空の範囲と混同しない。**"""
    state_path = _failing_gate_state(tmp_path, final_gate={"fix_base_sha": None})
    env_tmp_dir(state_path)
    write_result(state_path, "codex-final-fix", {"commits": []})

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_merge_final_fix(_args())

    assert e.value.code == 2
    assert merge_spy["pushed"] == []


def test_the_take_in_needs_the_gate_to_run_first(
    refactor, cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """担当が無いまま呼ばれたら**進行ごと止める**（終了コード 4）。"""
    state_path = _gate_state(tmp_path)
    env_tmp_dir(state_path)

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_merge_final_fix(_args())

    assert e.value.code == 4


# ---------- 名前の取り決め ----------

def test_the_final_fix_result_file_has_no_run_number(paths):
    """**最終ゲートの修正は実行の番号を名前に入れない**（I3）。

    `launch-cli.sh` の `--stem-template "{agent}-final-fix"` と揃える。
    """
    assert paths.stem_for("codex", "final-fix", 130) == "codex-final-fix"
    assert paths.stem_for("codex", "fix", 130) == "codex-fix-rf130"


# ---------- 起動（launch-cli.sh） ----------

def _launch_final_fix(tmp_path):
    """`launch-cli.sh` に final-fix のプロンプトを組み立てさせて中身を返す。

    実行の番号を名前に入れずに起動できることも、ここで固定する。
    """
    import os
    import pathlib
    import subprocess

    launch = (pathlib.Path(__file__).resolve().parent.parent
              / "scripts" / "launch-cli.sh")
    state_path = _gate_state(tmp_path)
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


def test_the_final_fix_phase_launches_with_the_whole_test(tmp_path):
    """全体のテストを渡す。雛形の変数が生のまま残らない。"""
    text = _launch_final_fix(tmp_path)
    assert "pytest -q" in text
    assert "RF_" not in text, "雛形の変数が生のまま残らない"


def test_the_final_fix_prompt_does_not_ask_for_an_item_id(tmp_path):
    """項目に属さない修正なので、`Item-Id` を書かせない。"""
    text = _launch_final_fix(tmp_path)
    assert "Impl-Runtime:" in text
    assert "Item-Id:" not in text


# ---------- R2-002: 提案フェーズの命名 ----------

def test_the_propose_result_file_carries_the_run_number(paths):
    """**提案の名前は実行の番号を持つ**（I3）。監視の `--stem-template` と揃える。

    番号が無いと、別の実行の提案が同じ一時ディレクトリで混ざる。
    """
    assert paths.stem_for("codex", "propose", 130) == "codex-propose-rf130"
    assert paths.stem_for("codex", "propose", 131) != paths.stem_for("codex", "propose", 130)


# ---------- 最終ゲートの修正で結果が無いとき（#674 / #728 の決定 11） ----------

def test_a_missing_final_fix_result_reverts_and_moves_the_base(
    cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """AC28: 結果が無ければ取り消し、起点を取り消し後の先端へ進める。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_merge_final_fix(_args())

    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert len(merge_spy["reverted"]) == 1
    assert gate["fix_base_sha"] == "HEADSHA"
    assert [(r["phase"], r["impl"], r["reason"]) for r in gate["failed_attempts"]] == [
        ("final-fix", "codex", "missing")]
    assert "fix_commits" not in gate


def test_a_missing_final_fix_with_an_unknown_range_keeps_the_attempt_open(
    patch_lib, cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """現状固定: 結果も範囲も無ければ、記録も取り消しも行わず止まる。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    patch_lib("commits_in_range", lambda work, base, head: None)
    before = read_state(state_path)["final_gate"]

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_merge_final_fix(_args())

    gate = read_state(state_path)["final_gate"]
    assert e.value.code == 2
    assert gate == before
    assert gate["fix_rounds"] == 1
    assert gate["fix_base_sha"] == "BASE"
    assert "failed_attempts" not in gate
    assert merge_spy["reverted"] == []


def test_the_next_gate_does_not_see_the_reverted_commits(
    patch_lib, cmd_gate, tmp_path, env_tmp_dir, merge_spy, gate_spy
):
    """AC29: 取り消した後の最終ゲートは、修正のコミットを数に入れない。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        cmd_gate.cmd_merge_final_fix(_args())

    gate_spy["test_code"] = 0
    cmd_gate.cmd_final_gate(_args())

    gate = read_state(state_path)["final_gate"]
    assert gate.get("fix_commits", []) == []
    assert gate["status"] == "passed"


def test_a_usage_limit_on_the_final_fix_stops_the_fix(
    cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """AC30 決定 23: 起動し直しても解けない結末では、次の最終ゲートで修正を打ち切る印を立てる。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    (state_path.parent / "codex-final-fix-monitor.json").write_text(
        __import__("json").dumps({"reason": "usage_limit", "detail": "上限"}),
        encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_merge_final_fix(_args())

    assert e.value.code == 2
    assert read_state(state_path)["final_gate"]["no_relaunch"] is True


def test_the_gate_after_the_cap_reports_without_reverting(
    cmd_gate, tmp_path, env_tmp_dir, merge_spy, gate_spy
):
    """AC30: 上限に達した後の最終ゲートは、落ちても取り消さず報告で終わる。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    (state_path.parent / "codex-final-fix-monitor.json").write_text(
        __import__("json").dumps({"reason": "usage_limit", "detail": "上限"}),
        encoding="utf-8")
    with pytest.raises(SystemExit):
        cmd_gate.cmd_merge_final_fix(_args())

    gate_spy["test_code"] = 1
    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())

    assert e.value.code == 1
    assert read_state(state_path)["final_gate"]["status"] == "failed"


def test_a_verified_final_fix_keeps_no_failure_record(
    cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """AC31: 検証を通る修正は、変更前と同じく取り込まれ、記録を持たない。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-final-fix",
                 {"elapsed_seconds": 7, "commits": [{"sha": "C1FULL"}]})

    cmd_gate.cmd_merge_final_fix(_args())

    gate = read_state(state_path)["final_gate"]
    assert "failed_attempts" not in gate
    assert gate["fix_commits"] == ["C1FULL"]


def test_the_same_final_fix_attempt_is_closed_only_once(
    cmd_gate, tmp_path, env_tmp_dir, merge_spy
):
    """同じ修正ラウンドで叩き直しても、結果ファイルを読まずに同じ終了コードを返す。"""
    state_path = _failing_gate_state(tmp_path)
    env_tmp_dir(state_path)
    with pytest.raises(SystemExit):
        cmd_gate.cmd_merge_final_fix(_args())
    write_result(state_path, "codex-final-fix", {"commits": [{"sha": "C1FULL"}]})

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_merge_final_fix(_args())

    assert e.value.code == 2
    assert len(read_state(state_path)["final_gate"]["failed_attempts"]) == 1


def test_the_final_fix_agent_is_the_implementer(
    cmd_gate, tmp_path, env_tmp_dir, gate_spy, capsys
):
    """最終ゲートの修正担当は実装担当（#933 の決定 1）。輪番から引かない。"""
    state_path = _gate_state(tmp_path, implementer="kiro", implementer_reason="named")
    env_tmp_dir(state_path)
    gate_spy["test_code"] = 1

    with pytest.raises(SystemExit):
        cmd_gate.cmd_final_gate(_args())

    assert read_state(state_path)["final_gate"]["impl"] == "kiro"
    assert "FINAL_FIX_IMPL=kiro" in capsys.readouterr().out
