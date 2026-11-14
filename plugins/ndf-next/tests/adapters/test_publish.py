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


@pytest.mark.parametrize(
    "ref",
    [f"git@github.com:{PRIVATE_REPO}.git", f"ssh://git@github.com/{PRIVATE_REPO}.git", f"github.com/{PRIVATE_REPO}/pull/1",
     f"https://api.github.com/repos/{PRIVATE_REPO}/issues/1", f"https://www.github.com/{PRIVATE_REPO}"],
)  # fmt: skip
def test_private_refs_in_ssh_bare_and_api_urls_are_found_and_dropped(ref: str) -> None:
    # SSH の clone の URL・scheme の無い URL・API の URL も、公開の書き先へは伏せる
    assert publish.repo_refs(f"見た: {ref} の件") == {PRIVATE_REPO}
    got = publish.scrub(f"見た: {ref} の件", True, {PRIVATE_REPO})
    assert got.text == f"見た: {publish.PRIVATE_REF_TEXT} の件" and got.dropped == {"private_ref": 1}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"（https://github.com/{PRIVATE_REPO}）", f"（{publish.PRIVATE_REF_TEXT}）"),
        (f"https://github.com/{PRIVATE_REPO}、次", f"{publish.PRIVATE_REF_TEXT}、次"),
        (f"https://github.com/{PRIVATE_REPO}, next", f"{publish.PRIVATE_REF_TEXT}, next"),
        (f"https://github.com/{PRIVATE_REPO}/issues/3を見た", f"{publish.PRIVATE_REF_TEXT}を見た"),
        (f"参照は{PRIVATE_REPO}#12", f"参照は{publish.PRIVATE_REF_TEXT}"),
    ],
)  # fmt: skip
def test_private_refs_next_to_japanese_text_and_punctuation_are_dropped(text: str, expected: str) -> None:
    # 日本語の文字・全角の括弧・句読点・`,` に接した参照も伏せる。URL の後ろの日本語の文は残す
    got = publish.scrub(text, True, {PRIVATE_REPO})
    assert (got.text, got.dropped) == (expected, {"private_ref": 1})


def test_a_longer_repo_name_is_not_taken_for_its_prefix() -> None:
    assert publish.repo_refs(f"https://github.com/{PRIVATE_REPO}.github を見た") == {f"{PRIVATE_REPO}.github"}


def test_private_refs_are_dropped_before_secrets_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    # 秘密の伏せ字が URL の利用者の部分を書き換えても、非公開の参照は原文から先に落ちている
    monkeypatch.setattr(publish.run_log, "redact", lambda t: t.replace("git@", "[REDACTED]@"))
    got = publish.scrub(f"ssh://git@github.com/{PRIVATE_REPO}.git を見た", True, {PRIVATE_REPO})
    assert got.text == f"{publish.PRIVATE_REF_TEXT} を見た" and got.dropped == {"private_ref": 1}


def test_bare_ssh_user_and_other_hosts_are_not_repo_refs() -> None:
    assert publish.repo_refs("git@github.com と https://api.github.com/user と pages.github.com/a/b") == set()


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


def test_publisher_scrubs_a_target_of_unknown_visibility_like_a_public_one() -> None:
    # 書き先の公開範囲を読めないときも、公開の書き先と同じ検査をかけて未検査の本文を送らない
    fake = FakeGitHub(PUBLIC_REPO, {PRIVATE_REPO: "PRIVATE"})
    out = publish.Publisher(fake.hub, ACCOUNTS).comment(4, BODY)
    body = fake.writes[0][2]["body"]
    assert PRIVATE_REPO not in body and "/home/alice-local" not in body and "alice@example.co.jp" not in body
    assert out.dropped == {"secret": 1, "private_ref": 2, "home": 2, "email": 1, "account": 1}
    origin = publish.Publisher(FakeGitHub(PUBLIC_REPO, {PRIVATE_REPO: "PRIVATE"}).hub, ACCOUNTS).origin("issue #12", PRIVATE_REPO)
    assert origin == "issue（非公開のリポジトリの課題）"


@pytest.mark.parametrize(
    ("email", "kept"),
    [("git@github.com", True), ("noreply@github.com", True), ("1+bot@users.noreply.github.com", True),
     ("git@gitlab.corp.example.co.jp", False), ("noreply@corp.example.co.jp", False), ("x@evil-users.noreply.github.com.example", False)],
)  # fmt: skip
def test_only_confirmed_public_emails_stay_on_a_public_target(email: str, kept: bool) -> None:
    # ローカル部（git・noreply）だけでは許さず、公開してよいと確かめた宛先だけを残す
    got = publish.scrub(f"宛先 {email} へ", True)
    assert (email in got.text) is kept


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


@pytest.mark.parametrize(
    ("origin", "target", "expected"),
    [
        ("issue #12", "example-owner/other-repo", "issue example-owner/example-repo#12"),
        ("PR #3", "example-owner/other-repo", "PR example-owner/example-repo#3"),
        ("issue #12", "example-owner/example-repo", "issue #12"),
        ("振り返り", "example-owner/other-repo", "振り返り"),
    ],
)
def test_qualified_ref_names_the_source_repo_for_other_repos(origin: str, target: str, expected: str) -> None:
    # #3077: 別のリポジトリへ書く由来の参照は、起票先の別の課題を指さない
    visibility = {"example-owner/example-repo": "PUBLIC", "example-owner/other-repo": "PUBLIC"}
    assert publish.qualified_ref(origin, "example-owner/example-repo", target, visibility) == expected


@pytest.mark.parametrize(
    ("origin", "expected"),
    [("issue #12", "issue（非公開のリポジトリの課題）"), ("example-owner/private-repo#12", "非公開のリポジトリの課題")],
)
def test_qualified_ref_hides_a_private_source_on_a_public_target(origin: str, expected: str) -> None:
    # 3391-2: 公開の書き先へ、非公開のリポジトリの参照を書かない
    visibility = {"example-owner/private-repo": "PRIVATE", "example-owner/public-repo": "PUBLIC"}
    assert publish.qualified_ref(origin, "example-owner/private-repo", "example-owner/public-repo", visibility) == expected
    # 書き先も非公開なら、完全な参照にしてよい
    both_private = {**visibility, "example-owner/public-repo": "PRIVATE"}
    assert publish.qualified_ref("issue #12", "example-owner/private-repo", "example-owner/public-repo", both_private) == (
        "issue example-owner/private-repo#12"
    )


def test_is_public_ignores_case_and_treats_unknown_as_not_public() -> None:
    got = {"a/pub": "PUBLIC", "b/priv": "PRIVATE", "c/gone": "UNKNOWN"}
    assert not publish.is_public(got, "c/gone") and publish.is_public(got, "A/Pub")
