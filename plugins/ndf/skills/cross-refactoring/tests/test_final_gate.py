"""最終ゲート（Step 7）の分岐のテスト（#436 B2 / B5）。

**起動のされ方で最終ゲートが変わる**（決定 7）。`development-workflow` の 1 工程と
して起動したときは `cross-review` を省き、全体のテストで判定する。単独で起動した
ときは `cross-review` を実行する。見分けは**引数で受け取る**。

**`--ci-check` の扱いは排他である。** 指定があれば手元のテストを実行せず継続的統合の
成功だけで判定し、無ければ手元のテストだけで判定する。**採った側が失敗した・結果を
得られないときは通過させない**（fail-closed）。
"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state


def _state(tmp_path, **over):
    return make_state(tmp_path, phase="final", outer_round=1, **over)


def _args(state_id=130):
    return type("A", (), {"id": state_id})()


@pytest.fixture
def spy(refactor, monkeypatch):
    """テストの実行と `gh` の呼び出しを差し替え、何を呼んだかを記録する。"""
    seen: dict[str, list] = {"tests": [], "gh": []}

    def fake_run(command, cwd, timeout, grace=5.0):
        seen["tests"].append(command)
        return seen.get("test_code", 0), False

    def fake_sh(cmd, cwd=None, check=True):
        seen["gh"].append(list(cmd))
        return seen.get("gh_out", "")

    monkeypatch.setattr(refactor, "_run_with_timeout", fake_run)
    monkeypatch.setattr(refactor, "_sh", fake_sh)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "HEADSHA")
    return seen


def _check_runs(*runs):
    return json.dumps({"total_count": len(runs), "check_runs": list(runs)})


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


# ---------- B2: 起動のされ方で最終ゲートが変わる ----------

def test_a_standalone_run_goes_to_cross_review(
    refactor, tmp_path, env_tmp_dir, spy, capsys
):
    """既定は単独起動。`cross-review` を実行する。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_final_gate(_args())

    assert "FINAL_GATE=cross-review" in capsys.readouterr().out
    assert spy["tests"] == [], "単独起動では全体のテストを実行しない"
    assert read_state(state_path)["final_gate"]["mode"] == "cross-review"


def test_a_workflow_step_run_skips_cross_review_and_runs_the_tests(
    refactor, tmp_path, env_tmp_dir, spy, capsys
):
    """工程の 1 つとして起動したときは `cross-review` を省く。"""
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)

    refactor.cmd_final_gate(_args())

    out = capsys.readouterr().out
    assert "FINAL_GATE=passed" in out
    assert "cross-review" not in out
    assert spy["tests"] == ["pytest -q"]
    assert read_state(state_path)["final_gate"]["status"] == "passed"


def test_the_launch_mode_comes_from_the_argument(refactor, monkeypatch):
    """決定 7 — 環境変数や控えの読み取りではなく、呼ぶ側が引数で伝える。"""
    captured = {}
    monkeypatch.setattr(refactor, "cmd_init", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(
        refactor.sys, "argv",
        ["refactor.py", "init", "130", "--scope", "src", "--host", "claude",
         "--baseline-test", "true", "--workflow-step"],
    )
    refactor.main()
    assert captured["workflow_step"] is True


# ---------- B2: `--ci-check` は排他 ----------

def test_the_ci_check_replaces_the_local_tests(
    refactor, tmp_path, env_tmp_dir, spy, capsys
):
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    refactor.cmd_final_gate(_args())

    assert spy["tests"] == [], "継続的統合を採ったら手元のテストは実行しない"
    assert "FINAL_GATE=passed" in capsys.readouterr().out


def test_the_check_runs_are_read_once_and_status_is_not_used(
    refactor, tmp_path, env_tmp_dir, spy
):
    """`status` は使わない（GitHub Actions は常に `pending` を返す）。"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests"))

    refactor.cmd_final_gate(_args())

    assert len(spy["gh"]) == 1, "読むのは check-runs の 1 回だけ"
    path = spy["gh"][0][-1]
    assert "check-runs" in path and "HEADSHA" in path
    assert not path.endswith("/status")


def test_a_failed_ci_check_does_not_pass(refactor, tmp_path, env_tmp_dir, spy):
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests", conclusion="failure"))

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 2
    assert read_state(state_path)["final_gate"]["checks"][-1]["status"] == "fail"


@pytest.mark.parametrize("payload", [
    "",                                        # 照会そのものができない
    '{"total_count": 0, "check_runs": []}',    # 検査が 1 件も無い
    "not json",                                # 応答を解釈できない
])
def test_no_result_does_not_pass(refactor, tmp_path, env_tmp_dir, spy, payload):
    """fail-closed — **結果を得られないときは通過させない。**"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = payload

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 2


def test_an_unfinished_ci_check_does_not_pass(refactor, tmp_path, env_tmp_dir, spy):
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("tests", conclusion=None, status="in_progress"))

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 2


def test_a_named_check_that_is_missing_does_not_pass(
    refactor, tmp_path, env_tmp_dir, spy
):
    """名前が一致しない検査の成功で通さない。"""
    state_path = _state(tmp_path, workflow_step=True, ci_check="tests")
    env_tmp_dir(state_path)
    spy["gh_out"] = _check_runs(_run("lint"))

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 2


def test_a_failing_local_test_is_not_overturned_by_the_ci(
    refactor, tmp_path, env_tmp_dir, spy
):
    """**「どちらか一方が通れば通過」とはしない。** 指定が無ければ手元のテストだけを見る。"""
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)
    spy["test_code"] = 1
    spy["gh_out"] = _check_runs(_run("tests"))

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 2
    assert spy["gh"] == [], "手元のテストを採ったら継続的統合は読まない"


# ---------- B5: 落ちたら修正ラウンドを回し、上限では取り消さない ----------

def test_a_failure_opens_a_fix_round(refactor, tmp_path, env_tmp_dir, spy):
    state_path = _state(tmp_path, workflow_step=True)
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 2
    gate = read_state(state_path)["final_gate"]
    assert gate["fix_rounds"] == 1
    assert gate["status"] == "failing"


def test_the_fix_cap_reports_the_failure_without_reverting(
    refactor, tmp_path, env_tmp_dir, spy, monkeypatch
):
    """**Step 7 は push 済みの地点である。** 上限に達しても取り消さない。

    取り消しの判断は Pull Request の読み手が持つ。失敗として報告に書く。
    """
    dropped: list = []
    monkeypatch.setattr(refactor, "_drop_items",
                        lambda *a, **k: dropped.append(a) or {})
    state_path = _state(
        tmp_path, workflow_step=True, max_fix_rounds=2,
        final_gate={"fix_rounds": 2, "checks": []})
    env_tmp_dir(state_path)
    spy["test_code"] = 1

    with pytest.raises(SystemExit) as e:
        refactor.cmd_final_gate(_args())
    assert e.value.code == 1, "報告へ抜ける。進行ごと止める中断（4）ではない"
    assert dropped == [], "取り消さない"
    assert read_state(state_path)["final_gate"]["status"] == "failed"


def test_the_report_shows_the_final_gate(refactor, tmp_path, env_tmp_dir, capsys):
    state_path = _state(
        tmp_path, workflow_step=True,
        final_gate={"fix_rounds": 2, "status": "failed", "mode": "test",
                    "checks": []})
    env_tmp_dir(state_path)
    refactor.cmd_report(type("A", (), {"id": 130, "metrics": False})())
    assert "最終ゲート" in capsys.readouterr().out


# ---------- B5: Step 5 は手元のテストを必須とする ----------

def test_the_apply_round_verification_never_uses_the_ci(
    refactor, tmp_path, env_tmp_dir, spy
):
    """**継続的統合で代替できるのは Step 7 だけである。**

    Step 5 は手元の未 push のコミットを検証するため、継続的統合の結果が無い。
    """
    items = [{
        "item_id": "R1-001", "round": 1, "path": "src/foo.py", "symbol": "f",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "status": "applied", "commits": ["sha1"], "apply_round": 1,
        "test_gap": False, "estimated_diff_lines": 10, "proposed_by": ["codex"],
    }]
    state_path = make_state(
        tmp_path, ci_check="tests", workflow_step=True, items=items,
        rounds=[{
            "round": 1, "kind": "structure", "impl": "codex",
            "reviewers": ["agy", "kiro"],
            "impl_model": {"requested": None, "observed": None},
            "reviewer_models": {}, "proposed": {}, "merged": 1, "adopted": 1,
            "deferred": 0, "items": ["R1-001"],
            "apply_rounds": [{
                "apply_round": 1, "impl": "codex", "items": ["R1-001"],
                "status": "applied", "base_sha": "base0", "head_sha": "sha1",
                "fix_rounds": 0,
            }],
            "apply_round": 1,
            "apply": {"apply_round": 1, "applied": ["R1-001"], "failed": [],
                      "merged_at": "2026-08-15T00:00:00"},
            "fix_rounds": 0, "durations": {}, "reviews": [],
        }],
    )
    env_tmp_dir(state_path)

    refactor.cmd_verify_round(type("A", (), {"id": 130, "round": 1})())

    assert spy["tests"] == ["pytest -q"], "Step 5 は手元のテストを実行する"
    assert spy["gh"] == [], "Step 5 で継続的統合は読まない"
