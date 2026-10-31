"""sprint-close.py の閉じる条件 3（最終工程の記録）と閉じる予定の参照（#2228・#2119・#2082）。

gh の偽物は test_sprint_close_merge_green.py のものを使う。記録の PR のコメントは url に `#issuecomment-<識別子>` を持つ。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sprint_close_harness_final", HERE / "test_sprint_close_merge_green.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
call, issues_of, DIST_PROD, verify_block = harness.call, harness.issues_of, harness.DIST_PROD, harness.verify_block
repo, gh = harness.repo, harness.gh  # pytest の fixture

URL = "https://github.com/o/r/pull/20#issuecomment-{}"


def retro(cid):
    return f"# 課題\n\n本文\n\n振り返り: https://github.com/o/r/pull/21#issuecomment-{cid}\n"


def comment(body, cid):
    return {"body": body, "url": URL.format(cid)}


def setup(gh, issue_body, *, dist_in_comment=False, comments=None, state="OPEN", prs="Fixes #1"):
    """記録の PR 20 と、PR 11 が閉じる課題 #1。dist_in_comment なら配布の記録を識別子 200 のコメントに置く。"""
    if comments is None:
        comments = [comment(DIST_PROD, 200)] if dist_in_comment else [comment("メモ", 100)]
    gh.set(
        records={"20": {"body": "記録" if dist_in_comment else DIST_PROD, "comments": comments}},
        bodies={"11": prs, "12": ""},
        issues={"o/r#1": [state]},
        issue_bodies={"o/r#1": issue_body},
    )


def wrote(gh):
    st = gh.get()
    return "closed" in st or "written" in st or any(c[:2] == ["project", "item-edit"] for c in st["calls"])


@pytest.mark.parametrize("verify", [False, True])
def test_an_issue_without_the_retro_record_is_kept_open(repo, gh, verify):
    comments = [comment("メモ", 100)] + ([comment(verify_block("1.1.0", [("#1", "合格")]), 101)] if verify else [])
    setup(gh, "# 課題\n\n本文\n", comments=comments)
    args = ["--record-pr", "20", "--repo", "o/r"] + (["--with-verification"] if verify else [])
    code, out, err = call("sprint-close.py", args, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and it["result"] == "kept_open" and "振り返り" in it["reason"], err
    assert not wrote(gh)
    assert out["metrics"]["final_stage"] == "振り返り"
    assert out["decisions"][0]["decision"] == "最終工程を 振り返り として判定した"
    assert "振り返り" in (out.get("next") or "")


def test_a_retro_line_of_a_previous_run_does_not_count(repo, gh):
    setup(gh, retro(150), dist_in_comment=True)
    code, out, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert it["result"] == "kept_open" and "振り返り" in it["reason"] and "前のもの" in it["reason"]
    setup(gh, retro(150) + retro(250), dist_in_comment=True)
    code, out, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "closed"
    assert sum(1 for c in gh.get()["calls"] if c[:3] == ["pr", "view", "20"]) == 1


def test_a_record_in_the_body_compares_with_the_first_comment(repo, gh):
    setup(gh, retro(100), comments=[comment("振り返りの記録", 100), comment("x", 300)])
    code, out, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "closed"


def test_a_record_without_comments_has_no_base_and_keeps_open(repo, gh):
    setup(gh, retro(100), comments=[])
    code, out, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert it["result"] == "kept_open" and "比べる配布の記録のコメントが無い" in it["reason"]


def test_a_closed_issue_without_the_record_is_not_reopened(repo, gh):
    setup(gh, "本文", state="CLOSED")
    code, out, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert it["result"] == "closed_unverified" and "振り返り" in it["reason"]
    assert not any(c[:2] == ["issue", "reopen"] for c in gh.get()["calls"])


@pytest.mark.parametrize("verify", [False, True])
def test_an_unreadable_body_fails_and_stops(repo, gh, verify):
    comments = [comment("メモ", 100)] + ([comment(verify_block("1.1.0", [("#1", "合格")]), 101)] if verify else [])
    setup(gh, retro(100), comments=comments, state=None)
    args = ["--record-pr", "20", "--repo", "o/r"] + (["--with-verification"] if verify else [])
    code, out, _ = call("sprint-close.py", args, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 1 and out["status"] == "stopped" and it["result"] == "failed" and "最終工程" in it["reason"]
    assert it["cmd"].endswith(" ".join(args)) and not wrote(gh)


def test_the_retro_record_and_passing_rows_close_and_mark(repo, gh):
    body = "# 課題\n\n## 受け入れ条件\n\n- [ ] 1. 一\n\n振り返り: https://github.com/o/r/pull/21#issuecomment-500\n"
    rows = "| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |\n| --- | --- | --- | --- | --- |\n| #1 | 1. 一 | x | t | 合格 |\n"
    setup(gh, body, comments=[comment(f"## リリース後テスト\n対象の版: 1.1.0（2026-09-24）\n\n{rows}\n合否: 済\n", 101)])
    code, out, err = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r", "--with-verification"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and it["result"] == "closed" and it["checked"] == [1], it
    assert gh.get()["issue_bodies"]["o/r#1"].count("- [x] 1.") == 1


def test_record_zero_closes_with_a_retro_line_and_keeps_without(repo, gh):
    setup(gh, retro(5))
    code, out, _ = call("sprint-close.py", ["--record-pr", "0", "--repo", "o/r", "--issues", "1"], gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "closed"
    setup(gh, "本文")
    code, out, _ = call("sprint-close.py", ["--record-pr", "0", "--repo", "o/r", "--issues", "1"], gh.env, repo)
    assert issues_of(out)["o/r#1"]["result"] == "kept_open"


def test_record_unknown_does_not_read_the_body(repo, gh):
    setup(gh, retro(5))
    code, out, _ = call("sprint-close.py", ["--record-pr", "unknown", "--repo", "o/r", "--issues", "1"], gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "kept_open"
    assert not any(c[:2] == ["issue", "view"] for c in gh.get().get("calls", []))


def test_the_verification_stage_always_reads_the_rows(repo, gh):
    setup(gh, "本文", comments=[comment(verify_block("1.1.0", [("#1", "不合格")]), 101)])
    args = ["--record-pr", "20", "--repo", "o/r", "--final-stage", "リリース後テスト"]
    code, out, _ = call("sprint-close.py", args, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and it["result"] == "kept_open" and "合格でない" in it["reason"]
    assert out["metrics"]["final_stage"] == "リリース後テスト"


def test_the_distribution_stage_closes_without_a_retro_line(repo, gh):
    setup(gh, "本文")
    code, out, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r", "--final-stage", "配布"], gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "closed"


def test_an_unknown_final_stage_is_a_call_error(repo, gh):
    setup(gh, "本文")
    code, _, _ = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r", "--final-stage", "棚卸し"], gh.env, repo)
    assert code == 3


def test_a_pr_with_only_deferred_references_collects_its_issue(repo, gh):
    """既定ブランチ宛で置き換えた本文だけのスプリントでも、課題が閉じる対象になる（#2082 の受け入れ条件 3）。"""
    setup(gh, retro(100), prs="## 閉じる課題\n\n最終工程の後に閉じる: #1\n")
    code, out, err = call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r", "--prs", "11", "--dry-run"], gh.env, repo)
    assert code == 0, err
    assert issues_of(out)["o/r#1"]["result"] == "would_close" and out["metrics"]["referenced_only"] == 0
    assert not wrote(gh)


def test_final_stage_gap_counts_only_lines_from_the_start():
    mod = harness.load_sprint_close()
    body = "- 振り返り: https://github.com/o/r/pull/2#issuecomment-9\n"
    assert "行が無い" in mod.final_stage_gap("振り返り", body, 1)
    assert mod.final_stage_gap("振り返り", body.lstrip("- "), 9) is None
    assert mod.final_stage_gap("配布", "", None) is None
