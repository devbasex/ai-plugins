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
from ndf.adapters.github_merge import MERGE_QUEUE_PENDING, FileAt, MergeOutcome, RuleFacts, Verified

# 呼び手（`ndf.domains.release.land`）が github から読む、github_merge の名前
from ndf.adapters.github_merge import BYPASS_ORDER as BYPASS_ORDER
from ndf.adapters.github_merge import MERGE_COMMIT as MERGE_COMMIT
from ndf.contracts import limits

T = TypeVar("T")
# GraphQL のエイリアスで引ける番号つきの節点
NUMBERED_NODES = ("pullRequest", "issue", "issueOrPullRequest")
# PR を読むときの GraphQL の選択（`Pull.from_graphql` が読む項目）
PULL_FIELDS = (
    "number state merged headRefOid headRefName baseRefName body mergeStateStatus author { login } authorAssociation mergeCommit { oid }"
    " isInMergeQueue"
)
# head のブランチごとに読む PR の件数（新しい順）
PULLS_PER_HEAD = 5
# 一覧の 1 ページの件数（REST と GraphQL の上限。ページ数の上限は contracts/limits/ の github.check-runs・github.list-pages）
PER_PAGE = 100
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


def text_of(value: Any) -> str:
    """文字列ならそのまま、ほかの値（None など）は空文字。"""
    return value if isinstance(value, str) else ""


def _login(user: Any) -> str:
    """書き手の login。消えたアカウント（REST の `user`・GraphQL の `author` が null）は空文字。"""
    return text_of(user["login"]) if isinstance(user, dict) else ""


def _account_id(raw: Any) -> int:
    """GitHub が発行するアカウント ID（REST の `user.id`・GraphQL の `databaseId`）。無い・読めなければ 0。"""
    if not isinstance(raw, dict):
        return 0
    got = raw["id"] if "id" in raw else raw["databaseId"] if "databaseId" in raw else None
    return got if isinstance(got, int) and not isinstance(got, bool) and got > 0 else 0


class Viewer(NamedTuple):
    """token の利用者。`account_id` は GitHub が発行するアカウント ID で、読めなければ 0（`login` は表示に使うだけ）。"""

    login: str
    account_id: int


class Authored(NamedTuple):
    """書き手の付いた本文（Issue・PR・コメント）。`association` は GitHub の author_association（大文字）。

    信用の区分は対象リポジトリでの権限で決まるため、`GitHub.trusts` が判定する（#3364）。
    """

    author: str
    association: str
    body: str
    # コメントの URL（REST の `html_url`。迂回の記録が照らした証跡を指す。#3506）。無ければ空
    url: str = ""
    # 作成の後に編集されたか（REST の `updated_at` が `created_at` と違う）。編集された証跡は承認に数えない（#3506）
    edited: bool = False
    # 書き手のアカウント ID（REST の `user.id`）。同じ名前の別のアカウントを分けるのに使う（#4409）。無ければ 0
    author_id: int = 0

    @classmethod
    def from_rest(cls, raw: dict) -> Authored:
        url = text_of(raw["html_url"]) if isinstance(raw.get("html_url"), str) else ""
        created, updated = raw.get("created_at"), raw.get("updated_at")
        edited = isinstance(created, str) and isinstance(updated, str) and updated != created
        user = raw["user"]
        return cls(_login(user), text_of(raw["author_association"]).upper(), text_of(raw["body"]), url, edited, _account_id(user))


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
    # マージで本流へ入ったコミット（squash・rebase のマージでは head と別）。マージされていなければ空
    merge_sha: str = ""
    # マージキューに入っているか（GraphQL で読んだときだけ分かる。REST では偽。#4409 の決定 8）
    queued: bool = False

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
            text_of(raw.get("merge_commit_sha")) if raw["merged"] else "",
        )  # fmt: skip

    @classmethod
    def from_graphql(cls, raw: dict) -> Pull:
        state = text_of(raw["state"]).lower()
        return cls(
            int(raw["number"]), "open" if state == "open" else "closed", bool(raw["merged"]), text_of(raw["headRefOid"]),
            text_of(raw["headRefName"]), text_of(raw["baseRefName"]), text_of(raw["body"]), text_of(raw["mergeStateStatus"]).lower(),
            _login(raw["author"]), text_of(raw["authorAssociation"]).upper(),
            text_of(raw["mergeCommit"].get("oid")) if isinstance(raw.get("mergeCommit"), dict) else "",
            raw.get("isInMergeQueue") is True,
        )  # fmt: skip


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
    """マージの要求の打ち方。`send` が要求を打ち、`refused` が真とみなした誤りは断られたとして扱う。`how` は成功の文に
    添える迂回のマーカー。`require_method` が真なら、キューの方式が選んだ方式と違うときキューへ入れずに断る。"""

    send: Callable[[int, "Pull", str], None]
    refused: Callable[["GitHubError"], bool]
    how: str
    require_method: bool = False
    jump: bool = False


def _refused_by_graphql(exc: GitHubError) -> bool:
    """GraphQL の誤りが GitHub の断り（届いて断られた。上限でない）か。届かなかった・上限なら偽（呼び手が投げ直す）。"""
    return exc.status == 0 and not exc.unreached and exc.quota is None


def _preflight(pull: Pull, number: int, expect_head: Optional[str]) -> Optional[MergeOutcome]:
    """マージの要求を送らずに返す結果（マージ済み・HEAD の食い違い・決めていない残リスク）。送ってよければ None。"""
    if pull.merged:
        return MergeOutcome(True, None, f"#{number} は既にマージ済み", pull.head_sha)
    if expect_head and not git.sha_matches(expect_head, pull.head_sha):
        return MergeOutcome(False, "refused", f"#{number} の HEAD が {expect_head} でない（今は {pull.head_sha}）", pull.head_sha)
    risks = github_merge.premerge_risks(pull.body)
    if risks:
        return MergeOutcome(False, "refused", f"#{number} の本文にマージの前に決める残リスクがある: {' / '.join(risks)}", pull.head_sha)
    return None


def _file_at(repo: dict, key: str) -> FileAt:
    """`object(expression)` の応答（キー `key`）を `FileAt` にする。Blob でない・無いなら本文と oid が None。"""
    node = repo.get(key)
    blob = node if isinstance(node, dict) and isinstance(node.get("oid"), str) else None
    text = blob.get("text") if blob is not None and isinstance(blob.get("text"), str) else None
    return FileAt(text_of(repo.get("nameWithOwner")), text, blob["oid"] if blob is not None else None)


def _verified(repo: dict) -> Verified:
    node = repo.get("pullRequest") if isinstance(repo.get("pullRequest"), dict) else {}
    return Verified(
        text_of(node.get("baseRefName")), text_of(node.get("headRefOid")), text_of(repo.get("nameWithOwner")), _file_at(repo, "config")
    )


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


def _method_refusal(pull: Pull, number: int, method: Optional[str], allowed: Sequence[str]) -> MergeOutcome:
    """マージの方式が基底のブランチで許されていないときの、要求を送らずに返す断り（3378-2）。"""
    want = method if method is not None else "（設定なし）"
    detail = f"#{number} のマージの方式 {want} は {pull.base_ref} で許されていない（許す方式: {', '.join(allowed)}）"
    return MergeOutcome(False, github_merge.METHOD_REFUSED, detail, pull.head_sha)


def _queue_method_refusal(pull: Pull, method: str, queue_method: Optional[str]) -> Optional[MergeOutcome]:
    """キューのマージの方式 `queue_method` が求めた方式 `method` と違う（読めないときも）ときの、キューへ入れずに返す断り。"""
    if queue_method == method:
        return None
    detail = (
        f"#{pull.number} の基底 {pull.base_ref} のマージキューは {queue_method or '（読めない）'} でマージし、"
        f"求めた {method} と違う。キューへ入れずに止める（キューのマージの方式を {method} にする）"
    )
    return MergeOutcome(False, "refused", detail, pull.head_sha)


def _response_body(resp: Any) -> Any:
    if not resp.content:
        return None
    if "json" in resp.headers.get("content-type", ""):  # noqa: NDF502 ネットワーク: 応答に content-type が無いことがある
        return resp.json()
    return resp.text


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


def _translated(exc: RequestFailed | RequestError | write_gate.Busy, label: str, detail_body: bool = False) -> GitHubError:
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
    """失敗（順番の待ちの `write_gate.Busy` も）を `_translated` で `GitHubError` へ写して投げ直す。二次レート制限は `tell` で知らせる。"""
    try:
        yield
    except (RateLimitExceeded, RequestFailed, RequestError, write_gate.Busy) as exc:
        error = _translated(exc, label, detail_body)
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

    def pulls(self, numbers: Iterable[int]) -> dict[int, Optional[Pull]]:
        """番号ごとの PR を 1 回のクエリ（100 件ごと）で読む。PR でない・無い番号は None。

        GraphQL のスロットが一次の上限なら、`github.rest-fallback` 件までは REST のスロットで読み替える（不足 g。二次レート制限は読み替えない）。
        """
        wanted = sorted({int(n) for n in numbers})
        try:
            raw = self.nodes(wanted, "pullRequest", PULL_FIELDS)
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
        got = self.aliased({b: selection.format(_graphql_string(b), PULLS_PER_HEAD, PULL_FIELDS) for b in wanted})
        return {b: [Pull.from_graphql(p) for p in got[b]["nodes"]] if got[b] else [] for b in wanted}

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
        if authored.association == OWNER_ASSOCIATION:
            return True
        if authored.association not in PERMISSION_CHECKED_ASSOCIATIONS or not authored.author:
            return False
        return self.permission(authored.author) in WRITE_PERMISSIONS

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
        return None if classic is None else github_merge.merge_facts(github_merge.rule_facts_of(rules, bypasses), classic)

    def _classic_facts(self, branch: str) -> Optional[RuleFacts]:
        """従来のブランチ保護の事実。保護が無ければ `present` が偽、有る（か有無を読めない）のに詳細を読めなければ None。
        先に読む権限で読める `branches/<b>` の `protection.enabled`（ruleset を含まない従来の保護だけの有無）を見て、
        偽なら保護の照会を省く（保護の照会は管理者でない利用者に 404 を返しうる）。"""
        path = f"/repos/{self.repo}/branches/{quote(branch, safe='')}"
        info = self.rest("GET", path)
        protection = info.get("protection") if isinstance(info, dict) else None
        if isinstance(protection, dict) and protection.get("enabled") is False:
            return github_merge.NO_RULES
        try:
            got = self.rest("GET", f"{path}/protection")
        except GitHubError:  # noqa: NDF401 ネットワーク: 保護の詳細を読めなければ None（呼び手が止める）
            return None
        return github_merge.classic_facts_of(got) if isinstance(got, dict) else None

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
        self._viewer.append(Viewer(login if isinstance(login, str) else "", _account_id(node)))

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

    def _send_rest_merge(self, number: int, pull: Pull, chosen: str) -> None:
        self.rest("PUT", f"/repos/{self.repo}/pulls/{number}/merge", {"sha": pull.head_sha, "merge_method": chosen})

    def _send_admin_merge(self, number: int, pull: Pull, chosen: str) -> None:
        node = self.repository(github_merge.PULL_ID_QUERY, {"number": int(number)})["pullRequest"]
        variables = {"id": text_of(node["id"]), "head": pull.head_sha, "method": github_merge.GRAPHQL_MERGE_METHODS[chosen]}
        self.graphql(github_merge.ADMIN_MERGE_MUTATION, variables)

    def _merge_with(
        self, number: int, expect_head: Optional[str], method: Optional[str], route: "_MergeRoute", expect: Optional[MergeExpect] = None
    ) -> MergeOutcome:
        """`merge`・`merge_as_admin` の共通の手順。先の確認と方式の選択を通り、`route.send` で要求を打つ。`route.refused` が
        真とみなした誤りは PR を読み直して種類を返し、ほかは投げ直す（`route` の各項目は `_MergeRoute`）。
        `expect` があれば最初の読み直しをやめ、方式の問い合わせの後・送る直前に照らす。"""
        pull = expect.pull if expect is not None else self.pull(number)
        early = _preflight(pull, number, expect_head)
        if early is not None:
            return early
        allowed = self.allowed_merge_methods(pull.base_ref)
        chosen = github_merge.pick_merge_method(method, allowed)
        if chosen is None:
            return _method_refusal(pull, number, method, allowed)
        if expect is not None:
            fresh = _verified(
                self.repository(github_merge.VERIFY_QUERY, {"number": int(number), "expr": f"{expect.base}:{expect.config_path}"})
            )
            stop = _verify(number, fresh, expect, self.repo)
            if stop is not None:
                return stop
        try:
            route.send(number, pull, chosen)
        except GitHubError as exc:
            if not route.refused(exc):
                raise
            required = chosen if route.require_method else None
            queued = self._enqueue(pull, required, route.jump, expect) if exc.status == github_merge.MERGE_QUEUE_REFUSED_STATUS else None
            if queued is not None:
                return queued
            after = self.pull(number)
            return MergeOutcome(False, refusal_kind(after, pull.head_sha), str(exc), after.head_sha)
        return MergeOutcome(True, None, f"#{number} を {chosen} で{route.how}マージした", pull.head_sha)

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
        stop = _verify(pull.number, _verified(repo), expect, self.repo) if expect is not None else None
        if stop is None and method is not None:
            stop = _queue_method_refusal(pull, method, github_merge.queue_merge_method(repo.get("mergeQueue")))
        if stop is None and not node["isInMergeQueue"]:
            stop = self._put_in_queue(pull, node["id"], jump, expect)
        if stop is not None:
            return stop
        return MergeOutcome(False, MERGE_QUEUE_PENDING, f"#{pull.number} をマージキューへ入れた（マージキューの待ち）", pull.head_sha)

    def _queue_state(self, pull: Pull, expect: Optional[MergeExpect]) -> dict[str, Any]:
        """マージキューの状態の問い合わせ。`expect` があれば確かめの項目を足して同じ 1 回で読む。"""
        if expect is None:
            return self.repository(github_merge.MERGE_QUEUE_STATE_QUERY, {"number": int(pull.number), "branch": pull.base_ref})
        variables = {"number": int(pull.number), "branch": pull.base_ref, "expr": f"{expect.base}:{expect.config_path}"}
        return self.repository(github_merge.MERGE_QUEUE_VERIFY_QUERY, variables)

    def _put_in_queue(self, pull: Pull, node_id: Any, jump: bool, expect: Optional[MergeExpect]) -> Optional[MergeOutcome]:
        """PR を HEAD のままキューへ入れる。`expect` があれば入れた後に読み直し、マージ先が変わっていれば外して
        `github_merge.TARGET_MOVED` で断る。"""
        self.graphql(github_merge.ENQUEUE_MUTATION, {"id": node_id, "head": pull.head_sha, "jump": bool(jump)})
        if expect is None:
            return None
        after = self.pull(pull.number)
        if after.base_ref == expect.base:
            return None
        self.graphql(github_merge.DEQUEUE_MUTATION, {"id": node_id})
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
            self.graphql(github_merge.DEQUEUE_MUTATION, {"id": node["id"]})


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
