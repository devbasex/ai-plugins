"""本番のマージの方式（`.ndf/project.json` の `production_merge.method`）を、本番ブランチの ruleset と照らす（#3572 の決定 17・I15）。

同時に承認された PR の組み合わせを確かめてから本番ブランチへ入れるため、方式を宣言で選ぶ。純粋な判定で、GitHub を読まない
（読むのは `land` の `_method_of`）。

| 方式 | 本番ブランチの ruleset に要るもの |
| --- | --- |
| `merge-queue` | merge queue（キューのマージの方式は merge commit） |
| `strict` | 必須のチェックの最新の取り込み（`strict_required_status_checks_policy`）が真で、merge queue が無い |

取り込み方の行が `github-flow` なら方式の宣言を必須にし、無い・食い違う、のどれでも止める（既定の方式へ倒さない）。
ほかの行では、宣言が無ければ今のマージ（方式なし）を使う。方式を使うとき、本番ブランチの PR の規則がレビューの承認を求め、
PR に承認が無ければ止める（NDF の承認ゲートの承認は GitHub のレビューの承認ではない）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ndf.adapters.github import MERGE_COMMIT
from ndf.domains.release.required_checks import REQUIRED_RULE

# 方式の値（宣言のスキーマの列挙と同じ値）
QUEUE = "merge-queue"
STRICT = "strict"
METHODS = (QUEUE, STRICT)
# 規則の種類と、照らす引数の名前（必須のチェックの規則の種類は `required_checks.REQUIRED_RULE`）
REVIEW_RULE = "pull_request"
REVIEW_COUNT = "required_approving_review_count"
STRICT_POLICY = "strict_required_status_checks_policy"
# PR のレビューの判定で承認を表す値
APPROVED = "APPROVED"


class RulesFacts(NamedTuple):
    """照らし合わせに使う本番ブランチの事実。`strict` は必須のチェックの規則が無ければ None（読めない）。"""

    queue_enabled: bool
    queue_method: str | None
    strict: bool | None
    review_count: int


class Method(NamedTuple):
    """方式の決め。`problem` が None のときだけ `value`（`QUEUE`・`STRICT`・None）を使う。"""

    value: str | None
    problem: str | None
    declared: str | None
    actual: str


def review_count(raw: Mapping[str, Any]) -> int:
    """規則か従来の保護の承認数（`required_approving_review_count`）。数でなければ 0。"""
    value = raw.get(REVIEW_COUNT)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _params(rule: Mapping[str, Any]) -> Mapping[str, Any]:
    got = rule.get("parameters")
    return got if isinstance(got, dict) else {}


def rules_of(rules: Sequence[Mapping[str, Any]], queue: Any) -> RulesFacts:
    """本番ブランチに効く規則の一覧（`GET /rules/branches/{branch}`）と merge queue の設定（無ければ None）から事実を取り出す。

    `queue` は `merge_method` の欄を持つ値（`github.QueueConfig`）。
    """
    checks = [_params(r) for r in rules if r.get("type") == REQUIRED_RULE]
    strict = any(p.get(STRICT_POLICY) is True for p in checks) if checks else None
    counts = [review_count(_params(r)) for r in rules if r.get("type") == REVIEW_RULE]
    method = getattr(queue, "merge_method", None) if queue is not None else None
    return RulesFacts(queue is not None, method, strict, max(counts) if counts else 0)


def _actual(facts: RulesFacts) -> str:
    """ruleset の値を人の読める 1 行にする。"""
    queue = f"merge queue あり（{facts.queue_method or '方式を読めない'}）" if facts.queue_enabled else "merge queue なし"
    strict = "読めない" if facts.strict is None else str(facts.strict).lower()
    return f"{queue}・{STRICT_POLICY}={strict}・{REVIEW_COUNT}={facts.review_count}"


def _mismatch(declared: str, facts: RulesFacts) -> str | None:
    """宣言の方式と ruleset の食い違い（無ければ None）。"""
    if declared == QUEUE and not facts.queue_enabled:
        return "ruleset に merge queue が無い"
    if declared == QUEUE and facts.queue_method != MERGE_COMMIT:
        return "キューのマージの方式が merge commit でない"
    if declared == STRICT and facts.queue_enabled:
        return "ruleset に merge queue があり、strict と食い違う"
    if declared == STRICT and facts.strict is not True:
        return "必須のチェックの最新の取り込みが偽か読めない"
    return None


def decide(flow: str | None, declared: str | None, facts: RulesFacts, review: str | None, required_flow: str) -> Method:
    """取り込み方の行・宣言の方式・ruleset の事実・PR のレビューの判定から、使う方式か止まる理由を決める（I15）。

    `flow` は取り込み方の行の id（None は既定の行）、`required_flow` は方式の宣言を必須にする行（`github-flow`）。
    """
    actual = _actual(facts)
    if declared is None:
        problem = f"取り込み方 {flow} ではマージの方式（production_merge.method）が要る" if flow == required_flow else None
        return Method(None, problem, None, actual)
    found = _mismatch(declared, facts)
    if found is None and facts.review_count >= 1 and review != APPROVED:
        found = "本番ブランチの規則がレビューの承認を求め、PR に承認が無い"
    return Method(None if found else declared, found, declared, actual)
