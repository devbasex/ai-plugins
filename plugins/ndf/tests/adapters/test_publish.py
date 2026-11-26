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
    """GraphQL（公開範囲）と REST（書き込み）を記録して回答する偽物。`visibility` はリポジトリ → 公開範囲。"""

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

リクエスト: https://github.com/{PRIVATE_REPO}/issues/12 と {PRIVATE_REPO}#3、公開の {PUBLIC_REPO}#4
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


def test_repo_refs_finds_urls_and_short_refs_but_not_anchors_or_other_hosts() -> None:
    text = f"https://github.com/{PRIVATE_REPO}/pull/1) と {PUBLIC_REPO}#2、tools/app#3 でなく docs/x.md#見出し"
    assert publish.repo_refs(text) == {PRIVATE_REPO, PUBLIC_REPO, "tools/app"}
    # SSH の利用者名とほかのホストは参照でない
    assert publish.repo_refs("git@github.com と https://api.github.com/user と pages.github.com/a/b") == set()


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


def test_a_private_ssh_url_is_dropped_whole_as_a_reference() -> None:
    # 秘密の範囲も参照の範囲も原文の上で求めるので、SSH の URL の参照は原文のまま一致して伏せる
    got = publish.scrub(f"ssh://git@github.com/{PRIVATE_REPO}.git を見た", True, {PRIVATE_REPO})
    assert got.text == f"{publish.PRIVATE_REF_TEXT} を見た" and got.dropped == {"private_ref": 1}


@pytest.mark.parametrize("public", [True, False])
def test_a_secret_crossing_the_end_of_a_reference_leaves_no_fragment(monkeypatch: pytest.MonkeyPatch, public: bool) -> None:
    # #3959 Task 9: 秘密の前半が参照の中、後半が参照の外にあっても、秘密のどの断片も残さない
    secret = "zz-secret-tail-9876"
    monkeypatch.setenv("NDF_TEST_TOKEN", secret)
    text = f"見た: https://github.com/{PRIVATE_REPO}/issues/1{secret} の件"
    got = publish.scrub(text, public, {PRIVATE_REPO})
    assert all(secret[i : i + 4] not in got.text for i in range(len(secret) - 3))
    assert got.dropped.get("secret") == 1 and PRIVATE_REPO not in got.text


@pytest.mark.parametrize("public", [True, False])
@pytest.mark.parametrize(
    "url",
    [
        f"https://x-access-token:{TOKEN}@github.com/{PRIVATE_REPO}.git",
        f"https://***@github.com/{PRIVATE_REPO}",
        f"https://git@github.com/{PUBLIC_REPO}",
        f"HTTPS://user:pa55@GitHub.COM/{PRIVATE_REPO}",
        f"Ssh://deploy@WWW.github.com/{PRIVATE_REPO}.git",
    ],
)
def test_a_url_with_credentials_is_masked_whole_on_any_target(url: str, public: bool) -> None:
    # 認証情報の付いた URL は、書き先の公開範囲によらず、所有者名とリポジトリ名を含む URL の全体を秘密の伏せ字にする
    got = publish.scrub(f"取得元: {url} だった", public, set())
    assert got.text == f"取得元: {publish.run_log.MASK} だった" and got.dropped == {"secret": 1}


def test_the_public_ssh_url_keeps_its_owner_and_repo() -> None:
    # 固定の SSH の利用者名 git@ は秘密でない。公開のリポジトリの SSH の URL は今どおり残す
    text = f"ssh://git@github.com/{PUBLIC_REPO}.git"
    assert publish.scrub(text, True, set()) == (text, {})
    assert publish.repo_refs(text) == {PUBLIC_REPO}


class StubHub:
    """公開範囲の照会（`visibilities`）と書き込み（`rest`）の引数を残す偽物。HTTP の層を通さない。"""

    def __init__(self, repo: str, visibility: dict[str, str]) -> None:
        self.repo, self.visibility = repo, visibility
        self.asked: list[list[str]] = []
        self.writes: list[dict] = []

    def visibilities(self, repos) -> dict[str, str]:
        self.asked.append(sorted(repos))
        return {r: self.visibility.get(r, "UNKNOWN") for r in repos}

    def rest(self, method: str, path: str, payload: dict) -> dict:
        self.writes.append(payload)
        return {"number": 9, "html_url": "https://github.com/x/y/issues/9"}


def test_references_overlapping_secrets_are_not_looked_up_and_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    # 秘密を含む参照は公開範囲の照会へ送らない。照会するのは秘密と重ならない参照だけ
    secret = "zz-owner-secret-42"
    monkeypatch.setenv("NDF_TEST_TOKEN", secret)
    stub = StubHub(PUBLIC_REPO, {PUBLIC_REPO: "PUBLIC", "other/public": "PUBLIC"})
    body = (
        f"{secret}/repo#1 と ssh://{secret}@github.com/{PUBLIC_REPO} と https://x-access-token:{TOKEN}@github.com/acme/hidden.git"
        " と ssh://git@github.com/other/public.git"
    )
    publish.Publisher(stub, ACCOUNTS).comment(4, body)  # type: ignore[arg-type]
    sent = stub.writes[0]["body"]
    assert secret not in sent and TOKEN not in sent and "acme/hidden" not in sent and "ssh://git@github.com/other/public.git" in sent
    assert stub.asked == [[PUBLIC_REPO], ["other/public"]]


def test_comment_unchanged_reads_the_target_and_references_in_one_query() -> None:
    stub = StubHub(PUBLIC_REPO, {PUBLIC_REPO: "PUBLIC", "other/public": "PUBLIC"})
    assert publish.Publisher(stub, ACCOUNTS).comment_unchanged(1, "other/public#2") is not None  # type: ignore[arg-type]
    assert stub.asked == [sorted([PUBLIC_REPO, "other/public"])]


def test_assume_public_scrubs_without_reading_the_target_visibility() -> None:
    stub = StubHub(PRIVATE_REPO, {PRIVATE_REPO: "PRIVATE"})
    (text,), dropped = publish.Publisher(stub, ACCOUNTS).guarded("/home/alice-local と alice@example.co.jp", assume_public=True)  # type: ignore[arg-type]
    assert text == f"{publish.HOME_TEXT} と {publish.EMAIL_TEXT}" and dropped == {"home": 1, "email": 1}
    assert stub.asked == []


SELF_REFS = (f"{PRIVATE_REPO}#12", f"https://github.com/{PRIVATE_REPO}/pull/34")


@pytest.mark.parametrize(("visibility", "kept"), [("PRIVATE", False), ("PUBLIC", True)])
def test_assume_public_looks_up_references_to_the_target_itself(visibility: str, kept: bool) -> None:
    # 結果の受け手は対象のリポジトリを読めるとは限らないので、自身への参照も公開と確かめられたときだけ残す
    stub = StubHub(PRIVATE_REPO, {PRIVATE_REPO: visibility})
    texts, _ = publish.Publisher(stub, ACCOUNTS).guarded(*SELF_REFS, assume_public=True)  # type: ignore[arg-type]
    assert stub.asked == [[PRIVATE_REPO]]
    if kept:
        assert texts == list(SELF_REFS)
    else:
        assert texts == [publish.PRIVATE_REF_TEXT] * 2 and all("#12" not in t and "34" not in t for t in texts)


def test_without_assume_public_references_to_the_target_itself_are_kept_unasked() -> None:
    # PR のコメントの経路（書き先そのものが閲覧の範囲を決める）は、自身への参照を照会せずに残す
    stub = StubHub(PRIVATE_REPO, {PRIVATE_REPO: "PUBLIC"})
    text = f"{SELF_REFS[0]} と {SELF_REFS[1]} と {PRIVATE_REPO}"
    (got,), _ = publish.Publisher(stub, ACCOUNTS).guarded(text)  # type: ignore[arg-type]
    assert got == text and stub.asked == [[PRIVATE_REPO]]


BARE_HIDDEN = [
    (f"{PRIVATE_REPO}", "{}"),
    (PRIVATE_REPO.upper(), "{}"),
    (f"Fix this in {PRIVATE_REPO}.", "Fix this in {}."),
    (f"{PRIVATE_REPO}、", "{}、"),
    (f"({PRIVATE_REPO})", "({})"),
    (f"{PRIVATE_REPO}/", "{}/"),
    (f"{PRIVATE_REPO}.git", "{}"),
]
BARE_KEPT = [f"{PRIVATE_REPO}-2", f"{PRIVATE_REPO}.js", f"{PRIVATE_REPO}/sub", f"other-{PRIVATE_REPO}", f"z{PRIVATE_REPO}"]


@pytest.mark.parametrize(("visibility", "hidden"), [("PRIVATE", True), ("PUBLIC", False)])
def test_assume_public_hides_the_bare_full_name_of_a_private_target(visibility: str, hidden: bool) -> None:
    # 単独の完全名は repo_refs が拾わない。照会の候補に対象のリポジトリを入れ、公開でなければ名前の範囲だけを伏せる
    stub = StubHub(PRIVATE_REPO, {PRIVATE_REPO: visibility})
    texts = [given for given, _ in BARE_HIDDEN] + BARE_KEPT
    got, _ = publish.Publisher(stub, ACCOUNTS).guarded(*texts, assume_public=True)  # type: ignore[arg-type]
    assert stub.asked == [[PRIVATE_REPO]]
    expected = [shape.format(publish.PRIVATE_REF_TEXT) for _, shape in BARE_HIDDEN] if hidden else [g for g, _ in BARE_HIDDEN]
    assert got == expected + BARE_KEPT


def test_assume_public_does_not_look_up_a_bare_name_holding_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    # 単独の完全名も、URL・番号付きの参照と同じく、秘密を含めば公開範囲の照会へ送らずに伏せる
    secret = "zz-secret-name-42"
    monkeypatch.setenv("NDF_TEST_TOKEN", secret)
    stub = StubHub(f"acme/{secret}", {f"acme/{secret}": "PUBLIC"})
    (got,), _ = publish.Publisher(stub, ACCOUNTS).guarded(f"直す先は acme/{secret} だ", assume_public=True)  # type: ignore[arg-type]
    assert stub.asked == [] and secret not in got and "acme" not in got


def test_spellings_of_one_repository_are_looked_up_once() -> None:
    # 大文字・小文字の違う同じリポジトリの参照は、照会の前にまとめて 1 件だけ問う。どの表記も伏せる
    stub = StubHub(PRIVATE_REPO, {})
    text = f"{PRIVATE_REPO.upper()}#1 と https://github.com/{PRIVATE_REPO.title()}/pull/2 と {PRIVATE_REPO}"
    (got,), _ = publish.Publisher(stub, ACCOUNTS).guarded(text, assume_public=True)  # type: ignore[arg-type]
    assert len(stub.asked) == 1 and len(stub.asked[0]) == 1 and stub.asked[0][0].lower() == PRIVATE_REPO
    assert got == f"{publish.PRIVATE_REF_TEXT} と {publish.PRIVATE_REF_TEXT} と {publish.PRIVATE_REF_TEXT}"


def test_the_bare_name_pattern_matches_only_the_name_and_its_git_suffix() -> None:
    pattern = publish._bare_repo(PRIVATE_REPO)
    assert [m.group() for m in pattern.finditer(f"{PRIVATE_REPO}.git と {PRIVATE_REPO}. と {PRIVATE_REPO}.js")] == [
        f"{PRIVATE_REPO}.git",
        PRIVATE_REPO,
    ]


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
    # ローカル部（git・noreply）だけでは許さず、公開してよいと確かめたアドレスだけを残す
    got = publish.scrub(f"アドレス {email} へ", True)
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
