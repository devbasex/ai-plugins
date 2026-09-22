"""結果ファイルを投稿へ変える層（#730 #583）。

担当が書いた指摘の控えと結果ファイルを読み、GitHub へ送る投稿を組み立てる。
**本文は引数にも標準出力にも出さない。** 受け取るのはファイルのパスだけで、本文は
この層の中だけを通る。

| 何を確かめるか | 受け入れ条件 |
| --- | --- |
| 控えと結果からレビューの投稿が組み立つ | AC5 |
| 本文が引数に現れない | AC5 |
| 自分の Pull Request では送った形だけを落とす | AC32 |
| 位置を解決できない拒まれ方で、インラインを総評へ退避する | AC16 |
| 同じ状態の別の拒まれ方は退避せず失敗として残す | AC16 |
| インラインが 0 件でも結果なしにしない | AC17 |
| 返信・決着・まとめが積まれる | AC7 |
| 送信は現在の頭を指定し、載ったことを確かめる | AC8・AC9 |
"""
from __future__ import annotations

import inspect
import json
import os
import pathlib
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import post_queue  # noqa: E402
import result_posts  # noqa: E402

REPO = "o/r"
PR = 730
ROUND = 3
SEAT = "codex"
SHA = "1" * 40
ACTOR = "takemi"


# ---------------- 偽の `gh` ----------------

_FAKE_GH = '''#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
joined = " ".join(argv)
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
log = os.environ["GH_FAKE_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": argv, "stdin": stdin}, ensure_ascii=False) + "\\n")
rules_file = os.environ.get("GH_FAKE_RULES")
rules = json.load(open(rules_file, encoding="utf-8")) if rules_file else []
prior = 0
with open(log, encoding="utf-8") as f:
    prior = sum(1 for line in f if line.strip()) - 1
for rule in rules:
    if "calls_lt" in rule and prior >= int(rule["calls_lt"]):
        continue
    if rule.get("match", "") in joined:
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(int(rule.get("exit", 0)))
sys.stdout.write("[]")
'''


class FakeGh:
    def __init__(self, log: pathlib.Path, rules: pathlib.Path, monkeypatch) -> None:
        self.log, self.rules, self._mp = log, rules, monkeypatch

    def set_rules(self, rules: list[dict]) -> None:
        self.rules.write_text(json.dumps(rules), encoding="utf-8")
        self._mp.setenv("GH_FAKE_RULES", str(self.rules))

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def joined(self) -> list[str]:
        return [" ".join(c["argv"]) for c in self.calls()]

    def sent(self) -> list[dict]:
        """状態を変える呼び出しの本文だけを取り出す。"""
        out = []
        for c in self.calls():
            if "--method POST" in " ".join(c["argv"]) and c["stdin"]:
                out.append(json.loads(c["stdin"]))
        return out


@pytest.fixture()
def fake_gh(monkeypatch, tmp_path) -> FakeGh:
    bindir = tmp_path / "fake-bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / "gh"
    script.write_text(_FAKE_GH, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("GH_FAKE_LOG", str(tmp_path / "gh-calls.log"))
    monkeypatch.delenv("GH_FAKE_RULES", raising=False)
    return FakeGh(tmp_path / "gh-calls.log", tmp_path / "gh-rules.json", monkeypatch)


# ---------------- 控えと結果ファイル ----------------

def _files(tmp_path: pathlib.Path, comments: list[dict] | None = None,
           summary: str = "設計の筋は通っている。", event: str = "REQUEST_CHANGES",
           ) -> tuple[pathlib.Path, pathlib.Path]:
    payload = tmp_path / f"{SEAT}-review-pr{PR}-round{ROUND}-payload.json"
    result = tmp_path / f"{SEAT}-review-pr{PR}-result.json"
    if comments is None:
        comments = [
            {"path": "a.py", "line": 12, "body": "[major / 正確性] 戻り値を確かめる",
             "severity": "major"},
            {"path": "b.py", "line": 34, "body": "[minor / 可読性] 名前を揃える",
             "severity": "minor"},
        ]
    payload.write_text(json.dumps({"summary": summary, "comments": comments},
                                  ensure_ascii=False), encoding="utf-8")
    result.write_text(json.dumps(
        {"event": event, "by_severity": {"critical": 0, "major": 1, "minor": 1, "nit": 0}},
        ensure_ascii=False), encoding="utf-8")
    return payload, result


def _review_items(tmp_path, **kw):
    payload, result = _files(tmp_path, **kw)
    return result_posts.review_posts(
        payload, result, repo=REPO, pr=PR, round_no=ROUND, seat=SEAT,
        head_sha=SHA, is_own_pr=False)


# ---------------- 組み立て ----------------

def test_a_review_is_built_from_the_note_and_the_result(tmp_path) -> None:
    items = _review_items(tmp_path)

    assert [i["kind"] for i in items] == ["review-post"]
    fields = items[0]["fields"]
    assert fields["event"] == "REQUEST_CHANGES"
    assert fields["commit_id"] == SHA
    assert [c["path"] for c in fields["comments"]] == ["a.py", "b.py"]


def test_the_first_line_carries_the_round_and_the_seat(tmp_path) -> None:
    body = _review_items(tmp_path)[0]["fields"]["body"]

    assert body.splitlines()[0] == \
        f"## 🤖 cross-review | round {ROUND} | {SEAT} | REQUEST_CHANGES"
    assert post_queue.review_match_key(body) == \
        f"## 🤖 cross-review | round {ROUND} | {SEAT}|"


def test_the_body_is_never_an_argument() -> None:
    """本文は引数として渡さない。どの関数もファイルのパスを受け取る。"""
    for fn in (result_posts.review_posts, result_posts.fix_posts):
        names = list(inspect.signature(fn).parameters)
        assert "body" not in names and "payload" not in names
    assert "payload_path" in inspect.signature(result_posts.review_posts).parameters


def test_only_what_is_sent_is_downgraded_on_ones_own_pull_request(tmp_path) -> None:
    """自分の Pull Request では送った形だけを落とし、本来の判定は落とさない（AC32）。"""
    payload, result = _files(tmp_path)
    items = result_posts.review_posts(
        payload, result, repo=REPO, pr=PR, round_no=ROUND, seat=SEAT,
        head_sha=SHA, is_own_pr=True)

    fields = items[0]["fields"]
    assert fields["event"] == "COMMENT"
    assert fields["body"].splitlines()[0].endswith("| REQUEST_CHANGES")
    assert items[0]["extra"]["intent"] == "REQUEST_CHANGES"
    assert items[0]["extra"]["posted_as"] == "COMMENT"


def test_a_finding_without_a_position_goes_to_the_summary(tmp_path) -> None:
    """位置を持たない指摘は、指す先が無いので総評へ入れる。"""
    items = _review_items(tmp_path, comments=[
        {"body": "[major / 設計] 層の分け方を見直す", "severity": "major"},
        {"path": "a.py", "line": 12, "body": "[minor / 可読性] 名前を揃える",
         "severity": "minor"},
    ])

    fields = items[0]["fields"]
    assert [c["path"] for c in fields["comments"]] == ["a.py"]
    assert "層の分け方を見直す" in fields["body"]
    assert items[0]["extra"]["inline"] == 1
    assert items[0]["extra"]["body"] == 1


# ---------------- 送信と退避 ----------------

def _queue(tmp_path) -> post_queue.Queue:
    return post_queue.Queue(tmp_path / "pending")


def _post_review(tmp_path, **kw):
    payload, result = _files(tmp_path, **kw)
    return result_posts.post_review(
        _queue(tmp_path), payload, result, repo=REPO, pr=PR, round_no=ROUND,
        seat=SEAT, head_sha=SHA, is_own_pr=False, actor=ACTOR), payload


_REJECT_POSITION = {
    "match": "pulls/730/reviews", "exit": 1,
    "stdout": json.dumps({"message": "Unprocessable Entity",
                          "errors": ["Line could not be resolved"], "status": "422"}),
    "stderr": "gh: Unprocessable Entity (HTTP 422)\n",
}
_REJECT_EVENT = {
    "match": "pulls/730/reviews", "exit": 1,
    "stdout": json.dumps({"message": "Unprocessable Entity",
                          "errors": ["Variable $event of type PullRequestReviewEvent"
                                     " was provided invalid value"], "status": "422"}),
    "stderr": "gh: Unprocessable Entity (HTTP 422)\n",
}
_ACCEPT = {"match": "pulls/730/reviews", "stdout": json.dumps(
    {"id": 99, "html_url": "https://x/pull/730#pullrequestreview-99"})}
_RATE_LIMITED = {
    "match": "pulls/730/reviews", "exit": 1,
    "stdout": json.dumps({"message": "API rate limit exceeded"}),
    "stderr": "gh: API rate limit exceeded (HTTP 429)\n",
}


def test_the_inlines_move_to_the_summary_when_the_position_is_not_resolved(
        tmp_path, fake_gh) -> None:
    fake_gh.set_rules([
        {"match": "pulls/730/reviews?", "stdout": "[]"},
        dict(_REJECT_POSITION, calls_lt=3),
        _ACCEPT,
    ])

    outcome, payload = _post_review(tmp_path)

    assert outcome.review_url == "https://x/pull/730#pullrequestreview-99"
    assert outcome.posted_inline == 0
    assert outcome.posted_body == 2
    assert outcome.queued == 0
    # 2 度目の要求はインラインを持たず、指摘は総評に入る。
    last = fake_gh.sent()[-1]
    assert "comments" not in last
    assert "戻り値を確かめる" in last["body"]
    # 控えには送れた先が残る。
    note = json.loads(payload.read_text(encoding="utf-8"))
    assert [c["posted_to"] for c in note["comments"]] == ["body", "body"]


def test_another_rejection_of_the_same_status_is_not_moved(tmp_path, fake_gh) -> None:
    """判定の値の誤りは退避の契機にしない。失敗として残す。"""
    fake_gh.set_rules([
        {"match": "pulls/730/reviews?", "stdout": "[]"},
        _REJECT_EVENT,
    ])

    outcome, _ = _post_review(tmp_path)

    assert outcome.failed is True
    assert outcome.review_url is None


def test_a_rate_limited_review_remains_queued_without_marking_the_note(
        tmp_path, fake_gh) -> None:
    """現状固定。上限時は失敗にせず、未投稿の要求と控えをそのまま残す。"""
    fake_gh.set_rules([
        {"match": "pulls/730/reviews?", "stdout": "[]"},
        _RATE_LIMITED,
    ])

    outcome, payload = _post_review(tmp_path)

    assert outcome.queued == 1
    assert outcome.failed is False
    assert outcome.review_url is None
    assert outcome.posted_inline == 0
    assert outcome.posted_body == 0
    note = json.loads(payload.read_text(encoding="utf-8"))
    assert all("posted_to" not in comment for comment in note["comments"])
    queued = _queue(tmp_path).items()
    assert len(queued) == 1
    assert queued[0][1]["kind"] == "review-post"
    assert queued[0][1]["attempts"] == 1


def test_a_review_that_is_already_on_github_is_not_posted_again(
        tmp_path, fake_gh) -> None:
    """投稿の後・記録の前に止まった実行をやり直しても、レビューは増えない（AC12）。"""
    head = f"## 🤖 cross-review | round {ROUND} | {SEAT} | APPROVE"
    fake_gh.set_rules([
        {"match": "pulls/730/reviews?",
         "stdout": json.dumps([{"user": {"login": ACTOR}, "state": "APPROVED",
                                "body": head + "\n\n先客", "id": 42,
                                "html_url": "https://x/pull/730#pullrequestreview-42"}])},
    ])

    outcome, _ = _post_review(tmp_path)

    assert outcome.review_url == "https://x/pull/730#pullrequestreview-42"
    assert [c for c in fake_gh.joined() if "--method POST" in c] == []


def test_a_note_with_findings_is_not_a_missing_result(tmp_path, fake_gh) -> None:
    """インラインとして送れたものが 0 件でも、その担当は結果なしにならない（AC17）。"""
    fake_gh.set_rules([
        {"match": "pulls/730/reviews?", "stdout": "[]"},
        dict(_REJECT_POSITION, calls_lt=3),
        _ACCEPT,
    ])

    outcome, _ = _post_review(tmp_path)

    assert outcome.findings == 2
    assert outcome.failed is False


# ---------------- 修正の投稿 ----------------

def _fix_file(tmp_path, **over) -> pathlib.Path:
    data = {
        "pr": PR,
        "fix_commit": "abc1234",
        "ci_status": "SUCCESS",
        "fixed_count": 2,
        "by_severity": {"critical": 0, "major": 2, "minor": 0, "nit": 0},
        "resolved_threads": [
            {"thread_id": "PRRT_a", "comment_id": 111, "path": "a.py", "line": 12},
            {"thread_id": "PRRT_b", "comment_id": 222, "path": "b.py", "line": 34},
        ],
        "deferred": [
            {"comment_id": 333, "thread_id": "PRRT_c", "severity": "nit",
             "summary": "末尾の書き方", "reason_for_deferral": "好みの範囲"},
        ],
        "rejected": [
            {"comment_id": 444, "path": "c.py", "line": 7, "severity": "minor",
             "summary": "引用の形", "reason_for_rejection": "意図して展開している"},
        ],
    }
    data.update(over)
    path = tmp_path / f"fix-pr{PR}-result.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_take_in_of_a_fix_builds_replies_resolves_and_a_summary(tmp_path) -> None:
    items = result_posts.fix_posts(_fix_file(tmp_path), repo=REPO, pr=PR,
                                   round_no=ROUND)

    kinds = [i["kind"] for i in items]
    assert kinds.count("review-reply") == 4      # 決着 2 + 見送り 1 + 却下 1
    assert kinds.count("thread-resolve") == 2
    assert kinds.count("pr-comment") == 1
    assert kinds[-1] == "pr-comment"             # まとめは最後


def test_the_summary_carries_the_round_in_the_head_of_the_body(tmp_path) -> None:
    """同じラウンドのまとめを 2 度積んでも増えないよう、鍵になる先頭へ入れる（AC14）。"""
    items = result_posts.fix_posts(_fix_file(tmp_path), repo=REPO, pr=PR,
                                   round_no=ROUND)
    body = [i for i in items if i["kind"] == "pr-comment"][0]["fields"]["body"]

    assert f"round {ROUND}" in body[:post_queue.BODY_MATCH_CHARS]
    assert "abc1234" in body[:post_queue.BODY_MATCH_CHARS]


def test_a_reply_points_at_the_comment_it_answers(tmp_path) -> None:
    items = result_posts.fix_posts(_fix_file(tmp_path), repo=REPO, pr=PR,
                                   round_no=ROUND)
    targets = sorted(i["fields"]["in_reply_to"] for i in items
                     if i["kind"] == "review-reply")

    assert targets == [111, 222, 333, 444]


def test_a_fix_without_threads_still_posts_the_summary(tmp_path) -> None:
    items = result_posts.fix_posts(
        _fix_file(tmp_path, resolved_threads=[], deferred=[], rejected=[]),
        repo=REPO, pr=PR, round_no=ROUND)

    assert [i["kind"] for i in items] == ["pr-comment"]


def test_fix_posts_skips_replies_with_non_numeric_comment_ids(tmp_path) -> None:
    """現状固定。不正な返信先を飛ばしても、決着とまとめは組み立てる。"""
    items = result_posts.fix_posts(
        _fix_file(
            tmp_path,
            resolved_threads=[{"comment_id": "invalid", "thread_id": "PRRT_a"}],
            deferred=[{"comment_id": "invalid", "thread_id": "PRRT_b"}],
            rejected=[{"comment_id": "invalid", "thread_id": "PRRT_c"}],
        ),
        repo=REPO,
        pr=PR,
        round_no=ROUND,
    )

    assert [item["kind"] for item in items] == ["thread-resolve", "pr-comment"]
    assert items[0]["fields"] == {"thread_id": "PRRT_a"}
    assert items[0]["extra"] == {"ident": "resolve-PRRT_a"}
    assert items[1]["extra"] == {"ident": f"fix-summary-{ROUND}"}
    assert "決着: 1 件 / 見送り: 1 件 / 却下: 1 件" in items[1]["fields"]["body"]


# ---------------- 送信 ----------------

def _repo_with_remote(tmp_path) -> tuple[pathlib.Path, pathlib.Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)],
                   check=True, capture_output=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(work), "config", key, value], check=True)
    (work / "a.txt").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "1"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "push", "origin", "main"],
                   check=True, capture_output=True)
    return work, remote


def _head(work: pathlib.Path) -> str:
    return subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()


def test_the_push_names_the_current_head(tmp_path) -> None:
    """切り離された頭でも現在の頭が送られる（AC8）。"""
    work, remote = _repo_with_remote(tmp_path)
    subprocess.run(["git", "-C", str(work), "checkout", "--detach"],
                   check=True, capture_output=True)
    (work / "a.txt").write_text("2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "commit", "-am", "2"],
                   check=True, capture_output=True)
    commit = _head(work)

    outcome = result_posts.push_fix(work, "main", commit)

    assert outcome.ok is True and outcome.contains is True
    remote_head = subprocess.run(
        ["git", "-C", str(remote), "rev-parse", "refs/heads/main"],
        check=True, capture_output=True, text=True).stdout.strip()
    assert remote_head == commit


def test_the_push_fails_when_the_reported_commit_is_not_on_the_branch(tmp_path) -> None:
    """報告されたコミットが送り先に載っていなければ止まる（AC9）。"""
    work, _ = _repo_with_remote(tmp_path)

    outcome = result_posts.push_fix(work, "main", "0" * 40)

    assert outcome.ok is False and outcome.contains is False


def test_nothing_is_pushed_when_no_commit_was_made(tmp_path) -> None:
    work, _ = _repo_with_remote(tmp_path)

    outcome = result_posts.push_fix(work, "main", None)

    assert outcome.ok is True and outcome.pushed is False


def test_the_push_fails_without_a_destination() -> None:
    outcome = result_posts.push_fix("", "", "abc1234")

    assert outcome.ok is False and outcome.pushed is False


def test_a_rejected_push_stops_before_checking_the_branch(tmp_path) -> None:
    """送信そのものが拒まれたら、載ったかを確かめずに理由を残して止まる。"""
    work, _ = _repo_with_remote(tmp_path)
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin",
                    str(tmp_path / "missing.git")], check=True)

    outcome = result_posts.push_fix(work, "main", _head(work))

    assert outcome[:3] == (False, False, False)
    assert outcome.detail != ""


# ---------------- 単独で使う口 ----------------

def test_the_standalone_command_pushes_and_posts_with_the_same_layer(
        tmp_path, fake_gh, monkeypatch) -> None:
    """単独の `fix` も同じ層を使い、1 行のコマンドで送信と投稿を終える（AC21・AC22）。"""
    work, remote = _repo_with_remote(tmp_path)
    (work / "a.txt").write_text("2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "commit", "-am", "2"],
                   check=True, capture_output=True)
    fix = _fix_file(tmp_path, fix_commit=_head(work))
    fake_gh.set_rules([
        {"match": "issues/730/comments?", "stdout": "[]"},
        {"match": "pulls/730/comments?", "stdout": "[]"},
        {"match": "reviewThreads", "stdout": "PRRT_a\nPRRT_b\n"},
        {"match": "issues/730/comments", "stdout": json.dumps(
            {"id": 7, "html_url": "https://x/pull/730#issuecomment-7"})},
        {"match": "", "stdout": "{}"},
    ])
    monkeypatch.delenv("CROSS_REVIEW_TMP_DIR", raising=False)

    r = subprocess.run(
        [sys.executable, str(LIB / "result_posts.py"), "fix", "--repo", REPO,
         "--pr", str(PR), "--result", str(fix), "--head", "main",
         "--worktree", str(work), "--round", str(ROUND), "--actor", ACTOR],
        capture_output=True, text=True, env=os.environ.copy())

    assert r.returncode == 0, r.stderr
    assert "PUSHED=1 COMMIT_ON_HEAD=1" in r.stdout
    assert "POSTED summary_url=https://x/pull/730#issuecomment-7" in r.stdout
    assert "REPLIED=4 RESOLVED=2 QUEUED=0" in r.stdout
    # 本文は出さない。
    assert "好みの範囲" not in r.stdout


def test_a_deferred_thread_marked_to_resolve_is_resolved(tmp_path) -> None:
    """最終スイープは見送りも決着させる。要素の `resolve` が真なら決着を積む。"""
    items = result_posts.fix_posts(
        _fix_file(tmp_path, resolved_threads=[], rejected=[], deferred=[
            {"comment_id": 333, "thread_id": "PRRT_c", "resolve": True,
             "reason_for_deferral": "好みの範囲"}]),
        repo=REPO, pr=PR)

    assert [i["kind"] for i in items] == ["review-reply", "thread-resolve", "pr-comment"]
    assert items[1]["fields"]["thread_id"] == "PRRT_c"


def test_a_deferred_thread_is_not_resolved_by_default(tmp_path) -> None:
    items = result_posts.fix_posts(
        _fix_file(tmp_path, resolved_threads=[], rejected=[]), repo=REPO, pr=PR)

    assert "thread-resolve" not in [i["kind"] for i in items]
