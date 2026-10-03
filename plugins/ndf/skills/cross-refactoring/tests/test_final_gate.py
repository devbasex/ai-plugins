"""最終ゲート（Step 7）の分岐のテスト（#436 B2 / B5）。

**起動のされ方で最終ゲートが変わる**（決定 7）。`development-workflow` の 1 工程と
して起動したときは `cross-review` を省き、全体のテストで判定する。単独で起動した
ときは全体のテストの後に `cross-review` を実行する。見分けは**引数で受け取る**。

**`--ci-check` が無ければ、起動のされ方によらず全体のテストを 1 度走らせる**（#933 の
AC16b）。検証の中で全体のテストが通り、取り消しが無く、HEAD が進んでいなければ使い回す。

**`--ci-check` の扱いは排他である。** 指定があれば手元のテストを実行せず継続的統合の
成功だけで判定し、無ければ手元のテストだけで判定する。**採った側が失敗した・結果を
得られないときは通過させない**（fail-closed）。
"""

from __future__ import annotations

import json

import pytest

from crossref_helpers import WHOLE_COMMAND, make_state_v2, read_state, strategy_state

# 状態の戦略の全体テスト（手元で 1 度走らせるコマンド）。
WHOLE = WHOLE_COMMAND


def _state(tmp_path, **over):
    over.setdefault("workflow_step", False)
    return make_state_v2(tmp_path, tmp_path / "work", phase="final", **over)


def _abort(refactor_lib):
    return refactor_lib.ABORT


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


@pytest.fixture
def spy(patch_lib, refactor, monkeypatch):
    """テストの実行と `gh` の呼び出しを差し替え、何を呼んだかを記録する。"""
    seen: dict[str, list] = {"tests": [], "gh": []}

    def fake_run(command, cwd, timeout, grace=5.0, output=None):
        seen["tests"].append(command)
        return seen.get("test_code", 0), False

    def fake_sh(cmd, cwd=None, check=True):
        seen["gh"].append(list(cmd))
        return seen.get("gh_out", "")

    patch_lib("run_with_timeout", fake_run)
    patch_lib("sh", fake_sh)
    patch_lib("git_out", lambda work, args, **k: "HEADSHA")
    return seen


def _check_runs(*runs):
    return json.dumps({"total_count": len(runs), "check_runs": list(runs)})


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


# ---------- B2: 起動のされ方で最終ゲートが変わる ----------


def test_a_standalone_run_goes_to_cross_review_after_the_whole_test(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """既定は単独起動。全体のテストを 1 度通してから `cross-review` を実行する（AC16b）。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=cross-review" in capsys.readouterr().out
    assert spy["tests"] == [WHOLE]
    gate = read_state(state_path)["final_gate"]
    assert gate["mode"] == "cross-review"
    assert gate["checked_mode"] == "test"
    assert gate["status"] == "passed"


def test_a_workflow_step_run_skips_cross_review_and_runs_the_tests(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """工程の 1 つとして起動したときは `cross-review` を省く。"""
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    out = capsys.readouterr().out
    assert "FINAL_GATE=passed" in out
    assert "cross-review" not in out
    assert spy["tests"] == [WHOLE]
    assert read_state(state_path)["final_gate"]["status"] == "passed"


def test_the_launch_mode_comes_from_the_argument(refactor, monkeypatch):
    """決定 7 — 環境変数や記録の読み取りではなく、呼ぶ側が引数で伝える。"""
    captured = {}
    # **入口の名前を差し替える。** `main()` は `refactor.py` が取り込んだ `cmd_init` を呼ぶ。
    monkeypatch.setattr(refactor, "cmd_init", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(
        refactor.sys,
        "argv",
        ["refactor.py", "init", "130", "--scope", "src", "--host", "claude", "--baseline-test", "true", "--workflow-step"],
    )
    refactor.main()
    assert captured["workflow_step"] is True


# ---------- B2: `--ci-check` は排他 ----------


def test_the_ci_check_replaces_the_local_tests(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == [], "継続的統合を採ったら手元のテストは実行しない"
    assert "FINAL_GATE=passed" in capsys.readouterr().out


def test_the_check_runs_are_read_once_and_status_is_not_used(refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """`status` は使わない（GitHub Actions は常に `pending` を返す）。"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert len(spy["gh"]) == 1, "読むのは check-runs の 1 回だけ"
    path = spy["gh"][0][-1]
    assert "check-runs" in path and "HEADSHA" in path
    assert not path.endswith("/status")


def test_a_failed_ci_check_does_not_pass(cmd_gate, tmp_path, env_tmp_dir, spy):
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests", conclusion="failure"))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2
    assert read_state(state_path)["final_gate"]["checks"][-1]["status"] == "fail"


@pytest.mark.parametrize(
    "payload",
    [
        "",  # 照会そのものができない
        '{"total_count": 0, "check_runs": []}',  # チェックが 1 件も無い
        "not json",  # 応答を解釈できない
    ],
)
def test_no_result_does_not_pass(refactor_lib, cmd_gate, tmp_path, env_tmp_dir, spy, payload, capsys):
    """fail-closed — **結果を得られないときは通過させず、「判断が要る」で終える**（#1334 非機能の可用性）。"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = payload

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == _abort(refactor_lib)
    assert "得られませんでした" in capsys.readouterr().err


def test_an_unfinished_ci_check_is_waited_for_until_the_limit_then_needs_a_decision(
    refactor_lib, cmd_gate, tmp_path, env_tmp_dir, spy, capsys
):
    """`pending` の間は `limits.ci_wait_timeout` まで待ち、上限に届いたら待った秒を出して「判断が要る」で終える（決定 9）。"""
    state_path = _state(
        tmp_path, workflow_step=True, ci_check="tests", limits={"test_timeout": 60, "whole_timeout": 60, "ci_wait_timeout": 25}
    )
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests", conclusion=None, status="in_progress"))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == _abort(refactor_lib)
    assert len(spy["gh"]) > 1, "pending の間は問い合わせ直す"
    err = capsys.readouterr().err
    assert "上限 25 秒" in err and "待ち" in err


def test_a_pending_check_that_turns_green_passes(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """待っている間に success へ変わればそのまま通す。"""
    state_path = _state(
        tmp_path, workflow_step=True, ci_check="tests", limits={"test_timeout": 60, "whole_timeout": 60, "ci_wait_timeout": 600}
    )
    env_tmp_dir(state_path)
    answers = [_check_runs(_run("tests", conclusion=None, status="in_progress")), _check_runs(_run("tests"))]
    spy["gh_out"] = answers[0]
    original = spy["gh"]

    class _Rotating(list):
        def append(self, item):
            super().append(item)
            spy["gh_out"] = answers[min(len(self), len(answers) - 1)]

    spy["gh"] = _Rotating(original)

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    assert spy["tests"] == []


def test_a_named_check_that_is_missing_does_not_pass(refactor_lib, cmd_gate, tmp_path, env_tmp_dir, spy):
    """名前が一致しないチェックの成功で通さない。結論を得られないので「判断が要る」で終える。"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("lint"))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == _abort(refactor_lib)


def test_a_failing_local_test_is_not_overturned_by_the_ci(refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """**「どちらか一方が通れば通過」とはしない。** 指定が無ければ手元のテストだけを見る。"""
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)
    spy["test_code"] = 1
    spy["gh_out"] = _check_runs(_run("tests"))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2
    assert spy["gh"] == [], "手元のテストを採ったら継続的統合は読まない"


# ---------- B5: 落ちたら修正ラウンドを回し、上限では取り消さない ----------


def test_a_failure_opens_a_fix_round(cmd_gate, tmp_path, env_tmp_dir, spy):
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["fix_rounds"] == 1
    assert gate["status"] == "failing"


def test_the_fix_cap_reports_the_failure_without_reverting(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy, monkeypatch):
    """**単独で起動したとき、Step 7 は push 済みの地点である。** 上限に達しても取り消さない（#1669 AC9）。

    取り消しの判断は Pull Request の読み手が持つ。失敗として報告に書く。
    """
    dropped: list = []
    patch_lib("drop", lambda *a, **k: dropped.append(a) or {})
    patch_lib("revert_range", lambda *a, **k: dropped.append(a))
    state_path = _state(tmp_path, started_at="2000-01-01T00:00:00", final_gate={"fix_rounds": 2, "checks": []})
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 1, "報告へ抜ける。進行ごと止める中断（4）ではない"
    assert dropped == [], "取り消さない"
    assert read_state(state_path)["final_gate"]["status"] == "failed"


def test_the_first_fix_is_tried_even_after_the_budget_ran_out(cmd_gate, tmp_path, env_tmp_dir, spy):
    """決定 26: 想定最大時間を使い切った後に落ちても、最終ゲートの修正は必ず 1 度試みる。"""
    state_path = _state(tmp_path, workflow_step=True, started_at="2000-01-01T00:00:00")
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2, "時計で打ち切らず修正ラウンドへ進む"
    gate = read_state(state_path)["final_gate"]
    assert gate["fix_rounds"] == 1 and gate["status"] == "failing"


def test_the_second_fix_after_the_budget_ran_out_is_not_tried(cmd_gate, tmp_path, env_tmp_dir, spy):
    """決定 26: 2 度目からは時計で打ち切る（単独の起動は取り消さずに 1 で終わる）。"""
    state_path = _state(tmp_path, started_at="2000-01-01T00:00:00", final_gate={"fix_rounds": 1, "checks": []})
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 1
    assert read_state(state_path)["final_gate"]["status"] == "failed"


def test_the_report_shows_the_final_gate(refactor, tmp_path, env_tmp_dir, capsys):
    state_path = _state(tmp_path, workflow_step=True, final_gate={"fix_rounds": 2, "status": "failed", "mode": "test", "checks": []})
    env_tmp_dir(state_path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert "最終ゲート" in capsys.readouterr().out


# ---------- B5: 検証は手元のテストを必須とする ----------


def test_the_verification_never_uses_the_ci(
    refactor, cmd_setup, cmd_implement, cmd_converge, tmp_path, monkeypatch, patch_lib, env_tmp_dir, capsys
):
    """**継続的統合で代替できるのは最終ゲートだけである。**

    検証は手元の未 push のコミットを見るため、継続的統合の結果が無い。
    """
    import argparse

    from crossref_helpers import CALC, build_git_flow, commit_with_trailers, git, item_trailers, write_state

    flow = build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir, ci_check="tests")
    read_ci: list = []
    patch_lib("check_run_result", lambda *a, **k: read_ci.append(a) or "success")
    work = flow["work"]
    state = read_state(flow["path"])
    state["items"] = [
        {
            "id": "I-001",
            "rank": 1,
            "path": "src/calc.py",
            "symbol": "total",
            "smell": "long_method",
            "technique": "extract_method",
            "severity": "major",
            "proposed_by": ["codex"],
            "tier": "high",
            "risk": False,
            "tests": [],
            "test_targets": ["tests/test_calc.py"],
            "command": ["pytest", "-q", "tests/test_calc.py"],
            "command_source": "targets",
            "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
            "start_deadline": "2099-01-01T00:00:00+00:00",
            "test_start_deadline": None,
            "status": "planned",
            "commits": {"test": None, "implement": None, "fix": []},
            "seconds": {},
            "fix_count": 0,
            "danger": [],
            "estimated_diff_lines": 20,
        }
    ]
    state["plan"] = {
        "base_sha": git("rev-parse", "HEAD", cwd=work).stdout.strip(),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.0, "fix": 5.5},
        "end_at": "2099-01-01T00:00:00+00:00",
        "table_source": "defaults",
    }
    state["phase"] = "implement"
    write_state(flow["path"], state)
    cmd_setup.cmd_start_phase(argparse.Namespace(id=130, phase="implement"))
    (work / "src" / "calc.py").write_text(
        CALC.replace(
            "    result = 0\n    for v in values:\n        result = add(result, v)\n    return result\n", "    return sum(values)\n"
        ),
        encoding="utf-8",
    )
    commit_with_trailers(work, "Refactor", item_trailers("I-001"))
    cmd_implement.cmd_merge_implement(argparse.Namespace(id=130))

    cmd_converge.cmd_verify(argparse.Namespace(id=130))

    assert read_state(flow["path"])["items"][0]["status"] == "verified"
    assert read_ci == [], "検証で継続的統合は読まない"


# ---------- 全体テストは最終ゲートで 1 回（#880 の AC3） ----------

ROUND_TEST = {"command": "pytest tests/services -q", "status": "green", "checked_at": "2026-09-24T10:00:00"}


def test_a_standalone_run_with_a_round_test_runs_the_baseline_test_once(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """AC3 — 単独起動でも、ラウンドのテストで検証してきたなら全体テストを 1 回通す。"""
    state_path = _state(tmp_path, round_test=ROUND_TEST)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == [WHOLE]
    assert "FINAL_GATE=cross-review" in capsys.readouterr().out
    gate = read_state(state_path)["final_gate"]
    assert gate["mode"] == "cross-review"
    assert gate["checks"][-1]["status"] == "pass"


def test_a_workflow_step_run_with_a_round_test_runs_the_baseline_test_once(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """AC3 — 工程として起動したときも、全体テストの呼び出しは 1 回。"""
    state_path = _state(tmp_path, workflow_step=True, round_test=ROUND_TEST)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == [WHOLE]
    assert "FINAL_GATE=passed" in capsys.readouterr().out


def test_a_standalone_run_whose_baseline_test_fails_enters_the_fix_round(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """落ちれば `--workflow-step` と同じ修正ラウンドへ入る。cross-review へは渡さない。"""
    state_path = _state(tmp_path, round_test=ROUND_TEST)
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())

    assert e.value.code == 2
    out = capsys.readouterr().out
    assert "FINAL_GATE=failing" in out and "FINAL_GATE=cross-review" not in out
    assert read_state(state_path)["final_gate"]["fix_rounds"] == 1


# ---------- 検証の中の全体のテストの使い回し（AC16b・決定 16） ----------

PASSED_IN_VERIFY = {"ran": True, "flags": ["D2"], "status": "pass", "seconds": 5.0, "head": "HEADSHA", "reverted": False}


@pytest.mark.parametrize("workflow_step", [False, True])
def test_a_whole_test_passed_in_verify_at_the_same_head_is_reused(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys, workflow_step):
    """検証の中で通り、取り消しが無く、HEAD が進んでいなければ走らせずに通す。"""
    state_path = _state(tmp_path, workflow_step=workflow_step, whole_test=PASSED_IN_VERIFY)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == []
    gate = read_state(state_path)["final_gate"]
    assert gate["whole_test_reused"] is True
    assert gate["status"] == "passed"
    assert gate["checks"] == []
    expected = "FINAL_GATE=passed" if workflow_step else "FINAL_GATE=cross-review"
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    "whole_test",
    [
        {**PASSED_IN_VERIFY, "reverted": True},  # 落ちて危険フラグの項目を取り消した
        {**PASSED_IN_VERIFY, "head": "OLDHEAD"},  # その後に HEAD が進んだ（同期のコミットなど）
        {**PASSED_IN_VERIFY, "status": "fail"},  # 落ちた
        {**PASSED_IN_VERIFY, "ran": False},  # 走らなかった
    ],
)
def test_the_whole_test_runs_unless_it_is_reusable(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, whole_test):
    """`whole_test.reverted` が真なら必ず走らせる。HEAD が進んだときも走らせる。"""
    state_path = _state(tmp_path, workflow_step=True, whole_test=whole_test)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == [WHOLE]
    gate = read_state(state_path)["final_gate"]
    assert not gate.get("whole_test_reused")
    assert gate["checks"][-1]["command"] == WHOLE


def test_a_ci_check_is_not_replaced_by_the_whole_test_in_verify(refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """`--ci-check` があれば継続的統合を読む。検証の中の全体のテストでは代えない。"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests", whole_test=PASSED_IN_VERIFY)
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == []
    assert len(spy["gh"]) == 1
    assert not read_state(state_path)["final_gate"].get("whole_test_reused")


def test_the_seconds_of_the_final_whole_test_are_kept_for_the_history(refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """履歴の `whole_test.final`（AC17）に使う所要を残す。継続的統合のときは残さない。"""
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert read_state(state_path)["final_gate"]["whole_test_seconds"] >= 0


def test_a_standalone_run_with_a_ci_check_reads_the_ci_instead(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """`--ci-check` があれば、手元の全体テストの代わりに継続的統合を見る。"""
    state_path = _state(tmp_path, round_test=ROUND_TEST, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert spy["tests"] == []
    assert "FINAL_GATE=cross-review" in capsys.readouterr().out


def test_the_gate_check_records_the_command_and_seconds(refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """最終ゲートの記録は、実行したコマンドと所要の秒数を持つ。"""
    state_path = _state(tmp_path, workflow_step=True, round_test=ROUND_TEST)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    check = read_state(state_path)["final_gate"]["checks"][-1]
    assert check["mode"] == "test"
    assert check["command"] == WHOLE
    assert isinstance(check["seconds"], (int, float)) and check["seconds"] >= 0


# ---------- 全体テストの打ち切り（run_with_timeout の timed_out=True）（R2-003） ----------
#
# 現状固定テスト。最終ゲートの成功と非ゼロ終了は固定されているが、全体テストが
# 打ち切り（timed_out=True）で止まったときに、失敗として記録し修正ラウンドへ進む
# 経路は固定されていなかった（gate.py の `_local_gate` の timed_out 分岐）。


def test_final_gate_records_a_timed_out_whole_test_and_enters_a_fix_round(
    patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys
):
    """R2-003 — 全体テストが打ち切りなら失敗として記録し、修正ラウンドへ進む。"""
    state_path = _state(tmp_path, workflow_step=True, limits={"test_timeout": 60})
    env_tmp_dir(state_path)
    # 全体テストの実行を打ち切りへ差し替える（spy の差し替えを上書きする）。
    patch_lib("run_with_timeout", lambda command, cwd, timeout, grace=5.0: (None, True))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    # 現状固定: 失敗の終了コード（修正ラウンドへ）。
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert "FINAL_GATE=failing" in out and "FINAL_GATE=cross-review" not in out

    gate = read_state(state_path)["final_gate"]
    # 修正ラウンドへ進む。
    assert gate["fix_rounds"] == 1
    assert gate["status"] == "failing"
    # 最終ゲートの記録は「打ち切り」相当の詳細を持つ（文言の完全一致は取らず、
    # 打ち切った秒数が含まれることだけを見る）。
    check = gate["checks"][-1]
    assert check["status"] == "fail"
    assert "60" in check["detail"]


# ---------- #1334: 全体テストを CI に任せる戦略の最終ゲート ----------

OLD_FAILURE = "tests/test_old.py::tests.test_old::test_old"
NEW_FAILURE = "tests/test_new.py::tests.test_new::test_new"


def _ci_whole_state(tmp_path, **over):
    over.setdefault("strategy", strategy_state("local-scoped-ci-whole"))
    over.setdefault("limits", {"test_timeout": 60, "whole_timeout": 60, "ci_wait_timeout": 60})
    over.setdefault("workflow_step", True)
    return _state(tmp_path, **over)


def _failed_ci(patch_lib, spy, ids):
    """CI が落ち、成果物の JUnit から `ids` が落ちたと読める。範囲の走らせ直しも落ちる。"""
    spy["gh_out"] = _check_runs(_run("tests", conclusion="failure"))
    spy["test_code"] = 1
    patch_lib("ci_junit", lambda state, sha, checks, runs=None: [b"<testsuites/>"])
    patch_lib("failed_ids_of", lambda state, ci_xmls=None: (list(ids), None))


def test_a_ci_whole_gate_never_runs_the_whole_test_locally(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """AC3・I4 — `local-scoped-ci-whole` は `--ci-check` が無くても CI の結果で判定し、手元で全体テストを走らせない。"""
    state_path = _ci_whole_state(tmp_path)
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    assert spy["tests"] == []
    assert read_state(state_path)["final_gate"]["mode"] == "ci"


def test_a_ci_failure_that_is_only_an_existing_failure_passes(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """AC10・I5 — 着手前に記録した既存失敗だけが落ちたなら通す。走らせ直すのは落ちたファイルだけ（全体テストは走らせない）。"""
    baseline = {"mode": "scope", "command": "x", "status": "red", "checked_at": "t", "seconds": 1.0, "existing_failures": [OLD_FAILURE]}
    state_path = _ci_whole_state(tmp_path, baseline_test=baseline)
    env_tmp_dir(state_path)
    _failed_ci(patch_lib, spy, [OLD_FAILURE])

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    assert WHOLE not in spy["tests"]
    assert all("tests/test_old.py" in words for words in spy["tests"])
    triage = read_state(state_path)["final_gate"]["triage"]
    assert triage["preexisting"] == [OLD_FAILURE] and triage["caused"] == []


def test_a_ci_failure_outside_the_existing_failures_does_not_pass(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """AC10 — 既存失敗の外で落ちたテストがあれば通さない（着手前の HEAD が分からないので変更起因）。"""
    baseline = {"mode": "scope", "command": "x", "status": "red", "checked_at": "t", "seconds": 1.0, "existing_failures": [OLD_FAILURE]}
    state_path = _ci_whole_state(tmp_path, baseline_test=baseline)
    env_tmp_dir(state_path)
    _failed_ci(patch_lib, spy, [OLD_FAILURE, NEW_FAILURE])

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2
    triage = read_state(state_path)["final_gate"]["triage"]
    assert triage["caused"] == [NEW_FAILURE]


def _item(item_id, rank):
    return {"id": item_id, "rank": rank, "status": "verified", "danger": ["D2"]}


def test_a_caused_failure_after_the_deadline_reverts_the_deferred_items_newest_first(
    patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys
):
    """AC8・I8 — 単独の起動で寄せた危険フラグの全体テストが落ちたら、原因の判定の順に取り消し、通った時点で止める。

    本文に項目のパスが現れず、項目のコミットも無い（外して走らせ直せない）ため原因は決まらず、新しい順になる（#1649 AC6）。
    """
    dropped: list = []
    pushed: list = []

    def fake_drop(path, state, ids, reason, on_conflict="stop"):
        dropped.extend(ids)
        spy["test_code"] = 0  # 取り消した後の走らせ直しは通る

    patch_lib("drop", fake_drop)
    patch_lib("push_with_retry_marker", lambda *a, **k: pushed.append(True))
    state_path = _ci_whole_state(
        tmp_path,
        workflow_step=False,
        started_at="2000-01-01T00:00:00",
        limits={"test_timeout": 60, "whole_timeout": 60, "ci_wait_timeout": 60, "final_end_at": "2000-01-01T00:30:00"},
        final_gate={"fix_rounds": 1, "checks": []},
        items=[_item("I1", 1), _item("I2", 2), _item("I3", 3)],
        whole_test={
            "ran": False,
            "flags": [],
            "status": None,
            "seconds": None,
            "head": None,
            "reverted": False,
            "deferred": {"flags": ["D2"], "items": ["I1", "I3"]},
        },
    )
    env_tmp_dir(state_path)
    _failed_ci(patch_lib, spy, [NEW_FAILURE])

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2, "取り消しを公開して、次の最終ゲートが CI を待ち直す"
    assert dropped == ["I3"], "原因が決まらなければ新しい（rank の大きい）項目から取り消し、通った時点で止める"
    assert pushed == [True]
    gate = read_state(state_path)["final_gate"]
    assert gate["reverted_deferred"] == ["I3"]
    # 修正の依頼ではない。起点を取り消し後の HEAD へ置き直し、取り消しを後の修正の範囲へ入れない。
    assert "FINAL_GATE=recheck" in capsys.readouterr().out
    assert gate["status"] == "recheck" and gate["fix_base_sha"]


def test_the_deferred_revert_reverts_the_culprit_named_by_the_failure_not_the_flagged_items(
    patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy, monkeypatch
):
    """#1649 AC6 — 寄せた危険フラグの取り消しも原因の判定に従う。本文に現れたファイルを変えた I2 だけを取り消す。"""
    import sys

    dropped: list = []

    def fake_drop(path, state, ids, reason, on_conflict="stop"):
        dropped.extend(ids)
        spy["test_code"] = 0

    patch_lib("drop", fake_drop)
    patch_lib("push_with_retry_marker", lambda *a, **k: None)
    culprit = sys.modules["refactor_lib.culprit"]
    monkeypatch.setattr(culprit, "_files", lambda work, item: {"I2": ["src/i2.py"]}.get(item["id"], [f"src/{item['id']}.py"]))
    items = [_item("I1", 1), {**_item("I2", 2), "danger": []}, _item("I3", 3)]
    state_path = _ci_whole_state(
        tmp_path,
        workflow_step=False,
        started_at="2000-01-01T00:00:00",
        limits={"test_timeout": 60, "whole_timeout": 60, "ci_wait_timeout": 60, "final_end_at": "2000-01-01T00:30:00"},
        final_gate={"fix_rounds": 1, "checks": []},
        items=items,
        whole_test={"ran": False, "flags": [], "status": None, "deferred": {"flags": ["D2"], "items": ["I1", "I3"]}},
    )
    env_tmp_dir(state_path)
    _failed_ci(patch_lib, spy, [NEW_FAILURE])
    classified = {"failed_tests": [NEW_FAILURE], "flaky": [], "preexisting": [], "caused": [NEW_FAILURE], "fallback_reason": None}
    classified.update({"caused_output": {NEW_FAILURE: "E  src/i2.py:3: boom"}, "rerun_command": "pytest -q tests/test_new.py"})
    monkeypatch.setattr(cmd_gate.triage, "classify", lambda state, timed_out, ci_xmls=None: dict(classified))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())

    assert e.value.code == 2
    assert dropped == ["I2"], "危険フラグの I1・I3 ではなく、本文に現れたファイルを変えた I2"
    gate = read_state(state_path)["final_gate"]
    assert gate["culprit"]["culprits"] == ["I2"] and gate["culprit"]["basis"] == "path"


def test_the_ci_gate_waits_without_pushing(patch_lib, refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """I9 — 最終ゲートは公開の手順が push 済みの HEAD のチェックを待つだけで、待ちの前に push しない。"""
    pushed: list = []
    patch_lib("push_with_retry_marker", lambda *a, **k: pushed.append(True))
    state_path = _ci_whole_state(tmp_path)
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert pushed == []
    assert not any(cmd[:2] == ["git", "push"] for cmd in spy["gh"])


def test_the_ci_gate_reads_every_check_with_one_query_per_poll(refactor, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """待つチェックが複数でも、1 回の照会でチェックジョブの一覧を 1 度だけ読む（照会の回数がチェックの本数倍にならない）。"""
    ci = {"check": "tests", "checks": ["tests", "lint", "e2e"], "junit_artifacts": None}
    state_path = _ci_whole_state(tmp_path, strategy=strategy_state("local-scoped-ci-whole", ci=ci))
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"), _run("lint"), _run("e2e"))

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    assert len([cmd for cmd in spy["gh"] if any("check-runs" in str(w) for w in cmd)]) == 1


def test_the_lint_limit_excludes_the_triage_time(cmd_gate, tmp_path, env_tmp_dir, spy, monkeypatch):
    """静的解析の上限は、テストの実行秒数だけを差し引いて数える。落ちたテストの見分けの時間は引かない。"""
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)
    clock = {"now": 1000.0}
    monkeypatch.setattr(cmd_gate.time, "monotonic", lambda: clock["now"])

    def fake_check(path, state, gate):
        # テストが 5 秒、見分けが 100 秒かかった
        gate["mode"] = "test"
        clock["now"] += 105.0
        return True, "d", 5.0

    seen = {}

    def fake_lint(path, state, gate, started=None):
        seen["started"] = started
        return True, ""

    monkeypatch.setattr(cmd_gate, "_run_and_record_gate_check", fake_check)
    monkeypatch.setattr(cmd_gate.gate_lint, "lint_gate", fake_lint)
    cmd_gate.cmd_final_gate(_args())
    assert seen["started"] == 1100.0
