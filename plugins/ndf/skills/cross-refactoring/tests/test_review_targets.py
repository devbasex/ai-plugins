"""再レビューの対象を絞る（#372）。

修正は指摘への対応であり、指摘を出していない担当が読む差分は、その担当が承認した
内容に対する他者の指摘への対応である。**変更要求を出した担当だけを起動し直す。**

差し戻し（`invalid`）はこの絞り込みの対象にしない。結果の形が判定に使えない状態は
修正の成否とは別で、承認した担当の結果も読めていない可能性がある。
"""
from __future__ import annotations

import pytest

from crossref_helpers import make_state, read_state, write_result

REVIEWERS = ["agy", "kiro"]
ROUND_ITEMS = ["R1-001", "R1-002"]
REVIEW_URL = "https://github.com/acme/demo/pull/130#pullrequestreview-1"


def review(verdict="APPROVE", findings=None, **over):
    base = {"verdict": verdict, "findings": findings or [], "review_url": REVIEW_URL}
    base.update(over)
    return base


def finding(item_id="R1-001", **over):
    base = {"item_id": item_id, "thread_id": "PRRT_x", "summary": "x", "resolved": False}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def posted_reviews(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_posted_review_state", lambda *a, **k: True)


def _state(tmp_path, **over):
    return make_state(
        tmp_path,
        phase="review",
        outer_round=1,
        items=[
            {"item_id": i, "round": 1, "path": "src/foo.py", "symbol": "S",
             "smell": "long_method", "technique": "extract_method", "severity": "major",
             "rationale": "", "plan": "", "test_gap": False,
             "estimated_diff_lines": 10, "proposed_by": ["codex"],
             "status": "reviewing", "commits": ["abc"]}
            for i in ROUND_ITEMS
        ],
        rounds=[{
            "round": 1, "impl": "codex", "reviewers": REVIEWERS,
            "impl_model": {"requested": None, "observed": None},
            "reviewer_models": {r: {"requested": None, "observed": None}
                                for r in REVIEWERS},
            "proposed": {}, "merged": 2, "adopted": 2, "deferred": 0,
            "items": list(ROUND_ITEMS),
            "apply": {"applied": list(ROUND_ITEMS), "failed": []},
            "fix_rounds": 0, "durations": {}, "reviews": [],
        }],
        **over,
    )


def _args(round_no=1):
    return type("A", (), {"id": 130, "round": round_no})()


# ---------- 初回のレビュー ----------

def test_first_review_targets_all_reviewers(refactor, tmp_path, env_tmp_dir, capsys):
    """`fix_reviewers` を持たないラウンドは初回として読み、担当 2 者を返す。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)

    refactor.cmd_review_targets(_args())

    out = capsys.readouterr().out
    assert "REVIEW_TARGETS='agy kiro'" in out
    assert "REVIEW_TARGETS_CSV=agy,kiro" in out


def test_existing_state_without_fix_reviewers_is_first_review(
    refactor, tmp_path, env_tmp_dir, capsys
):
    """既存の状態ファイル（このキーを持たない）でも初回として読める。"""
    state_path = _state(tmp_path)
    state = read_state(state_path)
    assert "fix_reviewers" not in state["rounds"][0]
    env_tmp_dir(state_path)

    refactor.cmd_review_targets(_args())

    assert "REVIEW_TARGETS='agy kiro'" in capsys.readouterr().out


# ---------- 再レビュー ----------

def test_retry_targets_only_the_reviewer_who_requested_changes(
    refactor, tmp_path, env_tmp_dir, no_git, capsys
):
    """変更要求を出した担当だけが再レビューの対象になる。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "agy-review-r1", review("REQUEST_CHANGES", [finding()]))
    write_result(state_path, "kiro-review-r1", review())

    with pytest.raises(SystemExit) as e:
        refactor.cmd_judge_review(_args())
    assert e.value.code == 2
    assert read_state(state_path)["rounds"][0]["fix_reviewers"] == ["agy"]

    capsys.readouterr()
    refactor.cmd_review_targets(_args())
    out = capsys.readouterr().out
    assert "REVIEW_TARGETS='agy'" in out
    assert "kiro" not in out


def test_retry_targets_both_when_both_requested_changes(
    refactor, tmp_path, env_tmp_dir, no_git, capsys
):
    """2 者とも変更要求なら、再レビューも 2 者になる。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    for r in REVIEWERS:
        write_result(state_path, f"{r}-review-r1",
                     review("REQUEST_CHANGES", [finding()]))

    with pytest.raises(SystemExit):
        refactor.cmd_judge_review(_args())
    assert read_state(state_path)["rounds"][0]["fix_reviewers"] == REVIEWERS

    capsys.readouterr()
    refactor.cmd_review_targets(_args())
    assert "REVIEW_TARGETS='agy kiro'" in capsys.readouterr().out


def test_invalid_verdict_does_not_narrow_targets(
    refactor, tmp_path, env_tmp_dir, capsys
):
    """差し戻しは 2 者へ戻す。結果の形が判定に使えないことは修正の成否と別である。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "agy-review-r1",
                 review("REQUEST_CHANGES", [finding("R9-999")]))
    write_result(state_path, "kiro-review-r1", review())

    with pytest.raises(SystemExit) as e:
        refactor.cmd_judge_review(_args())
    assert e.value.code == 3
    assert "fix_reviewers" not in read_state(state_path)["rounds"][0]

    capsys.readouterr()
    refactor.cmd_review_targets(_args())
    assert "REVIEW_TARGETS='agy kiro'" in capsys.readouterr().out


# ---------- 進めない状態 ----------

def test_empty_targets_fail(refactor, tmp_path, env_tmp_dir):
    """担当が 0 人になる状態では進まない。判定できない状態を進めないためである。"""
    state_path = _state(tmp_path)
    state = read_state(state_path)
    state["rounds"][0]["fix_reviewers"] = []
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_review_targets(_args())
    assert e.value.code == refactor.ABORT


def test_unknown_round_fails(refactor, tmp_path, env_tmp_dir):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_review_targets(_args(round_no=9))
    assert e.value.code == refactor.ABORT


# ---------- 引き継ぎ ----------

def test_approved_reviewer_result_is_carried_over(
    refactor, tmp_path, env_tmp_dir, no_git
):
    """起動しなかった担当の判定を、前の結果ファイルから引き継ぐ。

    再レビューでは変更要求を出した担当だけが結果を書き直す。承認した担当の結果は
    その場に残り、`judge` はそれを読んで承認と数える。
    """
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    write_result(state_path, "agy-review-r1", review("REQUEST_CHANGES", [finding()]))
    write_result(state_path, "kiro-review-r1", review())

    with pytest.raises(SystemExit):
        refactor.cmd_judge_review(_args())

    # 修正のラウンドを 1 つ進め、変更要求を出した担当だけが結果を書き直す。
    state = read_state(state_path)
    state["rounds"][0]["fix_rounds"] = 1
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    write_result(state_path, "agy-review-r1", review())

    refactor.cmd_judge_review(_args())

    state = read_state(state_path)
    assert state["phase"] == "propose"
    assert state["rounds"][0]["reviews"][-1]["kiro"] == "APPROVE"
