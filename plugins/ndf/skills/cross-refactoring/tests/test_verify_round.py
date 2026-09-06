"""検証（`verify-round`）と修正ラウンドの上限のテスト。

**Step 5 の判定はテストの結果で決まる**（#436 決定 3）。2 CLI のレビューは
起動しない。判定の単位は適用ラウンドで、失敗を項目までは特定しない。

適用そのものが通らないこと（競合・対象が消えている）と、テストが落ちることは
扱いが違う。前者は `merge-apply` がその群を取り消し、後者はこの工程が修正
ラウンドを回す。
"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state

ITEM_IDS = ["R1-001", "R1-002"]


def _item(item_id, status="applied"):
    return {
        "item_id": item_id, "round": 1, "path": "src/foo.py", "symbol": item_id,
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 10, "proposed_by": ["codex"],
        "status": status, "commits": ["sha1"], "apply_round": 1,
    }


def _state(tmp_path, applied=None, groups=None, apply_round=1, fix_rounds=0,
           **over):
    items = [_item(i) for i in ITEM_IDS]
    groups = groups or [{
        "apply_round": 1, "impl": "codex",
        "impl_model": {"requested": None, "observed": None},
        "items": list(ITEM_IDS), "status": "applied",
        "base_sha": "base0", "head_sha": "sha1", "fix_rounds": 0,
    }]
    return make_state(
        tmp_path,
        items=items,
        rounds=[{
            "round": 1, "impl": "codex", "reviewers": ["agy", "kiro"],
            "impl_model": {"requested": None, "observed": None},
            "reviewer_models": {},
            "proposed": {}, "merged": 2, "adopted": 2, "deferred": 0,
            "items": list(ITEM_IDS),
            "apply_rounds": groups,
            "apply_round": apply_round,
            "apply": {
                "apply_round": apply_round,
                "applied": list(ITEM_IDS) if applied is None else list(applied),
                "failed": [], "merged_at": "2026-08-15T00:00:00",
            },
            "fix_rounds": fix_rounds, "durations": {}, "reviews": [],
        }],
        **over,
    )


def _args():
    return type("A", (), {"id": 130, "round": 1})()


def _test_run(refactor, monkeypatch, code=0, timed_out=False):
    """`--baseline-test` の実行を差し替え、渡された引数を記録する。"""
    seen: list[tuple[str, str, int]] = []

    def fake(command, cwd, timeout, grace=5.0):
        seen.append((command, cwd, timeout))
        return code, timed_out

    monkeypatch.setattr(refactor, "_run_with_timeout", fake)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args, **k: "HEAD_NOW")
    return seen


# ---------- B1: 判定はテストの結果で決まる ----------

def test_a_passing_test_closes_the_apply_round(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    seen = _test_run(refactor, monkeypatch, code=0)

    refactor.cmd_verify_round(_args())

    assert seen and seen[0][0] == "pytest -q", "着手前のテストと同じコマンドを実行する"
    state = read_state(state_path)
    assert all(i["status"] == "done" for i in state["items"])
    assert state["rounds"][0]["apply_rounds"][0]["status"] == "verified"
    assert state["rounds"][0]["verifications"][-1]["status"] == "pass"


def test_a_failing_test_sends_the_apply_round_to_the_fix_loop(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """B1 / B3 — 失敗は適用ラウンドの単位で見る。項目までは特定しない。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=1)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_verify_round(_args())
    assert e.value.code == 2

    state = read_state(state_path)
    # どの項目が落としたのかは決めない。取り消しの単位と判定の単位を揃える
    assert all(i["status"] == "applied" for i in state["items"])
    assert state["rounds"][0]["verifications"][-1]["status"] == "fail"


def test_a_failing_test_records_the_fix_base(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """修正の範囲の起点を残す。無いと `merge-fix` が範囲を確定できない。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=1)

    with pytest.raises(SystemExit):
        refactor.cmd_verify_round(_args())

    entry = read_state(state_path)["rounds"][0]
    assert entry["fix_base_sha"] == "HEAD_NOW"
    assert entry["fix_attempts"] == 1


def test_a_timeout_counts_as_a_failure(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """打ち切りは通す側に倒さない。待ち続けると進行全体が止まる。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=None, timed_out=True)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_verify_round(_args())
    assert e.value.code == 2
    assert read_state(state_path)["rounds"][0]["verifications"][-1]["timed_out"] is True


def test_no_reviewer_is_launched(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """**2 CLI のレビューは起動しない**（決定 3）。判定はテストの結果だけで決まる。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=0)

    refactor.cmd_verify_round(_args())

    assert read_state(state_path)["rounds"][0]["reviews"] == []
    assert not hasattr(refactor, "cmd_judge_review")
    assert not hasattr(refactor, "cmd_review_targets")


def test_the_verification_uses_the_configured_timeout(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _state(tmp_path, test_timeout=42)
    env_tmp_dir(state_path)
    seen = _test_run(refactor, monkeypatch, code=0)
    refactor.cmd_verify_round(_args())
    assert seen[0][2] == 42


def test_verifying_a_dropped_apply_round_is_refused(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """取り消し済みの群には検証する対象が無い。素通りさせない。"""
    state_path = _state(tmp_path, applied=[])
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=0)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_verify_round(_args())
    assert e.value.code == 2


# ---------- 群が残っていればフェーズは適用のまま ----------

def test_the_phase_stays_on_apply_while_groups_remain(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    groups = [
        {"apply_round": 1, "impl": "codex", "items": ["R1-001"],
         "status": "applied", "base_sha": "base0", "head_sha": "sha1",
         "fix_rounds": 0},
        {"apply_round": 2, "impl": "agy", "items": ["R1-002"],
         "status": "pending", "base_sha": None, "head_sha": None,
         "fix_rounds": 0},
    ]
    state_path = _state(tmp_path, applied=["R1-001"], groups=groups)
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=0)

    refactor.cmd_verify_round(_args())
    assert read_state(state_path)["phase"] == "apply"


def test_the_phase_returns_to_propose_after_the_last_group(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=0)
    refactor.cmd_verify_round(_args())
    assert read_state(state_path)["phase"] == "propose"


# ---------- 修正ラウンドの上限 ----------

def test_should_abandon_only_at_the_limit(refactor, tmp_path, env_tmp_dir):
    """`--max-fix-rounds` は**1 つの適用ラウンドあたり**の上限である。"""
    state_path = _state(tmp_path)
    env_tmp_dir(state_path)
    args = _args()

    with pytest.raises(SystemExit) as e:
        refactor.cmd_should_abandon(args)
    assert e.value.code == 2

    state = read_state(state_path)
    state["rounds"][0]["fix_rounds"] = 3
    state_path.write_text(json.dumps(state), encoding="utf-8")
    refactor.cmd_should_abandon(args)  # 上限到達で正常終了 = 見送りへ


# ---------- 外へ出す文章の規約（#436 決定 6-b） ----------

def test_a_passing_verification_names_the_items_and_the_plan(
    refactor, tmp_path, env_tmp_dir, monkeypatch, capsys
):
    """D1 / D3 — 項目は `<ファイル>#<シンボル>` を併記し、改修計画は生の URL。"""
    url = "https://github.com/devbasex/ai-plugins/pull/130#issuecomment-7"
    state_path = _state(tmp_path, plan_mode="comment", plan_file="",
                        plan_comment={"id": 7, "url": url})
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=0)

    refactor.cmd_verify_round(_args())

    out = capsys.readouterr().err
    assert "src/foo.py#R1-001" in out
    assert f"改修計画: {url}" in out


def test_a_failing_verification_still_names_the_plan(
    refactor, tmp_path, env_tmp_dir, monkeypatch, capsys
):
    url = "https://github.com/devbasex/ai-plugins/pull/130#issuecomment-7"
    state_path = _state(tmp_path, plan_mode="comment", plan_file="",
                        plan_comment={"id": 7, "url": url})
    env_tmp_dir(state_path)
    _test_run(refactor, monkeypatch, code=1)

    with pytest.raises(SystemExit):
        refactor.cmd_verify_round(_args())

    assert url in capsys.readouterr().err
