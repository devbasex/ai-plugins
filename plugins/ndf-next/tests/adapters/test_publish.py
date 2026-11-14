"""GitHub へ書く本文のエントリポイントと公開範囲の検査（`ndf.adapters.publish`。3391-1）。ネットワークへ出ない。"""

from __future__ import annotations

import json
import re

import httpx
import pytest

from ndf.adapters import publish

from ._github_helpers import hub

PUBLIC_REPO = "example-owner/public-repo"
PRIVATE_REPO = "example-owner/private-repo"
ACCOUNTS = frozenset({"alice-local"})
TOKEN = "ghp_" + "A" * 36


class FakeGitHub:
    """GraphQL（公開範囲）と REST（書き込み）を記録して答える偽物。`visibility` はリポジトリ → 公開範囲。"""

    def __init__(self, repo: str, visibility: dict[str, str]) -> None:
        self.visibility = visibility
        self.queries: list[str] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.hub = hub(repo, self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            query = json.loads(request.content)["query"]
            self.queries.append(query)
            data = {}
            for alias, owner, name in _repo_aliases(query):
                value = self.visibility.get(f"{owner}/{name}")
                data[alias] = {"visibility": value} if value else None
            return httpx.Response(200, json={"data": data})
        self.writes.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={"number": 9, "html_url": f"https://github.com/{self.hub.repo}/issues/9"})


def _repo_aliases(query: str) -> list[tuple[str, str, str]]:
    return re.findall(r'(a\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\)', query)


BODY = f"""## 由来

依頼: https://github.com/{PRIVATE_REPO}/issues/12 と {PRIVATE_REPO}#3、公開の {PUBLIC_REPO}#4
手元のパス: /home/alice-local/work/x.py と /Users/alice-local/y
連絡: alice@example.co.jp（Co-Authored-By: Claude <noreply@anthropic.com>・1+bot@users.noreply.github.com）
アカウント: alice-local が打った。alice-locals は別の語
トークン: {TOKEN}
"""


def test_scrub_on_a_public_target_drops_each_kind_and_counts_it() -> None:
    got = publish.scrub(BODY, True, {PRIVATE_REPO}, ACCOUNTS)
    assert PRIVATE_REPO not in got.text and f"{PUBLIC_REPO}#4" in got.text
    assert "/home/alice-local" not in got.text and "/Users/alice-local" not in got.text
    assert "alice@example.co.jp" not in got.text
    assert "noreply@anthropic.com" in got.text and "1+bot@users.noreply.github.com" in got.text
    assert "alice-local が打った" not in got.text and "alice-locals" in got.text
    assert TOKEN not in got.text
    assert got.dropped == {"secret": 1, "private_ref": 2, "home": 2, "email": 1, "account": 1}


def test_scrub_on_a_private_target_drops_only_secrets() -> None:
    got = publish.scrub(BODY, False, {PRIVATE_REPO}, ACCOUNTS)
    assert TOKEN not in got.text and f"{PRIVATE_REPO}#3" in got.text and "/home/alice-local" in got.text
    assert got.dropped == {"secret": 1}


def test_repo_refs_finds_urls_and_short_refs_but_not_anchors() -> None:
    text = f"https://github.com/{PRIVATE_REPO}/pull/1) と {PUBLIC_REPO}#2、tools/app#3 でなく docs/x.md#見出し"
    assert publish.repo_refs(text) == {PRIVATE_REPO, PUBLIC_REPO, "tools/app"}


def test_publisher_reads_visibilities_once_and_drops_private_refs_on_a_public_target() -> None:
    fake = FakeGitHub(PUBLIC_REPO, {PUBLIC_REPO: "PUBLIC", PRIVATE_REPO: "PRIVATE"})
    out = publish.Publisher(fake.hub, ACCOUNTS).create_issue(f"{PRIVATE_REPO}#3 の続き", BODY, ["bug"])
    method, path, payload = fake.writes[0]
    assert (method, path) == ("POST", f"/repos/{PUBLIC_REPO}/issues")
    assert PRIVATE_REPO not in payload["title"] and PRIVATE_REPO not in payload["body"]
    assert payload["labels"] == ["bug"] and out.number == 9
    assert out.dropped["private_ref"] == 3
    # 書き先の公開範囲で 1 回、本文の参照の公開範囲で 1 回
    assert len(fake.queries) == 2


def test_publisher_treats_unreadable_repos_as_not_public() -> None:
    fake = FakeGitHub(PUBLIC_REPO, {PUBLIC_REPO: "PUBLIC"})
    out = publish.Publisher(fake.hub, ACCOUNTS).comment(4, "other-owner/unknown#5 を見た")
    assert fake.writes[0][2] == {"body": f"{publish.PRIVATE_REF_TEXT} を見た"}
    assert out.dropped == {"private_ref": 1}


def test_publisher_writes_private_targets_with_only_secrets_masked() -> None:
    fake = FakeGitHub(PRIVATE_REPO, {PRIVATE_REPO: "PRIVATE"})
    publish.Publisher(fake.hub, ACCOUNTS).edit_body(4, f"/home/alice-local と {TOKEN}")
    method, path, payload = fake.writes[0]
    assert (method, path) == ("PATCH", f"/repos/{PRIVATE_REPO}/issues/4")
    assert "/home/alice-local" in payload["body"] and TOKEN not in payload["body"]
    assert len(fake.queries) == 1


def test_publisher_origin_hides_a_private_source_on_a_public_target() -> None:
    # 3391-2: 別のリポジトリへの起票で、非公開の由来の参照を公開側へ書かない
    fake = FakeGitHub(PUBLIC_REPO, {PUBLIC_REPO: "PUBLIC", PRIVATE_REPO: "PRIVATE"})
    assert publish.Publisher(fake.hub, ACCOUNTS).origin("issue #12", PRIVATE_REPO) == "issue（非公開のリポジトリの課題）"


@pytest.mark.parametrize("name", ["ab", ""])
def test_short_account_names_are_not_dropped(name: str) -> None:
    assert publish.scrub(f"{name} と ab", True, (), {name}).dropped == {}


def test_local_accounts_reads_the_login_and_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(publish.getpass, "getuser", lambda: "alice-local")
    monkeypatch.setattr(publish.Path, "home", lambda: tmp_path / "bob-home")
    assert publish.local_accounts() == {"alice-local", "bob-home"}
