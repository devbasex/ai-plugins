"""GitHub の読み書き（外部との接続の層。githubkit の上に置く。#1142 の決定 16・17）。

- 単発の読み書きと読み直しは REST で行う。githubkit の `http_cache` が ETag 付きで読み直し、変わっていない応答は上限に数えられない
- 番号やブランチごとの読み取りは、GraphQL のエイリアスで 1 回のクエリへまとめる（A2）。ループの中で 1 件ずつ呼ばない
- 片方の枠（REST・GraphQL）が上限のとき、代われる読み取り（PR の状態）はもう片方の枠で読む。代われない操作は
  githubkit の再試行が回復の時刻まで待ってから打ち直し、待ちの残りを越えるなら `retry_after` で呼び手へ返す（不足 g）
- 件数と一覧は `totalCount` とページ送りで読み切り、既定の件数で黙って切らない（#3393）
- 本文には書き手と `author_association` を添え、write 以上の権限の無い書き手の本文を信用しない区分で返す（#3364）
- 失敗は `GitHubError`（HTTP の状態・上限の回復までの秒・上限に当たった枠）で返し、黙って空を返さない
- 認証は環境変数 `GH_TOKEN`・`GITHUB_TOKEN` か `gh auth token` の値を渡す（値はログにも結果にも出さない）

Issue・コメント・PR の本文を書く操作は、公開範囲を読むエントリポイント（`ndf.adapters.publish.Publisher`）を通す（#3391）。
テストは githubkit のクライアントへ `httpx.MockTransport` を渡して、ネットワークへ出ずに確かめる。
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional, TypeVar
from urllib.parse import quote

import httpx
from githubkit import GitHub as Client
from githubkit.exception import GitHubException, GraphQLFailed, RateLimitExceeded, RequestError, RequestFailed
from githubkit.retry import RETRY_DEFAULT, RetryOption

from ndf.adapters import git
from ndf.contracts import limits

T = TypeVar("T")
# GraphQL のエイリアスで引ける番号つきの節点
NUMBERED_NODES = ("pullRequest", "issue", "issueOrPullRequest")
# PR を読むときの GraphQL の選択（`Pull.from_graphql` が読む項目）
PULL_FIELDS = "number state merged headRefOid headRefName baseRefName body mergeStateStatus author { login } authorAssociation"
# head のブランチごとに読む PR の件数（新しい順）
PULLS_PER_HEAD = 5
# 一覧の 1 ページの件数（REST と GraphQL の上限。ページ数の上限は limits.json の github.check-runs・github.list-pages）
PER_PAGE = 100
# 上限に当たった枠（`GitHubError.quota`）
REST, GRAPHQL = "rest", "graphql"
# マージの要求を GitHub が断ったときの HTTP の状態（405: 規則・衝突、409: 先端が変わった、422: 形）
MERGE_REFUSED_STATUS = (405, 409, 422)
# マージの拒否を、拒否の後に読み直した `mergeStateStatus` で種類へ分ける（SF4）。標準エラーの文は読まない
_REFUSAL_KINDS = {"dirty": "base_conflict", "blocked": "not_ci", "behind": "not_ci", "draft": "not_ci"}
# マージの方式 → 「基底に含まれたか」の確かめ方（3378-2）。ancestry は head が基底の祖先か、merged は PR がマージ済み
# （`merged_at` がある）か。squash と rebase は新しいコミットを作り、head は基底の祖先にならない。
# 並びは、宣言が無いときに許された方式から選ぶ順
ANCESTRY, MERGED = "ancestry", "merged"


class _MergeMethod(NamedTuple):
    """マージの方式 1 つ。`landing` は基底に含まれたかの確かめ方、`setting` はリポジトリの設定の許可の鍵
    （ruleset に許す方式の指定が無いときに読む）。"""

    landing: str
    setting: str


# マージの方式の唯一の定義（並びは宣言が無いときに選ぶ順）。`MERGE_METHODS` は方式 → 確かめ方
_MERGE_METHOD_SPECS = {
    "squash": _MergeMethod(MERGED, "allow_squash_merge"),
    "merge": _MergeMethod(ANCESTRY, "allow_merge_commit"),
    "rebase": _MergeMethod(MERGED, "allow_rebase_merge"),
}
MERGE_METHODS = {name: spec.landing for name, spec in _MERGE_METHOD_SPECS.items()}
# 信用の区分（#3364）。OWNER はリポジトリの持ち主でそのまま信用する。MEMBER・COLLABORATOR は所属を表すだけで
# write を保証しない（read・triage もある）ため、対象リポジトリでの権限を読んで write 以上のときだけ信用する。
# ほかの値（CONTRIBUTOR・FIRST_TIME_CONTRIBUTOR・FIRST_TIMER・MANNEQUIN・NONE と知らない値）の本文は信用しない入力
OWNER_ASSOCIATION = "OWNER"
PERMISSION_CHECKED_ASSOCIATIONS = frozenset({"MEMBER", "COLLABORATOR"})
# 権限の読み取り（REST の collaborators/{login}/permission の `permission`）で write 以上を表す値。maintain は write に写る
WRITE_PERMISSIONS = frozenset({"admin", "write"})
# リポジトリの公開範囲（GraphQL の `visibility`）。読めないリポジトリは UNKNOWN で、公開でないとして扱う
PUBLIC, UNKNOWN = "PUBLIC", "UNKNOWN"
# PR 本文の「マージの前に決める」残リスクの印と、決めたことを表すチェック済みの項目（#2602）
PREMERGE_MARK = "マージの前に決める"
_CHECKED_ITEM = re.compile(r"^\s*[-*+]\s+\[[xX]\]\s")


class GitHubError(Exception):
    """GitHub の要求が失敗した。`status` は HTTP の状態（届かなければ 0）、`retry_after` は上限の回復までの秒、
    `quota` は上限に当たった枠（`rest`・`graphql`。上限でなければ None）。"""

    def __init__(self, detail: str, status: int = 0, retry_after: Optional[float] = None, quota: Optional[str] = None) -> None:
        super().__init__(detail)
        self.status = status
        self.retry_after = retry_after
        self.quota = quota


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


class RetryWithin:
    """githubkit の再試行の判断（既定の判断に、上限の待ちの残りを足す）。

    githubkit の既定は上限（rate limit）の解除の時刻まで同期で眠るため、待ちの予算を持つ呼び手（CI の監視）が
    数十分戻らない。解除が `remaining`（呼んだ時点で待ちに使える残りの秒）を越える上限は再試行せず、
    `GitHubError` の `retry_after` で呼び手へ返す。残りは呼び手が自分の予算の数え方で返す（`Watch.remaining` は
    ランナーの割り当ての待ちを予算に数えない）。サーバーの誤り（5xx）の再試行は既定のまま。
    """

    def __init__(self, remaining: Callable[[], float]) -> None:
        self.remaining = remaining

    def __call__(self, exc: GitHubException, retry_count: int) -> RetryOption:
        option = RETRY_DEFAULT(exc, retry_count)
        if option.do_retry and isinstance(exc, RateLimitExceeded):
            if exc.retry_after.total_seconds() > self.remaining():
                return RetryOption(False)
        return option


def client(
    auth: Optional[str] = None, transport: Optional[httpx.BaseTransport] = None, remaining: Optional[Callable[[], float]] = None
) -> Client:
    """githubkit のクライアント（ETag の読み直しと再試行つき）。`transport` はテストが差し替える。

    `remaining`（待ちに使える残りの秒を返す関数）を渡すと、解除がそれを越える上限の待ちは再試行しない（`RetryWithin`）。
    省くと上限（rate limit）は待たずに `GitHubError` で返す（残りを 0 秒とみなす）。githubkit の既定は解除の時刻まで
    （一次上限なら最大 1 時間）眠るため、そのままでは枠の読み替え（`GitHub.pulls`・`GitHub.pull`。不足 g）の前に止まる。
    """
    budget = remaining if remaining is not None else _no_wait
    return Client(auth if auth is not None else token(), http_cache=True, auto_retry=RetryWithin(budget), transport=transport)


def _no_wait() -> float:
    """上限の待ちに使える残りの秒（0。上限を再試行しない）。"""
    return 0.0


def text_of(value: Any) -> str:
    """文字列ならそのまま、ほかの値（None など）は空文字。"""
    return value if isinstance(value, str) else ""


def _login(user: Any) -> str:
    """書き手の login。消えたアカウント（REST の `user`・GraphQL の `author` が null）は空文字。"""
    return text_of(user["login"]) if isinstance(user, dict) else ""


class Authored(NamedTuple):
    """書き手の付いた本文（Issue・PR・コメント）。`association` は GitHub の author_association（大文字）。

    信用の区分は対象リポジトリでの権限で決まるため、`GitHub.trusts` が判定する（#3364）。
    """

    author: str
    association: str
    body: str

    @classmethod
    def from_rest(cls, raw: dict) -> Authored:
        return cls(_login(raw["user"]), text_of(raw["author_association"]).upper(), text_of(raw["body"]))


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
    author: str
    association: str

    @property
    def authored(self) -> Authored:
        """PR の本文と書き手（信用の区分は `GitHub.trusts`）。"""
        return Authored(self.author, self.association, self.body)

    @classmethod
    def from_rest(cls, raw: dict) -> Pull:
        head, base = raw["head"], raw["base"]
        return cls(
            int(raw["number"]), text_of(raw["state"]).lower(), bool(raw["merged"]), text_of(head["sha"]),
            text_of(head["ref"]), text_of(base["ref"]), text_of(raw["body"]), text_of(raw["mergeable_state"]).lower(),
            _login(raw["user"]), text_of(raw["author_association"]).upper(),
        )  # fmt: skip

    @classmethod
    def from_graphql(cls, raw: dict) -> Pull:
        state = text_of(raw["state"]).lower()
        return cls(
            int(raw["number"]), "open" if state == "open" else "closed", bool(raw["merged"]), text_of(raw["headRefOid"]),
            text_of(raw["headRefName"]), text_of(raw["baseRefName"]), text_of(raw["body"]), text_of(raw["mergeStateStatus"]).lower(),
            _login(raw["author"]), text_of(raw["authorAssociation"]).upper(),
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


def same_repo(a: str, b: str) -> bool:
    """2 つのリポジトリ名（`<所有者>/<リポジトリ>`）が同じか。GitHub の名前は大文字小文字を区別しない。"""
    return a.lower() == b.lower()


def pick_merge_method(declared: Optional[str], allowed: Sequence[str]) -> Optional[str]:
    """マージの方式を決める（3378-2）。宣言の値があればそれ（許されていなければ None）、無ければ許された方式の先頭。"""
    if declared is not None:
        return declared if declared in allowed else None
    return allowed[0] if allowed else None


def ruleset_merge_methods(rules: Iterable[dict]) -> Optional[list[str]]:
    """ブランチに当たる規則（`GET /repos/{repo}/rules/branches/{branch}`）が許すマージの方式（`MERGE_METHODS` の並び）。

    PR の規則が許す方式を指定していなければ None（リポジトリの設定を読む）。規則が 2 つ以上なら、どれもが許す方式だけ。
    """
    found: Optional[set[str]] = None
    for rule in rules:
        if rule["type"] != "pull_request" or "allowed_merge_methods" not in rule["parameters"]:
            continue
        methods = set(rule["parameters"]["allowed_merge_methods"])
        found = methods if found is None else found & methods
    return None if found is None else [m for m in MERGE_METHODS if m in found]


def landed(pull: Pull, method: str, base_ref: str, repo: git.Git) -> bool:
    """PR の head が基底 `base_ref`（`origin/develop` など）に含まれたかを、マージの方式ごとに確かめる（3378-2）。

    merge は head が基底の祖先か（git）、squash と rebase は PR がマージ済みか（`merged_at`）で読む。
    """
    if method not in MERGE_METHODS:
        raise ValueError(f"知らないマージの方式: {method}（{', '.join(MERGE_METHODS)}）")
    if MERGE_METHODS[method] == ANCESTRY:
        return repo.is_ancestor(pull.head_sha, base_ref)
    return pull.merged


def refusal_kind(after: Pull, sent_sha: str) -> str:
    """マージを断られた後に読み直した PR から、止まった理由の種類を決める（SF4）。"""
    if after.head_sha != sent_sha:
        return "refused"
    return _REFUSAL_KINDS.get(after.merge_state, "unknown")  # noqa: NDF502 ネットワーク: 表に無い mergeStateStatus（clean・unstable・unknown）は種類を決められない


def _preflight(pull: Pull, number: int, expect_head: Optional[str]) -> Optional[MergeOutcome]:
    """マージの要求を送らずに返す結果（マージ済み・先端の食い違い・決めていない残リスク）。送ってよければ None。"""
    if pull.merged:
        return MergeOutcome(True, None, f"#{number} は既にマージ済み", pull.head_sha)
    if expect_head and not git.sha_matches(expect_head, pull.head_sha):
        return MergeOutcome(False, "refused", f"#{number} の先端が {expect_head} でない（今は {pull.head_sha}）", pull.head_sha)
    risks = premerge_risks(pull.body)
    if risks:
        return MergeOutcome(False, "refused", f"#{number} の本文にマージの前に決める残リスクがある: {risks[0]}", pull.head_sha)
    return None


def _method_refusal(pull: Pull, number: int, method: Optional[str], allowed: Sequence[str]) -> MergeOutcome:
    """マージの方式が基底のブランチで許されていないときの、要求を送らずに返す断り（3378-2）。"""
    want = method if method is not None else "（宣言なし）"
    return MergeOutcome(
        False,
        "refused",
        f"#{number} のマージの方式 {want} は {pull.base_ref} で許されていない（許す方式: {', '.join(allowed)}）",
        pull.head_sha,
    )


def _response_body(resp: Any) -> Any:
    if not resp.content:
        return None
    if "json" in resp.headers.get("content-type", ""):  # noqa: NDF502 ネットワーク: 応答に content-type が無いことがある
        return resp.json()
    return resp.text


def _alias(i: int) -> str:
    """1 回のクエリの中の `i` 番目のエイリアスの名前（`a0`・`a1`…）。"""
    return f"a{i}"


def _batches(items: Sequence[T]) -> Iterator[Sequence[T]]:
    """`items` を `github.graphql-batch` 件ずつに切る（1 つが 1 回のエイリアスのクエリ）。"""
    size = int(limits.value("github.graphql-batch", "count"))
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _aliased_query(aliases: dict[str, str]) -> str:
    body = " ".join(f"{alias}: {selection}" for alias, selection in aliases.items())
    return f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {body} }} }}"


def _only_not_found(exc: GraphQLFailed) -> bool:
    errors = exc.response.errors
    return bool(errors) and all(e.type == "NOT_FOUND" for e in errors)


def _translated(exc: RequestFailed | RequestError, label: str, detail_body: bool = False) -> GitHubError:
    """githubkit の失敗を `GitHubError` へ写す。`label` は要求の名前（`GET /path`・`graphql`）。

    上限（`RateLimitExceeded`。`RequestFailed` の下位なので先に見る）は回復までの秒を、HTTP の拒否は状態を添える。
    `detail_body` なら拒否の文へ応答の本文の先頭 300 字を足す。
    """
    if isinstance(exc, RateLimitExceeded):
        quota = GRAPHQL if label == GRAPHQL else REST
        return GitHubError(f"GitHub の上限（{quota} の枠）: {label}", exc.response.status_code, exc.retry_after.total_seconds(), quota)
    if isinstance(exc, RequestFailed):
        status = exc.response.status_code
        body = f": {exc.response.text[:300]}" if detail_body else ""
        return GitHubError(f"GitHub が断った（{status}）: {label}{body}", status)
    return GitHubError(f"GitHub へ届かない: {label}: {exc}")


def _page_cap(name: str) -> int:
    """一覧を読むページ数の上限（limits.json の `name` の `count`）。"""
    return int(limits.value(name, "count"))


def _too_many_pages(what: str, pages: int, name: str) -> GitHubError:
    """一覧が上限 `name` の `pages` ページを超えたときの失敗（途中で切らずに止まる）。`what` は主語と助詞まで。"""
    return GitHubError(f"{what} {pages} ページを超える（上限 {name}）")


@contextmanager
def _translating(label: str, detail_body: bool = False) -> Iterator[None]:
    """githubkit の上限・拒否・不達を `_translated` で `GitHubError` へ写して投げ直す。"""
    try:
        yield
    except (RateLimitExceeded, RequestFailed, RequestError) as exc:
        raise _translated(exc, label, detail_body) from None


@dataclass(frozen=True)
class GitHub:
    """1 つのリポジトリ（`<所有者>/<リポジトリ>`）への GitHub の読み書き。"""

    repo: str
    api: Client
    # 書き手の login → 対象リポジトリでの権限（`permission`）。1 つの書き手を 1 回だけ読む
    _permissions: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def rest(self, method: str, path: str, payload: Any = None, params: Optional[dict] = None) -> Any:
        """REST の 1 回の要求の本文（JSON なら値、ほかは文字列、空なら None）。失敗は `GitHubError`。"""
        with _translating(f"{method} {path}", detail_body=True):
            resp = self.api.request(method, path, json=payload, params=params)
        return _response_body(resp)

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """GraphQL の 1 回の要求の `data`。読めない番号（`NOT_FOUND`）だけの誤りなら、読めた分を返す。"""
        with _translating(GRAPHQL):
            try:
                return self.api.graphql(query, variables)
            except GraphQLFailed as exc:
                if _only_not_found(exc) and exc.response.data is not None:
                    return exc.response.data
                raise GitHubError(f"GraphQL の誤り: {'; '.join(e.message for e in exc.response.errors)}") from None

    def _owner_name(self) -> dict[str, str]:
        owner, name = self.repo.split("/", 1)
        return {"owner": owner, "name": name}

    def repository(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """`repository(owner, name)` の下を読むクエリの `repository` の値。null（読めない・無い）なら `GitHubError`（3394-6）。"""
        data = self.graphql(query, {**self._owner_name(), **(variables if variables is not None else {})})
        repo = data["repository"] if isinstance(data, dict) and "repository" in data else None
        if not isinstance(repo, dict):
            raise GitHubError(f"{self.repo} を GraphQL で読めない（repository が null。リポジトリが無いか、権限が無い）")
        return repo

    def aliased(self, selections: dict[str, str]) -> dict[str, Any]:
        """名前 → `repository` の下の選択を、`github.graphql-batch` 件ずつのエイリアスのクエリで読む（A2）。

        返す辞書は `selections` のすべての名前を鍵に持ち、読めなかった名前の値は None。途中で失敗したら
        `GitHubError` で、一部だけの結果を返さない。リポジトリが null なら `GitHubError`（3394-6）。
        """
        out: dict[str, Any] = {}
        for keys in _batches(list(selections)):
            chunk = {_alias(i): selections[k] for i, k in enumerate(keys)}
            data = self.repository(_aliased_query(chunk))
            out.update({k: data[alias] if alias in data else None for alias, k in zip(chunk, keys)})
        return out

    def nodes(self, numbers: Iterable[int], node: str, fields: str) -> dict[int, dict]:
        """番号ごとの `node(number: N) { fields }` をまとめて読む。その種類で無い・無い番号の値は空の辞書。"""
        if node not in NUMBERED_NODES:
            raise ValueError(f"番号で引けない節点: {node}（{', '.join(NUMBERED_NODES)}）")
        wanted = sorted({int(n) for n in numbers})
        got = self.aliased({str(n): f"{node}(number: {n}) {{ {fields} }}" for n in wanted})
        return {n: got[str(n)] if isinstance(got[str(n)], dict) else {} for n in wanted}

    def pulls(self, numbers: Iterable[int]) -> dict[int, Optional[Pull]]:
        """番号ごとの PR を 1 回のクエリ（100 件ごと）で読む。PR でない・無い番号は None。

        GraphQL の枠が上限なら、`github.rest-fallback` 件までは REST の枠で読み替える（不足 g）。
        """
        wanted = sorted({int(n) for n in numbers})
        try:
            raw = self.nodes(wanted, "pullRequest", PULL_FIELDS)
        except GitHubError as exc:
            if exc.quota != GRAPHQL or len(wanted) > int(limits.value("github.rest-fallback", "count")):
                raise
            # 1 件ずつの REST の読み取りは github.rest-fallback 件まで（上で確かめた）
            return {n: self._rest_pull_or_none(n) for n in wanted}
        return {n: Pull.from_graphql(v) if v else None for n, v in raw.items()}

    def _rest_pull(self, number: int) -> Pull:
        """REST で読んだ PR。失敗は `GitHubError`。"""
        return Pull.from_rest(self.rest("GET", f"/repos/{self.repo}/pulls/{int(number)}"))

    def _rest_pull_or_none(self, number: int) -> Optional[Pull]:
        """REST で読んだ PR。無い番号（404）は None。"""
        try:
            return self._rest_pull(number)
        except GitHubError as exc:
            if exc.status != 404:
                raise
            return None

    def pulls_by_head(self, branches: Iterable[str]) -> dict[str, list[Pull]]:
        """head のブランチごとの PR（新しい順に 5 件まで）を 1 回のクエリ（100 件ごと）で読む。"""
        wanted = sorted(set(branches))
        selection = "pullRequests(headRefName: {}, first: {}, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ nodes {{ {} }} }}"
        got = self.aliased({b: selection.format(_graphql_string(b), PULLS_PER_HEAD, PULL_FIELDS) for b in wanted})
        return {b: [Pull.from_graphql(p) for p in got[b]["nodes"]] if got[b] else [] for b in wanted}

    def pull(self, number: int) -> Pull:
        """PR を REST で読む。REST の枠が上限なら GraphQL の枠で読み替える（不足 g）。"""
        try:
            return self._rest_pull(number)
        except GitHubError as exc:
            if exc.quota != REST:
                raise
            # 読み替えた先の枠も上限なら、REST へ戻さずにその上限で止まる
            raw = self.nodes([number], "pullRequest", PULL_FIELDS)[int(number)]
            if not raw:
                raise GitHubError(f"#{number} は PR でない（GraphQL の枠で読み替えた）", 404) from None
            return Pull.from_graphql(raw)

    def issue_body(self, number: int) -> Authored:
        """Issue（か PR）の本文と書き手。信用の区分は `trusts`（#3364）。"""
        return Authored.from_rest(self.rest("GET", f"/repos/{self.repo}/issues/{int(number)}"))

    def trusts(self, authored: Authored) -> bool:
        """write 以上の権限を持つ書き手の本文か（#3364）。偽なら読み手は本文を指示でなくデータとして扱う。

        OWNER はそのまま信用し、MEMBER・COLLABORATOR は対象リポジトリでの権限を読んで write 以上のときだけ信用する。
        """
        if authored.association == OWNER_ASSOCIATION:
            return True
        if authored.association not in PERMISSION_CHECKED_ASSOCIATIONS or not authored.author:
            return False
        return self.permission(authored.author) in WRITE_PERMISSIONS

    def permission(self, login: str) -> str:
        """`login` の対象リポジトリでの権限（`admin`・`write`・`read`・`none`）。書き手ごとに 1 回だけ読む。

        読めない（404・上限でない 403）なら `none` として信用しない。上限と他の失敗は `GitHubError` のまま投げる。
        """
        if login not in self._permissions:
            self._permissions[login] = self._read_permission(login)
        return self._permissions[login]

    def _read_permission(self, login: str) -> str:
        try:
            raw = self.rest("GET", f"/repos/{self.repo}/collaborators/{quote(login, safe='')}/permission")
        except GitHubError as exc:
            if exc.quota is None and exc.status in (403, 404):
                return "none"
            raise
        return text_of(raw["permission"]).lower() if isinstance(raw, dict) and "permission" in raw else "none"

    def comments(self, number: int) -> list[Authored]:
        """Issue（か PR）のコメントを古い順にすべて読む（`github.list-pages` ページまで。超えたら `GitHubError`）。"""
        return [Authored.from_rest(c) for c in self._pages(f"/repos/{self.repo}/issues/{int(number)}/comments")]

    def _pages(self, path: str) -> list[dict]:
        """配列を返す REST の一覧を、短いページが来るまで読む。`github.list-pages` ページを超えたら途中で切らずに止まる。"""
        pages = _page_cap("github.list-pages")
        rows: list[dict] = []
        for page in range(1, pages + 1):
            body = self.rest("GET", path, params={"per_page": PER_PAGE, "page": page})
            rows.extend(body)
            if len(body) < PER_PAGE:
                return rows
        raise _too_many_pages(f"{path} が", pages, "github.list-pages")

    def count_issues(self, labels: Sequence[str] = (), state: str = "OPEN") -> int:
        """ラベルと状態の Issue（PR を除く）の件数を `totalCount` で数える。既定の件数の上限で切れない（#3393）。"""
        query = (
            "query($owner: String!, $name: String!, $labels: [String!], $states: [IssueState!]) {"
            " repository(owner: $owner, name: $name) { issues(labels: $labels, states: $states) { totalCount } } }"
        )
        repo = self.repository(query, _issue_filter(labels, state))
        return int(repo["issues"]["totalCount"])

    def issue_numbers(self, labels: Sequence[str] = (), state: str = "OPEN") -> list[int]:
        """ラベルと状態の Issue（PR を除く）の番号をページ送りですべて読む（#3393）。

        読んだ件数が `totalCount` と合わなければ `GitHubError`。`github.list-pages` ページを超えたら途中で切らずに止まる。
        """
        pages = _page_cap("github.list-pages")
        variables: dict[str, Any] = {**_issue_filter(labels, state), "after": None}
        numbers: list[int] = []
        for _ in range(pages):
            conn = self.repository(_ISSUE_PAGE, variables)["issues"]
            numbers.extend(int(n["number"]) for n in conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                if len(numbers) != int(conn["totalCount"]):
                    raise GitHubError(f"Issue の番号を {len(numbers)} 件読んだが、totalCount は {conn['totalCount']} 件")
                return numbers
            variables["after"] = conn["pageInfo"]["endCursor"]
        raise _too_many_pages("Issue の一覧が", pages, "github.list-pages")

    def visibilities(self, repos: Iterable[str]) -> dict[str, str]:
        """リポジトリ（`<所有者>/<リポジトリ>`）ごとの公開範囲（`PUBLIC`・`PRIVATE`・`INTERNAL`）を 1 回のクエリで読む（3391-1）。

        読めない（無い・権限が無い）リポジトリは `UNKNOWN` で、公開でないとして扱う。`github.graphql-batch` 件ごとに 1 回。
        """
        wanted = sorted({r for r in repos if "/" in r})
        out: dict[str, str] = {}
        for chunk in _batches(wanted):
            data = self.graphql(_visibility_query(chunk), {})
            out.update({repo: _visibility_of(data, i) for i, repo in enumerate(chunk)})
        return out

    def allowed_merge_methods(self, branch: str) -> list[str]:
        """基底のブランチ `branch` で許されたマージの方式（`MERGE_METHODS` の並び。3378-2）。

        ruleset の PR の規則の `allowed_merge_methods` を先に読み、指定が無ければリポジトリの設定を読む。
        設定の項目（`allow_squash_merge` など）は権限の足りないトークンでは応答に無い。無い項目の方式は許すかが
        不明なので候補に残し、要求を送って GitHub の拒否（405）に判定を任せる。項目があれば値が True の方式だけを許す。
        """
        rules = self.rest("GET", f"/repos/{self.repo}/rules/branches/{quote(branch, safe='')}")
        methods = ruleset_merge_methods(rules if isinstance(rules, list) else [])
        if methods is not None:
            return methods
        raw = self.rest("GET", f"/repos/{self.repo}")
        settings = raw if isinstance(raw, dict) else {}
        return [m for m, spec in _MERGE_METHOD_SPECS.items() if spec.setting not in settings or settings[spec.setting] is True]

    def check_runs(self, sha: str) -> list[dict]:
        """コミットのチェックの生の一覧を `total_count` に届くまで読む（`github.check-runs` ページまで）。"""
        pages = _page_cap("github.check-runs")
        runs: list[dict] = []
        for page in range(1, pages + 1):
            body = self.rest("GET", f"/repos/{self.repo}/commits/{sha}/check-runs", params={"per_page": PER_PAGE, "page": page})
            runs.extend(body["check_runs"])
            if len(runs) >= int(body["total_count"]) or not body["check_runs"]:
                return runs
        raise _too_many_pages(f"{sha} のチェックが", pages, "github.check-runs")

    def job_log(self, job_id: int) -> str:
        """Actions のジョブのログの本文。"""
        body = self.rest("GET", f"/repos/{self.repo}/actions/jobs/{int(job_id)}/logs")
        return body if isinstance(body, str) else ""

    def merge(self, number: int, expect_head: Optional[str] = None, method: Optional[str] = None) -> MergeOutcome:
        """PR をマージする。先端の食い違い・決めていない残リスクは要求を送らずに断り、拒否は種類で返す（SF4）。

        `expect_head` は完全か短い SHA（#2041）。要求には読んだ先端の完全な SHA を添え、その後に先端が
        動いていれば GitHub が断る。`method` は宣言のマージの方式で、省けば基底のブランチで許された方式から選ぶ。
        許されていない方式は要求を送らずに断る（3378-2）。
        """
        pull = self.pull(number)
        early = _preflight(pull, number, expect_head)
        if early is not None:
            return early
        allowed = self.allowed_merge_methods(pull.base_ref)
        chosen = pick_merge_method(method, allowed)
        if chosen is None:
            return _method_refusal(pull, number, method, allowed)
        try:
            self.rest("PUT", f"/repos/{self.repo}/pulls/{number}/merge", {"sha": pull.head_sha, "merge_method": chosen})
        except GitHubError as exc:
            if exc.status not in MERGE_REFUSED_STATUS:
                raise
            after = self.pull(number)
            return MergeOutcome(False, refusal_kind(after, pull.head_sha), str(exc), after.head_sha)
        return MergeOutcome(True, None, f"#{number} を {chosen} でマージした", pull.head_sha)


def _issue_filter(labels: Sequence[str], state: str) -> dict[str, Any]:
    """Issue の絞り込みの変数（`count_issues`・`issue_numbers`）。ラベルが無ければ null で渡し、絞り込まない。"""
    return {"labels": list(labels) if labels else None, "states": [state]}


# Issue の番号の 1 ページ（`GitHub.issue_numbers`）
_ISSUE_PAGE = (
    "query($owner: String!, $name: String!, $labels: [String!], $states: [IssueState!], $after: String) {"
    f" repository(owner: $owner, name: $name) {{ issues(labels: $labels, states: $states, first: {PER_PAGE}, after: $after) {{"
    " totalCount pageInfo { hasNextPage endCursor } nodes { number } } } }"
)


def _visibility_query(repos: Sequence[str]) -> str:
    """リポジトリごとの `visibility` を、最上位のエイリアス（`a0`・`a1`…）で 1 回に読むクエリ。"""
    parts = []
    for i, repo in enumerate(repos):
        owner, name = repo.split("/", 1)
        parts.append(f"{_alias(i)}: repository(owner: {_graphql_string(owner)}, name: {_graphql_string(name)}) {{ visibility }}")
    return "query { " + " ".join(parts) + " }"


def _visibility_of(data: Any, i: int) -> str:
    """`_visibility_query` の応答の `i` 番目のリポジトリの公開範囲（大文字）。読めなければ `UNKNOWN`。"""
    node = data[_alias(i)] if isinstance(data, dict) and _alias(i) in data else None
    return text_of(node["visibility"]).upper() if isinstance(node, dict) else UNKNOWN


def _graphql_string(value: str) -> str:
    """GraphQL の文字列の値（JSON の文字列と同じ書き方）。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
