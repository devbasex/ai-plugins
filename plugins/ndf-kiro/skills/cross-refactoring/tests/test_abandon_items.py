"""見送り処理のテスト。

レビューはラウンド単位で回すが、**取り消しは項目単位**で行う。
指摘の無い項目と解決済みの項目は Pull Request に残す。
"""
from __future__ import annotations

import subprocess

import pytest

from conftest import make_state, read_state, write_result

REVIEWERS = ["gemini", "kiro"]


def _item(item_id, commits, status="reviewing"):
    return {
        "item_id": item_id, "round": 1, "path": "src/foo.py", "symbol": item_id,
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 10, "proposed_by": ["codex"],
        "status": status, "commits": commits,
    }


def _finding(item_id, resolved=False, thread="PRRT_x"):
    return {"reviewer": "gemini", "item_id": item_id,
            "thread_id": thread, "summary": "x", "resolved": resolved}


def _state(tmp_path, findings, item_ids=("R1-001", "R1-002")):
    items = [_item(i, [f"sha-{i}"]) for i in item_ids]
    return make_state(
        tmp_path,
        items=items,
        rounds=[{
            "round": 1, "impl": "codex", "reviewers": REVIEWERS,
            "impl_model": {"requested": None, "observed": None},
            "reviewer_models": {r: {"requested": None, "observed": None}
                                for r in REVIEWERS},
            "proposed": {}, "merged": 2, "adopted": 2, "deferred": 0,
            "items": list(item_ids),
            "apply": {"applied": list(item_ids), "failed": []},
            "fix_rounds": 3, "durations": {},
            "reviews": [{"round": 1, "gemini": "REQUEST_CHANGES", "kiro": "APPROVE",
                         "findings": findings}],
        }],
    )


def _args(dry_run=True):
    return type("A", (), {"id": 130, "round": 1, "dry_run": dry_run})()


def test_only_items_with_unresolved_findings_are_abandoned(
    refactor, tmp_path, env_tmp_dir
):
    state_path = _state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    refactor.cmd_abandon_items(_args())

    state = read_state(state_path)
    by_id = {i["item_id"]: i for i in state["items"]}
    assert by_id["R1-001"]["status"] == "abandoned"
    # 合意済みの項目は Pull Request に残す
    assert by_id["R1-002"]["status"] == "reviewing"
    assert [d["item_id"] for d in state["deferred_items"]] == ["R1-001"]


def test_resolved_findings_do_not_abandon(refactor, tmp_path, env_tmp_dir):
    state_path = _state(tmp_path, [_finding("R1-001", resolved=True)])
    env_tmp_dir(state_path)
    refactor.cmd_abandon_items(_args())
    state = read_state(state_path)
    assert all(i["status"] == "reviewing" for i in state["items"])
    assert state["deferred_items"] == []


def test_null_item_id_abandons_the_whole_round(refactor, tmp_path, env_tmp_dir):
    """どの項目にも紐づかない指摘が残ったら、そのラウンドの適用を全件取り消す。"""
    state_path = _state(tmp_path, [_finding(None)])
    env_tmp_dir(state_path)
    refactor.cmd_abandon_items(_args())
    state = read_state(state_path)
    assert all(i["status"] == "abandoned" for i in state["items"])
    assert len(state["deferred_items"]) == 2


def test_deferred_entry_records_the_reason(refactor, tmp_path, env_tmp_dir):
    state_path = _state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    refactor.cmd_abandon_items(_args())
    entry = read_state(state_path)["deferred_items"][0]
    assert entry["path"] == "src/foo.py"
    assert "修正ラウンドの上限" in entry["defer_reason"]


def test_revert_runs_newest_commit_first(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """新しいコミットから順に戻す。逆順にすると後続の取り消しが競合する。"""
    state_path = _state(tmp_path, [_finding("R1-001")], item_ids=("R1-001",))
    state = read_state(state_path)
    state["items"][0]["commits"] = ["old111", "new222"]
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    monkeypatch.setattr(refactor, "_sh", lambda *a, **k: "")
    refactor.cmd_abandon_items(_args(dry_run=False))

    reverts = [c for c in calls if c[:2] == ["git", "revert"]]
    assert [c[-1] for c in reverts] == ["new222", "old111"]


def test_revert_failure_aborts_and_stops(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """取り消しに失敗したら中断する。半端な状態を Pull Request に残さない。"""
    state_path = _state(tmp_path, [_finding("R1-001")], item_ids=("R1-001",))
    env_tmp_dir(state_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        rc = 1 if cmd[:2] == ["git", "revert"] and "--abort" not in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, "", "conflict")

    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        refactor.cmd_abandon_items(_args(dry_run=False))
    assert ["git", "revert", "--abort"] in calls


def test_push_never_uses_force(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """`--force` は使わない。他者の作業を消す事故を起こさないため。"""
    state_path = _state(tmp_path, [_finding("R1-001")], item_ids=("R1-001",))
    env_tmp_dir(state_path)
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    pushes: list[list[str]] = []
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: pushes.append(cmd) or "")
    refactor.cmd_abandon_items(_args(dry_run=False))

    assert pushes, "push が実行されていない"
    for cmd in pushes:
        assert "--force" not in cmd and "-f" not in cmd
        assert "--no-verify" not in cmd


# ---------- 修正の取り込み ----------

def test_merge_fix_resolves_threads_and_counts_rounds(
    refactor, tmp_path, env_tmp_dir
):
    state_path = _state(tmp_path, [_finding("R1-001", thread="PRRT_a")])
    state = read_state(state_path)
    state["rounds"][0]["fix_rounds"] = 0
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)
    write_result(state_path, "codex-fix-r1", {
        "resolved_thread_ids": ["PRRT_a"],
        "elapsed_seconds": 12,
        "commits": [{"sha": "fix111", "trailers": {
            "Item-Id": "R1-001", "Round": "1",
            "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"}}],
    })
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["fix_rounds"] == 1
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is True
    assert "fix111" in state["items"][0]["commits"]
