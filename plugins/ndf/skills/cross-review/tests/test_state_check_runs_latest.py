"""同名のチェックジョブは名前ごとの最新の実行で判定する（#632 / #849）。

再実行で `failure` → `success` になったチェックを失敗として数えると、承認されたラウンドが
code_failure へ差し戻される。畳み方は共通層の `gh_parts.fold_check_runs` が持つ。
"""
from __future__ import annotations

REPO = "o/r"
SHA = "b87b3ae"


def _run(state_mod, runs):
    return lambda path: state_mod.RestResponse(
        headers={}, body={"total_count": len(runs), "check_runs": runs},
        rate_remaining=None, rate_reset=None,
    )


def _check(name, conclusion, started, run_id):
    return {"id": run_id, "name": name, "status": "completed", "conclusion": conclusion,
            "started_at": started}


RERUN = [
    _check("pytest", "failure", "2026-09-25T01:00:00Z", 101),
    _check("lint", "success", "2026-09-25T01:00:00Z", 102),
    _check("pytest", "success", "2026-09-25T02:00:00Z", 103),
]


def test_rerun_success_supersedes_the_earlier_failure(state_mod, real_github, monkeypatch):
    monkeypatch.setattr(state_mod, "_gh_rest", _run(state_mod, RERUN))

    runs = state_mod._fetch_check_runs(REPO, SHA)

    assert [(r["name"], r["conclusion"]) for r in runs] == [("pytest", "success"),
                                                            ("lint", "success")]


def test_round_ci_converges_after_a_rerun(state_mod, real_github, monkeypatch):
    monkeypatch.setattr(state_mod, "_gh_rest", _run(state_mod, RERUN))

    ci = state_mod._round_ci({"repo": REPO}, {"head_sha": SHA}, 1)

    assert ci == {"verdict": "success", "sha": SHA}


def test_rerun_failure_after_success_still_fails(state_mod, real_github, monkeypatch):
    monkeypatch.setattr(state_mod, "_gh_rest", _run(state_mod, [
        _check("pytest", "success", "2026-09-25T01:00:00Z", 101),
        _check("pytest", "failure", "2026-09-25T02:00:00Z", 102),
    ]))

    ci = state_mod._round_ci({"repo": REPO}, {"head_sha": SHA}, 1)

    assert ci["verdict"] == "code_failure" and ci["failed"] == ["pytest"]
