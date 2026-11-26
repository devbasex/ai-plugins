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
import re
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

from ndf.adapters import cli_floor, git, github_limits, write_gate
from ndf.adapters.github_limits import GRAPHQL, REST, RetryWithin
from ndf.adapters.github_rules import NO_RULES, PULL_REQUEST_RULE, RuleFacts, classic_facts_of, merge_facts, rule_facts_of
from ndf.contracts import limits

T = TypeVar("T")
# GraphQL のエイリアスで引ける番号つきの節点
NUMBERED_NODES = ("pullRequest", "issue", "issueOrPullRequest")
PULL_FIELDS = (  # PR を読むときの GraphQL の選択（`Pull.from_graphql` が読む項目）
    "number state merged headRefOid headRefName baseRefName body mergeStateStatus author { login } authorAssociation mergeCommit { oid }"
    " title isCrossRepository"
)
# head のブランチごとに読む PR の件数（新しい順）
PULLS_PER_HEAD = 5
# 一覧の 1 ページの件数（REST と GraphQL の上限。ページ数の上限は contracts/limits/ の github.check-runs・github.list-pages）
PER_PAGE = 100
# 認証の値を読む環境変数（先のものを優先する。利用者の手順へ渡さないときに消す名前も同じ）
TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
# GitHub App の bot の login の末尾（`<アプリの slug>[bot]`。commit status の書き手からアプリを引く。#3506）
BOT_SUFFIX = "[bot]"
# マージの要求を GitHub が断ったときの HTTP の状態（405: 規則・衝突、409: HEAD が変わった、422: 形）
MERGE_REFUSED_STATUS = (405, 409, 422)
# PR の node の ID と、迂回のマージ（`gh pr merge --admin` が呼ぶのと同じ mergePullRequest を、HEAD を添えて打つ。#3506 の決定 5）
_PULL_ID = "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) { pullRequest(number: $number) { id } } }"
_ADMIN_MERGE = (
    "mutation($id: ID!, $head: GitObjectID!, $method: PullRequestMergeMethod!) {"
    " mergePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head, mergeMethod: $method}) { pullRequest { merged } } }"
)
# マージの方式が基底で許されていない断りの停止理由の種類（方式を渡した呼び手が、ほかの断りと分けて方式を決め直す）
METHOD_REFUSED = "merge_method"
# マージの拒否を、拒否の後に読み直した `mergeStateStatus` で種類へ分ける（SF4）。標準エラーの文は読まない
_REFUSAL_KINDS = {"dirty": "base_conflict", "blocked": "not_ci", "behind": "not_ci", "draft": "not_ci"}
# マージキューが必須の基底で直接のマージを断る状態（GitHub は 405 で返す）。このときはキューへ入れ、待ちとして返す
MERGE_QUEUE_REFUSED_STATUS = 405
# マージキューへ入れた後の停止理由の種類（実際のマージまでは CI の待ちとして扱う）
MERGE_QUEUE_PENDING = "ci_pending"
_MERGE_QUEUE_STATE = (
    "query($owner: String!, $name: String!, $number: Int!, $branch: String!) { repository(owner: $owner, name: $name) {"
    " pullRequest(number: $number) { id isMergeQueueEnabled isInMergeQueue }"
    " mergeQueue(branch: $branch) { configuration { mergeMethod } } } }"
)
_IN_MERGE_QUEUE = (
    "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {"
    " pullRequest(number: $number) { id isInMergeQueue } } }"
)
# `jump` は版上げの PR をキューの先頭へ入れる指定（#3672 の決定 18。承認の範囲を版上げの PR の head に固定する）
_ENQUEUE = (
    "mutation($id: ID!, $head: GitObjectID!, $jump: Boolean) {"
    " enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head, jump: $jump}) { clientMutationId } }"
)
_DEQUEUE = "mutation($id: ID!) { dequeuePullRequest(input: {id: $id}) { clientMutationId } }"
# マージの方式 → 「基底に含まれたか」の確かめ方（3378-2）。ancestry は head が基底の祖先か、merged は PR がマージ済み
# （`merged_at` がある）か。squash と rebase は新しいコミットを作り、head は基底の祖先にならない。
# 並びは、設定が無いときに許された方式から選ぶ順
ANCESTRY, MERGED = "ancestry", "merged"
# merge commit の方式の名前（祖先の関係を保つ方式。呼び出し側が方式を固定するときに参照する）
MERGE_COMMIT = "merge"


class _MergeMethod(NamedTuple):
    """マージの方式 1 つ。`landing` は基底に含まれたかの確かめ方、`setting` はリポジトリの設定の許可のキー
    （ruleset に許す方式の指定が無いときに読む）、`graphql` は GraphQL の mergePullRequest の方式（PullRequestMergeMethod。#3506）。"""

    landing: str
    setting: str
    graphql: str


# マージの方式の唯一の定義（並びは設定が無いときに選ぶ順）。`MERGE_METHODS` は方式 → 確かめ方
_MERGE_METHOD_SPECS = {
    "squash": _MergeMethod(MERGED, "allow_squash_merge", "SQUASH"),
    MERGE_COMMIT: _MergeMethod(ANCESTRY, "allow_merge_commit", "MERGE"),
    "rebase": _MergeMethod(MERGED, "allow_rebase_merge", "REBASE"),
}
MERGE_METHODS = {name: spec.landing for name, spec in _MERGE_METHOD_SPECS.items()}
# GraphQL の mergePullRequest のマージの方式（REST の merge_method → GraphQL の PullRequestMergeMethod。#3506）
GRAPHQL_MERGE_METHODS = {name: spec.graphql for name, spec in _MERGE_METHOD_SPECS.items()}
# 信用の区分（#3364）。OWNER はリポジトリのオーナーでそのまま信用する。MEMBER・COLLABORATOR は所属を表すだけで
# write を保証しない（read・triage もある）ため、対象リポジトリでの権限を読んで write 以上のときだけ信用する。
# ほかの値（CONTRIBUTOR・FIRST_TIME_CONTRIBUTOR・FIRST_TIMER・MANNEQUIN・NONE と知らない値）の本文は信用しない入力
OWNER_ASSOCIATION = "OWNER"
PERMISSION_CHECKED_ASSOCIATIONS = frozenset({"MEMBER", "COLLABORATOR"})
# 権限の読み取り（REST の collaborators/{login}/permission の `permission`）で write 以上を表す値。maintain は write に写る
WRITE_PERMISSIONS = frozenset({"admin", "write"})
# リポジトリの公開範囲（GraphQL の `visibility`）。読めないリポジトリは UNKNOWN で、公開でないとして扱う
PUBLIC, UNKNOWN = "PUBLIC", "UNKNOWN"
# PR 本文の「マージの前に決める」残リスクのマーカーと、決めたことを表すチェック済みの項目（#2602）
PREMERGE_MARK = "マージの前に決める"
_CHECKED_ITEM = re.compile(r"^\s*[-*+]\s+\[[xX]\]\s")


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

    @classmethod
    def from_rest(cls, raw: dict) -> Authored:
        url = text_of(raw["html_url"]) if isinstance(raw.get("html_url"), str) else ""
        created, updated = raw.get("created_at"), raw.get("updated_at")
        edited = isinstance(created, str) and isinstance(updated, str) and updated != created
        return cls(_login(raw["user"]), text_of(raw["author_association"]).upper(), text_of(raw["body"]), url, edited)


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
    # マージで本流へ入ったコミット（squash・rebase のマージでは head と別。マージされていなければ空）と PR の題（#4446）
    merge_sha: str = ""
    title: str = ""
    cross_repository: bool = False  # head のブランチがフォークにあるか（GraphQL で読んだときだけ埋める）

    @property
    def authored(self) -> Authored:
        """PR の本文と書き手（信用の区分は `GitHub.trusts`）。"""
        return Authored(self.author, self.association, self.body)

    @classmethod
    def from_rest(cls, raw: dict) -> Pull:
        head, base = raw["head"], raw["base"]
        return cls(
            number=int(raw["number"]),
            state=text_of(raw["state"]).lower(),
            merged=bool(raw["merged"]),
            head_sha=text_of(head["sha"]),
            head_ref=text_of(head["ref"]),
            base_ref=text_of(base["ref"]),
            body=text_of(raw["body"]),
            merge_state=text_of(raw["mergeable_state"]).lower(),
            author=_login(raw["user"]),
            association=text_of(raw["author_association"]).upper(),
            merge_sha=text_of(raw.get("merge_commit_sha")) if raw["merged"] else "",
            title=text_of(raw.get("title")),
        )

    @classmethod
    def from_graphql(cls, raw: dict) -> Pull:
        state = text_of(raw["state"]).lower()
        return cls(
            number=int(raw["number"]),
            state="open" if state == "open" else "closed",
            merged=bool(raw["merged"]),
            head_sha=text_of(raw["headRefOid"]),
            head_ref=text_of(raw["headRefName"]),
            base_ref=text_of(raw["baseRefName"]),
            body=text_of(raw["body"]),
            merge_state=text_of(raw["mergeStateStatus"]).lower(),
            author=_login(raw["author"]),
            association=text_of(raw["authorAssociation"]).upper(),
            merge_sha=text_of(raw["mergeCommit"].get("oid")) if isinstance(raw.get("mergeCommit"), dict) else "",
            title=text_of(raw.get("title")),
            cross_repository=bool(raw.get("isCrossRepository")),
        )


class MergeOutcome(NamedTuple):
    """マージの結果。`kind` は断りの停止理由の種類（`refused`・`METHOD_REFUSED`・`base_conflict`・`not_ci`・`unknown`）か待ち（`MERGE_QUEUE_PENDING`）。"""

    merged: bool
    kind: Optional[str]
    detail: str
    head_sha: str


def premerge_risks(body: str) -> list[str]:
    """PR 本文の「マージの前に決める」残リスクの行のうち、決めたマーカー（チェック済みの項目 `- [x]`）の無いもの（#2602）。"""
    return [line.strip() for line in body.splitlines() if PREMERGE_MARK in line and not _CHECKED_ITEM.match(line)]


def same_repo(a: str, b: str) -> bool:
    """2 つのリポジトリ名（`<所有者>/<リポジトリ>`）が同じか。GitHub の名前は大文字小文字を区別しない。"""
    return a.lower() == b.lower()


def pick_merge_method(declared: Optional[str], allowed: Sequence[str]) -> Optional[str]:
    """マージの方式を決める（3378-2）。設定の値があればそれ（許されていなければ None）、無ければ許された方式の先頭。"""
    if declared is not None:
        return declared if declared in allowed else None
    return allowed[0] if allowed else None


def ruleset_merge_methods(rules: Iterable[dict]) -> Optional[list[str]]:
    """ブランチに当たる規則（`GET /repos/{repo}/rules/branches/{branch}`）が許すマージの方式（`MERGE_METHODS` の並び）。

    PR の規則が許す方式を指定していなければ None（リポジトリの設定を読む）。規則が 2 つ以上なら、どれもが許す方式だけ。
    """
    found: Optional[set[str]] = None
    for rule in rules:
        if rule["type"] != PULL_REQUEST_RULE or "allowed_merge_methods" not in rule["parameters"]:
            continue
        methods = set(rule["parameters"]["allowed_merge_methods"])
        found = methods if found is None else found & methods
    return None if found is None else [m for m in MERGE_METHODS if m in found]


def settings_merge_methods(raw: Any, named: Optional[Sequence[str]]) -> list[str]:
    """リポジトリの設定（`GET /repos/{repo}` の生の応答）と ruleset が許す方式 `named` の両方が許すマージの方式。

    設定の項目が無い方式は候補に残し、項目があれば値が True の方式だけを許す。`named` が None なら設定だけで決める。
    """
    settings = raw if isinstance(raw, dict) else {}
    allowed = [m for m, spec in _MERGE_METHOD_SPECS.items() if spec.setting not in settings or settings[spec.setting] is True]
    return allowed if named is None else [m for m in allowed if m in named]


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
    risks = premerge_risks(pull.body)
    if risks:
        return MergeOutcome(False, "refused", f"#{number} の本文にマージの前に決める残リスクがある: {' / '.join(risks)}", pull.head_sha)
    return None


def _method_refusal(pull: Pull, number: int, method: Optional[str], allowed: Sequence[str]) -> MergeOutcome:
    """マージの方式が基底のブランチで許されていないときの、要求を送らずに返す断り（3378-2）。"""
    want = method if method is not None else "（設定なし）"
    detail = f"#{number} のマージの方式 {want} は {pull.base_ref} で許されていない（許す方式: {', '.join(allowed)}）"
    return MergeOutcome(False, METHOD_REFUSED, detail, pull.head_sha)


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
        data = self.graphql(query, {**self._owner_name(), **(variables if variables is not None else {})})
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
        `spare` は、ついでに読めればよい番号である。`numbers` を読むクエリが 100 件に満たない残りの分だけを読み、呼び出しの回数と
        読み替えの可否を変えない。残りに入らない番号と、REST へ読み替えたときの番号は、返す辞書に載らない。
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
        got = self.aliased({b: selection.format(_graphql_string(b), PULLS_PER_HEAD, PULL_FIELDS) for b in wanted})
        return {b: [Pull.from_graphql(p) for p in got[b]["nodes"]] if got[b] else [] for b in wanted}

    def open_pulls_of_head(self, branch: str, enough: int) -> list[Pull]:
        """head のブランチが `branch` で、このリポジトリの開いた PR（フォークの同名ブランチを除く。新しい順）。

        取得の時点で開いた PR に絞り、同じリポジトリの候補が `enough` 件そろうか一覧を読み終えるまでページを送る
        （候補が 1 件だけかを、取得の範囲から外れた古い PR で取り違えない）。`github.list-pages` ページを超えたら `GitHubError`。
        """
        pages = _page_cap("github.list-pages")
        variables: dict[str, Any] = {"head": branch, "after": None}
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
        """基底のブランチ `branch` で許されたマージの方式（`MERGE_METHODS` の並び。3378-2）。

        ruleset の PR の規則の `allowed_merge_methods` とリポジトリの設定の両方を読み、両方が許す方式だけを返す
        （ruleset は方式を絞るだけで、リポジトリの設定で無効にした方式を有効にしない）。ruleset に指定が無ければ設定だけで決める。
        設定の項目（`allow_squash_merge` など）は権限の足りないトークンでは応答に無い。無い項目の方式は許すかが
        不明なので候補に残し、要求を送って GitHub の拒否（405）に判定を任せる。項目があれば値が True の方式だけを許す。
        """
        rules = self.rest("GET", f"/repos/{self.repo}/rules/branches/{quote(branch, safe='')}")
        named = ruleset_merge_methods(rules if isinstance(rules, list) else [])
        if named == []:
            return []
        return settings_merge_methods(self.rest("GET", f"/repos/{self.repo}"), named)

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

    def merge(
        self, number: int, expect_head: Optional[str] = None, method: Optional[str] = None, require_method: bool = False, jump: bool = False
    ) -> MergeOutcome:
        """PR をマージする。HEAD の食い違い・決めていない残リスクは要求を送らずに断り、拒否は種類で返す（SF4）。

        `expect_head` は完全か短い SHA（#2041）。要求には読んだ HEAD の完全な SHA を添え、その後に HEAD が
        動いていれば GitHub が断る。`method` は設定のマージの方式で、省けば基底のブランチで許された方式から選ぶ。
        許されていない方式は要求を送らずに断る（3378-2）。基底がマージキューを持てば、キューは自分の方式でマージする。
        `require_method` が真の呼び手（merge commit を必須とする本番へのマージ）だけ、キューの方式が `method` と
        違えばキューへ入れずに断る。偽なら（プランの merge ステップ。方式は自動で決めた既定値）キューの方式に従う。
        `jump` はキューへ入れるときに先頭へ入れる（版上げの PR。#3672 の決定 18）。
        """
        route = _MergeRoute(self._send_rest_merge, lambda exc: exc.status in MERGE_REFUSED_STATUS, "", require_method, jump)
        return self._merge_with(number, expect_head, method, route)

    def merge_as_admin(self, number: int, expect_head: str, method: Optional[str] = None) -> MergeOutcome:
        """PR を管理者の迂回でマージする（#3506 の決定 5）。`merge` と同じ先の確認を通り、GraphQL の `mergePullRequest` を
        読んだ HEAD（`expectedHeadOid`）を添えて打つ。断られたら PR を読み直して種類を返し、届かない・上限は投げ直す。
        """
        return self._merge_with(number, expect_head, method, _MergeRoute(self._send_admin_merge, _refused_by_graphql, "管理者の迂回で"))

    def _send_rest_merge(self, number: int, pull: Pull, chosen: str) -> None:
        self.rest("PUT", f"/repos/{self.repo}/pulls/{number}/merge", {"sha": pull.head_sha, "merge_method": chosen})

    def _send_admin_merge(self, number: int, pull: Pull, chosen: str) -> None:
        node = self.repository(_PULL_ID, {"number": int(number)})["pullRequest"]
        variables = {"id": text_of(node["id"]), "head": pull.head_sha, "method": GRAPHQL_MERGE_METHODS[chosen]}
        self.graphql(_ADMIN_MERGE, variables)

    def _merge_with(self, number: int, expect_head: Optional[str], method: Optional[str], route: "_MergeRoute") -> MergeOutcome:
        """`merge`・`merge_as_admin` の共通の手順。先の確認と方式の選択を通り、`route.send` で要求を打つ。`route.refused` が
        真とみなした誤りは PR を読み直して種類を返し、ほかは投げ直す（`route` の各項目は `_MergeRoute`）。"""
        pull = self.pull(number)
        early = _preflight(pull, number, expect_head)
        if early is not None:
            return early
        allowed = self.allowed_merge_methods(pull.base_ref)
        chosen = pick_merge_method(method, allowed)
        if chosen is None:
            return _method_refusal(pull, number, method, allowed)
        try:
            route.send(number, pull, chosen)
        except GitHubError as exc:
            if not route.refused(exc):
                raise
            required = chosen if route.require_method else None
            queued = self._enqueue(pull, required, route.jump) if exc.status == MERGE_QUEUE_REFUSED_STATUS else None
            if queued is not None:
                return queued
            after = self.pull(number)
            return MergeOutcome(False, refusal_kind(after, pull.head_sha), str(exc), after.head_sha)
        return MergeOutcome(True, None, f"#{number} を {chosen} で{route.how}マージした", pull.head_sha)

    def _enqueue(self, pull: Pull, method: Optional[str], jump: bool = False) -> Optional[MergeOutcome]:
        """基底がマージキューを持てば PR を HEAD のままキューへ入れ、待ち（`ci_pending`）を返す。持たなければ None。

        直接のマージを 405 で断られたときだけ読む（キューの無い基底の要求の回数を増やさない）。既にキューにあれば入れ直さない。
        `method`（方式を必須とする呼び手だけが渡す）を指定したときは、キューのマージの方式が同じでなければ
        （読めないときも）キューへ入れずに断る（キューは自分の方式でマージし、merge commit を求めた呼び手の祖先の関係が壊れる）。
        `jump` は先頭へ入れる（版上げの PR。後ろへ並ぶと先に並んだ変更が承認の外で入る。#3672 の決定 18）。
        要求には読んだHEAD（`expectedHeadOid`）を添え、承認の後にHEADが動いていれば GitHub が断る。
        """
        repo = self.repository(_MERGE_QUEUE_STATE, {"number": int(pull.number), "branch": pull.base_ref})
        node = repo["pullRequest"]
        if not isinstance(node, dict) or not node["isMergeQueueEnabled"]:
            return None
        if method is not None:
            queue_method = _queue_merge_method(repo.get("mergeQueue"))
            if queue_method != method:
                return MergeOutcome(
                    False,
                    "refused",
                    f"#{pull.number} の基底 {pull.base_ref} のマージキューは {queue_method or '（読めない）'} でマージし、"
                    f"求めた {method} と違う。キューへ入れずに止める（キューのマージの方式を {method} にする）",
                    pull.head_sha,
                )
        if not node["isInMergeQueue"]:
            self.graphql(_ENQUEUE, {"id": node["id"], "head": pull.head_sha, "jump": bool(jump)})
        return MergeOutcome(False, MERGE_QUEUE_PENDING, f"#{pull.number} をマージキューへ入れた（マージキューの待ち）", pull.head_sha)

    def in_merge_queue(self, number: int) -> bool:
        """PR `number` が今マージキューに入っているか（キューが外したら偽）。"""
        node = self.repository(_IN_MERGE_QUEUE, {"number": int(number)})["pullRequest"]
        return isinstance(node, dict) and node["isInMergeQueue"] is True

    def dequeue(self, number: int) -> None:
        """PR `number` をマージキューから外す（入っていなければ何もしない）。"""
        node = self.repository(_IN_MERGE_QUEUE, {"number": int(number)})["pullRequest"]
        if isinstance(node, dict) and node["isInMergeQueue"]:
            self.graphql(_DEQUEUE, {"id": node["id"]})


def _queue_merge_method(queue: Any) -> Optional[str]:
    """`mergeQueue { configuration { mergeMethod } }` のマージの方式（`MERGE_METHODS` の名前。読めなければ None）。"""
    config = queue.get("configuration") if isinstance(queue, dict) else None
    got = config.get("mergeMethod") if isinstance(config, dict) else None
    return got.lower() if isinstance(got, str) else None


def _issue_filter(labels: Sequence[str], state: str) -> dict[str, Any]:
    """Issue の絞り込みの変数（`count_issues`・`issue_numbers`）。ラベルが無ければ null で渡し、絞り込まない。"""
    return {"labels": list(labels) if labels else None, "states": [state]}


# Issue の番号の 1 ページ（`GitHub.issue_numbers`）
_ISSUE_PAGE = (
    "query($owner: String!, $name: String!, $labels: [String!], $states: [IssueState!], $after: String) {"
    f" repository(owner: $owner, name: $name) {{ issues(labels: $labels, states: $states, first: {PER_PAGE}, after: $after) {{"
    " totalCount pageInfo { hasNextPage endCursor } nodes { number } } } }"
)

# head のブランチの開いた PR の 1 ページ（`GitHub.open_pulls_of_head`。新しい順）。1 ブランチ専用の照会なので、
# フォークの同名ブランチが先に並んでも往復が増えないよう、上限の件数（`PER_PAGE`）で読む
_OPEN_HEAD_PAGE = (
    "query($owner: String!, $name: String!, $head: String!, $after: String) {"
    f" repository(owner: $owner, name: $name) {{ pullRequests(headRefName: $head, states: [OPEN], first: {PER_PAGE},"
    f" after: $after, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ {PULL_FIELDS} }} }} }} }}"
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
