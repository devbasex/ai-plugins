"""ホストが自分で直した修正を記録にする `record-fix` と、同じコミットを 2 回取り込まない `merge-fix`（#1340 の AC9・AC10、I7・I8）。"""

from __future__ import annotations

import argparse
import json
import pathlib
import types

import pytest
import review_lib.commands.merge_fix
import review_lib.commands.read_result
import review_lib.commands.record_fix
import review_lib.commands.start_round
import review_lib.github

PR = 6300
REPO = "o/r"
HEAD = "f" * 40


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed(tmp_dir: pathlib.Path, **round_over) -> None:
    rnd = {
        "round": 1,
        "pr": PR,
        "started_at": "2026-01-01T00:00:00+00:00",
        "reviewers": ["codex"],
        "codex": {"intent": "REQUEST_CHANGES", "by_severity": {"major": 1}},
        "verdict": "changes_requested",
    }
    rnd.update(round_over)
    state = {
        "current_pr": PR,
        "repo": REPO,
        "viewer_login": "takemi",
        "worktree_path": str(tmp_dir),
        "head_branch": "feat/x",
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": [rnd],
        "deferred_nits": [],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _state(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def gh(monkeypatch, state_mod):
    """GitHub の口（head・compare・未解決の一覧）と、送信・投稿を差し替える。"""
    rp = review_lib.commands.read_result.result_posts
    seen: dict = {"push": [], "post": [], "compare": "ahead", "open": [], "post_fail": False}

    meta = review_lib.github.PrMetadata(REPO, "me", "feat/x", HEAD, "develop", False, None, None)
    monkeypatch.setattr(review_lib.github, "_fetch_pr_metadata", lambda pr, repo=None: meta)
    monkeypatch.setattr(
        review_lib.github,
        "_gh_rest",
        lambda path: types.SimpleNamespace(body={"status": seen["compare"]}) if seen["compare"] else None,
    )
    monkeypatch.setattr(
        review_lib.github,
        "_fetch_unresolved_threads",
        lambda repo, pr: None if seen["open"] is None else [{"id": i} for i in seen["open"]],
    )

    def push(worktree, head, commit):
        seen["push"].append(commit)
        return rp.PushResult(True, True, True, "")

    def post(queue, result_path, repo, pr, round_no=None, actor=None):
        seen["post"].append([i["kind"] for i in rp.fix_posts(result_path, repo, pr, round_no)])
        return rp.FixOutcome("https://x/c", 1, 1, 0, seen["post_fail"], "boom" if seen["post_fail"] else "")

    monkeypatch.setattr(rp, "push_fix", push)
    monkeypatch.setattr(rp, "post_fix", post)
    monkeypatch.setattr(review_lib.commands.merge_fix.design_body, "sync_after_push", lambda *a: None)
    monkeypatch.setattr(review_lib.commands.start_round, "_sync_before_round", lambda st, pr: None)
    monkeypatch.setattr(review_lib.github, "_fetch_existing_comments", lambda *a, **k: None)
    monkeypatch.setattr(review_lib.commands.start_round.posts, "_auto_flush", lambda pr: None)
    return seen


def _record(state_mod, *argv: str) -> None:
    args = state_mod.build_parser().parse_args(["record-fix", str(PR), *argv])
    args.func(args)


def _merge(tmp_dir: pathlib.Path) -> None:
    review_lib.commands.merge_fix.cmd_merge_fix(argparse.Namespace(pr=PR, file=str(tmp_dir / f"fix-pr{PR}-result.json")))


# ---------------- AC9: 手で JSON を書かずに記録ができ、start-round が止まらない ----------------


def test_record_fix_writes_the_contract_and_unblocks_start_round(tmp_dir, state_mod, gh, capsys):
    _seed(tmp_dir)
    _record(state_mod, "--resolved-thread", "PRRT_a", "--resolved-thread", "PRRT_b")

    result = json.loads((tmp_dir / f"fix-pr{PR}-result.json").read_text())
    assert result == {
        "pr": PR,
        "fix_commit": HEAD,
        "fixed_count": 2,
        "resolved_threads": [{"thread_id": "PRRT_a"}, {"thread_id": "PRRT_b"}],
        "deferred": [],
        "rejected": [],
        "ci_status": None,
        "recorded_by": "record-fix",
    }
    fix = _state(tmp_dir)["rounds"][-1]["fix"]
    assert fix["commit"] == HEAD
    assert fix["resolved_thread_ids"] == ["PRRT_a", "PRRT_b"]
    assert fix["merge"] == {"stage": "done", "exit_code": 0}
    assert gh["push"] == []  # ホストが送った修正は送り直さない
    assert len(gh["post"]) == 1

    review_lib.commands.start_round.cmd_start_round(argparse.Namespace(pr=PR))
    assert "ROUND=2" in capsys.readouterr().out


def test_record_fix_uses_the_given_commit(tmp_dir, state_mod, gh):
    _seed(tmp_dir)
    _record(state_mod, "--commit", "abc1234")
    assert _state(tmp_dir)["rounds"][-1]["fix"]["commit"] == "abc1234"


# ---------------- AC10・I7: 確かめられなければ記録を作らない ----------------


@pytest.mark.parametrize(
    "compare, open_threads, says",
    [
        ("behind", [], "PR の head にありません"),
        ("diverged", [], "PR の head にありません"),
        (None, [], "PR の head にありません"),
        ("ahead", ["PRRT_a"], "未解決のまま"),
        ("ahead", None, "一覧を取れない"),
    ],
    ids=["behind", "diverged", "compare-failed", "still-open", "list-failed"],
)
def test_record_fix_refuses_without_proof(tmp_dir, state_mod, gh, capsys, compare, open_threads, says):
    _seed(tmp_dir)
    gh["compare"], gh["open"] = compare, open_threads
    with pytest.raises(SystemExit) as e:
        _record(state_mod, "--resolved-thread", "PRRT_a")
    assert e.value.code == 5
    assert says in capsys.readouterr().err
    assert "fix" not in _state(tmp_dir)["rounds"][-1]
    assert not (tmp_dir / f"fix-pr{PR}-result.json").exists()


def test_record_fix_refuses_when_a_record_exists(tmp_dir, state_mod, gh):
    _seed(tmp_dir, fix={"commit": "x"})
    with pytest.raises(SystemExit) as e:
        _record(state_mod)
    assert e.value.code == 1


# ---------------- I8: 同じコミットを 2 回取り込まない ----------------


def test_merge_fix_after_record_fix_returns_the_recorded_code(tmp_dir, state_mod, gh, capsys):
    _seed(tmp_dir)
    _record(state_mod, "--resolved-thread", "PRRT_a")
    before = _state(tmp_dir)["rounds"][-1]["fix"]
    with pytest.raises(SystemExit) as e:
        _merge(tmp_dir)
    assert e.value.code == 0
    assert "取り込み済み" in capsys.readouterr().err
    assert _state(tmp_dir)["rounds"][-1]["fix"] == before
    assert len(gh["post"]) == 1


def test_merge_fix_after_record_fix_on_a_rotated_pr_reads_the_record(tmp_dir, state_mod, gh, capsys):
    """巻き直した後（current_pr が状態の鍵と違う）も、駆動の merge-fix（--file 無し）が記録を読める。"""
    _seed(tmp_dir)
    st = _state(tmp_dir)
    st["current_pr"] = PR + 1
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st))
    _record(state_mod, "--resolved-thread", "PRRT_a")
    assert json.loads((tmp_dir / f"fix-pr{PR}-result.json").read_text())["pr"] == PR
    with pytest.raises(SystemExit) as e:
        review_lib.commands.merge_fix.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))
    assert e.value.code == 0
    assert "取り込み済み" in capsys.readouterr().err


def _fix_file(tmp_dir: pathlib.Path, **over) -> None:
    body = {
        "pr": PR,
        "fix_commit": "abc1234",
        "fixed_count": 1,
        "resolved_threads": [{"thread_id": "PRRT_a"}],
        "deferred": [],
        "rejected": [],
    }
    body.update(over)
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(body))


def test_a_failed_post_is_continued_from_posting(tmp_dir, state_mod, gh):
    _seed(tmp_dir)
    _fix_file(tmp_dir, ci_status="SUCCESS")
    gh["post_fail"] = True
    with pytest.raises(SystemExit):
        _merge(tmp_dir)
    assert _state(tmp_dir)["rounds"][-1]["fix"]["merge"]["stage"] == "recorded"

    gh["post_fail"] = False
    _merge(tmp_dir)
    assert len(gh["push"]) == 1  # 送信と記録はやり直さない
    assert len(gh["post"]) == 2
    assert _state(tmp_dir)["rounds"][-1]["fix"]["merge"] == {"stage": "done", "exit_code": 0}


def test_a_code_ci_failure_is_returned_again(tmp_dir, state_mod, gh):
    _seed(tmp_dir)
    _fix_file(tmp_dir, ci_status="FAILURE", ci_failed_checks=["test"])
    with pytest.raises(SystemExit) as e:
        _merge(tmp_dir)
    assert e.value.code == 3
    with pytest.raises(SystemExit) as e:
        _merge(tmp_dir)
    assert e.value.code == 3
    assert len(gh["post"]) == 1


def test_a_record_without_merge_continues_from_posting(tmp_dir, state_mod, gh):
    """更新前の記録（merge が無い）は recorded と読み、投稿から続ける。終了コードは CI の分類で決まる。"""
    _seed(tmp_dir, fix={"commit": "abc1234", "fixed": 1})
    _fix_file(tmp_dir, ci_status="FAILURE", ci_failed_checks=["test"])
    with pytest.raises(SystemExit) as e:
        _merge(tmp_dir)
    assert e.value.code == 3
    assert gh["push"] == []
    assert len(gh["post"]) == 1


def test_a_different_commit_is_merged_as_new(tmp_dir, state_mod, gh):
    _seed(tmp_dir, fix={"commit": "old", "fixed": 1, "merge": {"stage": "done", "exit_code": 0}})
    _fix_file(tmp_dir, ci_status="SUCCESS")
    _merge(tmp_dir)
    assert gh["push"] == ["abc1234"]
    assert _state(tmp_dir)["rounds"][-1]["fix"]["commit"] == "abc1234"
