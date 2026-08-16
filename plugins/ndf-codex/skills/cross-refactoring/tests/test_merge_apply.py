"""適用結果の検証（トレーラー / テスト / 固定テストの先行 / 差分予算）のテスト。

読ませても手順を守るとは限らないため、**結果側から手順の遵守を確かめる**。
検証の材料は結果ファイルの申告ではなく **git と実際のテスト実行**から取る。
"""
from __future__ import annotations

import subprocess

import pytest

from conftest import make_state, read_state, write_result


def trailers(item_id="R1-001", round_no="1", runtime="codex", model="gpt-5.5"):
    return {
        "Item-Id": item_id, "Round": round_no,
        "Impl-Runtime": runtime, "Impl-Model": model,
    }


def fact(sha="abc1234", **over):
    """`collect_commit_facts()` が git から作る事実。"""
    base = {
        "sha": sha, "exists": True, "test_status": "pass",
        "touches_tests": False, "diff_lines": 30, "trailers": trailers(),
    }
    base.update(over)
    return base


def item(**over):
    base = {
        "item_id": "R1-001", "round": 1, "path": "src/foo.py", "symbol": "Foo.handle",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "rationale": "", "plan": "", "test_gap": False,
        "estimated_diff_lines": 40, "proposed_by": ["codex"],
        "status": "pending", "commits": [],
    }
    base.update(over)
    return base


# ---------- コミットトレーラー ----------

def test_all_four_trailers_pass(refactor):
    assert refactor.verify_commit_trailers(fact()) is None


@pytest.mark.parametrize("missing", ["Item-Id", "Round", "Impl-Runtime", "Impl-Model"])
def test_missing_any_trailer_fails(refactor, missing):
    t = trailers()
    del t[missing]
    problem = refactor.verify_commit_trailers(fact(trailers=t))
    assert problem is not None and missing in problem


def test_blank_trailer_value_fails(refactor):
    problem = refactor.verify_commit_trailers(fact(trailers=trailers(model="  ")))
    assert problem is not None and "Impl-Model" in problem


def test_item_fails_when_trailer_missing(refactor):
    t = trailers()
    del t["Impl-Model"]
    problem = refactor.verify_apply_item(item(), [fact(trailers=t)])
    assert problem is not None and "Impl-Model" in problem


# ---------- コミットの実在 ----------

def test_commit_that_does_not_exist_fails(refactor):
    """申告だけで実体が無いコミットを通さない。"""
    problem = refactor.verify_apply_item(item(), [{"sha": "ghost", "exists": False}])
    assert problem is not None and "範囲にありません" in problem


# ---------- 1 手 1 コミット ----------

def test_zero_commits_fails(refactor):
    problem = refactor.verify_apply_item(item(), [])
    assert problem is not None and "コミットが 1 件も" in problem


def test_commit_belonging_to_another_item_fails(refactor):
    """複数の項目を 1 コミットにまとめると、取り消し範囲が項目単位で決まらない。"""
    problem = refactor.verify_apply_item(
        item(), [fact(trailers=trailers(item_id="R1-002"))]
    )
    assert problem is not None and "Item-Id" in problem


def test_failed_test_on_any_commit_fails(refactor):
    problem = refactor.verify_apply_item(
        item(), [fact(), fact(sha="def5678", test_status="fail")]
    )
    assert problem is not None and "テストが成功していません" in problem


# ---------- 現状固定テストの先行 ----------

def test_test_gap_requires_characterization_test_first(refactor):
    problem = refactor.verify_apply_item(item(test_gap=True), [fact()])
    assert problem is not None and "現状固定テスト" in problem


def test_test_gap_passes_when_characterization_test_leads(refactor):
    facts = [fact(sha="aaa", touches_tests=True), fact(sha="bbb")]
    assert refactor.verify_apply_item(item(test_gap=True), facts) is None


def test_no_test_gap_does_not_require_characterization_test(refactor):
    assert refactor.verify_apply_item(item(test_gap=False), [fact()]) is None


# ---------- 差分予算 ----------

def test_diff_within_budget_passes(refactor):
    assert refactor.verify_apply_item(
        item(estimated_diff_lines=40), [fact(diff_lines=80)]
    ) is None


def test_diff_over_budget_fails(refactor):
    problem = refactor.verify_apply_item(
        item(estimated_diff_lines=40), [fact(diff_lines=81)]
    )
    assert problem is not None and "差分予算" in problem


def test_diff_lines_are_summed_across_commits(refactor):
    problem = refactor.verify_apply_item(
        item(estimated_diff_lines=40),
        [fact(sha="a", diff_lines=50), fact(sha="b", diff_lines=50)],
    )
    assert problem is not None and "100 行" in problem


def test_zero_estimate_disables_budget_check(refactor):
    """見積り 0 は「見積れなかった」を意味する。予算 0 で必ず落とす方が害が大きい。"""
    assert refactor.verify_apply_item(
        item(estimated_diff_lines=0), [fact(diff_lines=500)]
    ) is None


# ---------- git から事実を取る ----------

def test_commit_trailers_are_read_from_git(refactor, monkeypatch):
    """結果ファイルではなく実際のコミットメッセージから読む。"""
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: "Item-Id: R1-001\nRound: 1\n"
                           "Impl-Runtime: codex\nImpl-Model: gpt-5.5",
    )
    assert refactor.commit_trailers("/w", "abc") == {
        "Item-Id": "R1-001", "Round": "1",
        "Impl-Runtime": "codex", "Impl-Model": "gpt-5.5",
    }


def test_commit_trailers_are_empty_when_git_fails(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: None)
    assert refactor.commit_trailers("/w", "abc") == {}


def test_diff_lines_come_from_numstat(refactor, monkeypatch):
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: "10\t5\tsrc/a.py\n3\t2\tsrc/b.py\n-\t-\tbin.png",
    )
    assert refactor.commit_diff_lines("/w", "abc") == 20


def test_touches_tests_detects_test_paths(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out",
                        lambda work, args: "src/a.py\ntests/test_a.py")
    assert refactor.commit_touches_tests("/w", "abc") is True

    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "src/a.py")
    assert refactor.commit_touches_tests("/w", "abc") is False


def test_commits_in_range_uses_rev_list(refactor, monkeypatch):
    calls = []

    def fake(work, args):
        calls.append(args)
        return "aaa\nbbb"

    monkeypatch.setattr(refactor, "_git_out", fake)
    assert refactor.commits_in_range("/w", "base", "head") == ["aaa", "bbb"]
    assert calls == [["rev-list", "base..head"]]


def test_commits_in_range_is_none_without_base(refactor):
    """範囲を確定できないことと「0 件」を区別する。"""
    assert refactor.commits_in_range("/w", None, "head") is None


def test_commits_in_range_is_none_when_git_fails(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: None)
    assert refactor.commits_in_range("/w", "base", "head") is None


def test_run_test_at_checks_out_and_restores(refactor, monkeypatch):
    """テストは実際に走らせる。実行後は必ず元のブランチへ戻す。"""
    git_calls = []
    monkeypatch.setattr(
        refactor, "_git_out", lambda work, args: git_calls.append(args) or "")
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda *a, **kw: (git_calls.append(a[0]) if isinstance(a[0], list) else None)
        or subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_run_with_timeout",
                        lambda cmd, cwd, timeout, grace=5.0: (0, False))
    assert refactor.run_test_at("/w", "abc", "pytest -q", "main") == "pass"
    assert ["checkout", "--detach", "abc"] in git_calls
    assert ["git", "checkout", "main"] in git_calls


def test_run_test_at_reports_failure(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "")
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_run_with_timeout",
                        lambda cmd, cwd, timeout, grace=5.0: (1, False))
    assert refactor.run_test_at("/w", "abc", "pytest -q", "main") == "fail"


def test_run_test_at_restores_branch_even_when_the_test_raises(refactor, monkeypatch):
    restored = []

    def fake_run(cmd, **kw):
        if isinstance(cmd, list) and cmd[:2] == ["git", "checkout"]:
            restored.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise OSError("テスト実行が壊れた")

    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "")
    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    with pytest.raises(OSError):
        refactor.run_test_at("/w", "abc", "pytest -q", "main")
    assert restored, "元のブランチへ戻していない"


def test_collect_facts_marks_unknown_sha_as_missing(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: None)
    facts = refactor.collect_commit_facts("/w", ["ghost"], {"aaa"}, "true", "main")
    assert facts == [{"sha": "ghost", "exists": False}]


def test_collect_facts_marks_out_of_range_sha_as_missing(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "zzz")
    facts = refactor.collect_commit_facts("/w", ["zzz"], {"aaa"}, "true", "main")
    assert facts[0]["exists"] is False


# ---------- サブコマンド ----------

def _state_with_items(tmp_path, items, **over):
    return make_state(
        tmp_path,
        items=items,
        rounds=[{
            "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
            "impl_model": {"requested": "gpt-5.5", "observed": None},
            "reviewer_models": {"gemini": {"requested": None, "observed": None},
                                "kiro": {"requested": None, "observed": None}},
            "proposed": {}, "merged": 2, "adopted": len(items), "deferred": 0,
            "items": [i["item_id"] for i in items],
            "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
            "durations": {}, "reviews": [],
        }],
        **over,
    )


@pytest.fixture
def git_facts(refactor, monkeypatch):
    """git から取る事実を差し替える。`{sha: fact}` を渡す。"""
    def _set(mapping, in_range=None):
        ordered = list(mapping) if in_range is None else list(in_range)
        monkeypatch.setattr(
            refactor, "commits_in_range", lambda work, base, head: ordered)
        # 未割当コミットの判定は `rev-parse --verify <sha>^{commit}` を通るため、
        # SHA をそのまま返す形にしておく
        monkeypatch.setattr(
            refactor, "_git_out",
            lambda work, args: args[-1].replace("^{commit}", ""),
        )
        monkeypatch.setattr(
            refactor, "collect_commit_facts",
            lambda work, shas, rng, cmd, branch, timeout=None: [
                mapping.get(s, {"sha": s, "exists": False}) for s in shas
            ],
        )
    return _set


def test_partial_failure_keeps_the_rest(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """1 件の失敗でラウンドを止めず、失敗した項目だけを見送りにする。"""
    items = [item(item_id="R1-001"), item(item_id="R1-002")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa", "elapsed_seconds": 100,
        "items": [
            {"item_id": "R1-001", "commits": [{"sha": "ok111"}]},
            {"item_id": "R1-002", "commits": []},
        ],
    })
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    state = read_state(state_path)
    assert state["rounds"][0]["apply"]["applied"] == ["R1-001"]
    assert state["rounds"][0]["apply"]["failed"] == ["R1-002"]
    assert state["items"][0]["status"] == "reviewing"
    assert state["items"][1]["status"] == "abandoned"
    assert state["phase"] == "review"


def test_all_failed_exits_2(refactor, tmp_path, env_tmp_dir, no_git, git_facts):
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({})
    write_result(state_path, "codex-apply-r1", {"items": [
        {"item_id": "R1-001", "commits": []},
    ]})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    assert read_state(state_path)["phase"] == "propose"


def test_missing_item_in_result_is_a_failure(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({})
    write_result(state_path, "codex-apply-r1", {"items": []})
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert read_state(state_path)["items"][0]["status"] == "abandoned"


def test_self_reported_values_cannot_pass_the_check(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """結果ファイルに「テストは通った」と書いても、git 側の事実で落ちること。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"bad111": fact(sha="bad111", test_status="fail")})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{
            "item_id": "R1-001", "diff_lines": 1,
            # 申告は「通った」。git 側の事実は fail
            "commits": [{"sha": "bad111", "test_status": "pass",
                         "characterization_test": True,
                         "trailers": trailers()}],
        }],
    })
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    state = read_state(state_path)
    assert state["items"][0]["status"] == "abandoned"
    assert "テストが成功していません" in state["items"][0]["failure_reason"]


def _drop_env(refactor, monkeypatch, revert_rc=0, pick_rc=0, sync_dirty=False):
    """取り消しと積み直しを実際には走らせず、順序と引数を記録する。

    `git rev-parse HEAD` は**直前に積み直したコミット**に応じた値を返す。
    積み直しで SHA が変わることを、状態の更新まで含めて確かめられるようにする。
    """
    calls: list[list[str]] = []
    picked: list[str] = []
    reverted: list[str] = []
    statuses: list[int] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        rc = 0
        if cmd[:2] == ["git", "revert"] and "--abort" not in cmd:
            rc = revert_rc
            if rc == 0:
                reverted.append(cmd[-1])
        if cmd[:2] == ["git", "cherry-pick"] and "--abort" not in cmd:
            rc = pick_rc
            if rc == 0:
                picked.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, rc, "", "conflict" if rc else "")

    def fake_git_out(work, args):
        if args[:2] == ["rev-parse", "--verify"]:
            return args[-1].replace("^{commit}", "")
        if args == ["rev-parse", "HEAD"]:
            if picked:
                return f"new-{picked[-1]}"
            return "REVERTED_HEAD" if reverted else "HEAD_BEFORE"
        if "status" in args:
            # 同期の前後で 2 回呼ばれる。1 回目が同期前、2 回目以降が同期後。
            # 既定は「同期前も後も差分なし」
            statuses.append(len(statuses))
            if sync_dirty is False:
                return ""
            before, after = sync_dirty
            return before if len(statuses) == 1 else after
        return "HEAD_BEFORE"

    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    monkeypatch.setattr(refactor, "_git_out", fake_git_out)
    pushes: list[list[str]] = []
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: pushes.append(list(cmd)) or "")
    return calls, pushes


def _two_item_apply(tmp_path, env_tmp_dir, git_facts):
    """1 件成功・1 件失敗の適用結果を用意する。失敗するのは R1-002。"""
    items = [item(item_id="R1-001"), item(item_id="R1-002")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts(
        {
            "ok111": fact(sha="ok111"),
            "bad111": fact(sha="bad111", diff_lines=400,
                           trailers=trailers(item_id="R1-002")),
            "bad222": fact(sha="bad222", diff_lines=400,
                           trailers=trailers(item_id="R1-002")),
        },
        # 履歴は bad222 が最も新しい
        in_range=["bad222", "bad111", "ok111"],
    )
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [
            {"item_id": "R1-001", "commits": [{"sha": "ok111"}]},
            # 差分予算を超えた項目。コミットは既に push されている
            {"item_id": "R1-002", "commits": [{"sha": "bad111"}, {"sha": "bad222"}]},
        ],
    })
    return state_path


def test_dropping_an_item_replays_the_kept_items(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """範囲を新しい順に全て戻し、残す項目を古い順に積み直すこと。

    失敗した項目のコミット**だけ**を戻すと、あとから同じ箇所を触った別項目の
    コミットと必ず競合する。範囲全体の巻き戻しは履歴の逆再生なので競合しない。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    calls, pushes = _drop_env(refactor, monkeypatch)

    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    reverts = [c[-1] for c in calls if c[:2] == ["git", "revert"]]
    picks = [c[-1] for c in calls if c[:2] == ["git", "cherry-pick"]]
    assert reverts == ["bad222", "bad111", "ok111"], "範囲を新しい順に全て戻していない"
    assert picks == ["ok111"], "残す項目だけを積み直していない"
    assert pushes, "取り消し後に push していない"
    for cmd in pushes:
        assert "--force" not in cmd and "--no-verify" not in cmd

    state = read_state(state_path)
    by_id = {i["item_id"]: i for i in state["items"]}
    assert by_id["R1-001"]["status"] == "reviewing"
    assert by_id["R1-002"]["status"] == "abandoned"
    assert by_id["R1-002"]["reverted"] is True
    # 積み直しで SHA が変わるので、記録も追従すること
    assert by_id["R1-001"]["commits"] == ["new-ok111"]


def test_replay_conflict_falls_back_to_whole_round(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """積み直せないときはラウンド全件の取り消しへ退避すること。

    どの項目を残せるか決められない以上、半端な履歴を残すより全件捨てる方が安全。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    calls, _ = _drop_env(refactor, monkeypatch, pick_rc=1)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2, "全件失敗として次の提案ラウンドへ進むこと"

    assert ["git", "cherry-pick", "--abort"] in calls
    # 取り消しが済んだ地点へ戻すだけ。着手前まで戻して取り消しをやり直すと、
    # 同じ範囲の revert コミットが 2 組できて履歴が汚れる
    assert ["git", "reset", "--hard", "REVERTED_HEAD"] in calls
    reverts = [c[-1] for c in calls if c[:2] == ["git", "revert"]]
    assert reverts == ["bad222", "bad111", "ok111"], "取り消しを 2 度走らせている"
    state = read_state(state_path)
    assert all(i["status"] == "abandoned" for i in state["items"])
    assert state["rounds"][0]["apply"]["applied"] == []
    assert state["rounds"][0]["drops"][-1]["mode"] == "round"


def test_revert_failure_aborts_with_the_abort_code(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """取り消しに失敗したら「全件失敗」ではなく**中断**として終わること。

    2（全件失敗）と同じ扱いにすると、検証を通っていない変更を Pull Request に
    残したまま次の提案ラウンドが始まる。
    """
    _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    calls, _ = _drop_env(refactor, monkeypatch, revert_rc=1)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == refactor.ABORT == 4
    assert ["git", "revert", "--abort"] in calls
    assert ["git", "reset", "--hard", "HEAD_BEFORE"] in calls


def test_progress_is_recorded_before_reverting(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """取り消しの前に判定を残すこと。中断しても到達点が状態から読める。"""
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    seen: list[list[dict]] = []

    calls, _ = _drop_env(refactor, monkeypatch, revert_rc=1)
    real_run = refactor.subprocess.run

    def spying_run(cmd, **kwargs):
        if cmd[:2] == ["git", "revert"]:
            seen.append(read_state(state_path)["rounds"][0].get("apply_progress"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(refactor.subprocess, "run", spying_run)
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )

    assert seen, "取り消しが走っていない"
    recorded = {p["item_id"]: p["result"] for p in seen[0]}
    assert recorded == {"R1-001": "ok", "R1-002": "failed"}


def test_pending_push_is_marked_before_reverting(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """取り消しへ着手する前に再送信の印を立てること。

    取り消しは済んだのに push できずに終わると、検証を通っていない変更が
    Pull Request に残り、次の実行は処理済みガードで素通りする。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    marks: list[bool] = []

    calls, _ = _drop_env(refactor, monkeypatch, revert_rc=1)
    real_run = refactor.subprocess.run

    def spying_run(cmd, **kwargs):
        if cmd[:2] == ["git", "revert"]:
            marks.append(read_state(state_path)["rounds"][0].get("pending_push"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(refactor.subprocess, "run", spying_run)
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert marks and marks[0] is True


def test_pending_push_is_cleared_after_a_successful_push(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch)
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())
    assert read_state(state_path)["rounds"][0]["pending_push"] is False


def test_out_of_scope_commit_fails_the_item(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """指定した範囲の外を触ったコミットを検証で捕まえること。

    範囲を必須にした目的（提案の発散と変更の肥大を防ぐ）を、検証へ反映する。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"out111": fact(
        sha="out111", files=["src/foo.py", "dist/foo.py"],
    )})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{"item_id": "R1-001", "commits": [{"sha": "out111"}]}],
    })
    _drop_env(refactor, monkeypatch)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    state = read_state(state_path)
    assert state["items"][0]["out_of_scope"] is True
    assert "dist/foo.py" in state["items"][0]["failure_reason"]


def test_scope_check_matches_only_on_path_prefix(refactor):
    assert refactor.path_in_scope("src/foo.py", ["src"])
    assert refactor.path_in_scope("src", ["src"])
    assert not refactor.path_in_scope("src2/foo.py", ["src"]), "前方一致の取りこぼし"
    assert not refactor.path_in_scope("dist/foo.py", ["src"])
    # 範囲が空なら検査しない（指定が無いのに全件落とさない）
    assert refactor.out_of_scope_files({"files": ["any.py"]}, []) == []


@pytest.mark.parametrize("scope", [["./src"], ["src/"], ["./src/"], ["  ./src  "]])
def test_scope_accepts_shell_completed_paths(refactor, scope):
    """`--scope ./src` は補完で頻出する。git は `src/foo.py` と出すので正規化する。

    正規化しないと**全てのコミットが範囲外**になり、適用が必ず失敗する。
    """
    assert refactor.path_in_scope("src/foo.py", scope)
    assert not refactor.path_in_scope("dist/foo.py", scope)


@pytest.mark.parametrize("scope", [["."], ["./"], [".//"]])
def test_scope_dot_means_the_whole_repository(refactor, scope):
    assert refactor.path_in_scope("anywhere/deep/foo.py", scope)


def test_blank_scope_entry_is_ignored(refactor):
    """空の指定で全許可にしない。指定の書き損じで検査が骨抜きになる。"""
    assert not refactor.path_in_scope("dist/foo.py", ["", "  ", "src"])


def test_push_happens_once_per_merge_apply(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """全項目が通ったときも公開するが、push は 1 回だけにすること。

    公開するのは進行側だけである（実装担当は push しない）。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{"item_id": "R1-001", "commits": [{"sha": "ok111"}]}],
    })
    pushes: list[list[str]] = []
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: pushes.append(cmd) or "")
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())
    assert len([c for c in pushes if c[:2] == ["git", "push"]]) == 1


def test_dry_run_touches_neither_git_nor_state(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """確認目的の実行で状態だけが進むと、利用者の進行が壊れる。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    before = state_path.read_text(encoding="utf-8")
    git_facts({"bad111": fact(sha="bad111", diff_lines=400)})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{"item_id": "R1-001", "commits": [{"sha": "bad111"}]}],
    })
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": True})()
        )
    assert state_path.read_text(encoding="utf-8") == before
    assert not [c for c in no_git if c[:2] == ["git", "revert"]]
    assert not [c for c in no_git if c[:2] == ["git", "push"]]


def test_state_is_saved_before_push(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """push が失敗しても、記録とローカルの git が食い違わないようにする。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"bad111": fact(sha="bad111", diff_lines=400)})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{"item_id": "R1-001", "commits": [{"sha": "bad111"}]}],
    })
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    saved_at_push: list[bool] = []

    def fake_sh(cmd, **kw):
        if cmd[:2] == ["git", "push"]:
            saved_at_push.append(
                read_state(state_path)["items"][0]["status"] == "abandoned"
            )
        return ""

    monkeypatch.setattr(refactor, "_sh", fake_sh)
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert saved_at_push == [True], "push の前に状態が保存されていない"


def test_unverified_baseline_blocks_every_item(refactor, tmp_path, env_tmp_dir):
    """着手前のテストが成功と確認できていなければ、適用へ着手せず全項目を blocked にする。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(
        tmp_path, items,
        baseline_test={"command": "pytest -q", "status": "red", "checked_at": "x"},
    )
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", {"items": []})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    assert read_state(state_path)["items"][0]["status"] == "blocked"


def test_unknown_baseline_also_blocks(refactor, tmp_path, env_tmp_dir):
    """`red` だけでなく「確認していない」状態も通さない。

    確認していない状態を通すと、壊したのか元から壊れていたのかを判別する手段が
    無いまま進む。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(
        tmp_path, items,
        baseline_test={"command": None, "status": "unknown", "checked_at": "x"},
    )
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", {"items": []})
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    assert read_state(state_path)["items"][0]["status"] == "blocked"


def test_unassigned_commit_fails_the_whole_round(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """申告から漏れたコミットは検査を素通りして PR に残る。ラウンドごと取り消す。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    # 範囲には 2 件あるが、申告されているのは 1 件だけ
    git_facts({"ok111": fact(sha="ok111")}, in_range=["sneaky", "ok111"])
    write_result(state_path, "codex-apply-r1", {
        "items": [{"item_id": "R1-001", "commits": [{"sha": "ok111"}]}],
    })
    calls: list[list[str]] = []
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: calls.append(list(cmd))
        or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: "")

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2

    state = read_state(state_path)
    assert state["items"][0]["status"] == "abandoned"
    assert "割り当てられていないコミット" in state["items"][0]["failure_reason"]
    assert state["rounds"][0]["apply"]["unassigned_commits"] == ["sneaky"]
    # 範囲全体を新しい順に取り消す
    reverts = [c[-1] for c in calls if c[:2] == ["git", "revert"]]
    assert reverts == ["sneaky", "ok111"]


def test_range_that_cannot_be_determined_fails_closed(
    refactor, tmp_path, env_tmp_dir, no_git, monkeypatch
):
    """範囲を確定できないなら何も検証できない。素通しにせず失敗させる。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "commits_in_range", lambda work, base, head: None)
    write_result(state_path, "codex-apply-r1", {
        "items": [{"item_id": "R1-001", "commits": [{"sha": "abc"}]}],
    })
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    assert read_state(state_path)["items"][0]["status"] == "blocked"


def test_apply_base_is_recorded_by_the_orchestrator(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """起点を実装担当の申告に委ねない。"""
    from conftest import make_state as _make_state

    state_path = _make_state(tmp_path, rounds=[{
        "round": 1, "impl": "codex", "reviewers": ["gemini", "kiro"],
        "impl_model": {"requested": None, "observed": None},
        "reviewer_models": {}, "proposed": {}, "items": [],
        "apply": {"applied": [], "failed": []}, "fix_rounds": 0,
        "durations": {}, "reviews": [],
    }])
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "BASE_HEAD")
    for rt in ("codex", "gemini", "kiro"):
        write_result(state_path, f"{rt}-propose-rf130", {"items": []})
    with pytest.raises(SystemExit):
        refactor.cmd_merge_proposals(type("A", (), {"id": 130})())
    assert read_state(state_path)["rounds"][0]["apply_base_sha"] == "BASE_HEAD"


def test_commits_claimed_by_an_unknown_item_are_rejected(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """架空の項目 ID へ割り当てても、割り当て済みとは扱わない。

    数に入れてしまうと `unassigned` を通過するのに項目別の検証にも入らず、
    そのまま Pull Request に残せてしまう。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")}, in_range=["sneaky", "ok111"])
    write_result(state_path, "codex-apply-r1", {
        "items": [
            {"item_id": "R1-001", "commits": [{"sha": "ok111"}]},
            # 架空の項目。ここへ逃がしても割り当て済みにはならない
            {"item_id": "GHOST", "commits": [{"sha": "sneaky"}]},
        ],
    })
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: "")

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    apply_record = read_state(state_path)["rounds"][0]["apply"]
    assert apply_record["unknown_item_ids"] == ["GHOST"]
    assert apply_record["unassigned_commits"] == ["sneaky"]


# ---------- 結果ファイルの形が崩れていても落ちない ----------

@pytest.mark.parametrize("broken", [
    {"commits": "文字列"},
    {"commits": ["文字列"]},
    {"commits": [{"sha": None}]},
    {"commits": [{}]},
    {},
    "オブジェクトですらない",
])
def test_reported_shas_survives_broken_output(refactor, broken):
    assert refactor._reported_shas(broken) == []


def test_reported_shas_extracts_valid_entries_only(refactor):
    payload = {"commits": [{"sha": "aaa"}, "壊れた", {"sha": ""}, {"sha": " bbb "}]}
    assert refactor._reported_shas(payload) == ["aaa", "bbb"]


def test_broken_apply_result_does_not_crash(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """`commits` が配列でなくてもクラッシュせず、項目の失敗として扱う。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({})
    write_result(state_path, "codex-apply-r1", {
        "items": [{"item_id": "R1-001", "commits": "壊れている"}],
    })
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    assert read_state(state_path)["items"][0]["status"] == "abandoned"


def test_non_object_result_file_fails(refactor, tmp_path, env_tmp_dir, no_git):
    """結果が JSON オブジェクトでなければ、読み込みの時点で弾く。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    write_result(state_path, "codex-apply-r1", ["配列で返ってきた"])
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2


def test_same_commit_claimed_by_two_items_fails_the_round(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """1 コミットの所有項目は 1 つだけ。

    重複したまま進むと、片方が失敗して取り消したときにもう片方は成功のまま残り、
    状態ファイルと実際の差分が食い違う。
    """
    items = [item(item_id="R1-001"), item(item_id="R1-002")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"shared": fact(sha="shared")}, in_range=["shared"])
    write_result(state_path, "codex-apply-r1", {
        "items": [
            {"item_id": "R1-001", "commits": [{"sha": "shared"}]},
            {"item_id": "R1-002", "commits": [{"sha": "shared"}]},
        ],
    })
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: "")

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    state = read_state(state_path)
    assert state["rounds"][0]["apply"]["duplicated_commits"] == ["shared"]
    assert all(i["status"] == "abandoned" for i in state["items"])


def test_same_commit_reported_twice_for_one_item_is_fine(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """同じ項目が同じコミットを重ねて書いただけなら、食い違いは起きない。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")}, in_range=["ok111"])
    write_result(state_path, "codex-apply-r1", {
        "items": [{"item_id": "R1-001",
                   "commits": [{"sha": "ok111"}, {"sha": "ok111"}]}],
    })
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())
    assert read_state(state_path)["items"][0]["status"] == "reviewing"


def test_short_and_full_sha_are_seen_as_the_same_commit(
    refactor, tmp_path, env_tmp_dir, monkeypatch
):
    """一方が完全 SHA、他方が短縮 SHA でも重複として検出すること。

    申告の文字列をそのまま鍵にすると見逃す。
    """
    full = "a" * 40
    items = [item(item_id="R1-001"), item(item_id="R1-002")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    monkeypatch.setattr(refactor, "commits_in_range", lambda w, b, h: [full])
    # 短縮 SHA も完全 SHA も同じコミットへ解決される
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: full if args[:2] == ["rev-parse", "--verify"] else "HEAD",
    )
    monkeypatch.setattr(
        refactor, "collect_commit_facts",
        lambda work, shas, rng, cmd, branch, timeout=None: [fact(sha=s) for s in shas],
    )
    write_result(state_path, "codex-apply-r1", {
        "items": [
            {"item_id": "R1-001", "commits": [{"sha": full}]},
            {"item_id": "R1-002", "commits": [{"sha": full[:7]}]},
        ],
    })
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: "")

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2
    assert read_state(state_path)["rounds"][0]["apply"]["duplicated_commits"] == [full]


# ---------- 数値の型崩れ ----------

@pytest.mark.parametrize("broken", ["たくさん", ["50"], {"n": 1}, None, True])
def test_safe_int_falls_back_on_broken_values(refactor, broken):
    assert refactor._safe_int(broken) == 0


def test_safe_int_reads_numbers_and_numeric_strings(refactor):
    assert refactor._safe_int(42) == 42
    assert refactor._safe_int(4.9) == 4
    assert refactor._safe_int(" 7 ") == 7


def test_broken_diff_lines_does_not_crash(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """`elapsed_seconds` / `diff_lines` が非数値でも落ちないこと。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111", diff_lines=12)}, in_range=["ok111"])
    write_result(state_path, "codex-apply-r1", {
        "elapsed_seconds": "とても長い",
        "items": [{"item_id": "R1-001", "diff_lines": ["壊れている"],
                   "commits": [{"sha": "ok111"}]}],
    })
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    state = read_state(state_path)
    assert state["rounds"][0]["durations"]["apply"] == 0
    # 差分行数も git 由来の事実から取る
    assert state["items"][0]["diff_lines"] == 12


# ---------- 再実行の冪等性 ----------

def test_merge_apply_is_idempotent(refactor, tmp_path, env_tmp_dir, no_git, git_facts):
    """取り込み済みで叩き直しても、同じ判定を返して二重に処理しないこと。

    push の失敗などで再実行すると、前回作った取り消しコミットが「未割当」と
    判定され、成功した項目まで巻き込んでラウンド全体を取り消してしまう。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")}, in_range=["ok111"])
    write_result(state_path, "codex-apply-r1", {
        "items": [{"item_id": "R1-001", "commits": [{"sha": "ok111"}]}],
    })
    args = type("A", (), {"id": 130, "round": 1, "dry_run": False})()
    refactor.cmd_merge_apply(args)
    first = read_state(state_path)

    # 2 回目は範囲に取り消しコミットが増えた状況を模しても、判定が変わらない
    git_facts({}, in_range=["revert111", "ok111"])
    refactor.cmd_merge_apply(args)
    second = read_state(state_path)

    assert second["rounds"][0]["apply"]["applied"] == ["R1-001"]
    assert second["items"][0]["status"] == "reviewing"
    assert second["rounds"][0]["apply"] == first["rounds"][0]["apply"]


def test_merge_apply_dry_run_leaves_no_processed_marker(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """`--dry-run` は状態を進めないので、取り込み済みの印も残さない。"""
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")}, in_range=["ok111"])
    write_result(state_path, "codex-apply-r1", {
        "items": [{"item_id": "R1-001", "commits": [{"sha": "ok111"}]}],
    })
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": True})())
    assert not (read_state(state_path)["rounds"][0]["apply"] or {}).get("merged_at")


def test_revert_failure_keeps_the_round_retryable(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """取り消しに失敗したら、処理済みの印を立てないこと。

    先に `merged_at` を立てると、次の実行は処理済みガードで素通りし、
    **取り消しを再試行できないまま**未検証の変更が Pull Request に残り続ける。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch, revert_rc=1)

    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == refactor.ABORT

    entry = read_state(state_path)["rounds"][0]
    assert entry["apply"]["merged_at"] is None, "処理済みの印が立っている"
    assert entry["pending_drop"] == ["R1-002"], "再実行の対象が残っていない"


def test_pending_drop_is_retried_before_the_processed_guard(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """やり残した取り消しは、処理済みの判定より先に再実行すること。"""
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch, revert_rc=1)
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )

    # 2 回目は取り消しが通る状況を模す
    calls, pushes = _drop_env(refactor, monkeypatch)
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    assert [c[-1] for c in calls if c[:2] == ["git", "revert"]] == [
        "bad222", "bad111", "ok111"], "取り消しを再実行していない"
    entry = read_state(state_path)["rounds"][0]
    assert entry["pending_drop"] == []
    assert entry["pending_push"] is False
    assert entry["apply"]["merged_at"] is not None
    assert pushes, "再実行後に push していない"


def test_push_precedes_nothing_when_the_drop_is_unfinished(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """取り消しをやり残したまま push だけ先に流さないこと。

    未検証の HEAD をそのまま Pull Request へ反映してしまう。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch, revert_rc=1)
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )

    order: list[str] = []
    calls, _ = _drop_env(refactor, monkeypatch)
    real_run = refactor.subprocess.run
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: (order.append(cmd[1]) if cmd[:1] == ["git"] else None)
        or real_run(cmd, **kw),
    )
    monkeypatch.setattr(
        refactor, "_sh", lambda cmd, **k: order.append("push") or "")
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    assert "push" in order
    assert order.index("revert") < order.index("push"), "取り消しより先に push している"
    assert read_state(state_path)["rounds"][0]["apply"]["merged_at"] is not None


def test_push_failure_after_a_successful_drop_only_retries_the_push(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """取り消しが済んだあとに push だけ失敗したら、次は push の再送だけを行うこと。

    取り消しの完了を push より先に永続化しないと、次の実行が適用の検証をやり直し、
    取り消しと積み直しのコミットを「未割当」と判定してラウンドごと巻き込む。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch)
    monkeypatch.setattr(
        refactor, "_sh",
        lambda cmd, **k: (_ for _ in ()).throw(SystemExit(4))
        if cmd[:2] == ["git", "push"] else "",
    )
    with pytest.raises(SystemExit):
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )

    entry = read_state(state_path)["rounds"][0]
    assert entry["pending_drop"] == [], "取り消しは済んでいるのに再実行の対象が残っている"
    assert entry["apply"]["merged_at"] is not None, "取り消しの完了が保存されていない"
    assert entry["pending_push"] is True

    # 2 回目: push が通る。取り消しは繰り返さない
    calls, pushes = _drop_env(refactor, monkeypatch)
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    assert [c for c in calls if c[:2] == ["git", "revert"]] == [], "取り消しを繰り返している"
    assert [c for c in calls if c[:2] == ["git", "cherry-pick"]] == []
    assert [c for c in pushes if c[:2] == ["git", "push"]], "push を再送していない"
    entry = read_state(state_path)["rounds"][0]
    assert entry["pending_push"] is False
    assert entry["apply"]["applied"] == ["R1-001"]


# ---------- 適用で失敗した項目を「対象外」へ ----------

def test_failed_items_are_deferred(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """適用の検証で失敗した項目を「対象外」として記録すること。

    記録しないと**同じ提案が次のラウンドで再び採用され、同じ理由で失敗する**。
    実測では 3 ランタイム全員から再提案され、合意数が最大になって最優先で採用された。
    """
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch)
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    state = read_state(state_path)
    deferred = {d["item_id"]: d for d in state["deferred_items"]}
    assert list(deferred) == ["R1-002"], "失敗した項目だけを対象外にすること"
    entry = deferred["R1-002"]
    # 次ラウンドの除外は path + symbol + smell の組で行われる
    assert (entry["path"], entry["symbol"], entry["smell"]) == (
        "src/foo.py", "Foo.handle", "long_method")
    assert "差分予算" in entry["defer_reason"]


def test_deferring_is_idempotent(refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts):
    """叩き直しても対象外の記録を重複させないこと。"""
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch)
    args = type("A", (), {"id": 130, "round": 1, "dry_run": False})()
    refactor.cmd_merge_apply(args)
    refactor.cmd_merge_apply(args)
    assert [d["item_id"] for d in read_state(state_path)["deferred_items"]] == ["R1-002"]


# ---------- push の直前に生成物を同期する ----------

def _sync_state(tmp_path, env_tmp_dir, git_facts, command="make build"):
    state_path = _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    state = read_state(state_path)
    state["sync_command"] = command
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    return state_path


def test_push_syncs_generated_files_first(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """push の直前に同期し、差分があれば進行側のコミットとして積むこと。

    同期を実装担当にさせると範囲外の変更になり、範囲の検査で全件失敗する。
    かといって同期しないと、同期を検査する pre-push では push が通らない。
    """
    _sync_state(tmp_path, env_tmp_dir, git_facts)
    order: list[str] = []
    _drop_env(refactor, monkeypatch,
              sync_dirty=("", "?? plugins/generated/a.py"))
    monkeypatch.setattr(
        refactor, "_sh",
        lambda cmd, **k: order.append("push" if cmd[:2] == ["git", "push"] else cmd[1])
        or "",
    )
    ran: list[tuple[str, str]] = []
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: ran.append((command, cwd)) or (0, False),
    )
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    assert ran and ran[0][0] == "make build", "同期コマンドを実行していない"
    assert "add" in order and "commit" in order, "同期の差分をコミットしていない"
    assert order.index("commit") < order.index("push"), "コミットより先に push している"


def test_sync_failure_aborts_without_pushing(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """同期に失敗したら中断する。黙って push しない。"""
    _sync_state(tmp_path, env_tmp_dir, git_facts)
    calls, pushes = _drop_env(refactor, monkeypatch)
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: (1, False),
    )
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == refactor.ABORT
    assert [c for c in pushes if c[:2] == ["git", "push"]] == []


def test_no_sync_command_runs_nothing(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """`--sync-command` 未指定なら同期は走らない（既存の利用者に影響しない）。"""
    _two_item_apply(tmp_path, env_tmp_dir, git_facts)
    _drop_env(refactor, monkeypatch)
    ran: list = []
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: ran.append(command) or (0, False),
    )
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())
    assert ran == []


def test_merge_apply_pushes_even_when_every_item_passes(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """全項目が通ったときも進行側が push すること。

    実装担当が push しなくなったため、ここで公開しないとレビュー担当が
    Pull Request 上の差分へ指摘を書けない。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({"ok111": fact(sha="ok111")})
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{"item_id": "R1-001", "commits": [{"sha": "ok111"}]}],
    })
    calls, pushes = _drop_env(refactor, monkeypatch)
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    assert [c for c in pushes if c[:2] == ["git", "push"]], "push していない"
    for cmd in pushes:
        assert "--force" not in cmd and "--no-verify" not in cmd
    entry = read_state(state_path)["rounds"][0]
    assert entry["pending_push"] is False
    assert entry["apply"]["applied"] == ["R1-001"]


def test_sync_aborts_when_the_worktree_is_dirty(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """同期の前に作業ツリーが汚れていたら中断すること。

    汚れたまま同期すると、同期が作った差分と元からあった差分を区別できない。
    状態コードを比べても、元から ` M` のファイルを同期がさらに書き換えた場合を
    取りこぼす。`git commit` は index を丸ごと含めるため、staged 済みの変更も
    検証を受けないまま公開される。
    """
    _sync_state(tmp_path, env_tmp_dir, git_facts)
    staged: list[list[str]] = []
    _drop_env(
        refactor, monkeypatch,
        sync_dirty=(" M src/edited.py", " M src/edited.py"),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: staged.append(list(cmd)) or "")
    ran: list = []
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: ran.append(command) or (0, False),
    )
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == refactor.ABORT
    assert ran == [], "汚れたまま同期を走らせている"
    assert [c for c in staged if c[:2] == ["git", "push"]] == []


def test_dirt_inside_the_control_directory_does_not_abort(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """制御用ディレクトリの中は汚れていても止めないこと。

    状態ファイル・結果・ログは常にそこへ書かれる。
    """
    state_path = _sync_state(tmp_path, env_tmp_dir, git_facts)
    state = read_state(state_path)
    state["worktrees"]["work"] = str(state_path.parent.parent)
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    control = state_path.parent.name
    staged: list[list[str]] = []
    _drop_env(
        refactor, monkeypatch,
        sync_dirty=(f"?? {control}/codex-apply-r1-result.json",
                    f"?? {control}/codex-apply-r1-result.json\n M generated/a.py"),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: staged.append(list(cmd)) or "")
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: (0, False),
    )
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())
    assert [c for c in staged if c[:2] == ["git", "add"]] == [
        ["git", "add", "--", "generated/a.py"]]


def test_sync_excludes_the_control_directory(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """状態ファイル・結果・ログの置き場所を同期コミットへ入れないこと。"""
    state_path = _sync_state(tmp_path, env_tmp_dir, git_facts)
    # 既定の配置では制御用ディレクトリが作業ディレクトリの中にある
    state = read_state(state_path)
    state["worktrees"]["work"] = str(state_path.parent.parent)
    state_path.write_text(__import__("json").dumps(state), encoding="utf-8")
    control = state_path.parent.name          # `.cross_refactoring`
    staged: list[list[str]] = []
    _drop_env(
        refactor, monkeypatch,
        sync_dirty=("", f"?? {control}/codex-apply-r1-result.json\n M generated/a.py"),
    )
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: staged.append(list(cmd)) or "")
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: (0, False),
    )
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    adds = [c for c in staged if c[:2] == ["git", "add"]]
    assert adds == [["git", "add", "--", "generated/a.py"]]


def test_sync_without_changes_makes_no_commit(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """同期しても差分が出なければ、空のコミットを積まないこと。"""
    _sync_state(tmp_path, env_tmp_dir, git_facts)
    staged: list[list[str]] = []
    _drop_env(refactor, monkeypatch, sync_dirty=("", ""))
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: staged.append(list(cmd)) or "")
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: (0, False),
    )
    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    assert [c for c in staged if c[:2] == ["git", "commit"]] == []
    assert [c for c in staged if c[:2] == ["git", "push"]], "push はすること"


def test_whole_round_failure_also_defers_items(
    refactor, tmp_path, env_tmp_dir, no_git, git_facts
):
    """ラウンドごと取り消す経路でも「対象外」として記録すること。

    項目別の失敗だけを記録すると、未割当コミットで落ちた提案が次のラウンドで
    再び採用される。
    """
    items = [item(item_id="R1-001")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    # 範囲に 2 件あるが、申告は 1 件だけ（未割当コミットあり）
    git_facts({"ok111": fact(sha="ok111")}, in_range=["sneaky", "ok111"])
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [{"item_id": "R1-001", "commits": [{"sha": "ok111"}]}],
    })
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == 2

    state = read_state(state_path)
    deferred = {d["item_id"]: d for d in state["deferred_items"]}
    assert list(deferred) == ["R1-001"]
    assert "割り当てられていない" in deferred["R1-001"]["defer_reason"]


def test_sync_failure_discards_what_it_produced(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """同期が途中で失敗したら、作った差分を捨てて再開できる状態にすること。

    残すと次の実行は清浄性の検査で必ず止まり、`pending_push` の再試行が
    永久に進まなくなる。
    """
    _sync_state(tmp_path, env_tmp_dir, git_facts)
    calls, pushes = _drop_env(refactor, monkeypatch, sync_dirty=("", " M generated/a.py"))
    monkeypatch.setattr(
        refactor, "_run_with_timeout",
        lambda command, cwd, timeout, grace=5.0: (1, False),
    )
    with pytest.raises(SystemExit) as e:
        refactor.cmd_merge_apply(
            type("A", (), {"id": 130, "round": 1, "dry_run": False})()
        )
    assert e.value.code == refactor.ABORT
    assert ["git", "checkout", "--", "."] in calls, "同期の差分を戻していない"
    assert ["git", "clean", "-fd"] in calls, "同期が作ったファイルを消していない"
    # 無視されたファイル（制御用ディレクトリ）まで消さない
    assert not any("-x" in c for c in calls if c[:2] == ["git", "clean"])
    assert [c for c in pushes if c[:2] == ["git", "push"]] == []


def test_status_disables_path_quoting(refactor, monkeypatch):
    """`core.quotePath` の既定では非 ASCII のパスがエスケープされて `git add` が失敗する。"""
    seen: list[list[str]] = []
    monkeypatch.setattr(
        refactor, "_git_out",
        lambda work, args: seen.append(list(args)) or " M plugins/日本語/a.py",
    )
    assert refactor._worktree_changes("/w") == {"plugins/日本語/a.py": " M"}
    assert seen[0][:2] == ["-c", "core.quotePath=false"]
