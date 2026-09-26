"""Pull Request のメタデータを REST の 1 回で取る（#271）。

初期化は作成者・head・base を項目ごとに `gh pr view` で取っており、GraphQL 側の枠を
実行ごと 4 点使っていた。**尽きるのは GraphQL 側である。** REST の 1 回の応答から
同じ項目がすべて取れるので、そちらへ移す。

リポジトリ名は git の設定から求める。`repos/{owner}/{repo}/pulls/{PR}` がそのまま
検証になるため、誤った名前のまま進む経路はできない。
"""
from __future__ import annotations

import json

import pytest
import review_lib
import review_lib.ci
import review_lib.github
import gh_call  # review_lib が sys.path に足したライブラリの置き場から読む

PR = 320
REPO = "devbasex/ai-plugins"

# 実測した応答（`gh api repos/devbasex/ai-plugins/pulls/320`）を、使う項目だけへ縮めた形。
PULL_BODY = {
    "number": PR,
    "user": {"login": "takemi-ohama"},
    "head": {"ref": "feature/x", "sha": "b87b3ae", "repo": {"full_name": REPO}},
    "base": {"ref": "develop"},
    "changed_files": 12,
}

# `gh api -i` の実測の形。状態行だけ `\r` を持たず、ヘッダは `\r\n` で終わる。
RAW = (
    "HTTP/2.0 200 OK\n"
    "Content-Type: application/json; charset=utf-8\r\n"
    "X-Ratelimit-Limit: 5000\r\n"
    "X-Ratelimit-Remaining: 4972\r\n"
    "X-Ratelimit-Reset: 1788519069\r\n"
    "X-Ratelimit-Resource: core\r\n"
    "\r\n"
    + json.dumps(PULL_BODY)
)


def _response(state_mod, body=None, remaining=None):
    return review_lib.github.RestResponse(
        headers={}, body=PULL_BODY if body is None else body,
        rate_remaining=remaining, rate_reset=None,
    )


# ---------------- リポジトリ名を git から求める ----------------

@pytest.mark.parametrize("url", [
    "https://github.com/devbasex/ai-plugins.git",
    "https://github.com/devbasex/ai-plugins",
    "git@github.com:devbasex/ai-plugins.git",
    "ssh://git@github.com/devbasex/ai-plugins.git",
])
def test_the_repository_name_comes_from_the_git_remote(state_mod, monkeypatch, url):
    monkeypatch.setattr(review_lib.github, "_git_remote_url", lambda: url)

    assert review_lib.github._repo_from_git() == REPO


def test_a_remote_on_another_host_also_gives_the_name(state_mod, monkeypatch):
    """URL の読み取りはライブラリの `repo.owner_repo_from_url` が持ち、ホストを github.com に限らない（#1142 の D2）。

    名前が誤っていれば `repos/{owner}/{repo}/pulls/{PR}` の応答が失敗し、`gh repo view` で解決し直す。
    """
    monkeypatch.setattr(review_lib.github, "_git_remote_url", lambda: "git@ghe.example.com:devbasex/ai-plugins.git")

    assert review_lib.github._repo_from_git() == REPO


def test_an_unreadable_remote_gives_no_name(state_mod, monkeypatch):
    monkeypatch.setattr(review_lib.github, "_git_remote_url", lambda: "")

    assert review_lib.github._repo_from_git() is None


# ---------------- 1 回の応答から全部を埋める ----------------

def test_one_rest_response_fills_author_head_and_base(state_mod, real_github, monkeypatch):
    paths: list[str] = []
    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(review_lib.github, "_gh_rest", lambda p: (paths.append(p), _response(state_mod))[1])
    monkeypatch.setattr(review_lib.github, "_repo_from_gh", lambda: pytest.fail("GraphQL へ落ちてはならない"))

    meta = review_lib.github._fetch_pr_metadata(PR)

    assert paths == [f"repos/{REPO}/pulls/{PR}"]
    assert meta.repo == REPO
    assert meta.author == "takemi-ohama"
    assert meta.head_branch == "feature/x"
    assert meta.head_sha == "b87b3ae"
    assert meta.base_branch == "develop"
    assert meta.is_fork is False


def test_a_fork_pull_request_is_detected_from_the_same_response(state_mod, real_github, monkeypatch):
    body = json.loads(json.dumps(PULL_BODY))
    body["head"]["repo"]["full_name"] = "someone/ai-plugins"
    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(review_lib.github, "_gh_rest", lambda p: _response(state_mod, body))

    assert review_lib.github._fetch_pr_metadata(PR).is_fork is True


def test_a_wrong_repository_name_falls_back_to_gh_repo_view(state_mod, real_github, monkeypatch):
    """求めた名前が誤っていれば応答が失敗する。そのときだけ解決し直す。"""
    paths: list[str] = []

    def _rest(path):
        paths.append(path)
        return _response(state_mod) if path == f"repos/{REPO}/pulls/{PR}" else None

    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: "wrong/name")
    monkeypatch.setattr(review_lib.github, "_gh_rest", _rest)
    monkeypatch.setattr(review_lib.github, "_repo_from_gh", lambda: REPO)

    meta = review_lib.github._fetch_pr_metadata(PR)

    assert paths == [f"repos/wrong/name/pulls/{PR}", f"repos/{REPO}/pulls/{PR}"]
    assert meta.repo == REPO


def test_an_unreachable_pull_request_gives_nothing(state_mod, real_github, monkeypatch):
    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(review_lib.github, "_gh_rest", lambda p: None)
    monkeypatch.setattr(review_lib.github, "_repo_from_gh", lambda: REPO)

    assert review_lib.github._fetch_pr_metadata(PR) is None


# ---------------- 残量は通常の要求の応答ヘッダから読む ----------------

def test_the_rate_limit_is_read_from_the_response_header(state_mod, monkeypatch):
    """残量を読むためだけの呼び出しは置かない（`gh api rate_limit` は 0 を返す）。"""
    def _run(args, stdin=None, cwd=None):
        assert args[:2] == ["api", "-i"]
        return gh_call.GhResult(0, RAW, "")

    monkeypatch.setattr(gh_call, "RUNNER", _run)

    resp = review_lib.github._gh_rest(f"repos/{REPO}/pulls/{PR}")

    assert resp.rate_remaining == 4972
    assert resp.rate_reset == "1788519069"
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.body["head"]["sha"] == "b87b3ae"


def test_a_failed_call_returns_nothing(state_mod, monkeypatch):
    """失敗は例外にせず `None` で返す。待ち行列を挟む位置になる（#291）。"""
    monkeypatch.setattr(gh_call, "RUNNER",
                        lambda args, stdin=None, cwd=None: gh_call.GhResult(1, "", "HTTP 422"))

    assert review_lib.github._gh_rest("repos/o/r/commits/x/check-runs") is None


# ---------------- チェックジョブの一覧はページを読み切る ----------------

def _check_run(name: str) -> dict:
    return {"name": name, "status": "completed", "conclusion": "success"}


def _check_runs_response(state_mod, total: int, names: list[str]):
    return review_lib.github.RestResponse(
        headers={}, body={"total_count": total, "check_runs": [_check_run(n) for n in names]},
        rate_remaining=None, rate_reset=None,
    )


def test_check_runs_are_read_one_hundred_at_a_time(state_mod, real_github, monkeypatch):
    """既定の 30 件のままにしない。31 件目以降の失敗を見ないまま収束するため。"""
    paths = []

    def _rest(path):
        paths.append(path)
        return _check_runs_response(state_mod, 9, [f"job{i}" for i in range(9)])

    monkeypatch.setattr(review_lib.github, "_gh_rest", _rest)

    runs = review_lib.ci._fetch_check_runs(REPO, "b87b3ae")

    assert len(runs) == 9
    assert paths == [f"repos/{REPO}/commits/b87b3ae/check-runs?per_page=100&page=1"]


def test_check_runs_beyond_one_page_are_followed(state_mod, real_github, monkeypatch):
    """`total_count` は全体の件数を返す。届くまで後続のページを読む。"""
    pages = {
        1: [f"job{i}" for i in range(100)],
        2: [f"late{i}" for i in range(20)],
    }
    seen = []

    def _rest(path):
        page = int(path.rsplit("page=", 1)[1])
        seen.append(page)
        return _check_runs_response(state_mod, 120, pages[page])

    monkeypatch.setattr(review_lib.github, "_gh_rest", _rest)

    runs = review_lib.ci._fetch_check_runs(REPO, "b87b3ae")

    assert seen == [1, 2]
    assert len(runs) == 120
    assert runs[-1]["name"] == "late19"


def test_a_failing_later_page_gives_nothing(state_mod, real_github, monkeypatch):
    """途中のページを取れないときは「確かめられなかった」に倒す。"""
    def _rest(path):
        if path.endswith("page=1"):
            return _check_runs_response(state_mod, 120, [f"job{i}" for i in range(100)])
        return None

    monkeypatch.setattr(review_lib.github, "_gh_rest", _rest)

    assert review_lib.ci._fetch_check_runs(REPO, "b87b3ae") is None


def test_reading_stops_at_the_page_limit(state_mod, real_github, monkeypatch):
    """上限に達しても止めず、読めた範囲で判定する。"""
    def _rest(path):
        page = path.rsplit("page=", 1)[1]
        return _check_runs_response(state_mod, 10_000, [f"p{page}-job{i}" for i in range(100)])

    monkeypatch.setattr(review_lib.github, "_gh_rest", _rest)

    runs = review_lib.ci._fetch_check_runs(REPO, "b87b3ae")

    assert len(runs) == 100 * review_lib.ci.CHECK_RUNS_MAX_PAGES
