"""最終ゲート（Step 7）の分岐のテスト（#436 B2 / B5）。

**起動のされ方で最終ゲートが変わる**（決定 7）。`development-workflow` の 1 工程と
して起動したときは `cross-review` を省き、全体のテストで判定する。単独で起動した
ときは全体のテストの後に `cross-review` を実行する。見分けは**引数で受け取る**。

**`--ci-check` が無ければ、起動のされ方によらず全体のテストを 1 度走らせる**（#933 の
AC16b）。検証の巡の中では全体のテストを走らせないため、使い回す結果は無い（#3048）。

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

    def fake_run(command, cwd, timeout, grace=5.0, output=None, **kw):
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


def test_the_gate_keeps_fixing_past_the_budget(cmd_gate, tmp_path, env_tmp_dir, spy):
    """#2457 受け入れ条件 5: 実装の予算をとうに超えても、最終ゲート修正を時間で打ち切らない。"""
    state_path = _state(
        tmp_path, started_at="2000-01-01T00:00:00", budget={"minutes": 1, "extension": None}, final_gate={"fix_rounds": 2, "checks": []}
    )
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["status"] == "failing" and gate["fix_rounds"] == 3
    assert "check_skipped" not in gate


def test_a_stalled_final_fix_switches_the_fixer_once_then_halts_without_reverting(patch_lib, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """#2457 決定 4: 落ちたテストの集合が変わらなければ停滞。修正担当を 1 回替え、替えた後も停滞なら取り消さずに止める。"""
    dropped: list = []
    patch_lib("revert_range", lambda *a, **k: dropped.append(a))
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    impls = []
    for _ in range(3):
        with pytest.raises(SystemExit) as e:
            cmd_gate.cmd_final_gate(_args())
        assert e.value.code == 2
        impls.append(read_state(state_path)["final_gate"].get("impl"))
    gate = read_state(state_path)["final_gate"]
    assert [h["progress"] for h in gate["fix_history"]] == ["first", "stalled", "stalled"]
    assert impls[0] != impls[1] and gate["fixer_switched"] is True

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    gate = read_state(state_path)["final_gate"]
    assert gate["status"] == "halted" and "FINAL_GATE=halt" in capsys.readouterr().out
    assert read_state(state_path)["halt"]["reason"] == "final_gate"
    assert dropped == [], "取り消さない"


def test_the_report_shows_the_final_gate(refactor, tmp_path, env_tmp_dir, capsys):
    state_path = _state(tmp_path, workflow_step=True, final_gate={"fix_rounds": 2, "status": "failed", "mode": "test", "checks": []})
    env_tmp_dir(state_path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert "最終ゲート" in capsys.readouterr().out


# ---------- B5: 検証は手元のテストを必須とする ----------


def test_the_verification_never_uses_the_ci(
    refactor, cmd_setup, cmd_phases, cmd_implement, cmd_converge, tmp_path, monkeypatch, patch_lib, env_tmp_dir, capsys
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
    cmd_phases.cmd_start_phase(argparse.Namespace(id=130, phase="implement"))
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


# ---------- 最終ゲートの記録 ----------


def test_a_new_gate_run_drops_the_previous_triage(refactor, cmd_gate, tmp_path, env_tmp_dir, spy):
    """前回の見分けは今回の取り消しの根拠にしない（今回のテストが通れば変更起因は無い）。"""
    stale = {"caused": ["tests/test_a.py::t"], "rerun_command": "pytest -q tests/test_a.py"}
    material = {"ids": ["tests/test_a.py::t"], "source": "junit", "log": "", "commands": []}
    state_path = _state(tmp_path, final_gate={"fix_rounds": 1, "checks": [], "triage": stale, "material": material})
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    gate = read_state(state_path)["final_gate"]
    assert "triage" not in gate and "material" not in gate


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
    patch_lib("run_with_timeout", lambda command, cwd, timeout, grace=5.0, output=None, **kw: (None, True))

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
    assert "秒で終わりませんでした" in check["detail"]


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
    # 仕分け済みの既存失敗は修正の材料と前進の判定へ戻さない（静的解析だけが落ちた回も同じ材料を使う）
    assert read_state(state_path)["final_gate"]["material"]["ids"] == []


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


def test_the_gate_checks_even_when_the_tree_is_the_planned_tree(patch_lib, cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    """#2457 受け入れ条件 21・I11 — 木が起点と同じでも、確かめを省いて `passed` を返さない（確かめを走らせる）。"""
    patch_lib("push_with_retry_marker", lambda *a, **k: None)
    state_path = _state(tmp_path, workflow_step=True, started_at="2000-01-01T00:00:00", plan={"base_sha": "HEADSHA"})
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    gate = read_state(state_path)["final_gate"]
    assert spy["tests"], "確かめを走らせた"
    assert "check_skipped" not in gate and gate["checks"]


def test_without_junit_the_ci_failure_material_reaches_the_final_fix(patch_lib, cmd_gate, tmp_path, env_tmp_dir, spy, monkeypatch, capsys):
    """#2457 受け入れ条件 13・決定 12 — CI の JUnit を読めなくても、落ちたテストの材料（ログの置き場・走らせ直すコマンド）が
    最終ゲート修正の依頼に入る。"""
    state_path = _ci_whole_state(tmp_path)
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs({**_run("tests", conclusion="failure"), "html_url": "https://github.com/acme/demo/actions/runs/7"})
    patch_lib("ci_junit", lambda state, sha, checks, runs=None: [])
    fallback = {"failed_tests": None, "flaky": [], "preexisting": [], "caused": [], "fallback_reason": "JUnit が無い"}
    monkeypatch.setattr(cmd_gate.triage, "classify", lambda state, timed_out, ci_xmls=None: dict(fallback))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())

    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["status"] == "failing"
    assert gate["material"]["log"] == "https://github.com/acme/demo/actions/runs/7"
    assert gate["material"]["commands"]
    assert "落ちたテストの材料" in gate["fix_request"] and "actions/runs/7" in gate["fix_request"]


# ---------- 最終ゲートへ寄せた危険フラグの項目へ修正を向ける（#3048） ----------


def _deferred_state(tmp_path, ci):
    items = [
        {
            "id": "I-001",
            "rank": 1,
            "status": "verified",
            "danger": ["D1"],
            "path": "src/a.py",
            "symbol": "a",
            "technique": "extract",
            "commits": {"test": None, "implement": ["aaaaaaa1"], "fix": []},
        },
        {
            "id": "I-002",
            "rank": 2,
            "status": "verified",
            "danger": [],
            "path": "src/calc.py",
            "symbol": "add",
            "technique": "rename",
            "commits": {"test": None, "implement": ["bbbbbbb2"], "fix": []},
        },
        {"id": "I-003", "rank": 3, "status": "reverted", "danger": ["D2"], "commits": {"test": None, "implement": ["ccccccc3"], "fix": []}},
    ]
    whole = {"deferred": {"flags": ["D1", "D2"], "items": ["I-001", "I-003"]}, "resolution": "deferred"}
    if ci:
        return _ci_whole_state(tmp_path, items=items, whole_test=whole)
    return _state(tmp_path, workflow_step=True, items=items, whole_test=whole)


@pytest.mark.parametrize("ci", [False, True])
def test_a_caused_failure_points_the_final_fix_at_the_deferred_items(patch_lib, cmd_gate, tmp_path, env_tmp_dir, spy, monkeypatch, ci):
    """手元でも CI でも、変更起因の失敗が出たら、寄せた危険フラグの項目と本文にパスが現れた項目を最終ゲート修正の依頼に載せる。

    取り消した項目は載せない。原因の判定は `final_gate.culprit` に残る（項目を外した走らせ直しはしない）。
    """
    state_path = _deferred_state(tmp_path, ci)
    env_tmp_dir(state_path)
    if ci:
        _failed_ci(patch_lib, spy, [NEW_FAILURE])
    else:
        spy["test_code"] = 1
    classified = {
        "failed_tests": [NEW_FAILURE],
        "flaky": [],
        "preexisting": [],
        "caused": [NEW_FAILURE],
        "caused_output": {NEW_FAILURE: 'File "src/calc.py", line 3, in add'},
        "rerun_commands": ["pytest -q tests/test_new.py"],
    }
    monkeypatch.setattr(cmd_gate.triage, "classify", lambda state, timed_out, ci_xmls=None: dict(classified))
    changed = {"I-001": ["src/a.py"], "I-002": ["src/calc.py"]}
    monkeypatch.setattr(cmd_gate.culprit, "_changed_by", lambda work, item: changed.get(item["id"], []))
    monkeypatch.setattr(cmd_gate.culprit, "_isolate", lambda *a: pytest.fail("最終ゲートでは項目を外して走らせ直さない"))

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())

    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["status"] == "failing" and gate["mode"] == ("ci" if ci else "test")
    assert gate["culprit"]["culprits"] == ["I-002"] and gate["culprit"]["basis"] == "path"
    request = gate["fix_request"]
    assert "I-002" in request and "I-001" in request and "src/a.py#a" in request and "aaaaaaa" in request
    assert "I-003" not in request


def test_a_passing_final_gate_leaves_no_fix_request(cmd_gate, tmp_path, env_tmp_dir, spy):
    """寄せた項目があっても、最終ゲートが通れば修正の依頼を作らない。"""
    state_path = _deferred_state(tmp_path, ci=False)
    env_tmp_dir(state_path)

    cmd_gate.cmd_final_gate(_args())

    gate = read_state(state_path)["final_gate"]
    assert gate["status"] == "passed" and "fix_request" not in gate


# ---------- #3056: CI テストの宣言のあるリポジトリの最終ゲートは待たずに 1 回だけ読む ----------


def _ci_only_state(tmp_path, checks=("tests",), **over):
    ci = {"check": checks[0] if checks else None, "checks": list(checks), "junit_artifacts": None}
    over.setdefault("strategy", strategy_state("local-full", ci_only=True, ci=ci))
    over.setdefault("limits", {"test_timeout": 60, "whole_timeout": 60, "ci_wait_timeout": 600})
    over.setdefault("workflow_step", True)
    return _state(tmp_path, **over)


@pytest.mark.parametrize("payload", ["", '{"total_count": 0, "check_runs": []}', None])
def test_ci_only_gate_passes_an_unfinished_ci_after_one_read(cmd_gate, tmp_path, env_tmp_dir, spy, capsys, payload):
    state_path = _ci_only_state(tmp_path)
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests", conclusion=None, status="in_progress")) if payload is None else payload

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    assert len(spy["gh"]) == 1 and spy["tests"] == []
    gate = read_state(state_path)["final_gate"]
    assert gate["mode"] == "ci" and "CI 未完了" in gate["checks"][-1]["detail"]


def test_ci_only_gate_without_check_names_reads_every_head_check(cmd_gate, tmp_path, env_tmp_dir, spy, capsys):
    state_path = _ci_only_state(tmp_path, checks=())
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("lint"), _run("tests"))

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
    assert "CI 未完了" not in read_state(state_path)["final_gate"]["checks"][-1]["detail"]


def test_ci_only_gate_failure_goes_to_the_fix_round(patch_lib, cmd_gate, tmp_path, env_tmp_dir, spy):
    state_path = _ci_only_state(tmp_path)
    env_tmp_dir(state_path)
    _failed_ci(patch_lib, spy, [NEW_FAILURE])

    with pytest.raises(SystemExit) as e:
        cmd_gate.cmd_final_gate(_args())
    assert e.value.code == 2
    assert read_state(state_path)["final_gate"]["checks"][-1]["status"] == "fail"
    assert spy["tests"] == []  # 落ちたファイルも比較の木も手元で走らせ直さない
    assert read_state(state_path)["final_gate"]["triage"]["caused"] == [NEW_FAILURE]


@pytest.mark.parametrize("conclusion", ["skipped", "neutral"])
def test_ci_only_gate_passes_skipped_and_neutral_checks(cmd_gate, tmp_path, env_tmp_dir, spy, capsys, conclusion):
    state_path = _ci_only_state(tmp_path, checks=("tests", "e2e"))
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"), _run("e2e", conclusion=conclusion))

    cmd_gate.cmd_final_gate(_args())

    assert "FINAL_GATE=passed" in capsys.readouterr().out
