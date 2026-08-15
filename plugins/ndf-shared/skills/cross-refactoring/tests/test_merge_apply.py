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
    assert refactor.commits_in_range("/w", "base", "head") == {"aaa", "bbb"}
    assert calls == [["rev-list", "base..head"]]


def test_commits_in_range_is_empty_without_base(refactor):
    assert refactor.commits_in_range("/w", None, "head") == set()


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
    assert refactor.run_test_at("/w", "abc", "pytest -q", "main") == "pass"
    assert ["checkout", "--detach", "abc"] in git_calls
    assert ["git", "checkout", "main"] in git_calls


def test_run_test_at_reports_failure(refactor, monkeypatch):
    monkeypatch.setattr(refactor, "_git_out", lambda work, args: "")
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", ""),
    )
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
        monkeypatch.setattr(
            refactor, "commits_in_range",
            lambda work, base, head: set(mapping) if in_range is None else in_range,
        )
        monkeypatch.setattr(
            refactor, "collect_commit_facts",
            lambda work, shas, rng, cmd, branch: [
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


def test_failed_item_commits_are_reverted(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """検証に失敗した項目のコミットを Pull Request に残さない。

    実装担当は項目ごとに push しているため、状態を `abandoned` にするだけでは
    差分が残り、以後のレビュー対象にも混入する。
    """
    items = [item(item_id="R1-001"), item(item_id="R1-002")]
    state_path = _state_with_items(tmp_path, items)
    env_tmp_dir(state_path)
    git_facts({
        "ok111": fact(sha="ok111"),
        "bad111": fact(sha="bad111", diff_lines=400,
                       trailers=trailers(item_id="R1-002")),
        "bad222": fact(sha="bad222", diff_lines=400,
                       trailers=trailers(item_id="R1-002")),
    })
    write_result(state_path, "codex-apply-r1", {
        "base_sha": "aaa",
        "items": [
            {"item_id": "R1-001", "commits": [{"sha": "ok111"}]},
            # 差分予算を超えた項目。コミットは既に push されている
            {"item_id": "R1-002", "commits": [{"sha": "bad111"}, {"sha": "bad222"}]},
        ],
    })

    calls: list[list[str]] = []
    monkeypatch.setattr(
        refactor.subprocess, "run",
        lambda cmd, **kw: calls.append(list(cmd))
        or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    pushes: list[list[str]] = []
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: pushes.append(cmd) or "")

    refactor.cmd_merge_apply(type("A", (), {"id": 130, "round": 1, "dry_run": False})())

    reverts = [c for c in calls if c[:2] == ["git", "revert"]]
    # 新しいコミットから順に戻す
    assert [c[-1] for c in reverts] == ["bad222", "bad111"]
    assert pushes, "取り消し後に push していない"
    for cmd in pushes:
        assert "--force" not in cmd

    state = read_state(state_path)
    by_id = {i["item_id"]: i for i in state["items"]}
    assert by_id["R1-001"]["status"] == "reviewing"
    assert by_id["R1-002"]["status"] == "abandoned"
    assert by_id["R1-002"]["commits"] == ["bad111", "bad222"]


def test_no_push_when_nothing_was_reverted(
    refactor, tmp_path, env_tmp_dir, monkeypatch, git_facts
):
    """全項目が通ったときに余計な push をしない。"""
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
    assert pushes == []


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
