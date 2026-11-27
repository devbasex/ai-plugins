"""マージの部品（`ndf.adapters.github` から分けた。通信をしない部品）。

- マージの方式の唯一の定義（`_MERGE_METHOD_SPECS`）と、規則・リポジトリの設定・マージキューが許す方式の選び方
- マージの要求・確かめ・マージキューの GraphQL の形と、マージの結果の値（`MergeOutcome`・`FileAt`・`Verified`）
- 要求を送らずに返すマージの結果（`preflight`・`method_refusal`・`queue_method_refusal`）

GitHub への読み書きは `ndf.adapters.github.GitHub` が持ち、ここは送る形と、応答の値から方式を決める規則だけを持つ。
ruleset と従来のブランチ保護の事実は `ndf.adapters.github_rules` が持つ。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple, Optional, Protocol

from ndf.adapters import git
from ndf.adapters.github_rules import PULL_REQUEST_RULE

# マージの方式 → 「基底に含まれたか」の確かめ方（3378-2）。ancestry は head が基底の祖先か、merged は PR がマージ済み
# （`merged_at` がある）か。squash と rebase は新しいコミットを作り、head は基底の祖先にならない。
# 並びは、設定が無いときに許された方式から選ぶ順
ANCESTRY, MERGED = "ancestry", "merged"


class _MergeMethod(NamedTuple):
    """マージの方式 1 つ。`landing` は基底に含まれたかの確かめ方、`setting` はリポジトリの設定の許可のキー
    （ruleset に許す方式の指定が無いときに読む）、`graphql` は GraphQL の mergePullRequest の方式（PullRequestMergeMethod。#3506）。"""

    landing: str
    setting: str
    graphql: str


# マージの方式の唯一の定義（並びは設定が無いときに選ぶ順）。キーは REST の `merge_method` の名前で、GraphQL の方式の名前の
# 小文字（マージキューの方式を読む `queue_merge_method` と同じ対応）。`MERGE_METHODS` は方式 → 確かめ方
_MERGE_METHOD_SPECS = {
    spec.graphql.lower(): spec
    for spec in (
        _MergeMethod(MERGED, "allow_squash_merge", "SQUASH"),
        _MergeMethod(ANCESTRY, "allow_merge_commit", "MERGE"),
        _MergeMethod(MERGED, "allow_rebase_merge", "REBASE"),
    )
}
# merge commit の方式の名前（祖先の関係を保つただ 1 つの方式。呼び出し側が方式を固定するときに参照する）
MERGE_COMMIT = next(name for name, spec in _MERGE_METHOD_SPECS.items() if spec.landing == ANCESTRY)
MERGE_METHODS = {name: spec.landing for name, spec in _MERGE_METHOD_SPECS.items()}
# GraphQL の mergePullRequest のマージの方式（REST の merge_method → GraphQL の PullRequestMergeMethod。#3506）
GRAPHQL_MERGE_METHODS = {name: spec.graphql for name, spec in _MERGE_METHOD_SPECS.items()}


# マージの要求を GitHub が断ったときの HTTP の状態（405: 規則・衝突、409: HEAD が変わった、422: 形）
MERGE_REFUSED_STATUS = (405, 409, 422)
# PR の node の ID と、迂回のマージ（`gh pr merge --admin` が呼ぶのと同じ mergePullRequest を、HEAD を添えて打つ。#3506 の決定 5）
PULL_ID_QUERY = "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) { pullRequest(number: $number) { id } } }"
ADMIN_MERGE_MUTATION = (
    "mutation($id: ID!, $head: GitObjectID!, $method: PullRequestMergeMethod!) {"
    " mergePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head, mergeMethod: $method})"
    " { pullRequest { merged mergeCommit { oid } } } }"
)
# マージの方式が基底で許されていない断りの停止理由の種類（方式を渡した呼び手が、ほかの断りと分けて方式を決め直す）
METHOD_REFUSED = "merge_method"
# マージの直前の確かめで、判定した対象からマージ先か HEAD が動いたときの種類（`expect` を渡した呼び手だけが受ける。#4409 の r1-6）。
# 呼び手（`ndf ci merge`）は承認の対象を持つなら `approval_stale`（承認の取り直し）に、持たなければ `refused` に写す
TARGET_MOVED = "target_moved"
# マージキューが必須の基底で直接のマージを断る状態（GitHub は 405 で返す）。このときはキューへ入れ、待ちとして返す
MERGE_QUEUE_REFUSED_STATUS = 405
# マージキューへ入れた後の停止理由の種類（実際のマージまでは CI の待ちとして扱う）
MERGE_QUEUE_PENDING = "ci_pending"
MERGE_QUEUE_STATE_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!, $branch: String!) { repository(owner: $owner, name: $name) {"
    " pullRequest(number: $number) { id isMergeQueueEnabled isInMergeQueue }"
    " mergeQueue(branch: $branch) { configuration { mergeMethod } } } }"
)
# 確かめの項目（PR のマージ先・HEAD・ベースリポジトリと、マージ先の設定の blob の oid と本文。#4409 の決定 8）
_VERIFY_FIELDS = "nameWithOwner pullRequest(number: $number) {{ baseRefName headRefOid{extra} }} config: object(expression: $expr) {{ ... on Blob {{ oid text }} }}"
VERIFY_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!, $expr: String!) { repository(owner: $owner, name: $name) { "
    + _VERIFY_FIELDS.format(extra=" id isInMergeQueue")
    + " } }"
)
# キューの状態の問い合わせに確かめの項目を足したもの（要求を増やさずに照らす）
MERGE_QUEUE_VERIFY_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!, $branch: String!, $expr: String!) { repository(owner: $owner, name: $name) { "
    + _VERIFY_FIELDS.format(extra=" id isMergeQueueEnabled isInMergeQueue")
    + " mergeQueue(branch: $branch) { configuration { mergeMethod } } } }"
)
IN_MERGE_QUEUE_QUERY = (
    "query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {"
    " pullRequest(number: $number) { id isInMergeQueue } } }"
)
# `jump` は版上げの PR をキューの先頭へ入れる指定（#3672 の決定 18。承認の範囲を版上げの PR の head に固定する）
ENQUEUE_MUTATION = (
    "mutation($id: ID!, $head: GitObjectID!, $jump: Boolean) {"
    " enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head, jump: $jump}) { clientMutationId } }"
)
DEQUEUE_MUTATION = "mutation($id: ID!) { dequeuePullRequest(input: {id: $id}) { clientMutationId } }"
# PR 本文の「マージの前に決める」残リスクのマーカーと、決めたことを表すチェック済みの項目（#2602）
PREMERGE_MARK = "マージの前に決める"
_CHECKED_ITEM = re.compile(r"^\s*[-*+]\s+\[[xX]\]\s")


class PullFacts(Protocol):
    """要求を送らずに返す結果が読む PR の欄（`ndf.adapters.github.Pull` が満たす。import の輪を作らないための形）。"""

    @property
    def number(self) -> int: ...
    @property
    def merged(self) -> bool: ...
    @property
    def head_sha(self) -> str: ...
    @property
    def base_ref(self) -> str: ...
    @property
    def body(self) -> str: ...
    @property
    def merge_sha(self) -> str: ...


class MergeOutcome(NamedTuple):
    """マージの結果。`kind` は断りの停止理由の種類（`refused`・`METHOD_REFUSED`・`base_conflict`・`not_ci`・`unknown`）か待ち（`MERGE_QUEUE_PENDING`）。

    `merge_sha` はマージで本流へ入ったコミット（GitHub がマージの応答か PR の欄で返したときだけ。ほかは空）。
    """

    merged: bool
    kind: Optional[str]
    detail: str
    head_sha: str
    merge_sha: str = ""


class FileAt(NamedTuple):
    """ブランチのファイルの読み取り（`GitHub.branch_file`）。`repo` は応答のリポジトリ名、無いファイルは `text`・`oid` が None。"""

    repo: str
    text: Optional[str]
    oid: Optional[str]


class Verified(NamedTuple):
    """マージとmerge queue への追加の直前の確かめの応答（PR のマージ先・HEAD・ベースリポジトリと、マージ先の設定）。"""

    base: str
    head: str
    repo: str
    config: FileAt


def premerge_risks(body: str) -> list[str]:
    """PR 本文の「マージの前に決める」残リスクの行のうち、決めたマーカー（チェック済みの項目 `- [x]`）の無いもの（#2602）。"""
    return [line.strip() for line in body.splitlines() if PREMERGE_MARK in line and not _CHECKED_ITEM.match(line)]


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


def queue_merge_method(queue: Any) -> Optional[str]:
    """`mergeQueue { configuration { mergeMethod } }` のマージの方式（`MERGE_METHODS` の名前。読めなければ None）。"""
    config = queue.get("configuration") if isinstance(queue, dict) else None
    got = config.get("mergeMethod") if isinstance(config, dict) else None
    return got.lower() if isinstance(got, str) else None


def preflight(pull: PullFacts, number: int, expect_head: Optional[str]) -> Optional[MergeOutcome]:
    """マージの要求を送らずに返す結果（マージ済み・HEAD の食い違い・決めていない残リスク）。送ってよければ None。"""
    if pull.merged:
        return MergeOutcome(True, None, f"#{number} は既にマージ済み", pull.head_sha, pull.merge_sha)
    if expect_head and not git.sha_matches(expect_head, pull.head_sha):
        return MergeOutcome(False, "refused", f"#{number} の HEAD が {expect_head} でない（今は {pull.head_sha}）", pull.head_sha)
    risks = premerge_risks(pull.body)
    if risks:
        return MergeOutcome(False, "refused", f"#{number} の本文にマージの前に決める残リスクがある: {' / '.join(risks)}", pull.head_sha)
    return None


def method_refusal(pull: PullFacts, number: int, method: Optional[str], allowed: Sequence[str]) -> MergeOutcome:
    """マージの方式が基底のブランチで許されていないときの、要求を送らずに返す断り（3378-2）。"""
    want = method if method is not None else "（設定なし）"
    detail = f"#{number} のマージの方式 {want} は {pull.base_ref} で許されていない（許す方式: {', '.join(allowed)}）"
    return MergeOutcome(False, METHOD_REFUSED, detail, pull.head_sha)


def queue_method_refusal(pull: PullFacts, method: str, queue_method: Optional[str]) -> Optional[MergeOutcome]:
    """キューのマージの方式 `queue_method` が求めた方式 `method` と違う（読めないときも）ときの、キューへ入れずに返す断り。"""
    if queue_method == method:
        return None
    detail = (
        f"#{pull.number} の基底 {pull.base_ref} のマージキューは {queue_method or '（読めない）'} でマージし、"
        f"求めた {method} と違う。キューへ入れずに止める（キューのマージの方式を {method} にする）"
    )
    return MergeOutcome(False, "refused", detail, pull.head_sha)
