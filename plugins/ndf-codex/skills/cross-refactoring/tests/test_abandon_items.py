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


def _history(refactor, monkeypatch, newest_first):
    """`git rev-list HEAD` の結果（新しい順）と SHA 解決を差し替える。"""
    def fake_git_out(work, args):
        if args[:1] == ["rev-list"]:
            return "\n".join(newest_first)
        if args[:2] == ["rev-parse", "--verify"]:
            return args[-1].replace("^{commit}", "")
        return "HEAD_BEFORE"
    monkeypatch.setattr(refactor, "_git_out", fake_git_out)


@pytest.mark.parametrize("claimed", [
    ["old111", "new222"],      # 古い順の申告
    ["new222", "old111"],      # 新しい順の申告
])
def test_revert_runs_newest_commit_first(
    refactor, tmp_path, env_tmp_dir, monkeypatch, claimed
):
    """新しいコミットから順に戻す。逆順にすると後続の取り消しが競合する。

    **申告の順序は信用しない。** git の履歴から並べ直す。
    """
    state_path = _state(tmp_path, [_finding("R1-001")], item_ids=("R1-001",))
    state = read_state(state_path)
    state["items"][0]["commits"] = claimed
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)
    _history(refactor, monkeypatch, ["new222", "old111"])

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

    _history(refactor, monkeypatch, ["new222", "old111"])
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
    state["rounds"][0]["fix_attempts"] = 1
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
        lambda work, shas, rng, cmd, branch, timeout=None: resolved_facts,
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


def _no_git(refactor, monkeypatch):
    """取り消しと push を実際には走らせず、実行されたコマンドを記録する。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: calls.append(list(cmd))
        or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: calls.append(list(cmd)) or "")
    return calls


def test_merge_fix_rejects_commits_that_skip_the_procedure(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """修正側だけ素通しにすると、手順を外れた変更がそのまま収束済みになる。

    記録しないだけでは Pull Request に残るため、**範囲ごと取り消す**。
    """
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"],
        facts=[_fix_commit(test_status="fail")],
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    calls = _no_git(refactor, monkeypatch)
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False
    assert "fix111" not in state["items"][0]["commits"]
    assert [c[-1] for c in calls if c[:2] == ["git", "revert"]] == ["fix111"]
    assert any(c[:2] == ["git", "push"] for c in calls)


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
    calls = _no_git(refactor, monkeypatch)
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False
    assert [c[-1] for c in calls if c[:2] == ["git", "revert"]] == ["fix111"]


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
        lambda work, shas, rng, cmd, branch, timeout=None: [],
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
    state["rounds"][0]["fix_attempts"] = 1
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
    calls = _no_git(refactor, monkeypatch)
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    state = read_state(state_path)
    assert state["rounds"][0]["reviews"][0]["findings"][0]["resolved"] is False
    # 取り消し済みのコミットを状態へ残さない（後の見送りで二重に取り消さない）
    assert "fix111" not in state["items"][0]["commits"]
    assert [c[-1] for c in calls if c[:2] == ["git", "revert"]] == ["fix111"]


def test_reverted_fix_commits_are_not_recorded_in_state(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """全件取り消しになるときは、状態へコミットを記録しないこと。

    先に記録すると、取り消し済みのコミットが状態ファイルに残り、後の見送り処理が
    同じコミットをもう一度取り消そうとして落ちる。
    """
    good = _fix_commit(sha="good111")
    bad = _fix_commit(sha="bad222", test_status="fail")
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"], facts=[good, bad]
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    _no_git(refactor, monkeypatch)
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    recorded = read_state(state_path)["items"][0]["commits"]
    assert "good111" not in recorded, "取り消したコミットが状態に残っている"
    assert "bad222" not in recorded


def test_broken_elapsed_seconds_does_not_crash(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """修正結果の `elapsed_seconds` が非数値でも落ちないこと。"""
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    result = state_path.parent / "codex-fix-r1-result.json"
    payload = __import__("json").loads(result.read_text(encoding="utf-8"))
    payload["elapsed_seconds"] = {"だいたい": 10}
    result.write_text(__import__("json").dumps(payload), encoding="utf-8")
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())
    assert read_state(state_path)["rounds"][0]["durations"]["fix"] == 0


def test_revert_is_idempotent(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """取り消し済みの項目へもう一度取り消しを掛けないこと。

    push の失敗などで叩き直したときに、既に戻したコミットへ `git revert` を
    掛けると必ず失敗し、そこから先へ進めなくなる。
    """
    state_path = _state(tmp_path, [_finding("R1-001")], item_ids=("R1-001",))
    env_tmp_dir(state_path)
    calls = _no_git(refactor, monkeypatch)
    refactor.cmd_abandon_items(_args(dry_run=False))
    first = [c for c in calls if c[:2] == ["git", "revert"]]
    assert first, "1 回目で取り消していない"
    assert read_state(state_path)["items"][0]["reverted"] is True

    calls.clear()
    refactor.cmd_abandon_items(_args(dry_run=False))
    assert [c for c in calls if c[:2] == ["git", "revert"]] == []


def test_abandon_items_is_idempotent(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """叩き直しても見送りの記録を重複させないこと。"""
    state_path = _state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    _no_git(refactor, monkeypatch)
    refactor.cmd_abandon_items(_args(dry_run=False))
    refactor.cmd_abandon_items(_args(dry_run=False))
    state = read_state(state_path)
    assert [d["item_id"] for d in state["deferred_items"]] == ["R1-001"]


def test_abandon_items_records_processing_even_with_no_targets(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _state(tmp_path, [_finding("R1-001", resolved=True)])
    env_tmp_dir(state_path)
    _no_git(refactor, monkeypatch)
    refactor.cmd_abandon_items(_args(dry_run=False))
    assert read_state(state_path)["rounds"][0]["abandoned"] == []


def test_merge_fix_is_idempotent_for_the_same_input(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """同じ結果ファイルと同じ HEAD で叩き直しても、修正ラウンドを二重に数えない。

    修正は同じラウンドで何度も回るため「処理済みか」では判定できない。
    入力が前回と同じかで見る。
    """
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    args = type("A", (), {"id": 130, "round": 1})()
    refactor.cmd_merge_fix(args)
    refactor.cmd_merge_fix(args)

    entry = read_state(state_path)["rounds"][0]
    assert entry["fix_rounds"] == 1
    assert read_state(state_path)["items"][0]["commits"].count("fix111") == 1


def test_merge_fix_counts_a_new_result_as_a_new_round(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """結果ファイルが書き換わったら、次の修正ラウンドとして数えること。"""
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    args = type("A", (), {"id": 130, "round": 1})()
    refactor.cmd_merge_fix(args)

    result = state_path.parent / "codex-fix-r1-result.json"
    payload = __import__("json").loads(result.read_text(encoding="utf-8"))
    payload["elapsed_seconds"] = 99
    result.write_text(__import__("json").dumps(payload), encoding="utf-8")
    refactor.cmd_merge_fix(args)

    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 2


def test_identical_payload_in_a_later_round_still_counts(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """次の修正ラウンドが同じ JSON を返しても、別の実行として数えること。

    内容だけを鍵にすると過去のラウンドと衝突し、`fix_rounds` が進まないまま
    同じ修正を起動し続ける。
    """
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    args = type("A", (), {"id": 130, "round": 1})()
    refactor.cmd_merge_fix(args)

    # 2 回目の起動。結果ファイルの内容は**まったく同じ**だが、その前に
    # `judge-review` が走って試行番号が進んでいる
    state = read_state(state_path)
    state["rounds"][0]["fix_attempts"] = state["rounds"][0].get("fix_attempts", 1) + 1
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")

    refactor.cmd_merge_fix(args)
    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 2


def test_rerunning_without_relaunch_is_still_skipped(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """起動し直していない（ファイルがそのまま）なら、叩き直しても数えないこと。"""
    state_path = _prepare_fix(refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"])
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    args = type("A", (), {"id": 130, "round": 1})()
    refactor.cmd_merge_fix(args)
    refactor.cmd_merge_fix(args)
    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 1


def test_merge_fix_is_idempotent_after_a_revert(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """検証に失敗して取り消したあと、同じ結果ファイルで叩き直しても再処理しないこと。

    取り消しで HEAD が変わるため、鍵に HEAD を混ぜると一致しなくなる。
    """
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"],
        facts=[_fix_commit(test_status="fail")],
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    calls = _no_git(refactor, monkeypatch)
    args = type("A", (), {"id": 130, "round": 1})()
    refactor.cmd_merge_fix(args)
    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 1

    # 取り消しで HEAD が進んだ状況を模す
    calls.clear()
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: ("HEAD_AFTER_REVERT" if args[:1] == ["rev-parse"]
                            else args[-1].replace("^{commit}", "")),
    )
    refactor.cmd_merge_fix(args)

    assert read_state(state_path)["rounds"][0]["fix_rounds"] == 1, "二重に数えている"
    assert [c for c in calls if c[:2] == ["git", "revert"]] == []


def test_merge_fix_saves_before_pushing(refactor, tmp_path, env_tmp_dir, monkeypatch):
    """push が失敗しても、取り消しと起点の更新が食い違わないこと。

    先に push すると、取り消しコミットはローカルに残るのに起点の更新が保存されず、
    叩き直しで二重に取り消してしまう。
    """
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"],
        facts=[_fix_commit(test_status="fail")],
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    saved_at_push: list[bool] = []

    def fake_sh(cmd, **kw):
        if cmd[:2] == ["git", "push"]:
            entry = read_state(state_path)["rounds"][0]
            saved_at_push.append(entry.get("fix_base_sha") != "FIX_BASE")
        return ""

    monkeypatch.setattr(refactor, "_sh", fake_sh)
    refactor.cmd_merge_fix(type("A", (), {"id": 130, "round": 1})())

    assert saved_at_push == [True], "push の前に起点の更新が保存されていない"


def test_pending_push_is_retried_on_the_next_run(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """push に失敗したら、次の実行で必ず再試行すること。

    印を残さないと、取り消しがローカルだけに留まったまま処理済みガードで
    素通りし、Pull Request へ永久に反映されない。
    """
    state_path = _prepare_fix(
        refactor, tmp_path, env_tmp_dir, monkeypatch, ["PRRT_a"],
        facts=[_fix_commit(test_status="fail")],
    )
    monkeypatch.setattr(refactor, "resolved_threads_on_github",
                        lambda repo, pr: {"PRRT_a"})
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    pushes: list[list[str]] = []

    def failing_sh(cmd, **kw):
        pushes.append(list(cmd))
        if cmd[:2] == ["git", "push"]:
            raise SystemExit(1)
        return ""

    monkeypatch.setattr(refactor, "_sh", failing_sh)
    args = type("A", (), {"id": 130, "round": 1})()
    with pytest.raises(SystemExit):
        refactor.cmd_merge_fix(args)

    assert read_state(state_path)["rounds"][0]["pending_push"] is True

    # 次の実行では、処理済みの判定より先に push を片づける
    pushes.clear()
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: pushes.append(list(cmd)) or "")
    refactor.cmd_merge_fix(args)

    assert [c for c in pushes if c[:2] == ["git", "push"]], "再試行していない"
    assert read_state(state_path)["rounds"][0]["pending_push"] is False


# ---------- 巻き戻して積み直す取り消し ----------

def _range_state(tmp_path, findings, item_ids=("R1-001", "R1-002")):
    """適用の起点を記録した状態。**積み直しの経路**を通る。"""
    import json as _json
    state_path = _state(tmp_path, findings, item_ids=item_ids)
    state = read_state(state_path)
    state["rounds"][0]["apply_base_sha"] = "BASE"
    state_path.write_text(_json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return state_path


def _range_env(refactor, monkeypatch, ordered, pick_rc=0):
    """範囲と git 操作を差し替える。`ordered` は新しい順。"""
    calls: list[list[str]] = []
    picked: list[str] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        rc = 0
        if cmd[:2] == ["git", "cherry-pick"] and "--abort" not in cmd:
            rc = pick_rc
            if rc == 0:
                picked.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, rc, "", "conflict" if rc else "")

    def fake_git_out(work, args):
        if args[:2] == ["rev-parse", "--verify"]:
            return args[-1].replace("^{commit}", "")
        if args == ["rev-parse", "HEAD"]:
            return f"new-{picked[-1]}" if picked else "HEAD_BEFORE"
        return "HEAD_BEFORE"

    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    monkeypatch.setattr(refactor, "_git_out", fake_git_out)
    monkeypatch.setattr(refactor, "commits_in_range",
                        lambda work, base, head: list(ordered))
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: "")
    return calls


def test_abandon_replays_the_items_that_stay(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """見送る項目より新しいコミットがあっても競合しないこと。

    範囲を新しい順に全て戻してから、残す項目を古い順に積み直す。
    """
    state_path = _range_state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    # 履歴は R1-002 のコミットが新しい
    calls = _range_env(refactor, monkeypatch, ["sha-R1-002", "sha-R1-001"])

    refactor.cmd_abandon_items(_args())

    assert [c[-1] for c in calls if c[:2] == ["git", "revert"]] == [
        "sha-R1-002", "sha-R1-001"]
    assert [c[-1] for c in calls if c[:2] == ["git", "cherry-pick"]] == ["sha-R1-002"]

    state = read_state(state_path)
    by_id = {i["item_id"]: i for i in state["items"]}
    assert by_id["R1-001"]["status"] == "abandoned"
    assert by_id["R1-002"]["status"] == "reviewing"
    assert by_id["R1-002"]["commits"] == ["new-sha-R1-002"]


def test_abandon_falls_back_to_the_whole_round_on_a_replay_conflict(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _range_state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    calls = _range_env(refactor, monkeypatch, ["sha-R1-002", "sha-R1-001"], pick_rc=1)

    refactor.cmd_abandon_items(_args())

    assert ["git", "cherry-pick", "--abort"] in calls
    state = read_state(state_path)
    assert all(i["status"] == "abandoned" for i in state["items"])
    assert sorted(d["item_id"] for d in state["deferred_items"]) == ["R1-001", "R1-002"]
    assert state["rounds"][0]["drops"][-1]["mode"] == "round"


def test_abandon_marks_pending_push_before_reverting(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    state_path = _range_state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    marks: list[bool] = []
    _range_env(refactor, monkeypatch, ["sha-R1-002", "sha-R1-001"])
    real_run = refactor.subprocess.run

    def spying_run(cmd, **kwargs):
        if cmd[:2] == ["git", "revert"]:
            marks.append(read_state(state_path)["rounds"][0].get("pending_push"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(refactor.subprocess, "run", spying_run)
    refactor.cmd_abandon_items(_args())

    assert marks and marks[0] is True
    assert read_state(state_path)["rounds"][0]["pending_push"] is False


def test_abandon_saves_the_drop_result_before_pushing(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """取り消しの結果を push より先に保存すること。

    保存しないまま落ちると、積み直しで変わった SHA と取り消し済みの印が失われ、
    次の実行が**履歴に無い SHA を相手に**取り消しをやり直す。
    """
    state_path = _range_state(tmp_path, [_finding("R1-001")])
    env_tmp_dir(state_path)
    _range_env(refactor, monkeypatch, ["sha-R1-002", "sha-R1-001"])
    seen: list[dict] = []
    monkeypatch.setattr(
        refactor, "_sh",
        lambda cmd, **k: seen.append(read_state(state_path)) or "",
    )
    refactor.cmd_abandon_items(_args())

    assert seen, "push が実行されていない"
    at_push = seen[0]
    by_id = {i["item_id"]: i for i in at_push["items"]}
    assert by_id["R1-002"]["commits"] == ["new-sha-R1-002"], "SHA の追従が保存前"
    assert by_id["R1-001"]["reverted"] is True
    assert at_push["rounds"][0]["pending_drop"] == []


def test_abandon_retries_the_drop_before_resending_the_push(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """やり残した取り消しは、push の再送より先に片づけること。

    先に push すると、取り消しが途中の HEAD をそのまま公開してしまう。
    """
    state_path = _range_state(tmp_path, [_finding("R1-001")])
    state = read_state(state_path)
    state["rounds"][0]["pending_drop"] = ["R1-001"]
    state["rounds"][0]["pending_push"] = True
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    env_tmp_dir(state_path)

    order: list[str] = []
    calls = _range_env(refactor, monkeypatch, ["sha-R1-002", "sha-R1-001"])
    real_run = refactor.subprocess.run
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: (order.append(cmd[1]) if cmd[:1] == ["git"] else None)
        or real_run(cmd, **kw),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: order.append("push") or "")
    refactor.cmd_abandon_items(_args())

    assert "revert" in order and "push" in order
    assert order.index("revert") < order.index("push"), "取り消しより先に push している"
    assert read_state(state_path)["rounds"][0]["pending_push"] is False
