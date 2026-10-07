"""PR / issue の取得と本文の節の差し替えの共通部品（`lib/gh_parts.py`、#849）。

`gh` は定義元の `gh_call.RUNNER` を見本の応答へ差し替えて呼ぶ（#1142 の L0 で分けた）。GitHub へは届かない。
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
import clock  # noqa: E402
import gh_call  # noqa: E402

REPO = "o/r"
PR = 812
SHA = "abc123"
RATE = "GraphQL: API rate limit exceeded for user ID 1."


def _rest_out(body, remaining=4999):
    return f"HTTP/2.0 200 OK\nX-Ratelimit-Remaining: {remaining}\r\nX-Ratelimit-Reset: 1700000000\r\n\r\n{json.dumps(body)}"


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
            if tuple(args[: len(prefix)]) == prefix or (len(prefix) == 1 and prefix[0] in " ".join(args)):
                return res(args, stdin) if callable(res) else res
        pytest.fail(f"想定外の gh の呼び出し: {args}")

    def argvs(self):
        return [" ".join(a) for a, _ in self.calls]


@pytest.fixture()
def fake(monkeypatch):
    f = FakeGh()
    monkeypatch.setattr(gh_call, "RUNNER", f)
    return f


GRAPHQL_PR = {
    "number": PR,
    "title": "t",
    "body": "本文",
    "state": "OPEN",
    "isDraft": False,
    "author": {"login": "alice"},
    "headRefName": "feat/x",
    "headRefOid": SHA,
    "baseRefName": "main",
    "url": "https://github.com/o/r/pull/812",
    "additions": 10,
    "deletions": 2,
    "changedFiles": 3,
    "labels": [{"name": "bug"}],
    "isCrossRepository": False,
}
REST_PR = {
    "number": PR,
    "title": "t",
    "body": "本文",
    "state": "open",
    "draft": False,
    "user": {"login": "alice"},
    "head": {"ref": "feat/x", "sha": SHA, "repo": {"full_name": REPO}},
    "base": {"ref": "main"},
    "html_url": "https://github.com/o/r/pull/812",
    "additions": 10,
    "deletions": 2,
    "changed_files": 3,
    "labels": [],
}


def _run(name, conclusion, started, run_id, status="completed"):
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "started_at": started,
        "details_url": f"https://github.com/o/r/actions/runs/9/job/{run_id}",
    }


def _checks(*runs):
    return _rest_out({"total_count": len(runs), "check_runs": list(runs)})


# ---------------- 上限の見分け ----------------


@pytest.mark.parametrize(
    "text, expected",
    [
        (RATE, True),
        ("RATE_LIMITED", True),
        ("unknown owner type", True),
        ("HTTP 404: Not Found", False),
        ("", False),
    ],
)
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
    assert obj["metrics"] == {"source": "graphql", "additions": 10, "deletions": 2, "changed_files": 3}


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
    fake.on(
        "api",
        "-i",
        out=_checks(
            _run("pytest", "failure", "2026-09-25T01:00:00Z", 101),
            _run("lint", "success", "2026-09-25T01:00:00Z", 102),
            _run("pytest", "success", "2026-09-25T02:00:00Z", 103),
        ),
    )

    obj, _ = gp.pr_info(PR, REPO, {"checks"}, tmp_path)

    checks = [i for i in obj["items"] if i["kind"] == "check"]
    assert [(c["name"], c["result"]) for c in checks] == [("pytest", "success"), ("lint", "success")]
    assert obj["metrics"]["failed_checks"] == 0
    assert obj["metrics"]["superseded_checks"] == 1


def test_fold_uses_run_order_when_times_are_missing():
    runs = [{"name": "t", "status": "completed", "conclusion": "failure"}, {"name": "t", "status": "completed", "conclusion": "success"}]
    assert [gp.run_result(r) for r in gp.fold_check_runs(runs)] == ["success"]
    # 一覧が新しい順でも、開始時刻の新しい方が勝つ
    newest_first = [_run("t", "success", "2026-09-25T02:00:00Z", 2), _run("t", "failure", "2026-09-25T01:00:00Z", 1)]
    assert gp.check_result(newest_first, "t") == "success"
    assert gp.check_result(newest_first, "other") is None
    assert gp.check_result(None, "t") is None


def test_fold_takes_a_run_without_times_as_the_newest():
    """時刻がどちらも無い項目（まだ始まっていない再実行）は最も新しい。マージの待ちと同じ規則（#1645）。"""
    runs = [_run("t", "failure", "2026-09-25T02:00:00Z", 9), {"id": 1, "name": "t", "status": "queued", "conclusion": None}]
    assert [gp.run_result(r) for r in gp.fold_check_runs(runs)] == ["pending"]


def test_failed_check_logs_are_saved_to_files_not_embedded(fake, tmp_path):
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on(
        "api",
        "-i",
        out=_checks(
            _run("pytest", "failure", "2026-09-25T01:00:00Z", 101), _run("build", None, "2026-09-25T01:00:00Z", 102, status="in_progress")
        ),
    )
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
    fake.on(
        "api", "-i", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100&page=1", out=_rest_out({"total_count": 101, "check_runs": first})
    )
    fake.on(
        "api",
        "-i",
        f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100&page=2",
        out=_rest_out({"total_count": 101, "check_runs": [_run("last", "failure", "", 999)]}),
    )

    runs = gp.fetch_check_runs(REPO, SHA)

    assert len(runs) == 101 and runs[-1]["name"] == "last"


def test_no_check_runs_yet_is_an_empty_list_only_when_asked(fake):
    """push 直後（`total_count` 0）は、待つ側が頼んだときだけ空の一覧（照会の失敗の `None` と分ける。#1354）。"""
    fake.on("api", "-i", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100&page=1", out=_rest_out({"total_count": 0, "check_runs": []}))

    assert gp.fetch_check_runs(REPO, SHA) is None
    assert gp.fetch_check_runs(REPO, SHA, empty_ok=True) == []


def test_checks_outcome_waits_for_checks_that_are_not_listed_yet():
    """待つチェックが一覧にまだ無いのは `pending`。照会できないときだけ `None`（#1354）。"""
    import gh_checks

    assert gh_checks.checks_outcome(None, ["t"]) is None
    assert gh_checks.checks_outcome([], ["t"]) == "pending"
    assert gh_checks.checks_outcome([_run("other", "success", "", 1)], ["t"]) == "pending"
    assert gh_checks.checks_outcome([_run("t", "success", "", 1), _run("u", "", "", 2, status="in_progress")], ["t", "u"]) == "pending"
    assert gh_checks.checks_outcome([_run("t", "success", "", 1), _run("u", "failure", "", 2)], ["t", "u"]) == "failure"
    assert gh_checks.checks_outcome([_run("t", "success", "", 1)], ["t"]) == "success"


def test_checks_outcome_edge_cases_are_fixed():
    """現状固定（I-007）: 待つ名前が空・再実行・結論の順・空の名前・大文字・重複の扱い。"""
    import gh_checks

    t_ok, u_fail, v_cancel = _run("t", "SUCCESS", "", 1), _run("u", "failure", "", 2), _run("v", "cancelled", "", 3)
    assert gh_checks.checks_outcome([], []) == "success"
    assert gh_checks.checks_outcome([u_fail], []) == "success"
    assert gh_checks.checks_outcome([t_ok], ["t", "t"]) == "success"
    assert gh_checks.checks_outcome([t_ok, u_fail, v_cancel], ["t", "v", "u"]) == "cancelled"
    assert gh_checks.checks_outcome([t_ok, u_fail, v_cancel], ["t", "u", "v"]) == "failure"
    assert gh_checks.checks_outcome([t_ok, v_cancel, _run("w", "", "", 4, status="queued")], ["v", "w"]) == "pending"
    assert gh_checks.checks_outcome([t_ok, _run("x", "", "", 5)], ["x"]) == "unknown"
    assert gh_checks.checks_outcome([t_ok], ["t", ""]) == "pending"
    rerun = [_run("u", "failure", "2026-01-01T00:00:00Z", 2), _run("u", "success", "2026-01-01T00:05:00Z", 6)]
    assert gh_checks.checks_outcome(rerun, ["u"]) == "success"
    assert gh_checks.checks_outcome(list(reversed(rerun)), ["u"]) == "success"


# ---------------- unresolved-threads ----------------


def test_unresolved_threads_carry_thread_id(fake):
    fake.on("api", "graphql", out="PRRT_a\tsrc/foo.py\t42\t[major] 空を弾く\nPRRT_b\tdocs/bar.md\t\t\n")

    threads = gp.unresolved_threads(REPO, PR)

    assert threads == [
        {"thread_id": "PRRT_a", "path": "src/foo.py", "line": "42", "body": "[major] 空を弾く"},
        {"thread_id": "PRRT_b", "path": "docs/bar.md", "line": "", "body": ""},
    ]
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


def test_pr_info_with_all_parts_is_fixed(fake, tmp_path):
    """現状固定（I-002）: diff → checks → threads の順に items と notes が並ぶ。"""
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "-H", out="diff --git a/x b/x\n")
    fake.on("api", "graphql", out="PRRT_a\tsrc/foo.py\tx\n")
    fake.on("api", "-i", out=_checks(_run("pytest", "failure", "2026-09-25T01:00:00Z", 101)))

    obj, code = gp.pr_info(PR, REPO, {"diff", "checks", "threads"}, tmp_path)

    assert code == 0 and obj["status"] == "ok"
    assert [i["kind"] for i in obj["items"]] == ["pr", "diff", "check", "thread"]
    assert obj["items"][0]["name"] == f"#{PR}" and obj["items"][0]["repo"] == REPO and obj["items"][0]["result"] == "open"
    assert obj["items"][1] == {"kind": "diff", "name": "diff", "result": "saved", "path": str(tmp_path / "pr.diff")}
    assert "log_path" not in obj["items"][2]
    assert obj["items"][3]["line"] is None
    assert obj["metrics"] == {
        "source": "graphql",
        "additions": 10,
        "deletions": 2,
        "changed_files": 3,
        "failed_checks": 1,
        "pending_checks": 0,
        "superseded_checks": 0,
        "unresolved_threads": 1,
    }
    assert obj["summary"] == f"PR #{PR} open（graphql） / checks 失敗 1 / 保留 0 / 未解決 1"


def test_pr_info_with_every_part_unavailable_is_fixed(fake, tmp_path):
    """現状固定（I-002）: 取得できない部分は diff → checks → threads の順に unavailable へ入る。"""
    fake.on("pr", "view", out=json.dumps(GRAPHQL_PR))
    fake.on("api", "-H", rc=1, err="HTTP 500\n")
    fake.on("api", "graphql", rc=1, err="boom")
    fake.on("api", "-i", rc=1, err="HTTP 422")

    obj, code = gp.pr_info(PR, REPO, {"diff", "logs", "threads"}, tmp_path)

    assert code == 0 and obj["status"] == "ok"
    assert obj["items"][1] == {
        "kind": "diff",
        "name": "diff",
        "result": "unavailable",
        "path": None,
        "reason": "差分を取得できない: HTTP 500",
    }
    assert [i["kind"] for i in obj["items"]] == ["pr", "diff"]
    assert obj["metrics"]["unavailable"] == ["diff", "checks", "threads"]
    assert obj["metrics"]["failed_checks"] is None and obj["metrics"]["pending_checks"] is None
    assert obj["metrics"]["unresolved_threads"] is None
    assert obj["summary"] == f"PR #{PR} open（graphql） / 取得できない: diff, checks, threads"


def test_pr_info_stops_when_the_repo_cannot_be_resolved(fake, tmp_path, monkeypatch):
    """現状固定（I-002）: リポジトリを決められなければ前提の不足で止まる。"""
    monkeypatch.setattr(gh_call, "resolve_repo", lambda repo=None: None)

    obj, code = gp.pr_info(PR, None, set(), tmp_path)

    assert (obj["status"], code, obj["summary"]) == ("stopped", gp.step_result.EXIT_PRECONDITION, "リポジトリを決められない")
    assert obj["items"] == [] and obj["metrics"] == {}
    assert fake.calls == []


def test_pr_info_stop_summary_carries_the_reason_and_source(fake, tmp_path):
    """現状固定（I-002）: メタを読めないときは理由と取得元を結果に残す。"""
    fake.on("pr", "view", rc=1, err=RATE)
    fake.on("api", "-i", f"repos/{REPO}/pulls/{PR}", out=_rest_out({}))

    obj, code = gp.pr_info(PR, REPO, {"diff"}, tmp_path)

    assert (obj["status"], code) == ("stopped", gp.step_result.EXIT_UNREADABLE)
    assert obj["summary"] == f"PR #{PR} を取得できない: GraphQL が上限で、REST でも取得できない"
    assert obj["metrics"] == {"source": "rest"} and obj["items"] == []
    assert not any("-H" in a for a, _ in fake.calls)


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
    body = "## 進行状況\n\n別の節\n\n```md\n## 進行\n```\n"
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
        seen.update(repo=repo, pr=pr, head_sha=head_sha, is_own_pr=is_own_pr, since=kw.get("since"))
        return result_posts.ReviewOutcome(
            review_url="https://github.com/o/r/pull/812#pullrequestreview-1",
            posted_inline=2,
            posted_body=0,
            queued=0,
            findings=2,
            failed=False,
            posted_as="COMMENT" if is_own_pr else "REQUEST_CHANGES",
            intent="REQUEST_CHANGES",
            detail="",
        )

    monkeypatch.setattr(result_posts, "post_review", _post)

    obj, code = gp.review_post("p.json", "r.json", PR, 1, "codex", REPO, queue_dir=str(tmp_path / "q"))

    since = seen.pop("since")
    assert code == 0 and seen == {"repo": REPO, "pr": PR, "head_sha": SHA, "is_own_pr": own}
    # 入口の時刻（タイムゾーン付き）を since に渡し、前の実行の同じラウンド・席のレビューを「既投稿」にしない
    assert clock.parse(since, naive="reject") is not None and (clock.now(utc=True) - clock.parse(since)).total_seconds() < 60
    assert obj["items"][0]["posted_as"] == ("COMMENT" if own else "REQUEST_CHANGES")
    assert obj["metrics"]["is_own_pr"] is own


def test_review_post_stops_when_the_viewer_is_unknown(fake, tmp_path):
    fake.on("api", "-i", f"repos/{REPO}/pulls/{PR}", out=_rest_out(REST_PR))
    fake.on("api", "user", rc=1, err="HTTP 401")

    obj, code = gp.review_post("p.json", "r.json", PR, 1, "codex", REPO, queue_dir=str(tmp_path / "q"))

    assert (obj["status"], code) == ("stopped", 3)


def test_fold_takes_the_newer_of_completed_and_started_times():
    """新しさは `completed_at` と `started_at` の新しい方で決める（#632）。

    長く走って後に終わった失敗は、後に始まって先に終わった成功より新しい。
    """
    long_failure = {
        "id": 1,
        "name": "t",
        "status": "completed",
        "conclusion": "failure",
        "started_at": "2026-09-25T01:00:00Z",
        "completed_at": "2026-09-25T03:00:00Z",
    }
    short_success = {
        "id": 2,
        "name": "t",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-09-25T02:00:00Z",
        "completed_at": "2026-09-25T02:10:00Z",
    }
    assert gp.check_result([long_failure, short_success], "t") == "failure"
    assert gp.check_result([short_success, long_failure], "t") == "failure"


# ---------------- view-json（gh pr view / gh issue view の --json） ----------------

REST_MERGED = {
    "number": 1210,
    "title": "Release",
    "body": "本文",
    "state": "closed",
    "merged_at": "2026-09-26T03:52:01Z",
    "merged": True,
    "merge_commit_sha": "8a0cdaa7",
    "html_url": "https://github.com/o/r/pull/1210",
}


def test_view_json_returns_graphql_output_as_is(fake):
    fake.on("pr", "view", out=json.dumps({"title": "t", "state": "MERGED"}))
    r = gp.view_json("pr", 5, "title,state")
    assert r.returncode == 0 and json.loads(r.stdout) == {"title": "t", "state": "MERGED"}
    assert fake.argvs() == ["pr view 5 --json title,state"]


def test_view_json_reads_rest_in_graphql_shape_when_rate_limited(fake):
    fake.on("pr", "view", rc=1, err=RATE)
    fake.on("api", out=json.dumps(REST_MERGED))
    r = gp.view_json("pr", 1210, "number,title,state,mergeCommit,url,body")
    assert r.returncode == 0
    assert json.loads(r.stdout) == {
        "number": 1210,
        "title": "Release",
        "state": "MERGED",
        "mergeCommit": {"oid": "8a0cdaa7"},
        "url": "https://github.com/o/r/pull/1210",
        "body": "本文",
    }
    assert fake.argvs()[-1] == "api repos/{owner}/{repo}/pulls/1210"


@pytest.mark.parametrize(
    "rest, state, merge",
    [
        ({"state": "open", "merged_at": None, "merge_commit_sha": "x"}, "OPEN", None),
        ({"state": "closed", "merged_at": None, "merge_commit_sha": "x"}, "CLOSED", None),
    ],
)
def test_view_json_rest_state_of_unmerged_pr(fake, rest, state, merge):
    fake.on("pr", "view", rc=1, err=RATE)
    fake.on("api", out=json.dumps({"number": 5, **rest}))
    d = json.loads(gp.view_json("pr", 5, "state,mergeCommit", repo=REPO).stdout)
    assert d == {"state": state, "mergeCommit": merge}
    assert fake.argvs() == [f"pr view 5 --repo {REPO} --json state,mergeCommit", f"api repos/{REPO}/pulls/5"]


def test_view_json_issue_reads_issues_endpoint(fake):
    fake.on("issue", "view", rc=1, err=RATE)
    fake.on(
        "api",
        out=json.dumps({"number": 7, "title": "課題", "state": "closed", "body": None, "html_url": "https://github.com/o/r/issues/7"}),
    )
    d = json.loads(gp.view_json("issue", 7, "title,state,body,url").stdout)
    assert d == {"title": "課題", "state": "CLOSED", "body": "", "url": "https://github.com/o/r/issues/7"}
    assert fake.argvs()[-1] == "api repos/{owner}/{repo}/issues/7"


def test_view_json_unknown_field_returns_the_original_error(fake):
    fake.on("pr", "view", rc=1, err=RATE)
    r = gp.view_json("pr", 5, "title,headRefName")
    assert (r.returncode, r.stderr) == (1, RATE)
    assert not any(a.startswith("api") for a in fake.argvs())


def test_view_json_other_failure_is_not_retried_on_rest(fake):
    fake.on("pr", "view", rc=1, err="GraphQL: Could not resolve to a PullRequest")
    r = gp.view_json("pr", 5, "title")
    assert r.returncode == 1 and "Could not resolve" in r.stderr
    assert len(fake.calls) == 1


def test_view_json_rest_failure_keeps_both_errors(fake):
    fake.on("pr", "view", rc=1, err=RATE)
    fake.on("api", rc=1, err="HTTP 403: API rate limit exceeded")
    r = gp.view_json("pr", 5, "title")
    assert r.returncode == 1 and RATE in r.stderr and "HTTP 403" in r.stderr
