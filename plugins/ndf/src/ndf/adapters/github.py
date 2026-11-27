"""GitHub の読み書き（外部との接続の層。githubkit の上に置く。docs/specifications/ndf-rebuild-libraries.md の決定 16・17）。

- 単発の読み書きと読み直しは REST で行う。githubkit の `http_cache` が ETag 付きで読み直し、変わっていない応答は上限に数えられない
- 番号やブランチごとの読み取りは、GraphQL のエイリアスで 1 回のクエリへまとめる（A2）。ループの中で 1 件ずつ呼ばない
- 片方のスロット（REST・GraphQL）が上限のとき、代われる読み取り（PR の状態）はもう片方のスロットで読む。代われない操作は
  githubkit の再試行が回復の時刻まで待ってから打ち直し、待ちの残りを越えるなら `retry_after` で呼び手へ返す（不足 g）
- 件数と一覧は `totalCount` とページ送りで読み切り、既定の件数で黙って切らない（#3393）
- 本文には書き手と `author_association` を添え、write 以上の権限の無い書き手の本文を信用しない区分で返す（#3364）
- 失敗は `GitHubError`（HTTP の状態・上限の回復までの秒・上限に当たったスロット）で返し、黙って空を返さない。書き込みはマシン全体の
  順番（`write_gate`）を取り、二次レート制限の見分けと待ちの終わりの共有は `github_limits` が持つ（#4016）
- 認証は環境変数 `GH_TOKEN`・`GITHUB_TOKEN` か `gh auth token` の値を渡す（値はログにも結果にも出さない）

Issue・コメント・PR の本文を書く操作は、公開範囲を読むエントリポイント（`ndf.adapters.publish.Publisher`）を通す（#3391）。
テストは githubkit のクライアントへ `httpx.MockTransport` を渡して、ネットワークへ出ずに確かめる。
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, NamedTuple, Optional, TypeVar
from urllib.parse import quote

import httpx
from githubkit import GitHub as Client
from githubkit.exception import GraphQLFailed, PrimaryRateLimitExceeded, RateLimitExceeded, RequestError, RequestFailed

from ndf.adapters import cli_floor, git, github_limits, github_merge, write_gate
from ndf.adapters.github_limits import GRAPHQL, REST, RetryWithin
from ndf.adapters.github_merge import MERGE_QUEUE_PENDING, FileAt, MergeOutcome, Verified

# 呼び手（`ndf.domains.release.land`）が github から読む、github_merge の名前
from ndf.adapters.github_merge import MERGE_COMMIT as MERGE_COMMIT

# `Pull`・`Authored`・`text_of` は呼び手が github から読む名前でもある
from ndf.adapters.github_pulls import PER_PAGE, PULL_FIELDS, Authored, Pull, account_id_of, graphql_string, target_of, text_of
from ndf.adapters.github_rules import NO_RULES, RuleFacts, classic_facts_of, merge_facts, rule_facts_of
from ndf.contracts import limits

T = TypeVar("T")
NUMBERED_NODES = ("pullRequest", "issue", "issueOrPullRequest")  # GraphQL のエイリアスで引ける番号つきの節点
# `issueOrPullRequest`（union）から本文を選ぶ選択。union なので、body は型ごとのインラインフラグメントで選ぶ（#3908 の r2-1）
BODY_FIELDS = "... on Issue { body } ... on PullRequest { body }"
# head のブランチごとに読む PR の件数（新しい順）
PULLS_PER_HEAD = 5
# 認証の値を読む環境変数（先のものを優先する。利用者の手順へ渡さないときに消す名前も同じ）
TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
# GitHub App の bot の login の末尾（`<アプリの slug>[bot]`。commit status の書き手からアプリを引く。#3506）
BOT_SUFFIX = "[bot]"
# マージの拒否を、拒否の後に読み直した `mergeStateStatus` で種類へ分ける（SF4）。標準エラーの文は読まない
_REFUSAL_KINDS = {"dirty": "base_conflict", "blocked": "not_ci", "behind": "not_ci", "draft": "not_ci"}
# マージ先の設定の読み取りに、token の利用者の login とアカウント ID（`viewer`）も同じ 1 回の要求で載せる（承認の証跡の書き手を照らす。#4409）
_BRANCH_FILE = (
    "query($owner: String!, $name: String!, $expr: String!) { viewer { login databaseId } repository(owner: $owner, name: $name) {"
    " nameWithOwner object(expression: $expr) { ... on Blob { oid text } } } }"
)
# token の利用者の login だけを読む問い合わせ。REST の `/user` と違い、GitHub App のインストールトークンと Actions の
# `GITHUB_TOKEN` でもボットの login を返す
_VIEWER = "query { viewer { login databaseId } }"
# 信用の区分（#3364）。OWNER はリポジトリのオーナーでそのまま信用する。MEMBER・COLLABORATOR は所属を表すだけで
# write を保証しない（read・triage もある）ため、対象リポジトリでの権限を読んで write 以上のときだけ信用する。
# ほかの値（CONTRIBUTOR・FIRST_TIME_CONTRIBUTOR・FIRST_TIMER・MANNEQUIN・NONE と知らない値）の本文は信用しない入力
OWNER_ASSOCIATION = "OWNER"
PERMISSION_CHECKED_ASSOCIATIONS = frozenset({"MEMBER", "COLLABORATOR"})
# 権限の読み取り（REST の collaborators/{login}/permission の `permission`）で write 以上を表す値。maintain は write に写る
WRITE_PERMISSIONS = frozenset({"admin", "write"})
# リポジトリの公開範囲（GraphQL の `visibility`）。読めないリポジトリは UNKNOWN で、公開でないとして扱う
PUBLIC, UNKNOWN = "PUBLIC", "UNKNOWN"


class GitHubError(Exception):
    """GitHub の要求が失敗した。`status` は HTTP の状態（届かなければ 0）、`retry_after` は上限の回復までの秒、
    `quota` は上限に当たったスロット（`rest`・`graphql`。上限でなければ None）。`unreached` は要求が GitHub へ届かなかったマーカーで、
    状態が 0 でも届いて断られた失敗（GraphQL の誤り・repository が null・ページの上限・認証が無い）は False。
    `secondary` は二次レート制限か、`hinted` は GitHub が待ちの秒を示したか、`reasons` は拒否の理由の全文（切り詰めない。`of` が書く）。
    `recorded` は同じ応答について待ちの終わりを知らせ済みか（`_translating` が知らせ直さない）。"""

    secondary = hinted = recorded = False
    reasons: tuple[str, ...] = ()

    def __init__(
        self, detail: str, status: int = 0, retry_after: Optional[float] = None, quota: Optional[str] = None, unreached: bool = False
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.retry_after = retry_after
        self.quota = quota
        self.unreached = unreached

    @property
    def passing(self) -> bool:
        """打ち直せば通りうる失敗（不達・5xx・上限）か。状態が 0 でも相手が受けて断った失敗（GraphQL の誤り・認証が無い）は偽。"""
        return self.unreached or self.status >= 500 or self.retry_after is not None or self.status == 429

    @classmethod
    def of(cls, failure: github_limits.Failure) -> GitHubError:
        """`github_limits` の写した失敗から作る。"""
        made = cls(failure.detail, failure.status, failure.retry_after, failure.quota, failure.unreached)
        made.secondary, made.hinted, made.reasons, made.recorded = failure.secondary, failure.hinted, failure.reasons, failure.recorded
        return made


class PermissionUnknown(GitHubError):
    """書き手の権限を照会できない（レート制限によるものでない 403・404。#4580）。`login` は照会できなかった書き手。"""

    def __init__(self, login: str, status: int = 0) -> None:
        super().__init__(
            f"{login} の対象のリポジトリでの権限を照会できない（{status or '403・404'}）。権限が無いとは扱わずに止める", status
        )
        self.login = login


def token() -> str:
    """認証の値。環境変数を先に読み、無ければ `gh auth token`。得られなければ `GitHubError`。"""
    found = next((os.environ[name] for name in TOKEN_ENV if os.environ.get(name)), None)
    if found:
        return found
    try:
        cli_floor.refuse_old("gh", "gh", GitHubError)
        done = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GitHubError(f"gh を起動できない: {exc}") from None
    if done.returncode != 0 or not done.stdout.strip():
        raise GitHubError("GitHub の認証が無い（GH_TOKEN も gh auth token も無い）")
    return done.stdout.strip()


def client(
    auth: Optional[str] = None,
    transport: Optional[httpx.BaseTransport] = None,
    remaining: Optional[Callable[[], float]] = None,
    gate: Optional[write_gate.WriteGate] = None,
) -> Client:
    """githubkit のクライアント（ETag の読み直しと再試行つき）。`transport` はテストが差し替える。

    `remaining`（待ちに使える残りの秒を返す関数）を渡すと、解除がそれを越える上限の待ちは再試行しない（`RetryWithin`）。
    省くと上限（rate limit）は待たずに `GitHubError` で返す（残りを 0 秒とみなす）。githubkit の既定は解除の時刻まで
    （一次上限なら最大 1 時間）眠るため、そのままではスロットの読み替え（`GitHub.pulls`・`GitHub.pull`。不足 g）の前に止まる。
    書き込みは `gate`（省けば `machine_gate()`。`transport` を渡すテストでは取らない）の順番を `remaining` の中で取り（決定 10）、
    待ちの終わりは応答のフックで記録する。
    """
    if gate is None and transport is None:
        gate = write_gate.machine_gate()
    retry = RetryWithin(remaining if remaining is not None else github_limits.no_wait, gate)
    options = github_limits.client_options(gate, remaining)
    return Client(auth if auth is not None else token(), http_cache=True, auto_retry=retry, transport=transport, **options)


class Viewer(NamedTuple):
    """token の利用者。`account_id` は GitHub が発行するアカウント ID で、読めなければ 0（`login` は表示に使うだけ）。"""

    login: str
    account_id: int


class MergeExpect(NamedTuple):
    """`ndf ci merge` が判定に使った値（#4409 の決定 8）。`pull` は判定の前に読んだ PR で、先の確認と方式の選択に使う。

    `rejudge` は確かめの応答の設定の本文（無ければ None）で判定をやり直し、送ってよければ None、止めるなら結果を返す。
    """

    pull: "Pull"
    base: str
    head: str
    config_path: str
    config_oid: Optional[str]
    rejudge: Callable[[Optional[str]], Optional["MergeOutcome"]]


def same_repo(a: str, b: str) -> bool:
    """2 つのリポジトリ名（`<所有者>/<リポジトリ>`）が同じか。GitHub の名前は大文字小文字を区別しない。"""
    return a.lower() == b.lower()


def landed(pull: Pull, method: str, base_ref: str, repo: git.Git) -> bool:
    """PR の head が基底 `base_ref`（`origin/develop` など）に含まれたかを、マージの方式ごとに確かめる（3378-2）。

    merge は head が基底の祖先か（git）、squash と rebase は PR がマージ済みか（`merged_at`）で読む。
    """
    if method not in github_merge.MERGE_METHODS:
        raise ValueError(f"知らないマージの方式: {method}（{', '.join(github_merge.MERGE_METHODS)}）")
    if github_merge.MERGE_METHODS[method] == github_merge.ANCESTRY:
        return repo.is_ancestor(pull.head_sha, base_ref)
    return pull.merged


def refusal_kind(after: Pull, sent_sha: str) -> str:
    """マージを断られた後に読み直した PR から、停止理由の種類を決める（SF4）。"""
    if after.head_sha != sent_sha:
        return "refused"
    return _REFUSAL_KINDS.get(after.merge_state, "unknown")  # noqa: NDF502 ネットワーク: 表に無い mergeStateStatus（clean・unstable・unknown）は種類を決められない


@dataclass(frozen=True)
class _MergeRoute:
    """マージの要求の打ち方。`send` が要求を打ってマージコミット（応答に無ければ空）を返し、`refused` が真とみなした誤りは断られたとして扱う。`how` は成功の文に
    添える迂回のマーカー。`require_method` が真なら、キューの方式が選んだ方式と違うときキューへ入れずに断る。"""

    send: Callable[[int, "Pull", str], str]
    refused: Callable[["GitHubError"], bool]
    how: str
    require_method: bool = False
    jump: bool = False


def _refused_by_graphql(exc: GitHubError) -> bool:
    """GraphQL の誤りが GitHub の断り（届いて断られた。上限でない）か。届かなかった・上限なら偽（呼び手が投げ直す）。"""
    return exc.status == 0 and not exc.unreached and exc.quota is None


def _file_at(repo: dict, key: str) -> FileAt:
    """`object(expression)` の応答（キー `key`）を `FileAt` にする。Blob でない・無いなら本文と oid が None。"""
    node = repo.get(key)
    blob = node if isinstance(node, dict) and isinstance(node.get("oid"), str) else None
    text = blob.get("text") if blob is not None and isinstance(blob.get("text"), str) else None
    return FileAt(text_of(repo.get("nameWithOwner")), text, blob["oid"] if blob is not None else None)


def _verified(repo: dict) -> Verified:
    node = repo.get("pullRequest") if isinstance(repo.get("pullRequest"), dict) else {}
    return Verified(*target_of(node), text_of(repo.get("nameWithOwner")), _file_at(repo, "config"))


def _verify(number: int, fresh: Verified, expect: MergeExpect, repo: str) -> Optional[MergeOutcome]:
    """確かめの応答 `fresh` と判定に使った値を比べる。マージ先・HEAD が違えば `github_merge.TARGET_MOVED`、
    ベースリポジトリが違えば `refused`。設定の oid だけが違えば、同じ応答の本文で判定をやり直す（設定を読み直す要求を
    足さない）。送ってよければ None。"""
    moved = github_merge.TARGET_MOVED
    if fresh.base != expect.base:
        return MergeOutcome(
            False, moved, f"#{number} のマージ先が判定した {expect.base} から {fresh.base} へ変わった（判定からやり直す）", fresh.head
        )
    if not git.sha_matches(expect.head, fresh.head):
        return MergeOutcome(False, moved, f"#{number} の HEAD が判定した {expect.head[:12]} から {fresh.head[:12]} へ動いた", fresh.head)
    if not same_repo(fresh.repo, repo):
        return MergeOutcome(False, "refused", f"#{number} のベースリポジトリ {fresh.repo} が {repo} と違う", fresh.head)
    if fresh.config.oid != expect.config_oid:
        return expect.rejudge(fresh.config.text)
    return None


def _response_body(resp: Any) -> Any:
    if not resp.content:
        return None
    if "json" in resp.headers.get("content-type", ""):  # noqa: NDF502 ネットワーク: 応答に content-type が無いことがある
        return resp.json()
    return resp.text


def _next_cursor(resp: Any) -> Optional[str]:
    """応答の Link の `next` の URL のカーソル（`after`）。次のページが無ければ None。"""
    nxt = resp.raw_response.links.get("next")
    return httpx.URL(nxt["url"]).params.get("after") if isinstance(nxt, dict) and "url" in nxt else None


def _alias(i: int) -> str:
    """1 回のクエリの中の `i` 番目のエイリアスの名前（`a0`・`a1`…）。"""
    return f"a{i}"


def _batches(items: Sequence[T], batch: str = "github.graphql-batch") -> Iterator[Sequence[T]]:
    """`items` を上限 `batch`（既定 `github.graphql-batch`）の件数ずつに切る（1 つが 1 回のエイリアスのクエリ）。"""
    size = int(limits.value(batch, "count"))
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _aliased_query(aliases: dict[str, str]) -> str:
    body = " ".join(f"{alias}: {selection}" for alias, selection in aliases.items())
    return f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {body} }} }}"


def _only_not_found(exc: GraphQLFailed) -> bool:
    errors = exc.response.errors
    return bool(errors) and all(e.type == "NOT_FOUND" for e in errors)


def _as_github_error(exc: RequestFailed | RequestError | write_gate.Busy, label: str, detail_body: bool = False) -> GitHubError:
    """githubkit の失敗を `GitHubError` へ写す。`label` は要求の名前（`GET /path`・`graphql`）。写し方は `github_limits.failure_of`。"""
    return GitHubError.of(github_limits.failure_of(exc, label, detail_body, time.time()))


def _page_cap(name: str) -> int:
    """一覧を読むページ数の上限（`contracts/limits/<name>.json` の `count`）。"""
    return int(limits.value(name, "count"))


def _too_many_pages(what: str, pages: int, name: str) -> GitHubError:
    """一覧が上限 `name` の `pages` ページを超えたときの失敗（途中で切らずに止まる）。`what` は主語と助詞まで。"""
    return GitHubError(f"{what} {pages} ページを超える（上限 {name}）")


class _Counted(NamedTuple):
    """件数のキーを持つ一覧の読み方（`GitHub._counted_pages`）。"""

    rows: str  # 行の配列のキー
    total: str  # 総件数のキー
    cap: str  # ページ数の上限の名前（contracts/limits/<名前>.json）
    what: str  # 上限を超えたときの文の主語と助詞


@contextmanager
def _translating(label: str, detail_body: bool = False, gate: Optional[write_gate.WriteGate] = None) -> Iterator[None]:
    """失敗（順番の待ちの `write_gate.Busy` も）を `_as_github_error` で `GitHubError` へ写して投げ直す。二次レート制限は `tell` で知らせる。"""
    try:
        yield
    except (RateLimitExceeded, RequestFailed, RequestError, write_gate.Busy) as exc:
        error = _as_github_error(exc, label, detail_body)
        github_limits.tell(gate, error.secondary, error.retry_after, error.recorded)
        raise error from None
    except GitHubError as error:
        github_limits.tell(gate, error.secondary, error.retry_after, error.recorded)
        raise


@dataclass(frozen=True)
class GitHub:
    """1 つのリポジトリ（`<所有者>/<リポジトリ>`）への GitHub の読み書き。"""

    repo: str
    api: Client
    # 書き手の login → 対象リポジトリでの権限（`permission`）。1 つの書き手を 1 回だけ読む
    _permissions: dict[str, str] = field(default_factory=dict, compare=False, repr=False)
    # 権限を照会できなかった書き手の login → 照会の状態（403・404）。`permission` は `none` と読み、`permission_strict` は止める
    _unreadable: dict[str, int] = field(default_factory=dict, compare=False, repr=False)
    # token の利用者（`viewer`。1 回だけ読む。読んだら 1 件）
    _viewer: list[Viewer] = field(default_factory=list, compare=False, repr=False)
    # 二次レート制限の待ちの終わりを知らせる先。省けば `api` の throttler の `WriteGate`（A5）
    gate: Optional[write_gate.WriteGate] = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.gate is None:
            object.__setattr__(self, "gate", write_gate.gate_of(self.api.config.throttler))

    def rest(self, method: str, path: str, payload: Any = None, params: Optional[dict] = None) -> Any:
        """REST の 1 回の要求の本文（JSON なら値、ほかは文字列、空なら None）。失敗は `GitHubError`。"""
        with _translating(f"{method} {path}", detail_body=True, gate=self.gate):
            resp = self.api.request(method, path, json=payload, params=params)
        return _response_body(resp)

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """GraphQL の 1 回の要求の `data`。読めない番号（`NOT_FOUND`）だけの誤りなら、読めた分を返す。
        HTTP 200 の誤りが二次レート制限なら `secondary` の `GitHubError` にする（`block` は応答のフックか `_translating`。A6）。"""
        with _translating(GRAPHQL, gate=self.gate):
            resp = self.api.graphql._request(query, variables)  # 誤りの写しへ応答のヘッダー（`Retry-After`）を渡す
            try:
                return self.api.graphql.parse_graphql_response(resp)
            except GraphQLFailed as exc:
                if _only_not_found(exc) and exc.response.data is not None:
                    return exc.response.data
                messages = "; ".join(e.message for e in exc.response.errors)
                raise GitHubError.of(github_limits.graphql_failure(messages, resp.raw_response)) from None
            except PrimaryRateLimitExceeded as exc:
                limited = github_limits.graphql_limited(exc)
                if limited is None:
                    raise
                raise GitHubError.of(limited) from None

    # ページの上限の失敗（このモジュールの外の一覧の読み手 `github_pulls.merged_pulls` も使う）
    too_many_pages = staticmethod(_too_many_pages)

    def _owner_name(self) -> dict[str, str]:
        owner, name = self.repo.split("/", 1)
        return {"owner": owner, "name": name}

    def repository(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """`repository(owner, name)` の下を読むクエリの `repository` の値。null（読めない・無い）なら `GitHubError`（3394-6）。"""
        return self._repository_of(self.graphql(query, {**self._owner_name(), **(variables if variables is not None else {})}))

    def _repository_of(self, data: Any) -> dict[str, Any]:
        repo = data["repository"] if isinstance(data, dict) and "repository" in data else None
        if not isinstance(repo, dict):
            raise GitHubError(f"{self.repo} を GraphQL で読めない（repository が null。リポジトリが無いか、権限が無い）")
        return repo

    def aliased(self, selections: dict[str, str], batch: str = "github.graphql-batch") -> dict[str, Any]:
        """名前 → `repository` の下の選択を、`github.graphql-batch` 件ずつのエイリアスのクエリで読む（A2）。

        返す辞書は `selections` のすべての名前をキーに持ち、読めなかった名前の値は None。途中で失敗したら
        `GitHubError` で、一部だけの結果を返さない。リポジトリが null なら `GitHubError`（3394-6）。
        """
        out: dict[str, Any] = {}
        for keys in _batches(list(selections), batch):
            chunk = {_alias(i): selections[k] for i, k in enumerate(keys)}
            data = self.repository(_aliased_query(chunk))
            out.update({k: data[alias] if alias in data else None for alias, k in zip(chunk, keys)})
        return out

    def nodes(self, numbers: Iterable[int], node: str, fields: str, batch: str = "github.graphql-batch") -> dict[int, dict]:
        """番号ごとの `node(number: N) { fields }` を上限 `batch` の件数ずつまとめて読む。その種類で無い・無い番号の値は空の辞書。"""
        if node not in NUMBERED_NODES:
            raise ValueError(f"番号で引けない節点: {node}（{', '.join(NUMBERED_NODES)}）")
        wanted = sorted({int(n) for n in numbers})
        got = self.aliased({str(n): f"{node}(number: {n}) {{ {fields} }}" for n in wanted}, batch)
        return {n: got[str(n)] if isinstance(got[str(n)], dict) else {} for n in wanted}

    def pulls(self, numbers: Iterable[int], spare: Iterable[int] = ()) -> dict[int, Optional[Pull]]:
        """番号ごとの PR を 1 回のクエリ（100 件ごと）で読む。PR でない・無い番号は None。

        GraphQL のスロットが一次の上限なら、`github.rest-fallback` 件までは REST のスロットで読み替える（不足 g。二次レート制限は読み替えない）。
        `spare`（ついでに読めればよい番号）は、`numbers` を読むクエリの 100 件に満たない残りの分だけを読み、呼び出しの回数と読み替えの
        可否を変えない。残りに入らない番号と、REST へ読み替えたときの番号は、返す辞書に載らない。
        """
        wanted = sorted({int(n) for n in numbers})
        free = -len(wanted) % int(limits.value("github.graphql-batch", "count"))
        extra = sorted({int(n) for n in spare} - set(wanted))[:free]
        try:
            raw = self.nodes([*wanted, *extra], "pullRequest", PULL_FIELDS)
        except GitHubError as exc:
            if exc.quota != GRAPHQL or exc.secondary or len(wanted) > int(limits.value("github.rest-fallback", "count")):
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
        got = self.aliased({b: selection.format(graphql_string(b), PULLS_PER_HEAD, PULL_FIELDS) for b in wanted})
        return {b: [Pull.from_graphql(p) for p in got[b]["nodes"]] if got[b] else [] for b in wanted}

    def open_pulls_of_head(self, branch: str, enough: int, base: Optional[str] = None) -> list[Pull]:
        """head のブランチが `branch` で、このリポジトリの開いた PR（フォークの同名ブランチを除く。新しい順）。

        取得の時点で開いた PR に絞り（`base` があればマージ先も GitHub 側で絞る）、同じリポジトリの候補が `enough` 件そろうか一覧を読み終えるまでページを送る
        （候補が 1 件だけかを、取得の範囲から外れた古い PR で取り違えない）。`github.list-pages` ページを超えたら `GitHubError`。
        """
        pages = _page_cap("github.list-pages")
        variables: dict[str, Any] = {"head": branch, "base": base, "after": None}
        found: list[Pull] = []
        for _ in range(pages):
            conn = self.repository(_OPEN_HEAD_PAGE, variables)["pullRequests"]
            found += [p for p in (Pull.from_graphql(n) for n in conn["nodes"]) if not p.cross_repository]
            if len(found) >= enough or not conn["pageInfo"]["hasNextPage"]:
                return found[:enough]
            variables["after"] = conn["pageInfo"]["endCursor"]
        raise _too_many_pages(f"ブランチ {branch} の開いた PR の一覧が", pages, "github.list-pages")

    def pull(self, number: int) -> Pull:
        """PR を REST で読む。REST のスロットが一次の上限なら GraphQL のスロットで読み替える（不足 g）。二次レート制限は読み替えない。"""
        try:
            return self._rest_pull(number)
        except GitHubError as exc:
            if exc.quota != REST or exc.secondary:
                raise
            # 読み替えた先のスロットも上限なら、REST へ戻さずにその上限で止まる
            raw = self.nodes([number], "pullRequest", PULL_FIELDS)[int(number)]
            if not raw:
                raise GitHubError(f"#{number} は PR でない（GraphQL のスロットで読み替えた）", 404) from None
            return Pull.from_graphql(raw)

    def issue_body(self, number: int) -> Authored:
        """Issue（か PR）の本文と書き手。信用の区分は `trusts`（#3364）。"""
        return Authored.from_rest(self.rest("GET", f"/repos/{self.repo}/issues/{int(number)}"))

    def issue_text(self, number: int) -> tuple[str, Authored]:
        """Issue（か PR）の題と、書き手の付いた本文（信用の区分は `trusts`）。"""
        raw = self.rest("GET", f"/repos/{self.repo}/issues/{int(number)}")
        return text_of(raw["title"]), Authored.from_rest(raw)

    def trusts(self, authored: Authored) -> bool:
        """write 以上の権限を持つ書き手の本文か（#3364）。偽なら読み手は本文を指示でなくデータとして扱う。

        OWNER はそのまま信用し、MEMBER・COLLABORATOR は対象リポジトリでの権限を読んで write 以上のときだけ信用する。
        """
        return self._trusts_by(authored, self.permission)

    def trusts_strict(self, authored: Authored) -> bool:
        """`trusts` と同じ区分の扱いで、権限を照会できない書き手では偽を返さず `PermissionUnknown` を投げる（#4580）。"""
        return self._trusts_by(authored, self.permission_strict)

    @staticmethod
    def _trusts_by(authored: Authored, read: Callable[[str], str]) -> bool:
        if authored.association == OWNER_ASSOCIATION:
            return True
        if authored.association not in PERMISSION_CHECKED_ASSOCIATIONS or not authored.author:
            return False
        return read(authored.author) in WRITE_PERMISSIONS

    def token_trusted(self) -> bool:
        """このクライアントの token で書いた本文を、読み手が `trusts` で信用するか（#3487）。

        利用者の token なら書き手の login の権限が write 以上のときだけ真。利用者を持たない token（Actions の
        `github.token` など、`GET /user` が 403・404）の書き手は author_association がオーナー・所属にならず、信用されない。
        """
        try:
            raw = self.rest("GET", "/user")
        except GitHubError as exc:
            if exc.quota is None and exc.status in (403, 404):
                return False
            raise
        login = text_of(raw["login"]) if isinstance(raw, dict) and "login" in raw else ""
        return bool(login) and self.permission(login) in WRITE_PERMISSIONS

    def permission(self, login: str) -> str:
        """`login` の対象リポジトリでの権限（`admin`・`write`・`read`・`none`）。書き手ごとに 1 回だけ読む。

        読めない（404・上限でない 403）なら `none` として信用しない。上限と他の失敗は `GitHubError` のまま投げる。
        """
        if login not in self._permissions and login not in self._unreadable:
            got = self._read_permission(login)
            if got is not None:
                self._permissions[login] = got
        return self._permissions[login] if login in self._permissions else "none"

    def permission_strict(self, login: str) -> str:
        """`permission` と同じ照会で、照会できない（404・上限でない 403）書き手では `PermissionUnknown` を投げる（#4580）。

        GitHub が 200 で返した値（`none` も）だけを保存し、照会できなかった書き手は別に覚えて照会し直さない。"""
        got = self.permission(login)
        if login in self._unreadable:
            raise PermissionUnknown(login, self._unreadable[login])
        return got

    def _read_permission(self, login: str) -> Optional[str]:
        """照会した権限。照会できない（404・上限でない 403）なら状態を `_unreadable` に覚えて None。"""
        try:
            raw = self.rest("GET", f"/repos/{self.repo}/collaborators/{quote(login, safe='')}/permission")
        except GitHubError as exc:
            if exc.quota is None and exc.status in (403, 404):
                self._unreadable[login] = exc.status
                return None
            raise
        return text_of(raw["permission"]).lower() if isinstance(raw, dict) and "permission" in raw else "none"

    def comments(self, number: int) -> list[Authored]:
        """Issue（か PR）のコメントを古い順にすべて読む（`github.list-pages` ページまで。超えたら `GitHubError`）。"""
        return [Authored.from_rest(c) for c in self._pages(f"/repos/{self.repo}/issues/{int(number)}/comments")]

    def delete_comment(self, comment_id: int) -> None:
        """コメント `comment_id` を消す（`DELETE /issues/comments/<id>`。#4580 で自分が書いたコメントを消す）。失敗は `GitHubError`。"""
        self.rest("DELETE", f"/repos/{self.repo}/issues/comments/{int(comment_id)}")

    def branch_activity(self, branch: str) -> Iterator[dict]:
        """ブランチ `branch` の更新（`GET /activity?ref=refs/heads/<branch>`。`before`・`after`・`activity_type`）を新しい順に返す。

        ページはページ番号でなく Link の `next` のカーソル（`after`）で送り、要るだけ 1 ページずつ読む（呼び手が打ち切れる）。
        `github.list-pages` ページを超えたら途中で切らずに `GitHubError`。"""
        pages = _page_cap("github.list-pages")
        path = f"/repos/{self.repo}/activity"
        params: dict[str, Any] = {"ref": f"refs/heads/{branch}", "per_page": PER_PAGE}
        for _ in range(pages):
            with _translating(f"GET {path}", detail_body=True, gate=self.gate):
                resp = self.api.request("GET", path, params=params)
            yield from _response_body(resp)
            cursor = _next_cursor(resp)
            if cursor is None:
                return
            params = {**params, "after": cursor}
        raise _too_many_pages(f"{path} が", pages, "github.list-pages")

    def workflow_runs(self, workflow: str, branch: str, commit: str, timeout: Optional[float] = None) -> list[dict]:
        """workflow `workflow` のうち、ブランチ `branch` のコミット `commit` を push で起動した実行（新しい順。1 ページ 100 件）。

        起動のイベントを push で絞り、同じコミットの手動の実行を数えない（#4580）。`timeout` はこの照会の時間の上限の秒（None は既定）。"""
        path = f"/repos/{self.repo}/actions/workflows/{quote(workflow, safe='')}/runs"
        params = {"branch": branch, "head_sha": commit, "event": "push", "per_page": PER_PAGE}
        body = self._limited_to(timeout).rest("GET", path, params=params)
        return list(body["workflow_runs"])

    def _limited_to(self, timeout: Optional[float]) -> GitHub:
        """時間の上限を `timeout` 秒にした同じリポジトリの読み書き（None なら自分）。"""
        if timeout is None:
            return self
        config = dataclasses.replace(self.api.config, timeout=type(self.api.config.timeout)(timeout))
        return GitHub(self.repo, Client(self.api.auth, config=config), gate=self.gate)

    def commit_parents(self, sha: str) -> list[str]:
        """コミット `sha` の親の SHA（第 1 親から順）。"""
        body = self.rest("GET", f"/repos/{self.repo}/git/commits/{quote(sha, safe='')}")
        return [text_of(p["sha"]) for p in body["parents"]]

    def compare_commits(self, base: str, head: str) -> list[dict]:
        """`head` から辿れて `base` から辿れないコミットを REST の形のまま、`total_commits` に届くまで読む。

        `pulls/<番号>/commits` と違い 250 件で切れない。`github.list-pages` ページを超えたら途中で切らずに止まる。
        """
        path = f"/repos/{self.repo}/compare/{quote(base, safe='')}...{quote(head, safe='')}"
        return self._counted_pages(path, _Counted("commits", "total_commits", "github.list-pages", f"{base}...{head} のコミットが"))

    def _counted_pages(self, path: str, spec: _Counted) -> list[dict]:
        """件数のキーを持つ REST の一覧を、行が件数に届くか空のページが来るまで読む。`spec.cap` ページを超えたら途中で切らずに止まる。"""
        pages = _page_cap(spec.cap)
        rows: list[dict] = []
        for page in range(1, pages + 1):
            body = self.rest("GET", path, params={"per_page": PER_PAGE, "page": page})
            rows.extend(body[spec.rows])
            if len(rows) >= int(body[spec.total]) or not body[spec.rows]:
                return rows
        raise _too_many_pages(spec.what, pages, spec.cap)

    def reviews(self, number: int) -> list[dict]:
        """PR のレビューを REST の形のまま古い順にすべて読む（`approval.via: review` の承認の判定が使う）。"""
        return self._pages(f"/repos/{self.repo}/pulls/{int(number)}/reviews")

    def open_heads(self, base: str) -> list[tuple[int, str]]:
        """`base` 宛ての開いた PR の番号と head の SHA（番号の昇順）。承認したがキューへ入れていない版上げの PR を探す（決定 18）。

        一覧の応答は `merged`・`mergeable_state` を持たないため `Pull` にしない。"""
        rows = self._pages(f"/repos/{self.repo}/pulls", {"state": "open", "base": base})
        return sorted((int(p["number"]), text_of(p["head"]["sha"])) for p in rows)

    def _pages(self, path: str, query: Optional[dict] = None) -> list[dict]:
        """配列を返す REST の一覧を、短いページが来るまで読む。`github.list-pages` ページを超えたら途中で切らずに止まる。
        `query` はページの指定に足す絞り込み。"""
        pages = _page_cap("github.list-pages")
        rows: list[dict] = []
        narrow = dict(query) if query is not None else {}
        for page in range(1, pages + 1):
            body = self.rest("GET", path, params={**narrow, "per_page": PER_PAGE, "page": page})
            rows.extend(body)
            if len(body) < PER_PAGE:
                return rows
        raise _too_many_pages(f"{path} が", pages, "github.list-pages")

    def branch_rules(self, branch: str) -> list[dict]:
        """ブランチに当たる規則（`GET /rules/branches/<名前>`）を全ページ読む。`github.list-pages` ページを超えたら `GitHubError`
        （必須のチェックの規則が後ろのページにあっても落とさない。#3506）。"""
        return [r for r in self._pages(f"/repos/{self.repo}/rules/branches/{quote(branch, safe='')}") if isinstance(r, dict)]

    def rule_facts(self, branch: str) -> Optional[RuleFacts]:
        """本番ブランチの ruleset と従来のブランチ保護から、有無・承認の数・merge queue の有無・最新のマージの要否・
        迂回の可否を読む（10 系の `release_case.read_rule_facts` と同じ値。#3672 の I10）。

        GitHub は両方を重ねて効かせるため両方を読んで重ねる（保護のある本番ブランチを「保護なし」の経路へ入れない）。
        規則も保護も無ければ `present` が偽。権限・ネットワークで読めなければ None（呼び手が止める）。
        """
        try:
            rules = self.branch_rules(branch)
            bypasses = []
            for rid in sorted({r["ruleset_id"] for r in rules if isinstance(r.get("ruleset_id"), int)}):
                got = self.rest("GET", f"/repos/{self.repo}/rulesets/{rid}")
                if not isinstance(got, dict):
                    return None
                bypasses.append(got.get("current_user_can_bypass"))
            classic = self._classic_facts(branch)
        except GitHubError:  # noqa: NDF401 ネットワーク: 読めない事実は None で返し、land が止める（比べずに入れない）
            return None
        return None if classic is None else merge_facts(rule_facts_of(rules, bypasses), classic)

    def _classic_facts(self, branch: str) -> Optional[RuleFacts]:
        """従来のブランチ保護の事実。保護が無ければ `present` が偽、有る（か有無を読めない）のに詳細を読めなければ None。
        先に読む権限で読める `branches/<b>` の `protection.enabled`（ruleset を含まない従来の保護だけの有無）を見て、
        偽なら保護の照会を省く（保護の照会は管理者でない利用者に 404 を返しうる）。"""
        path = f"/repos/{self.repo}/branches/{quote(branch, safe='')}"
        info = self.rest("GET", path)
        protection = info.get("protection") if isinstance(info, dict) else None
        if isinstance(protection, dict) and protection.get("enabled") is False:
            return NO_RULES
        try:
            got = self.rest("GET", f"{path}/protection")
        except GitHubError:  # noqa: NDF401 ネットワーク: 保護の詳細を読めなければ None（呼び手が止める）
            return None
        return classic_facts_of(got) if isinstance(got, dict) else None

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
        """基底のブランチ `branch` で許されたマージの方式（`github_merge.MERGE_METHODS` の並び。3378-2）。

        ruleset の PR の規則の `allowed_merge_methods` とリポジトリの設定の両方を読み、両方が許す方式だけを返す
        （ruleset は方式を絞るだけで、リポジトリの設定で無効にした方式を有効にしない）。ruleset に指定が無ければ設定だけで決める。
        設定の項目（`allow_squash_merge` など）は権限の足りないトークンでは応答に無い。無い項目の方式は許すかが
        不明なので候補に残し、要求を送って GitHub の拒否（405）に判定を任せる。項目があれば値が True の方式だけを許す。
        """
        rules = self.rest("GET", f"/repos/{self.repo}/rules/branches/{quote(branch, safe='')}")
        named = github_merge.ruleset_merge_methods(rules if isinstance(rules, list) else [])
        if named == []:
            return []
        return github_merge.settings_merge_methods(self.rest("GET", f"/repos/{self.repo}"), named)

    def check_runs(self, sha: str) -> list[dict]:
        """コミットのチェックの生の一覧を `total_count` に届くまで読む（`github.check-runs` ページまで）。"""
        path = f"/repos/{self.repo}/commits/{sha}/check-runs"
        return self._counted_pages(path, _Counted("check_runs", "total_count", "github.check-runs", f"{sha} のチェックが"))

    def statuses(self, sha: str) -> list[dict]:
        """コミットの commit status の全件（`GET /commits/<sha>/statuses`。書き手の `creator` を持つ。#3506）。"""
        return [s for s in self._pages(f"/repos/{self.repo}/commits/{sha}/statuses") if isinstance(s, dict)]

    def bot_app(self, login: str) -> Optional[int]:
        """GitHub App の bot（`<slug>[bot]`）が属するアプリの ID。bot でない・アプリが無ければ None（#3506）。"""
        slug = login.removesuffix(BOT_SUFFIX)
        if slug == login or not slug:
            return None
        try:
            got = self.rest("GET", f"/apps/{quote(slug, safe='')}")
        except GitHubError as exc:
            if exc.status == HTTPStatus.NOT_FOUND:
                return None
            raise
        app_id = got.get("id") if isinstance(got, dict) else None
        return app_id if isinstance(app_id, int) and not isinstance(app_id, bool) else None

    def job_log(self, job_id: int) -> str:
        """Actions のジョブのログの本文。"""
        body = self.rest("GET", f"/repos/{self.repo}/actions/jobs/{int(job_id)}/logs")
        return body if isinstance(body, str) else ""

    def job(self, job_id: int) -> dict:
        """Actions のジョブ（`GET /actions/jobs/<id>`。`run_id` を持つ）の本文。"""
        return self.rest("GET", f"/repos/{self.repo}/actions/jobs/{int(job_id)}")

    def workflow_run(self, run_id: int) -> dict:
        """ワークフローの実行（`GET /actions/runs/<id>`。`path` が `.github/workflows/<ファイル>@<ref>` の形）の本文。"""
        return self.rest("GET", f"/repos/{self.repo}/actions/runs/{int(run_id)}")

    def run_artifacts(self, run_id: int) -> list[dict]:
        """ワークフローの実行の成果物の一覧を `total_count` に届くまで読む（`github.list-pages` ページまで）。"""
        path = f"/repos/{self.repo}/actions/runs/{int(run_id)}/artifacts"
        return self._counted_pages(path, _Counted("artifacts", "total_count", "github.list-pages", f"実行 {run_id} の成果物が"))

    def artifact_zip(self, artifact_id: int) -> bytes:
        """成果物の ZIP の中身（302 の転送先の本文をバイト列のまま）。期限切れは状態 410 の `GitHubError`。

        `rest` は JSON でない本文を文字列にして ZIP を壊すため、応答の `content` を直に返す。
        """
        path = f"/repos/{self.repo}/actions/artifacts/{int(artifact_id)}/zip"
        with _translating(f"GET {path}", gate=self.gate):
            resp = self.api.request("GET", path)
        return bytes(resp.content)

    def branch_file(self, base: str, path: str) -> FileAt:
        """ブランチ `base` のファイル `path` の本文と blob の oid を、応答のリポジトリ名と合わせて 1 回の GraphQL で読む（#4409 の決定 3）。

        同じ要求で token の利用者も読み、`viewer` が要求を増やさずに返せるよう残す。応答の `viewer` が空でも、空として残す
        （読めなかった結果を同じ実行の中で読み直さない）。
        """
        data = self.graphql(_BRANCH_FILE, {**self._owner_name(), "expr": f"{base}:{path}"})
        self._remember_viewer(data.get("viewer") if isinstance(data, dict) else None)
        return _file_at(self._repository_of(data), "object")

    def _remember_viewer(self, node: Any) -> None:
        if self._viewer:
            return
        login = node.get("login") if isinstance(node, dict) else None
        self._viewer.append(Viewer(login if isinstance(login, str) else "", account_id_of(node)))

    def viewer(self) -> Viewer:
        """この token の利用者（1 回だけ読む。`branch_file` が読んでいれば、空だったときも要求を送らない）。

        GraphQL の `viewer` で読むので、GitHub App のインストールトークンと Actions の `GITHUB_TOKEN` ではボットを返す。
        読めなければアカウント ID が 0（呼び手は証跡を数えも書きもしない）。届かない・上限は投げ直す。
        """
        if not self._viewer:
            try:
                data = self.graphql(_VIEWER, {})
            except GitHubError as exc:
                if exc.quota is not None or exc.passing:
                    raise
                data = {}
            self._remember_viewer(data.get("viewer") if isinstance(data, dict) else None)
        return self._viewer[0]

    def merge(
        self,
        number: int,
        expect_head: Optional[str] = None,
        method: Optional[str] = None,
        require_method: bool = False,
        jump: bool = False,
        expect: Optional[MergeExpect] = None,
    ) -> MergeOutcome:
        """PR をマージする。HEAD の食い違い・決めていない残リスクは要求を送らずに断り、拒否は種類で返す（SF4）。

        `expect_head` は完全か短い SHA（#2041）。要求には読んだ HEAD の完全な SHA を添え、その後に HEAD が
        動いていれば GitHub が断る。`method` は設定のマージの方式で、省けば基底のブランチで許された方式から選ぶ。
        許されていない方式は要求を送らずに断る（3378-2）。基底がマージキューを持てば、キューは自分の方式でマージする。
        `require_method` が真の呼び手（merge commit を必須とする本番へのマージ）だけ、キューの方式が `method` と
        違えばキューへ入れずに断る。偽なら（プランの merge ステップ。方式は自動で決めた既定値）キューの方式に従う。
        `jump` はキューへ入れるときに先頭へ入れる（版上げの PR。#3672 の決定 18）。
        `expect`（`ndf ci merge` の判定の値）があれば、PR を読み直さずに `expect.pull` で先の確認をし、要求を送る直前に
        マージ先・HEAD・設定の oid を照らす（#4409 の決定 8）。
        """
        route = _MergeRoute(self._send_rest_merge, lambda exc: exc.status in github_merge.MERGE_REFUSED_STATUS, "", require_method, jump)
        return self._merge_with(number, expect_head, method, route, expect)

    def merge_as_admin(self, number: int, expect_head: str, method: Optional[str] = None) -> MergeOutcome:
        """PR を管理者の迂回でマージする（#3506 の決定 5）。`merge` と同じ先の確認を通り、GraphQL の `mergePullRequest` を
        読んだ HEAD（`expectedHeadOid`）を添えて打つ。断られたら PR を読み直して種類を返し、届かない・上限は投げ直す。
        """
        return self._merge_with(number, expect_head, method, _MergeRoute(self._send_admin_merge, _refused_by_graphql, "管理者の迂回で"))

    def _send_rest_merge(self, number: int, pull: Pull, chosen: str) -> str:
        body = self.rest("PUT", f"/repos/{self.repo}/pulls/{number}/merge", {"sha": pull.head_sha, "merge_method": chosen})
        return text_of(body.get("sha")) if isinstance(body, dict) else ""

    def _send_admin_merge(self, number: int, pull: Pull, chosen: str) -> str:
        node = self.repository(github_merge.PULL_ID_QUERY, {"number": int(number)})["pullRequest"]
        variables = {"id": text_of(node["id"]), "head": pull.head_sha, "method": github_merge.GRAPHQL_MERGE_METHODS[chosen]}
        data = self.graphql(github_merge.ADMIN_MERGE_MUTATION, variables)
        payload = data.get("mergePullRequest")
        merged = payload.get("pullRequest") if isinstance(payload, dict) else None
        commit = merged.get("mergeCommit") if isinstance(merged, dict) else None
        return text_of(commit.get("oid")) if isinstance(commit, dict) else ""

    def _merge_with(
        self, number: int, expect_head: Optional[str], method: Optional[str], route: "_MergeRoute", expect: Optional[MergeExpect] = None
    ) -> MergeOutcome:
        """`merge`・`merge_as_admin` の共通の手順。先の確認と方式の選択を通り、`route.send` で要求を打つ。`route.refused` が
        真とみなした誤りは PR を読み直して種類を返し、ほかは投げ直す（`route` の各項目は `_MergeRoute`）。
        `expect` があれば最初の読み直しをやめ、方式の問い合わせの後・送る直前に照らす。"""
        pull = expect.pull if expect is not None else self.pull(number)
        early = github_merge.preflight(pull, number, expect_head)
        if early is not None:
            return early
        allowed = self.allowed_merge_methods(pull.base_ref)
        chosen = github_merge.pick_merge_method(method, allowed)
        if chosen is None:
            return github_merge.method_refusal(pull, number, method, allowed)
        if expect is not None:
            state = self.repository(github_merge.VERIFY_QUERY, {"number": int(number), "expr": f"{expect.base}:{expect.config_path}"})
            stop = self._verify_before_send(number, state, expect)
            if stop is not None:
                return stop
        try:
            landed = route.send(number, pull, chosen)
        except GitHubError as exc:
            if not route.refused(exc):
                raise
            required = chosen if route.require_method else None
            queued = self._enqueue(pull, required, route.jump, expect) if exc.status == github_merge.MERGE_QUEUE_REFUSED_STATUS else None
            if queued is not None:
                return queued
            after = self.pull(number)
            return MergeOutcome(False, refusal_kind(after, pull.head_sha), str(exc), after.head_sha)
        return MergeOutcome(True, None, f"#{number} を {chosen} で{route.how}マージした", pull.head_sha, landed)

    def _enqueue(
        self, pull: Pull, method: Optional[str], jump: bool = False, expect: Optional[MergeExpect] = None
    ) -> Optional[MergeOutcome]:
        """基底がマージキューを持てば PR を HEAD のままキューへ入れ、待ち（`ci_pending`）を返す。持たなければ None。

        直接のマージを 405 で断られたときだけ読む（キューの無い基底の要求の回数を増やさない）。既にキューにあれば入れ直さない。
        `method`（方式を必須とする呼び手だけが渡す）を指定したときは、キューのマージの方式が同じでなければ
        （読めないときも）キューへ入れずに断る（キューは自分の方式でマージし、merge commit を求めた呼び手の祖先の関係が壊れる）。
        `jump` は先頭へ入れる（版上げの PR。後ろへ並ぶと先に並んだ変更が承認の外で入る。#3672 の決定 18）。
        要求には読んだHEAD（`expectedHeadOid`）を添え、承認の後にHEADが動いていれば GitHub が断る。
        `expect` があれば、キューの状態の問い合わせで照らしてから入れ、入れた後に PR を読み直してマージ先が変わっていれば外す。
        """
        repo = self._queue_state(pull, expect)
        node = repo["pullRequest"]
        if not isinstance(node, dict) or not node["isMergeQueueEnabled"]:
            return None
        stop = self._verify_before_send(pull.number, repo, expect) if expect is not None else None
        if stop is None and method is not None:
            stop = github_merge.queue_method_refusal(pull, method, github_merge.queue_merge_method(repo.get("mergeQueue")))
        if stop is None and not node["isInMergeQueue"]:
            stop = self._put_in_queue(pull, node["id"], jump, expect)
        if stop is not None:
            return stop
        return MergeOutcome(False, MERGE_QUEUE_PENDING, f"#{pull.number} をマージキューへ入れた（マージキューの待ち）", pull.head_sha)

    def _queue_state(self, pull: Pull, expect: Optional[MergeExpect]) -> dict[str, Any]:
        """マージキューの状態の問い合わせ。`expect` があれば確かめの項目を足して同じ 1 回で読む。"""
        variables: dict[str, Any] = {"number": int(pull.number), "branch": pull.base_ref}
        if expect is None:
            return self.repository(github_merge.MERGE_QUEUE_STATE_QUERY, variables)
        return self.repository(github_merge.MERGE_QUEUE_VERIFY_QUERY, {**variables, "expr": f"{expect.base}:{expect.config_path}"})

    def _verify_before_send(self, number: int, repo: dict[str, Any], expect: MergeExpect) -> Optional[MergeOutcome]:
        """確かめの応答 `repo` を `_verify` で照らす。止めるとき（設定が変わり承認が要るようになった等）は、PR が
        マージキューにあれば外す（止めた PR をキューがマージしない。読み直しの要求を足さない）。"""
        stop = _verify(number, _verified(repo), expect, self.repo)
        node = repo.get("pullRequest")
        if stop is not None and isinstance(node, dict) and node.get("isInMergeQueue") is True:
            self._leave_queue(node["id"])
        return stop

    def _leave_queue(self, node_id: Any) -> None:
        """ノード ID `node_id` の PR をマージキューから外す要求を打つ（キューにあるかは呼び手が確かめる）。"""
        self.graphql(github_merge.DEQUEUE_MUTATION, {"id": node_id})

    def _put_in_queue(self, pull: Pull, node_id: Any, jump: bool, expect: Optional[MergeExpect]) -> Optional[MergeOutcome]:
        """PR を HEAD のままキューへ入れる。`expect` があれば入れた後に読み直し、マージ先が変わっていれば外して
        `github_merge.TARGET_MOVED` で断る。"""
        self.graphql(github_merge.ENQUEUE_MUTATION, {"id": node_id, "head": pull.head_sha, "jump": bool(jump)})
        if expect is None:
            return None
        after = self.pull(pull.number)
        if after.base_ref == expect.base:
            return None
        self._leave_queue(node_id)
        detail = f"#{pull.number} のマージ先がキューへ入れた後に {expect.base} から {after.base_ref} へ変わった。キューから外した"
        return MergeOutcome(False, github_merge.TARGET_MOVED, detail, after.head_sha)

    def in_merge_queue(self, number: int) -> bool:
        """PR `number` が今マージキューに入っているか（キューが外したら偽）。"""
        node = self.repository(github_merge.IN_MERGE_QUEUE_QUERY, {"number": int(number)})["pullRequest"]
        return isinstance(node, dict) and node["isInMergeQueue"] is True

    def dequeue(self, number: int) -> None:
        """PR `number` をマージキューから外す（入っていなければ何もしない）。"""
        node = self.repository(github_merge.IN_MERGE_QUEUE_QUERY, {"number": int(number)})["pullRequest"]
        if isinstance(node, dict) and node["isInMergeQueue"]:
            self._leave_queue(node["id"])


def _issue_filter(labels: Sequence[str], state: str) -> dict[str, Any]:
    """Issue の絞り込みの変数（`count_issues`・`issue_numbers`）。ラベルが無ければ null で渡し、絞り込まない。"""
    return {"labels": list(labels) if labels else None, "states": [state]}


# Issue の番号の 1 ページ（`GitHub.issue_numbers`）
_ISSUE_PAGE = (
    "query($owner: String!, $name: String!, $labels: [String!], $states: [IssueState!], $after: String) {"
    f" repository(owner: $owner, name: $name) {{ issues(labels: $labels, states: $states, first: {PER_PAGE}, after: $after) {{"
    " totalCount pageInfo { hasNextPage endCursor } nodes { number } } } }"
)

# head のブランチの開いた PR の 1 ページ（`GitHub.open_pulls_of_head`。新しい順。`$base` が null ならマージ先で絞らない）。1 ブランチ専用の照会なので、
# フォークの同名ブランチが先に並んでも往復が増えないよう、上限の件数（`PER_PAGE`）で読む
_OPEN_HEAD_PAGE = (
    "query($owner: String!, $name: String!, $head: String!, $base: String, $after: String) {"
    f" repository(owner: $owner, name: $name) {{ pullRequests(headRefName: $head, baseRefName: $base, states: [OPEN], first: {PER_PAGE},"
    f" after: $after, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {PULL_FIELDS} }} }} }} }}"
)


def _visibility_query(repos: Sequence[str]) -> str:
    """リポジトリごとの `visibility` を、最上位のエイリアス（`a0`・`a1`…）で 1 回に読むクエリ。"""
    parts = []
    for i, repo in enumerate(repos):
        owner, name = repo.split("/", 1)
        parts.append(f"{_alias(i)}: repository(owner: {graphql_string(owner)}, name: {graphql_string(name)}) {{ visibility }}")
    return "query { " + " ".join(parts) + " }"


def _visibility_of(data: Any, i: int) -> str:
    """`_visibility_query` の応答の `i` 番目のリポジトリの公開範囲（大文字）。読めなければ `UNKNOWN`。"""
    node = data[_alias(i)] if isinstance(data, dict) and _alias(i) in data else None
    return text_of(node["visibility"]).upper() if isinstance(node, dict) else UNKNOWN
