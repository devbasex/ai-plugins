"""sprint-close.py の参照だけの課題と、閉じる課題が 0 件の停止（#767）。

スプリントの PR の題か本文が番号で参照し、closing keywords（GitHub が課題を自動で閉じるキーワード
Closes / Fixes / Resolves）も --issues も無い開いた課題を kept_open で載せる。gh の偽物は
test_sprint_close_merge_green.py のものを使う。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sprint_close_harness_refs", HERE / "test_sprint_close_merge_green.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
call, issues_of, DIST_PROD = harness.call, harness.issues_of, harness.DIST_PROD
repo, gh = harness.repo, harness.gh  # pytest の fixture

PROD_ONE = "## 配布の記録\n段階: 本番（2026-10-08 承認）\n版: 1.0.0 → 1.1.0\nスプリント: PR #11\n"
NOT_GIVEN = "--issues も無い参照"


def run(gh, repo, *extra):
    return call("sprint-close.py", ["--record-pr", "20", "--repo", "o/r", *extra], gh.env, repo)


def issue_calls(gh, verb, n=None):
    return [c for c in gh.get()["calls"] if c[:2] == ["issue", verb] and (n is None or c[2] == str(n))]


def test_referenced_issue_is_kept_open_in_dry_run(repo, gh):
    gh.set(
        records={"20": {"body": PROD_ONE, "comments": []}},
        bodies={"11": "Closes #1\nRefs #2"},
        issues={"o/r#1": ["OPEN"], "o/r#2": ["OPEN"]},
    )
    code, out, err = run(gh, repo, "--dry-run")
    res = issues_of(out)
    assert code == 0, (out, err)
    assert res["o/r#1"]["result"] == "would_close"
    assert res["o/r#2"]["result"] == "kept_open" and NOT_GIVEN in res["o/r#2"]["reason"]
    assert out["metrics"]["referenced_only"] == 1 and "参照だけ 1 件" in out["summary"]


def test_referenced_issue_is_not_closed(repo, gh):
    gh.set(
        records={"20": {"body": PROD_ONE, "comments": []}},
        bodies={"11": "Closes #1\nRefs #2"},
        issues={"o/r#1": ["OPEN"], "o/r#2": ["OPEN"]},
    )
    code, out, err = run(gh, repo)
    res = issues_of(out)
    assert code == 0, (out, err)
    assert res["o/r#1"]["result"] == "closed" and res["o/r#2"]["result"] == "kept_open"
    assert gh.get()["closed"] == ["o/r#1"] and not issue_calls(gh, "close", 2)
    # 参照だけの課題 1 件あたり issue view は 1 回（close_one を通らない）、PR 1 本あたり pr view は 1 回
    assert len(issue_calls(gh, "view", 2)) == 1
    assert len([c for c in gh.get()["calls"] if c[:3] == ["pr", "view", "11"]]) == 1


def test_reference_only_in_title_and_title_closing_keyword_does_not_close(repo, gh):
    gh.set(
        records={"20": {"body": PROD_ONE, "comments": []}},
        titles={"11": "feat: #3 を直す。Fixes #4"},
        bodies={"11": "Closes #1"},
        issues={"o/r#1": ["OPEN"], "o/r#3": ["OPEN"], "o/r#4": ["OPEN"]},
    )
    code, out, err = run(gh, repo)
    res = issues_of(out)
    assert res["o/r#3"]["result"] == "kept_open" and res["o/r#4"]["result"] == "kept_open"
    assert gh.get()["closed"] == ["o/r#1"]


def test_pull_requests_and_closed_issues_are_not_listed(repo, gh):
    gh.set(
        records={"20": {"body": PROD_ONE, "comments": []}},
        bodies={"11": "Closes #1 #5 #6"},
        issues={
            "o/r#1": ["OPEN"],
            "o/r#5": ["MERGED\thttps://github.com/o/r/pull/5"],
            "o/r#6": ["CLOSED\thttps://github.com/o/r/issues/6"],
        },
    )
    code, out, err = run(gh, repo)
    assert code == 0 and sorted(issues_of(out)) == ["o/r#1"]
    assert out["metrics"]["referenced_only"] == 0


def test_given_and_repeated_references_appear_once(repo, gh):
    gh.set(
        records={"20": {"body": PROD_ONE, "comments": []}},
        bodies={"11": "Closes #1\nRefs #2 #2 #7\n関連 #7"},
        issues={"o/r#1": ["OPEN"], "o/r#2": ["OPEN"], "o/r#7": ["OPEN"]},
    )
    code, out, err = run(gh, repo, "--issues", "2")
    nums = [i["number"] for i in out["items"] if i["kind"] == "issue"]
    assert sorted(nums) == [1, 2, 7]
    res = issues_of(out)
    assert res["o/r#2"]["result"] == "closed" and res["o/r#7"]["result"] == "kept_open"
    assert len(issue_calls(gh, "view", 7)) == 1


def test_unreadable_reference_is_kept_open_and_others_continue(repo, gh):
    gh.set(records={"20": {"body": PROD_ONE, "comments": []}}, bodies={"11": "Closes #1\nRefs #8"}, issues={"o/r#1": ["OPEN"]})
    code, out, err = run(gh, repo)
    res = issues_of(out)
    assert code == 0 and out["status"] == "ok"
    assert res["o/r#1"]["result"] == "closed"
    assert res["o/r#8"]["result"] == "kept_open" and "状態を読めない" in res["o/r#8"]["reason"]


def test_other_repository_references_are_ignored(repo, gh):
    gh.set(records={"20": {"body": PROD_ONE, "comments": []}}, bodies={"11": "Closes #1\nx/y#3 o/r#9"}, issues={"o/r#1": ["OPEN"]})
    code, out, err = run(gh, repo)
    assert sorted(issues_of(out)) == ["o/r#1"]
    assert not [c for c in gh.get()["calls"] if c[:2] == ["issue", "view"] and c[2] in ("3", "9")]


def test_no_closing_keywords_and_no_issues_stops_without_writing(repo, gh):
    gh.set(records={"20": {"body": PROD_ONE, "comments": []}}, bodies={"11": "- 課題: #5 #6"}, issues={"o/r#5": ["OPEN"]})
    code, out, err = run(gh, repo)
    assert code == 2 and out["status"] == "stopped" and out["items"] == []
    assert "閉じる課題が 0 件" in out["summary"] and "--issues" in out["next"] and "#5 #6" in out["next"]
    assert not issue_calls(gh, "close") and not issue_calls(gh, "view")


def test_given_issue_avoids_the_stop(repo, gh):
    gh.set(records={"20": {"body": PROD_ONE, "comments": []}}, bodies={"11": "- 課題: #5"}, issues={"o/r#5": ["OPEN"]})
    code, out, err = run(gh, repo, "--issues", "5")
    assert code == 0 and issues_of(out)["o/r#5"]["result"] == "closed"


def test_before_production_keeps_references_open_and_closes_nothing(repo, gh):
    rec = "## 配布の記録\n段階: 検証\n版: 1.0.0 → 1.1.0-dev.1\nスプリント: PR #11\n"
    gh.set(
        records={"20": {"body": rec, "comments": []}}, bodies={"11": "Closes #1\nRefs #2"}, issues={"o/r#1": ["OPEN"], "o/r#2": ["OPEN"]}
    )
    code, out, err = run(gh, repo)
    res = issues_of(out)
    assert code == 0 and res["o/r#1"]["result"] == "kept_open" and res["o/r#2"]["result"] == "kept_open"
    assert NOT_GIVEN in res["o/r#2"]["reason"] and "closed" not in gh.get()


def test_pr_766_case(repo, gh):
    gh.set(
        records={"20": {"body": DIST_PROD, "comments": []}},
        bodies={"11": "Closes #561\nRefs #554", "12": "Fixes #623\n関連 #550\n本文の #540 にも触れる"},
        issues={f"o/r#{n}": ["OPEN"] for n in (561, 623, 554, 550, 540)},
    )
    code, out, err = run(gh, repo)
    res = issues_of(out)
    assert code == 0, (out, err)
    assert all(res[f"o/r#{n}"]["result"] == "closed" for n in (561, 623))
    assert all(res[f"o/r#{n}"]["result"] == "kept_open" for n in (554, 550, 540))
    assert out["metrics"]["referenced_only"] == 3
