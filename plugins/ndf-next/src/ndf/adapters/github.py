"""GitHub の読み書き（外部との接続の層。githubkit の上に置く。#1142 の決定 16・17）。

- 単発の読み書きと読み直しは REST で行う。githubkit の `http_cache` が ETag 付きで読み直し、変わっていない応答は上限に数えられない
- 番号やブランチごとの読み取りは、GraphQL のエイリアスで 1 回のクエリへまとめる（A2）。ループの中で 1 件ずつ呼ばない
- 失敗は `GitHubError`（HTTP の状態・上限の回復までの秒）で返し、黙って空を返さない
- 認証は環境変数 `GH_TOKEN`・`GITHUB_TOKEN` か `gh auth token` の値を渡す（値はログにも結果にも出さない）

テストは githubkit のクライアントへ `httpx.MockTransport` を渡して、ネットワークへ出ずに確かめる。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional

import httpx
from githubkit import GitHub as Client
from githubkit.exception import GraphQLFailed, RateLimitExceeded, RequestError, RequestFailed

from ndf.adapters import git
from ndf.contracts import limits

# GraphQL のエイリアスで引ける番号つきの節点
NUMBERED_NODES = ("pullRequest", "issue", "issueOrPullRequest")
# PR を読むときの GraphQL の選択（`Pull.from_graphql` が読む項目）
PULL_FIELDS = "number state merged headRefOid headRefName baseRefName body mergeStateStatus"
# マージの要求を GitHub が断ったときの HTTP の状態（405: 規則・衝突、409: 先端が変わった、422: 形）
MERGE_REFUSED_STATUS = (405, 409, 422)
# マージの拒否を、拒否の後に読み直した `mergeStateStatus` で種類へ分ける（SF4）。標準エラーの文は読まない
_REFUSAL_KINDS = {"dirty": "base_conflict", "blocked": "not_ci", "behind": "not_ci", "draft": "not_ci"}
# PR 本文の「マージの前に決める」残リスクの印と、決めたことを表すチェック済みの項目（#2602）
PREMERGE_MARK = "マージの前に決める"
_CHECKED_ITEM = re.compile(r"^\s*[-*+]\s+\[[xX]\]\s")
# 由来の参照（`issue #12`・`PR #12`）。別のリポジトリへ書くときはリポジトリを足す（#3077）
_BARE_REF = re.compile(r"^(?P<label>(?:issue|PR|pr|Issue)\s*)#(?P<number>\d+)$")


class GitHubError(Exception):
    """GitHub の要求が失敗した。`status` は HTTP の状態（届かなければ 0）、`retry_after` は上限の回復までの秒。"""

    def __init__(self, detail: str, status: int = 0, retry_after: Optional[float] = None) -> None:
        super().__init__(detail)
        self.status = status
        self.retry_after = retry_after


def token() -> str:
    """認証の値。環境変数を先に読み、無ければ `gh auth token`。得られなければ `GitHubError`。"""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    try:
        done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GitHubError(f"gh を起動できない: {exc}") from None
    if done.returncode != 0 or not done.stdout.strip():
        raise GitHubError("GitHub の認証が無い（GH_TOKEN も gh auth token も無い）")
    return done.stdout.strip()


def client(auth: Optional[str] = None, transport: Optional[httpx.BaseTransport] = None) -> Client:
    """githubkit のクライアント（ETag の読み直しと再試行つき）。`transport` はテストが差し替える。"""
    return Client(auth if auth is not None else token(), http_cache=True, auto_retry=True, transport=transport)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class Pull(NamedTuple):
    """PR の状態。`state` は `open`・`closed`、`merge_state` は GitHub の mergeStateStatus の小文字。"""

    number: int
    state: str
    merged: bool
    head_sha: str
    head_ref: str
    base_ref: str
    body: str
    merge_state: str

    @classmethod
    def from_rest(cls, raw: dict) -> Pull:
        head, base = raw["head"], raw["base"]
        return cls(
            int(raw["number"]), _text(raw["state"]).lower(), bool(raw["merged"]), _text(head["sha"]),
            _text(head["ref"]), _text(base["ref"]), _text(raw["body"]), _text(raw["mergeable_state"]).lower(),
        )  # fmt: skip

    @classmethod
    def from_graphql(cls, raw: dict) -> Pull:
        state = _text(raw["state"]).lower()
        return cls(
            int(raw["number"]), "open" if state == "open" else "closed", bool(raw["merged"]), _text(raw["headRefOid"]),
            _text(raw["headRefName"]), _text(raw["baseRefName"]), _text(raw["body"]), _text(raw["mergeStateStatus"]).lower(),
        )  # fmt: skip


class MergeOutcome(NamedTuple):
    """マージの結果。断られたら `kind` に止まった理由の種類（`refused`・`base_conflict`・`not_ci`・`unknown`）。"""

    merged: bool
    kind: Optional[str]
    detail: str
    head_sha: str


def premerge_risks(body: str) -> list[str]:
    """PR 本文の「マージの前に決める」残リスクの行のうち、決めた印（チェック済みの項目 `- [x]`）の無いもの（#2602）。"""
    return [line.strip() for line in body.splitlines() if PREMERGE_MARK in line and not _CHECKED_ITEM.match(line)]


def qualified_ref(origin: str, source_repo: str, target_repo: str) -> str:
    """由来の参照を、書き込む先のリポジトリの画面でも同じ課題を指す形にする（#3077）。

    書き込む先が由来のリポジトリと違えば、番号だけの参照（`issue #12`）を `issue <所有者>/<リポジトリ>#12` にする。
    同じリポジトリ・番号だけの形でない参照はそのまま返す。
    """
    m = _BARE_REF.match(origin.strip())
    if source_repo == target_repo or m is None:
        return origin
    return f"{m.group('label')}{source_repo}#{m.group('number')}"


def refusal_kind(after: Pull, sent_sha: str) -> str:
    """マージを断られた後に読み直した PR から、止まった理由の種類を決める（SF4）。"""
    if after.head_sha != sent_sha:
        return "refused"
    return _REFUSAL_KINDS.get(after.merge_state, "unknown")  # noqa: NDF502 ネットワーク: 表に無い mergeStateStatus（clean・unstable・unknown）は種類を決められない


def _response_body(resp: Any) -> Any:
    if not resp.content:
        return None
    if "json" in resp.headers.get("content-type", ""):  # noqa: NDF502 ネットワーク: 応答に content-type が無いことがある
        return resp.json()
    return resp.text


def _aliased_query(aliases: dict[str, str]) -> str:
    body = " ".join(f"{alias}: {selection}" for alias, selection in aliases.items())
    return f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {body} }} }}"


def _only_not_found(exc: GraphQLFailed) -> bool:
    errors = exc.response.errors
    return bool(errors) and all(e.type == "NOT_FOUND" for e in errors)


@dataclass(frozen=True)
class GitHub:
    """1 つのリポジトリ（`<所有者>/<リポジトリ>`）への GitHub の読み書き。"""

    repo: str
    api: Client

    def rest(self, method: str, path: str, payload: Any = None, params: Optional[dict] = None) -> Any:
        """REST の 1 回の要求の本文（JSON なら値、ほかは文字列、空なら None）。失敗は `GitHubError`。"""
        try:
            resp = self.api.request(method, path, json=payload, params=params)
        except RateLimitExceeded as exc:
            status = exc.response.status_code
            raise GitHubError(f"GitHub の上限: {method} {path}", status, exc.retry_after.total_seconds()) from None
        except RequestFailed as exc:
            status = exc.response.status_code
            raise GitHubError(f"GitHub が断った（{status}）: {method} {path}: {exc.response.text[:300]}", status) from None
        except RequestError as exc:
            raise GitHubError(f"GitHub へ届かない: {method} {path}: {exc}") from None
        return _response_body(resp)

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """GraphQL の 1 回の要求の `data`。読めない番号（`NOT_FOUND`）だけの誤りなら、読めた分を返す。"""
        try:
            return self.api.graphql(query, variables)
        except GraphQLFailed as exc:
            if _only_not_found(exc) and exc.response.data is not None:
                return exc.response.data
            raise GitHubError(f"GraphQL の誤り: {'; '.join(e.message for e in exc.response.errors)}") from None
        except RateLimitExceeded as exc:
            raise GitHubError("GitHub の上限: graphql", exc.response.status_code, exc.retry_after.total_seconds()) from None
        except RequestFailed as exc:
            raise GitHubError(f"GitHub が断った（{exc.response.status_code}）: graphql", exc.response.status_code) from None
        except RequestError as exc:
            raise GitHubError(f"GitHub へ届かない: graphql: {exc}") from None

    def aliased(self, selections: dict[str, str]) -> dict[str, Any]:
        """名前 → `repository` の下の選択を、`github.graphql-batch` 件ずつのエイリアスのクエリで読む（A2）。

        返す辞書は `selections` のすべての名前を鍵に持ち、読めなかった名前の値は None。途中で失敗したら
        `GitHubError` で、一部だけの結果を返さない。
        """
        owner, name = self.repo.split("/", 1)
        size = int(limits.value("github.graphql-batch", "count"))
        keys = list(selections)
        out: dict[str, Any] = {}
        for start in range(0, len(keys), size):
            chunk = {f"a{i}": selections[k] for i, k in enumerate(keys[start : start + size])}
            data = self.graphql(_aliased_query(chunk), {"owner": owner, "name": name})["repository"]
            out.update({k: data.get(alias) for alias, k in zip(chunk, keys[start : start + size])})
        return out

    def nodes(self, numbers: Iterable[int], node: str, fields: str) -> dict[int, dict]:
        """番号ごとの `node(number: N) { fields }` をまとめて読む。その種類で無い・無い番号の値は空の辞書。"""
        if node not in NUMBERED_NODES:
            raise ValueError(f"番号で引けない節点: {node}（{', '.join(NUMBERED_NODES)}）")
        wanted = sorted({int(n) for n in numbers})
        got = self.aliased({str(n): f"{node}(number: {n}) {{ {fields} }}" for n in wanted})
        return {n: got[str(n)] if isinstance(got[str(n)], dict) else {} for n in wanted}

    def pulls(self, numbers: Iterable[int]) -> dict[int, Optional[Pull]]:
        """番号ごとの PR を 1 回のクエリ（100 件ごと）で読む。PR でない・無い番号は None。"""
        raw = self.nodes(numbers, "pullRequest", PULL_FIELDS)
        return {n: Pull.from_graphql(v) if v else None for n, v in raw.items()}

    def pulls_by_head(self, branches: Iterable[str]) -> dict[str, list[Pull]]:
        """head のブランチごとの PR（新しい順に 5 件まで）を 1 回のクエリ（100 件ごと）で読む。"""
        wanted = sorted(set(branches))
        selection = "pullRequests(headRefName: {}, first: 5, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ nodes {{ {} }} }}"
        got = self.aliased({b: selection.format(_graphql_string(b), PULL_FIELDS) for b in wanted})
        return {b: [Pull.from_graphql(p) for p in got[b]["nodes"]] if got[b] else [] for b in wanted}

    def pull(self, number: int) -> Pull:
        return Pull.from_rest(self.rest("GET", f"/repos/{self.repo}/pulls/{int(number)}"))

    def check_runs(self, sha: str) -> list[dict]:
        """コミットのチェックの生の一覧を `total_count` に届くまで読む（`github.check-runs` ページまで）。"""
        pages = int(limits.value("github.check-runs", "count"))
        runs: list[dict] = []
        for page in range(1, pages + 1):
            body = self.rest("GET", f"/repos/{self.repo}/commits/{sha}/check-runs", params={"per_page": 100, "page": page})
            runs.extend(body["check_runs"])
            if len(runs) >= int(body["total_count"]) or not body["check_runs"]:
                return runs
        raise GitHubError(f"{sha} のチェックが {pages} ページを超える（上限 github.check-runs）")

    def job_log(self, job_id: int) -> str:
        """Actions のジョブのログの本文。"""
        body = self.rest("GET", f"/repos/{self.repo}/actions/jobs/{int(job_id)}/logs")
        return body if isinstance(body, str) else ""

    def merge(self, number: int, expect_head: Optional[str] = None, method: str = "squash") -> MergeOutcome:
        """PR をマージする。先端の食い違い・決めていない残リスクは要求を送らずに断り、拒否は種類で返す（SF4）。

        `expect_head` は完全か短い SHA（#2041）。要求には読んだ先端の完全な SHA を添え、その後に先端が
        動いていれば GitHub が断る。
        """
        pull = self.pull(number)
        if pull.merged:
            return MergeOutcome(True, None, f"#{number} は既にマージ済み", pull.head_sha)
        if expect_head and not git.sha_matches(expect_head, pull.head_sha):
            return MergeOutcome(False, "refused", f"#{number} の先端が {expect_head} でない（今は {pull.head_sha}）", pull.head_sha)
        risks = premerge_risks(pull.body)
        if risks:
            return MergeOutcome(False, "refused", f"#{number} の本文にマージの前に決める残リスクがある: {risks[0]}", pull.head_sha)
        try:
            self.rest("PUT", f"/repos/{self.repo}/pulls/{number}/merge", {"sha": pull.head_sha, "merge_method": method})
        except GitHubError as exc:
            if exc.status not in MERGE_REFUSED_STATUS:
                raise
            after = self.pull(number)
            return MergeOutcome(False, refusal_kind(after, pull.head_sha), str(exc), after.head_sha)
        return MergeOutcome(True, None, f"#{number} をマージした", pull.head_sha)


def _graphql_string(value: str) -> str:
    """GraphQL の文字列の値（JSON の文字列と同じ書き方）。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
