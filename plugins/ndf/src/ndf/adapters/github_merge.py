"""マージの部品（`ndf.adapters.github` から分けた。通信をしない部品）。

- 本番ブランチの ruleset と従来のブランチ保護を重ねた事実（`RuleFacts`）を、読んだ応答から組む
- マージの方式の唯一の定義（`_MERGE_METHOD_SPECS`）と、規則・リポジトリの設定・マージキューが許す方式の選び方
- マージの要求・確かめ・マージキューの GraphQL の形と、マージの結果の値（`MergeOutcome`・`FileAt`・`Verified`）

GitHub への読み書きは `ndf.adapters.github.GitHub` が持ち、ここは送る形と、応答の値から事実と方式を決める規則だけを持つ。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple, Optional

# 迂回の可否（ruleset の `current_user_can_bypass`）の強さ。弱いほうを全体の値にする。`always`・`pull_requests_only` だけが
# 本番の PR を越えられる（10 系の `release_case._BYPASS_ORDER` と同じ並び）
BYPASS_ORDER = ("never", "exempt", "pull_requests_only", "always")
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
    " mergePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head, mergeMethod: $method}) { pullRequest { merged } } }"
)
# マージの方式が基底で許されていない断りの停止理由の種類（方式を渡した呼び手が、ほかの断りと分けて方式を決め直す）
METHOD_REFUSED = "merge_method"
# 直前の照合で、判定した対象からマージ先か HEAD が動いたときの種類（`expect` を渡した呼び手だけが受ける。#4409 の r1-6）。
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
    + _VERIFY_FIELDS.format(extra="")
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


class MergeOutcome(NamedTuple):
    """マージの結果。`kind` は断りの停止理由の種類（`refused`・`METHOD_REFUSED`・`base_conflict`・`not_ci`・`unknown`）か待ち（`MERGE_QUEUE_PENDING`）。"""

    merged: bool
    kind: Optional[str]
    detail: str
    head_sha: str


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


class RuleFacts(NamedTuple):
    """本番ブランチの ruleset と従来のブランチ保護を重ねた事実（#3672 の I10。10 系の `release_case.RuleFacts` と同じ値）。

    `bypass` は当たった ruleset の `current_user_can_bypass`（どれか 1 つでも越えられない値なら、その値。ruleset が無ければ None）。
    `queue_merge_method` は merge queue の規則の `parameters.merge_method`（`MERGE`・`SQUASH`・`REBASE`）。
    """

    present: bool
    approvals: int
    queue: bool
    up_to_date: bool
    bypass: Optional[str]
    queue_merge_method: Optional[str] = None


NO_RULES = RuleFacts(False, 0, False, False, None)


def _weakest(bypasses: Iterable[Optional[str]]) -> Optional[str]:
    known = [b for b in bypasses if b in BYPASS_ORDER]
    return min(known, key=BYPASS_ORDER.index) if known else None


def _review_count(raw: Any) -> int:
    value = raw.get("required_approving_review_count") if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def rule_facts_of(rules: Sequence[dict], bypasses: Sequence[Optional[str]]) -> RuleFacts:
    """`rules/branches/<branch>` の規則の配列と、当たった ruleset ごとの迂回の可否から事実を組む。"""
    if not rules:
        return NO_RULES
    params = [(r.get("type"), r.get("parameters") if isinstance(r.get("parameters"), dict) else {}) for r in rules]
    reviews = [_review_count(p) for kind, p in params if kind == "pull_request"]
    up_to_date = any(kind == "required_status_checks" and p.get("strict_required_status_checks_policy") for kind, p in params)
    queue_method = next((p.get("merge_method") for kind, p in params if kind == "merge_queue"), None)
    queue = any(kind == "merge_queue" for kind, _ in params)
    return RuleFacts(True, max(reviews) if reviews else 0, queue, bool(up_to_date), _weakest(bypasses), queue_method)


def classic_facts_of(protection: dict) -> RuleFacts:
    """従来のブランチ保護（`branches/<branch>/protection`）から事実を組む。保護を読めるのは管理者だけなので、
    管理者に保護を課さない（`enforce_admins` が偽）ときは迂回できる（`always`）とみなす。"""
    checks = protection.get("required_status_checks")
    enforce = protection.get("enforce_admins")
    up_to_date = isinstance(checks, dict) and bool(checks.get("strict"))
    enforced = isinstance(enforce, dict) and bool(enforce.get("enabled"))
    approvals = _review_count(protection.get("required_pull_request_reviews"))
    return RuleFacts(True, approvals, False, up_to_date, "never" if enforced else "always")


def merge_facts(a: RuleFacts, b: RuleFacts) -> RuleFacts:
    """ruleset と従来のブランチ保護の事実を重ねる。GitHub は両方を効かせるため、厳しいほうを採る（迂回は弱いほう）。"""
    if not a.present or not b.present:
        return a if a.present else b
    bypass, queue_method = _weakest((a.bypass, b.bypass)), a.queue_merge_method or b.queue_merge_method
    return RuleFacts(True, max(a.approvals, b.approvals), a.queue or b.queue, a.up_to_date or b.up_to_date, bypass, queue_method)


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
        if rule["type"] != "pull_request" or "allowed_merge_methods" not in rule["parameters"]:
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
