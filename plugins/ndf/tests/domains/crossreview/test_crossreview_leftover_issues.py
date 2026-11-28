"""直さずに残した指摘の起票と起票の記録（#3780）。GitHub は API の偽物で、伏せ字は本物の `publish.Publisher` を通す。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf.adapters import publish
from ndf.adapters.github import PUBLIC, GitHub, GitHubError, Viewer
from ndf.adapters.github_pulls import PER_PAGE
from ndf.domains.crossreview import leftover, leftover_decide, report_decide, store
from ndf.domains.crossreview.leftover_decide import Filed, Listed

REPO = "o/r"
PR_URL = f"https://github.com/{REPO}/pull/7"
ISSUES = f"https://github.com/{REPO}/issues/"
# 偽の GitHub の login ごとのアカウント ID
IDS = {"me": 1, "app[bot]": 2, "human": 3, "x": 4}


class FakeGitHub:
    """起票が使う GitHub の API の偽物。時刻は書き込みのたびに 1 秒進む。`fail` の (method, パスの末尾) は、指定の状態で 1 回だけ断る。
    `indexed` が偽なら、作った Issue は検索にまだ出ない。"""

    repo = REPO
    trusts = GitHub.trusts  # 本物の判定を、偽の権限に当てる

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], int] = {}
        self.indexed = True
        self.login = "me"
        self.viewed = 0
        self.permissions: dict[str, str] = {}
        self._tick = 0

    def now(self) -> str:
        self._tick += 1
        return f"2026-11-27T00:00:{self._tick:02d}Z"

    def viewer(self) -> Viewer:
        self.viewed += 1
        return Viewer(self.login, IDS[self.login])

    def permission(self, login: str) -> str:
        return self.permissions.get(login, "none")

    def visibilities(self, repos) -> dict[str, str]:
        return {r: PUBLIC for r in repos}

    def _by(self, body: str) -> dict[str, Any]:
        return {"body": body, "user": {"login": self.login, "id": IDS[self.login]}, "author_association": "NONE"}

    def issue(self, body: str, **other: Any) -> dict[str, Any]:
        number = 100 + len(self.issues)
        row = self._by(body) | {"number": number, "html_url": f"https://github.com/{REPO}/issues/{number}", "created_at": self.now()}
        self.issues.append(row | other)
        return self.issues[-1]

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        path = path.removeprefix("/")
        self.calls.append((method, path))
        for (wanted, tail), status in list(self.fail.items()):
            if wanted == method and path.endswith(tail):
                del self.fail[(wanted, tail)]
                raise GitHubError(f"{method} {path} は {status}", status)
        if path == "search/issues":
            ident = params["q"].split('"')[1]
            return {"items": [i for i in self.issues if self.indexed and ident in i["body"]]}
        if path == f"repos/{REPO}/issues" and method == "GET":
            ordered = sorted(self.issues, key=lambda i: i["created_at"], reverse=True)
            start = (params["page"] - 1) * params["per_page"]
            return ordered[start : start + params["per_page"]]
        if path == f"repos/{REPO}/issues":
            return self.issue(payload["body"], title=payload["title"])
        return self._comment(method, path.removeprefix(f"repos/{REPO}/issues/"), payload)

    def nodes(self, numbers: Any, node: str, fields: str) -> dict[int, dict[str, Any]]:
        """GraphQL のまとめ読み。bot の login は末尾の `[bot]` を持たず、無い番号は空の辞書。"""
        wanted = sorted(set(numbers))
        self.calls.append(("GRAPHQL", node))
        found = {i["number"]: i for i in self.issues}
        return {n: self._node(found[n]) if n in found else {} for n in wanted}

    def _node(self, issue: dict[str, Any]) -> dict[str, Any]:
        login = issue["user"]["login"]
        author = {"__typename": "Bot" if login.endswith("[bot]") else "User", "login": login.removesuffix("[bot]")}
        author["databaseId"] = issue["user"]["id"]
        read = {"url": issue["html_url"], "body": issue["body"], "createdAt": issue["created_at"]}
        return read | {"authorAssociation": issue["author_association"], "author": author}

    def _comment(self, method: str, tail: str, payload: Any) -> Any:
        if tail.startswith("comments/"):
            found = next((c for c in self.records if c["id"] == int(tail.split("/")[1])), None)
            if found is None:
                raise GitHubError("無い", 404)
            if method == "PATCH":
                found.update(body=payload["body"], updated_at=self.now())
            return dict(found)
        pr = int(tail.split("/")[0])
        if method == "GET":
            return [dict(c) for c in self.records if c["pr"] == pr]
        self.records.append(self._by(payload["body"]) | {"id": 500 + len(self.records), "pr": pr, "updated_at": self.now()})
        return dict(self.records[-1])

    def count(self, method: str, tail: str) -> int:
        return sum(1 for m, p in self.calls if m == method and p.endswith(tail))

    def created(self) -> int:
        return self.count("POST", f"repos/{REPO}/issues")


def _row(fid: str = "r2-1", text: str = "古い行で値を落とす", **other: Any) -> dict[str, Any]:
    row = {"id": fid, "text": text, "severity": "major", "impact": "利用者の値が消える", "path": "a.py", "line": 5}
    return row | {"outcome": "unresolved", "parked": True, "protect": ["user_data"]} | other


class Run:
    """1 つの状態の保存先からの公開。`go` は起票を 1 回打つ。"""

    def __init__(self, github: FakeGitHub, *rows: dict[str, Any], current: int = 7):
        self.github = github
        self.data: dict[str, Any] = {"pr": 7, "findings": list(rows) or [_row()]}
        self.ref = store.StateRef(7, current, Path("unused"))
        self.saved: list[str] = []
        self.port = leftover.GitHubIssues(github, publish.Publisher(github, frozenset()), GitHubError, github.viewer, sleep=lambda s: None)

    def go(self, **options: Any) -> list[str]:
        return leftover.file_issues(self.data, self.ref, self.port, lambda: self.saved.append(json.dumps(self.data)), **options)

    @property
    def row(self) -> dict[str, Any]:
        return self.data["findings"][0]


# --- 判断（純粋な関数） -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "filed"),
    [
        pytest.param({"severity": "major", "check": {"tracks": ["delivered"]}}, True, id="delivered"),
        pytest.param({"severity": "critical", "protect": ["user_data"]}, True, id="user-data"),
        pytest.param({"severity": "major", "protect": ["authz"]}, False, id="authz-only"),
        pytest.param({"severity": "major", "protect": ["authz"], "check": {"tracks": ["delivered"]}}, True, id="authz-and-delivered"),
        pytest.param({"severity": "minor", "protect": ["secret"]}, False, id="minor"),
    ],
)
def test_issue_3780_2_files_issue(row: dict, filed: bool) -> None:
    assert leftover_decide.files_issue(row) is filed


def test_identity_folds_spaces_and_case_and_reads_the_whole_text() -> None:
    same = leftover_decide.identity(7, "Alpha  beta\n")
    assert leftover_decide.identity(7, "alpha beta") == same and len(same) == 12
    assert leftover_decide.identity(7, "alpha betb") != same != leftover_decide.identity(8, "alpha beta")


def test_issue_parts_omits_empty_sections() -> None:
    title, body = leftover_decide.issue_parts("あ" * 100, "", "", [PR_URL])
    assert title == "あ" * leftover_decide.TITLE_CHARS
    assert PR_URL in body and leftover_decide.issue_mark("abc").endswith("cross-review-leftover abc -->")
    assert "起きること" not in body and "場所" not in body


def _listed(body: str, **other: Any) -> Listed:
    return Listed("u", body, "me", "NONE", "t", False)._replace(**other)


def test_issue_3780_matches_finding_needs_the_mark_and_the_url_outside_quotes() -> None:
    text = "値を落とす\n\n```\n閉じていないコード"
    _, body = leftover_decide.issue_parts(text, "消える", "a.py:5", [PR_URL])
    body = f"{body.rstrip()}\n\n{leftover_decide.issue_mark('abc')}\n"
    assert leftover_decide.matches_finding(_listed(body), "abc", 7)
    assert not leftover_decide.matches_finding(_listed(body), "abc", 70)
    mark = body.rstrip().splitlines()[-1]
    quoted = "\n".join(f"> {line}" for line in body.splitlines()) + f"\n\n{mark}\n"
    for other in (quoted, body + "後ろの文\n", f"```\n{body}\n```\n{mark}\n"):
        assert not leftover_decide.matches_finding(_listed(other), "abc", 7)
    assert not leftover_decide.matches_finding(_listed(body, is_pull=True), "abc", 7)


@pytest.mark.parametrize(
    ("author", "author_id", "association", "permission", "wanted"),
    [
        ("x", 4, "OWNER", "none", True),
        ("x", 4, "MEMBER", "write", True),
        ("x", 4, "MEMBER", "read", False),
        ("x", 4, "COLLABORATOR", "none", False),
        ("x", 4, "CONTRIBUTOR", "write", False),
        ("me", 1, "NONE", "none", True),
        ("app[bot]", 2, "NONE", "none", True),
        # 前に起票した login を、別のアカウントが取得した
        ("app[bot]", 9, "NONE", "none", False),
        ("", 0, "NONE", "none", False),
    ],
)
def test_trusted_authors(author: str, author_id: int, association: str, permission: str, wanted: bool) -> None:
    github = FakeGitHub()
    github.permissions[author] = permission
    run = Run(github)
    run.data["issue_account_ids"] = [IDS["app[bot]"]]
    filing = leftover._Filing(run.data, run.ref, run.port, lambda: None, {}, me=IDS["me"])
    assert filing._trusts(author, author_id, association) is wanted


def test_the_record_reads_back_what_it_writes_and_a_broken_body_is_empty() -> None:
    entries = {"0123456789ab": {"at": "2026-11-27T00:00:01Z", "url": f"{ISSUES}7"}, "ba9876543210": {}}
    body = leftover_decide.record_body(entries, ISSUES)
    assert leftover_decide.record_entries(body, ISSUES) == entries and "github.com" not in body
    assert leftover_decide.record_entries(leftover_decide.record_body({}, ISSUES), ISSUES) == {}
    assert leftover_decide.record_entries(leftover_decide.RECORD_MARK + "\n壊れた本文", ISSUES) == {}
    # 形の違う識別値・時刻・URL は書かない
    odd = {"秘密の文": {}, "0123456789ab": {"at": "秘密の文", "url": "https://example.com/秘密の文"}}
    written = leftover_decide.record_body(odd, ISSUES)
    assert "秘密の文" not in written and leftover_decide.record_entries(written, ISSUES) == {"0123456789ab": {}}


# --- 起票 ----------------------------------------------------------------------------------


def test_issue_3780_2_files_once_and_writes_the_url_to_the_row() -> None:
    github = FakeGitHub()
    run = Run(github)
    assert run.go() == []
    url = github.issues[0]["html_url"]
    assert run.row[Filed.URL] == url and Filed.ERROR not in run.row
    assert run.data["issue_account_ids"] == [IDS["me"]] and run.data["issue_record_id"] == github.records[0]["id"]
    # 作成を試みていない候補だけなら、検索も一覧も呼ばない
    assert github.count("GET", "search/issues") == 0 and github.count("GET", f"repos/{REPO}/issues") == 0
    assert run.go() == [] and github.created() == 1
    # 別の保存先の打ち直しは、起票の記録の URL を使う
    again = Run(github)
    assert again.go() == [] and again.row[Filed.URL] == url and github.created() == 1


@pytest.mark.parametrize(("reason", "kept"), [pytest.param("前の理由", True, id="same"), pytest.param(None, False, id="changed")])
def test_defer_keeps_the_skip_reason_only_in_the_state(reason: str | None, kept: bool) -> None:
    row = _row(issue_error="前の失敗")
    data = {leftover.SKIPPED: "前の理由", report_decide.PUBLISHED_KEY: "公開済み"}
    leftover.defer(data, [row], reason)
    assert data[leftover.SKIPPED] == reason and Filed.ERROR not in row
    assert (report_decide.PUBLISHED_KEY in data) is kept


def test_only_candidates_call_github() -> None:
    github = FakeGitHub()
    others = (_row(parked=False), _row(protect=["authz"]), _row(outcome="fixed"), _row(issue="u"))
    assert Run(github, *others).go() == []
    assert github.calls == [] and github.viewed == 0


def test_the_login_is_read_once_and_the_record_of_a_bot_is_trusted() -> None:
    github = FakeGitHub()
    github.login = "app[bot]"
    first = Run(github)
    first.go()
    github.login = "human"
    again = Run(github)
    again.data["issue_account_ids"] = list(first.data["issue_account_ids"])
    again.go()
    again.go()
    assert again.row[Filed.URL] == first.row[Filed.URL] and github.created() == 1 and github.viewed == 2


def test_the_comments_are_read_only_up_to_the_record() -> None:
    github = FakeGitHub()
    Run(github).go()
    github.records += [{**github.records[0], "id": 600 + n, "body": "別のコメント"} for n in range(PER_PAGE)]
    before = github.count("GET", "issues/7/comments")
    assert Run(github).go() == [] and github.count("GET", "issues/7/comments") == before + 1


def test_the_marks_survive_an_account_named_like_them() -> None:
    github = FakeGitHub()
    for _ in range(2):
        run = Run(github, _row(text="<!-- ndf: の形の引用で値を落とす"))
        run.port.publisher = publish.Publisher(github, frozenset({"ndf"}))
        assert run.go() == [] and run.row[Filed.URL]
    assert github.created() == 1
    # 伏せないのは NDF が足した目印だけで、指摘の本文の同じ形は伏せる
    made = github.issues[0]
    assert "<!-- ndf:" not in made["title"] and made["body"].count("<!-- ndf:") == 1


@pytest.mark.parametrize("account", [pytest.param("number", id="record-key"), pytest.param("100", id="issue-number")])
def test_issue_3780_the_record_survives_an_account_named_like_its_content(account: str) -> None:
    github = FakeGitHub()
    for _ in range(2):
        run = Run(github)
        run.port.publisher = publish.Publisher(github, frozenset({account}))
        assert run.go() == [] and run.row[Filed.URL] == f"{ISSUES}100"
    ident = leftover_decide.identity(7, run.row["text"])
    assert leftover_decide.record_entries(github.records[0]["body"], ISSUES)[ident]["url"] == f"{ISSUES}100"
    assert github.created() == 1


def test_recorded_issues_are_read_together() -> None:
    github = FakeGitHub()
    rows = (_row(), _row("r2-2", text="別の指摘で値を落とす"))
    Run(github, *rows).go()
    again = Run(github, _row(), _row("r2-2", text="別の指摘で値を落とす"))
    assert again.go() == [] and all(r[Filed.URL] for r in again.data["findings"])
    assert github.count("GRAPHQL", "issue") == 1 and github.created() == 2


def test_a_refused_creation_keeps_the_finding_and_can_be_retried() -> None:
    github = FakeGitHub()
    github.fail[("POST", f"repos/{REPO}/issues")] = 422
    run = Run(github)
    warnings = run.go()
    assert run.row[Filed.ERROR] and Filed.URL not in run.row and "r2-1" in warnings[0]
    assert leftover_decide.record_entries(github.records[0]["body"], ISSUES) == {}
    assert run.go() == [] and run.row[Filed.URL] and Filed.ERROR not in run.row


def test_nothing_is_created_when_the_record_cannot_be_written() -> None:
    github = FakeGitHub()
    github.fail[("POST", "issues/7/comments")] = 502
    run = Run(github)
    assert run.go() and run.row[Filed.ERROR] and github.created() == 0


@pytest.mark.parametrize("current", [pytest.param(7, id="same-pr"), pytest.param(9, id="reopened-pr")])
@pytest.mark.parametrize("indexed", [pytest.param(True, id="search"), pytest.param(False, id="list")])
def test_issue_3780_a_lost_result_does_not_create_a_second_issue(current: int, indexed: bool) -> None:
    github = FakeGitHub()
    github.indexed = indexed
    github.fail[("PATCH", "comments/500")] = 502
    assert Run(github).go()
    again = Run(github, current=current)
    assert again.go() == [] and again.row[Filed.URL] == github.issues[0]["html_url"]
    assert github.created() == 1
    assert leftover_decide.record_entries(github.records[0]["body"], ISSUES)[leftover_decide.identity(7, again.row["text"])]["url"]


def test_an_issue_created_at_the_attempted_time_is_found_in_the_list() -> None:
    github = FakeGitHub()
    github.indexed = False
    github.fail[("PATCH", "comments/500")] = 502
    Run(github).go()
    github.issues[0]["created_at"] = github.records[0]["updated_at"]
    again = Run(github)
    again.go()
    assert again.row[Filed.URL] and github.created() == 1


def test_an_unknown_result_with_no_issue_is_created_after_checking() -> None:
    github = FakeGitHub()
    github.fail[("POST", f"repos/{REPO}/issues")] = 502
    run = Run(github)
    assert run.go() and run.row[Filed.ERROR]
    assert run.go() == [] and run.row[Filed.URL]
    assert github.count("GET", "search/issues") == 1 and github.count("GET", f"repos/{REPO}/issues") == 1


def test_an_unreadable_search_does_not_create() -> None:
    github = FakeGitHub()
    github.fail[("POST", f"repos/{REPO}/issues")] = 502
    run = Run(github)
    run.go()
    github.fail[("GET", "search/issues")] = 403
    assert run.go() and run.row[Filed.ERROR] and github.created() == 1


def test_a_list_over_the_page_limit_is_held_until_a_manual_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(leftover.limits, "value", lambda name, key: 1)
    github = FakeGitHub()
    github.fail[("POST", f"repos/{REPO}/issues")] = 502
    run = Run(github)
    run.go()
    for _ in range(PER_PAGE):
        github.issue("無関係")
    assert run.go() and run.data["issue_list_full"] == 1
    listed = github.count("GET", f"repos/{REPO}/issues")
    assert run.go() and github.count("GET", f"repos/{REPO}/issues") == listed
    monkeypatch.setattr(leftover.limits, "value", lambda name, key: 3)
    assert run.go() == [] and run.row[Filed.URL] and "issue_list_full" not in run.data


def test_a_secret_across_the_title_cut_does_not_reach_the_issue() -> None:
    secret = "ghp_" + "Zq7x" * 9
    text = "あ" * (leftover_decide.TITLE_CHARS - 8) + " " + secret + " が漏れる"
    github = FakeGitHub()
    run = Run(github, _row(text=text, impact=f"{secret} が読める", protect=["secret"]))
    assert run.go() == []
    made = github.issues[0]
    for written in (made["title"], made["body"], github.records[0]["body"]):
        assert "ghp_" not in written and "Zq7x" not in written


@pytest.mark.parametrize(
    "text",
    [pytest.param("```\nTraceback: 落ちる\n```", id="code-only"), pytest.param("> 引用だけの指摘", id="quote-only")],
)
def test_issue_3780_a_finding_without_prose_is_found_again(text: str) -> None:
    github = FakeGitHub()
    github.fail[("PATCH", "comments/500")] = 502
    Run(github, _row(text=text)).go()
    again = Run(github, _row(text=text))
    assert again.go() == [] and again.row[Filed.URL] and github.created() == 1


def test_issue_3780_a_rerun_that_redacts_differently_finds_the_issue() -> None:
    github = FakeGitHub()
    github.fail[("PATCH", "comments/500")] = 502
    Run(github, _row(text="runner が値を落とす")).go()
    again = Run(github, _row(text="runner が値を落とす"))
    again.port.publisher = publish.Publisher(github, frozenset({"runner"}))
    assert again.go() == [] and again.row[Filed.URL] and github.created() == 1


@pytest.mark.parametrize(
    "number",
    [pytest.param(100, id="unrelated-issue"), pytest.param(999, id="missing-issue")],
)
def test_a_record_url_that_is_not_the_issue_of_the_finding_is_not_used(number: int) -> None:
    github = FakeGitHub()
    github.permissions["x"] = "write"
    github.issue("無関係")
    run = Run(github)
    ident = leftover_decide.identity(7, run.row["text"])
    forged = leftover_decide.record_body({ident: {"url": f"{ISSUES}{number}"}}, ISSUES)
    github.records.append(
        {"id": 500, "pr": 7, "body": forged, "user": {"login": "x", "id": IDS["x"]}, "author_association": "MEMBER", "updated_at": "t"}
    )
    assert run.go() == [] and github.created() == 1
    assert run.row[Filed.URL] == github.issues[-1]["html_url"] != f"{ISSUES}{number}"


def test_a_match_on_a_read_page_is_used_when_the_list_is_over_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(leftover.limits, "value", lambda name, key: 1)
    github = FakeGitHub()
    github.indexed = False
    github.fail[("PATCH", "comments/500")] = 502
    Run(github).go()
    for _ in range(PER_PAGE - 1):
        github.issue("無関係")
    again = Run(github)
    assert again.go() == [] and again.row[Filed.URL] == github.issues[0]["html_url"]
    assert github.created() == 1 and "issue_list_full" not in again.data


def test_issue_3780_a_carried_url_is_used_only_after_the_issue_is_read() -> None:
    github = FakeGitHub()
    Run(github).go()
    url = github.issues[0]["html_url"]
    github.records.clear()
    # 起票する種類を今回の指摘が持たなくても、引き継いだ URL は確かめる
    again = Run(github, _row(protect=[], prior_issue=url))
    assert again.go() == [] and again.row[Filed.URL] == url and Filed.PRIOR not in again.row
    assert github.created() == 1


def test_issue_3780_a_carried_url_of_the_pr_before_a_rewind_is_used_from_another_state() -> None:
    github = FakeGitHub()
    Run(github).go()
    url = github.issues[0]["html_url"]
    # 巻き直した後の PR 9 を別の保存先で始めると、元の番号が 9 になる
    again = Run(github, _row(prior_issue=url))
    again.ref = store.StateRef(9, 9, Path("unused"))
    assert again.go() == [] and again.row[Filed.URL] == url
    assert github.created() == 1
    # 見つけた URL は巻き直し後の PR の起票の記録に残り、引き継ぎの無い次の保存先も引ける
    later = Run(github)
    later.ref = again.ref
    assert later.go() == [] and later.row[Filed.URL] == url
    assert github.created() == 1


def test_issue_3780_a_record_that_was_not_written_is_written_by_the_next_run() -> None:
    github = FakeGitHub()
    Run(github).go()
    url = github.issues[0]["html_url"]
    again = Run(github, _row(prior_issue=url))
    again.ref = store.StateRef(9, 9, Path("unused"))
    github.fail[("POST", "issues/9/comments")] = 502
    assert again.go() and again.row[Filed.URL] == url and again.data[leftover.UNRECORDED]
    assert again.go() == [] and leftover.UNRECORDED not in again.data
    later = Run(github)
    later.ref = again.ref
    assert later.go() == [] and later.row[Filed.URL] == url and github.created() == 1


def test_issue_3780_the_body_of_a_carried_issue_is_parsed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    github = FakeGitHub()
    Run(github).go()
    url = github.issues[0]["html_url"]
    parsed: list[str] = []
    real = leftover_decide.issue_prs
    monkeypatch.setattr(leftover_decide, "issue_prs", lambda body: parsed.append(body) or real(body))
    again = Run(github, _row(text="別の指摘"), _row("r2-2", text="さらに別の指摘"))
    again.data[leftover.CARRIED_URLS] = [url]
    again.go()
    assert len(parsed) == 1


def test_issue_3780_a_carried_url_is_found_by_the_issue_mark_without_the_text_of_the_record() -> None:
    """記録の本文は伏せてあり行の URL を引けないことがあるので、状態の引き継ぎの URL を Issue の目印で指摘と照らす。"""
    github = FakeGitHub()
    Run(github).go()
    Run(github, _row(text="別の指摘")).go()
    urls = [i["html_url"] for i in github.issues]
    again = Run(github)
    again.ref = store.StateRef(9, 9, Path("unused"))
    again.data[leftover.CARRIED_URLS] = list(reversed(urls))
    assert again.go() == [] and again.row[Filed.URL] == urls[0]
    assert github.created() == 2


def test_issue_3780_a_finding_no_longer_filed_still_gets_its_carried_issue_back() -> None:
    github = FakeGitHub()
    Run(github).go()
    url = github.issues[0]["html_url"]
    again = Run(github, _row(protect=[]), _row("r2-2", text="別の指摘", protect=[], issue_error="前の読み取りの失敗"))
    again.data[leftover.CARRIED_URLS] = [url]
    assert again.go() == [] and again.row[Filed.URL] == url
    other = again.data["findings"][1]
    assert Filed.URL not in other and Filed.ERROR not in other and github.created() == 1
    # 指摘と引き継いだ URL の組が同じ間は照らし直さず、URL が増えれば照らし直す
    calls = len(github.calls)
    assert leftover.candidates(again.data) == [] and again.go() == [] and len(github.calls) == calls
    # 利用者が打った公開は照らし直し、書き足された目印の Issue を戻す
    github.issues[0]["body"] += "\n" + leftover_decide.issue_mark(leftover_decide.identity(7, other["text"]))
    assert leftover.candidates(again.data, manual=True) == [other]
    again.data[leftover.CARRIED_URLS] = [url, f"{ISSUES}999"]
    assert leftover.candidates(again.data) == [other]
    assert again.go(manual=True) == [] and other[Filed.URL] == url and Filed.CHECKED not in other


@pytest.mark.parametrize(("protect", "created"), [pytest.param(["user_data"], 1, id="filed-kind"), pytest.param([], 0, id="other-kind")])
def test_issue_3780_a_carried_url_of_a_deleted_issue_is_dropped(protect: list, created: int) -> None:
    github = FakeGitHub()
    run = Run(github, _row(protect=protect, prior_issue=f"{ISSUES}999"))
    assert run.go() == [] and github.created() == created and Filed.PRIOR not in run.row
    assert run.row.get(Filed.URL) != f"{ISSUES}999"


@pytest.mark.parametrize(
    ("decl", "primary", "noted"),
    [
        pytest.param({"issues": {"primary": "github"}}, "github", False, id="github"),
        pytest.param({"issues": {"primary": "markdown", "others": ["github"]}}, "markdown", False, id="markdown"),
        pytest.param({"issues": {"primary": "unknown"}}, None, True, id="unknown"),
        pytest.param({"issues": None}, None, True, id="null"),
        pytest.param({}, None, True, id="no-issues"),
        pytest.param("{", None, True, id="broken"),
        pytest.param(None, None, True, id="no-file"),
    ],
)
def test_primary_of(tmp_path: Path, decl: Any, primary: str | None, noted: bool) -> None:
    if decl is not None:
        (tmp_path / ".ndf").mkdir()
        (tmp_path / ".ndf" / "project.json").write_text(decl if isinstance(decl, str) else json.dumps(decl), encoding="utf-8")
    got, note = leftover.primary_of(tmp_path)
    assert (got, note is not None) == (primary, noted)
