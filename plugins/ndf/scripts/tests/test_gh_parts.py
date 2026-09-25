"""PR / issue の取得と本文の節の差し替えの共通部品（`lib/gh_parts.py`、#849）。

`gh` は `gh_parts.RUNNER` を見本の応答へ差し替えて呼ぶ。GitHub へは届かない。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
_spec = importlib.util.spec_from_file_location("ndf_lib_gh_parts", LIB / "gh_parts.py")
gp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gp)

REPO = "o/r"
PR = 812
SHA = "abc123"
RATE = "GraphQL: API rate limit exceeded for user ID 1."


def _rest_out(body, remaining=4999):
    return (f"HTTP/2.0 200 OK\nX-Ratelimit-Remaining: {remaining}\r\n"
            f"X-Ratelimit-Reset: 1700000000\r\n\r\n{json.dumps(body)}")


class FakeGh:
    """argv の先頭一致で応答を返す。呼び出しを `calls` に残す。"""

    def __init__(self):
        self.routes: list[tuple[tuple[str, ...], object]] = []
        self.calls: list[tuple[list[str], str | None]] = []

    def on(self, *prefix, rc=0, out="", err=""):
        self.routes.append((prefix, gp.GhResult(rc, out, err)))
        return self

    def on_fn(self, *prefix, fn):
        self.routes.append((prefix, fn))
        return self

    def __call__(self, args, stdin=None):
        self.calls.append((list(args), stdin))
        for prefix, res in self.routes:
            if tuple(args[:len(prefix)]) == prefix or (
                    len(prefix) == 1 and prefix[0] in " ".join(args)):
                return res(args, stdin) if callable(res) else res
        pytest.fail(f"想定外の gh の呼び出し: {args}")

    def argvs(self):
        return [" ".join(a) for a, _ in self.calls]


@pytest.fixture()
def fake(monkeypatch):
    f = FakeGh()
    monkeypatch.setattr(gp, "RUNNER", f)
    return f


GRAPHQL_PR = {
    "number": PR, "title": "t", "body": "本文", "state": "OPEN", "isDraft": False,
    "author": {"login": "alice"}, "headRefName": "feat/x", "headRefOid": SHA,
    "baseRefName": "main", "url": "https://github.com/o/r/pull/812",
    "additions": 10, "deletions": 2, "changedFiles": 3, "labels": [{"name": "bug"}],
    "isCrossRepository": False,
}
REST_PR = {
    "number": PR, "title": "t", "body": "本文", "state": "open", "draft": False,
    "user": {"login": "alice"}, "head": {"ref": "feat/x", "sha": SHA, "repo": {"full_name": REPO}},
    "base": {"ref": "main"}, "html_url": "https://github.com/o/r/pull/812",
    "additions": 10, "deletions": 2, "changed_files": 3, "labels": [],
}


def _run(name, conclusion, started, run_id, status="completed"):
    return {"id": run_id, "name": name, "status": status, "conclusion": conclusion,
            "started_at": started,
            "details_url": f"https://github.com/o/r/actions/runs/9/job/{run_id}"}


def _checks(*runs):
    return _rest_out({"total_count": len(runs), "check_runs": list(runs)})


# ---------------- 上限の見分け ----------------

@pytest.mark.parametrize("text, expected", [
    (RATE, True),
    ("RATE_LIMITED", True),
    ("unknown owner type", True),
    ("HTTP 404: Not Found", False),
    ("", False),
])
def test_rate_limit_is_recognized_with_the_same_words_as_projects_common(text, expected):
    assert gp.is_rate_limited(text) is expected


# ---------------- pr-info ----------------

def test_pr_info_returns_meta_body_and_diff_stats_in_one_result(fake, tmp_path):
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))

    obj, code = gp.pr_info(PR, REPO, set(), tmp_path)

    assert code == 0 and gp.step_result.validate_result(obj, code) == []
    pr = obj["items"][0]
    assert pr["kind"] == "pr" and pr["author"] == "alice" and pr["body"] == "本文"
    assert pr["head_sha"] == SHA and pr["state"] == "open"
    assert obj["metrics"] == {"source": "graphql", "additions": 10, "deletions": 2,
                              "changed_files": 3}


def test_pr_info_falls_back_to_rest_when_graphql_is_rate_limited(fake, tmp_path):
    fake.on("pr", "view", rc=1, err=RATE)
    fake.on("api", "-i", f"repos/{REPO}/pulls/{PR}", out=_rest_out(REST_PR))

    obj, code = gp.pr_info(PR, REPO, set(), tmp_path)

    assert code == 0
    assert obj["metrics"]["source"] == "rest"
    pr = obj["items"][0]
    assert pr["author"] == "alice" and pr["head_sha"] == SHA and pr["changed_files"] == 3


def test_pr_info_does_not_fall_back_on_other_failures(fake, tmp_path):
    """上限以外の失敗（存在しない PR など）は REST で読み直さず、読めないとして止まる。"""
    fake.on("pr", "view", rc=1, err="GraphQL: Could not resolve to a PullRequest")

    obj, code = gp.pr_info(PR, REPO, set(), tmp_path)

    assert (obj["status"], code) == ("stopped", 2)
    assert not any("pulls" in a for a in fake.argvs())


def test_checks_fold_to_the_latest_run_per_name(fake, tmp_path):
    """同名の check が failure → success の順に 2 件あるとき success を返す（#632）。"""
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "-i", out=_checks(
        _run("pytest", "failure", "2026-09-25T01:00:00Z", 101),
        _run("lint", "success", "2026-09-25T01:00:00Z", 102),
        _run("pytest", "success", "2026-09-25T02:00:00Z", 103),
    ))

    obj, _ = gp.pr_info(PR, REPO, {"checks"}, tmp_path)

    checks = [i for i in obj["items"] if i["kind"] == "check"]
    assert [(c["name"], c["result"]) for c in checks] == [("pytest", "success"), ("lint", "success")]
    assert obj["metrics"]["failed_checks"] == 0
    assert obj["metrics"]["superseded_checks"] == 1


def test_fold_uses_run_order_when_times_are_missing():
    runs = [{"name": "t", "status": "completed", "conclusion": "failure"},
            {"name": "t", "status": "completed", "conclusion": "success"}]
    assert [gp.run_result(r) for r in gp.fold_check_runs(runs)] == ["success"]
    # 一覧が新しい順でも、開始時刻の新しい方が勝つ
    newest_first = [_run("t", "success", "2026-09-25T02:00:00Z", 2),
                    _run("t", "failure", "2026-09-25T01:00:00Z", 1)]
    assert gp.check_result(newest_first, "t") == "success"
    assert gp.check_result(newest_first, "other") is None
    assert gp.check_result(None, "t") is None


def test_failed_check_logs_are_saved_to_files_not_embedded(fake, tmp_path):
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "-i", out=_checks(_run("pytest", "failure", "2026-09-25T01:00:00Z", 101),
                                     _run("build", None, "2026-09-25T01:00:00Z", 102,
                                          status="in_progress")))
    fake.on("run", "view", out="E   assert 1 == 2\n")

    obj, _ = gp.pr_info(PR, REPO, {"checks", "logs"}, tmp_path)

    failed = next(i for i in obj["items"] if i["name"] == "pytest")
    assert failed["result"] == "failure"
    assert Path(failed["log_path"]).read_text() == "E   assert 1 == 2\n"
    assert "assert 1 == 2" not in json.dumps(obj, ensure_ascii=False)
    assert "--log-failed" in fake.argvs()[-1] and "--job 101" in fake.argvs()[-1]
    assert (obj["metrics"]["failed_checks"], obj["metrics"]["pending_checks"]) == (1, 1)


def test_unreadable_checks_are_null_not_zero(fake, tmp_path):
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "-i", rc=1, err="HTTP 422")

    obj, code = gp.pr_info(PR, REPO, {"checks"}, tmp_path)

    assert code == 0
    assert obj["metrics"]["failed_checks"] is None
    assert obj["metrics"]["unavailable"] == ["checks"]


def test_diff_is_written_to_a_file(fake, tmp_path):
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "-H", out="diff --git a/x b/x\n")

    obj, _ = gp.pr_info(PR, REPO, {"diff"}, tmp_path)

    diff = next(i for i in obj["items"] if i["kind"] == "diff")
    assert Path(diff["path"]).read_text() == "diff --git a/x b/x\n"


def test_check_runs_are_read_to_total_count_across_pages(fake):
    first = [_run(f"c{i}", "success", "", i) for i in range(100)]
    fake.on("api", "-i", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100&page=1",
            out=_rest_out({"total_count": 101, "check_runs": first}))
    fake.on("api", "-i", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100&page=2",
            out=_rest_out({"total_count": 101, "check_runs": [_run("last", "failure", "", 999)]}))

    runs = gp.fetch_check_runs(REPO, SHA)

    assert len(runs) == 101 and runs[-1]["name"] == "last"


# ---------------- unresolved-threads ----------------

def test_unresolved_threads_carry_thread_id(fake):
    fake.on("api", "graphql", out="PRRT_a\tsrc/foo.py\t42\nPRRT_b\tdocs/bar.md\t\n")

    threads = gp.unresolved_threads(REPO, PR)

    assert threads == [{"thread_id": "PRRT_a", "path": "src/foo.py", "line": "42"},
                       {"thread_id": "PRRT_b", "path": "docs/bar.md", "line": ""}]
    joined = fake.argvs()[0]
    assert "owner=o" in joined and "name=r" in joined and f"pr={PR}" in joined


def test_unresolved_threads_distinguish_failure_from_zero(fake):
    fake.on("api", "graphql", rc=1, err=RATE)
    assert gp.unresolved_threads(REPO, PR) is None
    assert gp.unresolved_threads("no-slash", PR) is None


def test_pr_info_with_threads_counts_and_lists_them(fake, tmp_path):
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "graphql", out="PRRT_a\tsrc/foo.py\t42\n")

    obj, _ = gp.pr_info(PR, REPO, {"threads"}, tmp_path)

    thread = next(i for i in obj["items"] if i["kind"] == "thread")
    assert (thread["thread_id"], thread["line"]) == ("PRRT_a", 42)
    assert obj["metrics"]["unresolved_threads"] == 1


# ---------------- body-section ----------------

PROGRESS = "## 進行\n\nモード: full\n\n- [x] 着手 — 2026-09-25 10:00\n- [ ] 振り返り\n"


def test_replace_keeps_everything_outside_the_section():
    body = "## 何をするか\n\n本文\n\n## 進行\n\n古い\n\n## 関連\n\n#480\n"

    new = gp.replace_section(body, "## 進行", "新しい")

    assert new.startswith("## 何をするか\n\n本文\n\n## 進行\n\n新しい\n")
    assert new.endswith("## 関連\n\n#480\n")
    assert "古い" not in new
    assert gp.get_section(new, "## 進行") == "新しい"


def test_replace_is_idempotent_and_does_not_duplicate():
    body = "## 何をするか\n\n本文\n"
    once = gp.replace_section(body, "## 進行", PROGRESS)
    twice = gp.replace_section(once, "## 進行", PROGRESS)

    assert once == twice
    assert once.count("## 進行") == 1


def test_appended_line_survives_replacing_the_last_section():
    """#659: `## 進行` が最後の節でも、末尾へ足した振り返りの 1 行が次の差し替えで消えない。"""
    body = gp.replace_section("## 何をするか\n\n本文\n", "## 進行", PROGRESS)
    url = "振り返り: https://github.com/o/r/pull/652#issuecomment-1"

    body = gp.append_line(body, url)
    body = gp.replace_section(body, "## 進行", PROGRESS.replace("- [ ] 振り返り", "- [x] 振り返り"))

    assert url in body
    assert "- [x] 振り返り" in body
    assert gp.get_section(body, "## 進行").count("振り返り: https") == 0


def test_append_before_any_marker_closes_the_last_section_first():
    """節を書いたことの無い本文（目印が無い）へ足しても、次の差し替えで消えない。"""
    body = "## 何をするか\n\n本文\n\n" + PROGRESS
    url = "振り返り: https://example.com/1"

    body = gp.append_line(body, url)
    body = gp.replace_section(body, "## 進行", "置き換えた")

    assert url in body and "置き換えた" in body and "- [ ] 振り返り" not in body


def test_append_is_idempotent():
    body = gp.append_line("本文\n", "振り返り: x")
    assert gp.append_line(body, "振り返り: x") == body
    assert body.count("振り返り: x") == 1


def test_heading_must_match_a_whole_line_outside_code_blocks():
    body = ("## 進行状況\n\n別の節\n\n```md\n## 進行\n```\n")
    assert gp.get_section(body, "## 進行") is None
    new = gp.replace_section(body, "## 進行", "x")
    assert new.startswith(body.rstrip("\n")) and new.count("## 進行\n") == 2


def test_subheadings_stay_inside_the_section():
    body = "## 進行\n\n### 細目\n\na\n\n## 関連\n\nb\n"
    assert gp.get_section(body, "## 進行") == "### 細目\n\na"


def test_body_section_command_uses_rest_and_skips_unchanged_writes(fake):
    body = gp.replace_section("本文\n", "## 進行", "x")
    fake.on("api", "-i", f"repos/{REPO}/issues/659", out=_rest_out({"body": body}))

    obj, code = gp.body_section("replace", 659, REPO, "## 進行", "x")

    assert (code, obj["items"][0]["result"]) == (0, "unchanged")
    assert not any("PATCH" in a for a in fake.argvs())
    assert not any("graphql" in a for a in fake.argvs())


def test_body_section_command_writes_the_new_body(fake):
    fake.on("api", "-X", "PATCH", out=_rest_out({"body": ""}))
    fake.on("api", "-i", f"repos/{REPO}/issues/659", out=_rest_out({"body": "本文\n"}))

    obj, code = gp.body_section("append", 659, REPO, line="振り返り: x")

    assert (code, obj["items"][0]["result"]) == (0, "updated")
    args, stdin = next(c for c in fake.calls if "PATCH" in c[0])
    assert json.loads(stdin)["body"] == "本文\n\n振り返り: x\n"


def test_body_section_command_stops_when_the_body_cannot_be_read(fake):
    fake.on("api", "-i", rc=1, err="HTTP 404")
    obj, code = gp.body_section("get", 659, REPO, "## 進行")
    assert (obj["status"], code) == ("stopped", 2)


# ---------------- review-post ----------------

@pytest.mark.parametrize("viewer, own", [("alice", True), ("bob", False)])
def test_review_post_downgrades_on_own_pr(fake, monkeypatch, tmp_path, viewer, own):
    import result_posts

    fake.on("api", "-i", f"repos/{REPO}/pulls/{PR}", out=_rest_out(REST_PR))
    fake.on("api", "user", out=f"{viewer}\n")
    seen = {}

    def _post(queue, payload, result, repo, pr, round_no, seat, head_sha, is_own_pr, **kw):
        seen.update(repo=repo, pr=pr, head_sha=head_sha, is_own_pr=is_own_pr)
        return result_posts.ReviewOutcome(
            review_url="https://github.com/o/r/pull/812#pullrequestreview-1", posted_inline=2,
            posted_body=0, queued=0, findings=2, failed=False,
            posted_as="COMMENT" if is_own_pr else "REQUEST_CHANGES",
            intent="REQUEST_CHANGES", detail="")

    monkeypatch.setattr(result_posts, "post_review", _post)

    obj, code = gp.review_post("p.json", "r.json", PR, 1, "codex", REPO,
                               queue_dir=str(tmp_path / "q"))

    assert code == 0 and seen == {"repo": REPO, "pr": PR, "head_sha": SHA, "is_own_pr": own}
    assert obj["items"][0]["posted_as"] == ("COMMENT" if own else "REQUEST_CHANGES")
    assert obj["metrics"]["is_own_pr"] is own


def test_review_post_stops_when_the_viewer_is_unknown(fake, tmp_path):
    fake.on("api", "-i", f"repos/{REPO}/pulls/{PR}", out=_rest_out(REST_PR))
    fake.on("api", "user", rc=1, err="HTTP 401")

    obj, code = gp.review_post("p.json", "r.json", PR, 1, "codex", REPO,
                               queue_dir=str(tmp_path / "q"))

    assert (obj["status"], code) == ("stopped", 3)


def test_fold_takes_the_newer_of_completed_and_started_times():
    """新しさは `completed_at` と `started_at` の新しい方で決める（#632）。

    長く走って後に終わった失敗は、後に始まって先に終わった成功より新しい。
    """
    long_failure = {"id": 1, "name": "t", "status": "completed", "conclusion": "failure",
                    "started_at": "2026-09-25T01:00:00Z", "completed_at": "2026-09-25T03:00:00Z"}
    short_success = {"id": 2, "name": "t", "status": "completed", "conclusion": "success",
                     "started_at": "2026-09-25T02:00:00Z", "completed_at": "2026-09-25T02:10:00Z"}
    assert gp.check_result([long_failure, short_success], "t") == "failure"
    assert gp.check_result([short_success, long_failure], "t") == "failure"
