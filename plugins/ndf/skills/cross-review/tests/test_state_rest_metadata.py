"""Pull Request のメタデータを REST の 1 回で取る（#271）。

初期化は作成者・head・base を項目ごとに `gh pr view` で取っており、GraphQL 側の枠を
実行ごと 4 点使っていた。**尽きるのは GraphQL 側である。** REST の 1 回の応答から
同じ項目がすべて取れるので、そちらへ移す。

リポジトリ名は git の設定から求める。`repos/{owner}/{repo}/pulls/{PR}` がそのまま
検証になるため、誤った名前のまま進む経路はできない。
"""
from __future__ import annotations

import json
import subprocess

import pytest

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
    return state_mod.RestResponse(
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
    monkeypatch.setattr(state_mod, "_git_remote_url", lambda: url)

    assert state_mod._repo_from_git() == REPO


def test_an_unreadable_remote_gives_no_name(state_mod, monkeypatch):
    monkeypatch.setattr(state_mod, "_git_remote_url", lambda: "")

    assert state_mod._repo_from_git() is None


# ---------------- 1 回の応答から全部を埋める ----------------

def test_one_rest_response_fills_author_head_and_base(state_mod, real_github, monkeypatch):
    paths: list[str] = []
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(state_mod, "_gh_rest", lambda p: (paths.append(p), _response(state_mod))[1])
    monkeypatch.setattr(state_mod, "_sh", lambda *a, **k: pytest.fail("GraphQL へ落ちてはならない"))

    meta = state_mod._fetch_pr_metadata(PR)

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
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(state_mod, "_gh_rest", lambda p: _response(state_mod, body))

    assert state_mod._fetch_pr_metadata(PR).is_fork is True


def test_a_wrong_repository_name_falls_back_to_gh_repo_view(state_mod, real_github, monkeypatch):
    """求めた名前が誤っていれば応答が失敗する。そのときだけ解決し直す。"""
    paths: list[str] = []

    def _rest(path):
        paths.append(path)
        return _response(state_mod) if path == f"repos/{REPO}/pulls/{PR}" else None

    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: "wrong/name")
    monkeypatch.setattr(state_mod, "_gh_rest", _rest)
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: REPO)

    meta = state_mod._fetch_pr_metadata(PR)

    assert paths == [f"repos/wrong/name/pulls/{PR}", f"repos/{REPO}/pulls/{PR}"]
    assert meta.repo == REPO


def test_an_unreachable_pull_request_gives_nothing(state_mod, real_github, monkeypatch):
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(state_mod, "_gh_rest", lambda p: None)
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: REPO)

    assert state_mod._fetch_pr_metadata(PR) is None


# ---------------- 残量は通常の要求の応答ヘッダから読む ----------------

def test_the_rate_limit_is_read_from_the_response_header(state_mod, monkeypatch):
    """残量を読むためだけの呼び出しは置かない（`gh api rate_limit` は 0 を返す）。"""
    def _run(cmd, capture_output=True, text=True):
        assert cmd[:3] == ["gh", "api", "-i"]
        return subprocess.CompletedProcess(cmd, 0, stdout=RAW, stderr="")

    monkeypatch.setattr(state_mod.subprocess, "run", _run)

    resp = state_mod._gh_rest(f"repos/{REPO}/pulls/{PR}")

    assert resp.rate_remaining == 4972
    assert resp.rate_reset == "1788519069"
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.body["head"]["sha"] == "b87b3ae"


def test_a_failed_call_returns_nothing(state_mod, monkeypatch):
    """失敗は例外にせず `None` で返す。待ち行列を挟む位置になる（#291）。"""
    monkeypatch.setattr(
        state_mod.subprocess, "run",
        lambda cmd, capture_output=True, text=True:
            subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 422"),
    )

    assert state_mod._gh_rest("repos/o/r/commits/x/check-runs") is None
