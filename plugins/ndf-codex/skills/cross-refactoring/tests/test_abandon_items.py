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


def _args(dry_run=False):
    return type("A", (), {"id": 130, "round": 1, "dry_run": dry_run})()


def test_only_items_with_unresolved_findings_are_abandoned(
    refactor, tmp_path, env_tmp_dir, no_git
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


def test_resolved_findings_do_not_abandon(refactor, tmp_path, env_tmp_dir, no_git):
    state_path = _state(tmp_path, [_finding("R1-001", resolved=True)])
    env_tmp_dir(state_path)
    refactor.cmd_abandon_items(_args())
    state = read_state(state_path)
    assert all(i["status"] == "reviewing" for i in state["items"])
    assert state["deferred_items"] == []


def test_null_item_id_abandons_the_whole_round(refactor, tmp_path, env_tmp_dir, no_git):
    """どの項目にも紐づかない指摘が残ったら、そのラウンドの適用を全件取り消す。"""
    state_path = _state(tmp_path, [_finding(None)])
    env_tmp_dir(state_path)
    refactor.cmd_abandon_items(_args())
    state = read_state(state_path)
    assert all(i["status"] == "abandoned" for i in state["items"])
    assert len(state["deferred_items"]) == 2


def test_deferred_entry_records_the_reason(refactor, tmp_path, env_tmp_dir, no_git):
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


def test_revert_failure_rolls_back_to_the_starting_head(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """複数コミットの途中で失敗したとき、成功済みの取り消しも巻き戻すこと。

    先行して成功した取り消しだけが履歴に残ると、再実行で不整合になって進めなくなる。
    """
    state_path = _state(tmp_path, [_finding("R1-001")], item_ids=("R1-001",))
    state = read_state(state_path)
    state["items"][0]["commits"] = ["old111", "new222"]
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        # 2 件目（古い方）の取り消しで失敗させる
        failing = cmd[:2] == ["git", "revert"] and cmd[-1] == "old111"
        return subprocess.CompletedProcess(cmd, 1 if failing else 0, "", "conflict")

    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "HEAD_BEFORE")
    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        refactor.cmd_abandon_items(_args(dry_run=False))

    assert ["git", "reset", "--hard", "HEAD_BEFORE"] in calls


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

def _fix_commit(**over):
    """`collect_commit_facts()` が git から作る事実。"""
    base = {
        "sha": "fix111", "exists": True, "test_status": "pass",
        "touches_tests": False, "diff_lines": 10,
        "trailers": {"Item-Id": "R1-001", "Round": "1",
                     "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5"},
    }
    base.update(over)
    return base


def _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, claimed,
                 thread="PRRT_a", facts=None):
    state_path = _state(tmp_path, [_finding("R1-001", thread=thread)])
    state = read_state(state_path)
    state["rounds"][0]["fix_rounds"] = 0
    state["rounds"][0]["fix_base_sha"] = "FIX_BASE"
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)
    resolved_facts = [_fix_commit()] if facts is None else facts
    # `rev-parse --verify <sha>^{commit}` は SHA をそのまま返す形にしておく。
    # 未申告コミットの判定がこの解決を通るため。
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: (args[-1].replace("^{commit}", "")
                            if args[:2] == ["rev-parse", "--verify"] else "HEAD_NOW"),
    )
    monkeypatch.setattr(
        refactor, "commits_in_range",
        lambda work, base, head: [f["sha"] for f in resolved_facts],
    )
    # 検証の材料は git から取る。テストでは git 由来の事実だけを差し替える。
    monkeypatch.setattr(
        refactor, "collect_commit_facts",
        lambda work, shas, rng, cmd, branch: resolved_facts,
    )
    write_result(state_path, "codex-fix-r1", {
        "resolved_thread_ids": claimed,
        "elapsed_seconds": 12,
        "commits": [{"sha": f["sha"]} for f in resolved_facts],
    })
    return state_path


def test_merge_fix_resolves_threads_and_counts_rounds(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["fix_rounds"] == 1
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is True
    assert "fix111" in state["items"][0]["commits"]


def test_merge_fix_rejects_unverified_resolution_claims(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """解決 API に失敗・未実行でも「解決済み」と書けてしまうため、突き合わせる。"""
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github", lambda repo, pr: set())
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False


def test_merge_fix_rejects_commits_that_skip_the_procedure(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """修正側だけ素通しにすると、手順を外れた変更がそのまま収束済みになる。"""
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"],
        facts=[_fix_commit(test_status="fail")],
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False
    assert "fix111" not in state["items"][0]["commits"]


def test_merge_fix_rejects_commits_missing_trailers(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    commit = _fix_commit()
    del commit["trailers"]["Impl-Model"]
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"], facts=[commit]
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False


def test_merge_fix_treats_unreachable_github_as_unresolved(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """取得できないことと「解決済みが 0 件」を混同しない。安全側に倒す。"""
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github", lambda repo, pr: None)
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False


def test_resolved_threads_returns_none_when_gh_fails(refactor, monkeypatch):
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "auth required"),
    )
    assert refactor.resolved_threads_on_github("a/b", 1) is None


def test_resolved_threads_reads_only_resolved_ids(refactor, monkeypatch):
    payload = {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"id": "T1", "isResolved": True}, {"id": "T2", "isResolved": False}],
    }}}}}
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, __import__("json").dumps(payload), ""),
    )
    assert refactor.resolved_threads_on_github("a/b", 1) == {"T1"}


def test_resolved_threads_follows_pagination(refactor, monkeypatch):
    pages = [
        {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
            "nodes": [{"id": "T1", "isResolved": True}]}}}}},
        {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"id": "T2", "isResolved": True}]}}}}},
    ]
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, __import__("json").dumps(pages[len(calls) - 1]), "")

    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    assert refactor.resolved_threads_on_github("a/b", 1) == {"T1", "T2"}
    assert any("cursor=C1" in "".join(c) for c in calls)


@pytest.mark.parametrize("broken_ids", ["文字列", 123, True, {"a": 1}])
def test_broken_fix_result_does_not_crash(
    refactor, tmp_path, env_tmp_dir, monkeypatch, broken_ids
):
    """`commits` や `resolved_thread_ids` が壊れていてもクラッシュしない。

    文字列は 1 文字ずつに分解され、数値や真偽値は反復できずに落ちる。
    **反復できない値まで含めて**確かめる。
    """
    state_path = _state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    write_result(state_path, "codex-fix-r1", {
        "resolved_thread_ids": broken_ids,
        "commits": {"sha": "辞書ではあるが配列でない"},
    })
    monkeypatch.setattr(refactor, "resolved_threads_on_github", lambda repo, pr: set())
    monkeypatch.setattr(refactor, "commits_in_range", lambda work, base, head: [])
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "HEAD")
    monkeypatch.setattr(
        refactor, "collect_commit_facts",
        lambda work, shas, rng, cmd, branch: [],
    )
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())
    state = read_state(state_path)
    assert state["rounds"][0]["fix_rounds"] == 4
    # 壊れた申告は採用されない
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False


def test_merge_fix_uses_the_recorded_range(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """修正の範囲も、オーケストレータが記録した起点から取ること。

    空集合を渡すと全ての修正コミットが「範囲外」になって必ず不正扱いになる。
    """
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    state = read_state(state_path)
    state["rounds"][0]["fix_base_sha"] = "FIX_BASE"
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")

    seen: list = []
    monkeypatch.setattr(
        refactor, "commits_in_range",
        lambda work, base, head: seen.append((base, head)) or ["fix111"],
    )
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: (args[-1].replace("^{commit}", "")
                            if args[:2] == ["rev-parse", "--verify"] else "HEAD_NOW"),
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    assert seen == [("FIX_BASE", "HEAD_NOW")]
    assert read_state(state_path)["rounds"][0]["reviews"][0]["findings"][0]["resolved"]


def test_merge_fix_fails_when_the_range_cannot_be_determined(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "commits_in_range", lambda work, base, head: None)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "HEAD_NOW")
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())
    assert e.value.code == 2


def test_merge_fix_rejects_unreported_commits(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """申告から漏れた修正コミットは検証を受けずに残る。範囲ごと取り消す。"""
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    # 範囲には 2 件あるが、申告は 1 件だけ
    monkeypatch.setattr(
        refactor, "commits_in_range", lambda work, base, head: ["sneaky", "fix111"])
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: args[-1].replace("^{commit}", "") if args[0] == "rev-parse"
        else "HEAD_NOW",
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    calls: list[list[str]] = []
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: calls.append(list(cmd))
        or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: "")

    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    reverts = [c[-1] for c in calls if c[:2] == ["git", "revert"]]
    assert reverts == ["sneaky", "fix111"]
    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False


def test_unattributable_invalid_commit_blocks_all_resolutions(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """項目を特定できない不正コミットがあれば、解決の申告を一切採らない。

    `invalid_items` に `None` を入れても指摘の `item_id` とは一致しないため、
    そのままではスレッドが解決済みへ進んでしまう。
    """
    broken = _fix_commit(sha="fix111", trailers={})   # Item-Id を取れない
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"], facts=[broken]
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False
