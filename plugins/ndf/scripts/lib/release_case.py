"""リリースの場合の判定（#2773・#3506）。CD の有無・レビュー必須の有無・迂回の宣言から、リリース承認を取る場所とマージの主体を決める。

| 場合 | 条件 | 承認を取る場所 | マージの主体 |
| --- | --- | --- | --- |
| `cd-no-review` | CD の行があり、本番チャネルの承認数が 0（読めないときもここ。迂回の宣言は問わない） | リリースの PR の証跡のコメント | 利用者のアカウント |
| `cd-review` | CD の行があり、承認数が 1 以上、迂回の宣言が無い | PR のレビューの承認 | 利用者のアカウント |
| `cd-admin` | CD の行があり、承認数が 1 以上、迂回の宣言（ブランチ戦略の宣言の `merge.admin: true`）がある | リリースの PR の証跡のコメント（利用者が書いたものだけ） | 利用者のアカウント（管理者の迂回） |
| `no-cd` | 本番チャネルへのマージで届く `kind: auto` の行が無い | 手元の承認資料 | NDF（今の工程） |

CD の行は `lib/delivery.py` の `merge_rows`。承認数は本番チャネルの ruleset（`rules/branches/<branch>`）の
`pull_request` 規則と従来のブランチ保護を重ねて読む（多いほう）。読めなければ `cd-no-review` へ倒す:
その経路は管理者の迂回を使わないため、レビューが本当は必須なら GitHub がマージを拒んで止まるだけである。

分岐は値どうしを比べず、場合の属性（`trail_approval`・`admin_merge`・`human_approval`）で書く。属性の正本は下の 3 つの集合である。
"""

from __future__ import annotations

import json
from typing import NamedTuple

import check_sources
import delivery
import gh_call
import repo
from step_result import EXIT_PRECONDITION, StepError

CD_NO_REVIEW = "cd-no-review"
CD_REVIEW = "cd-review"
CD_ADMIN = "cd-admin"
NO_CD = "no-cd"

TRAIL_APPROVAL = frozenset({CD_NO_REVIEW, CD_ADMIN})  # 承認を PR の上の証跡で取る
ADMIN_MERGE = frozenset({CD_ADMIN})  # 本番の PR を管理者の迂回でマージする
HUMAN_APPROVAL = frozenset({CD_REVIEW, CD_ADMIN})  # MVV 判定で承認ゲートを代えない（ruleset が人の承認を求める）
USER_TRAIL = TRAIL_APPROVAL & HUMAN_APPROVAL  # 利用者が書いた証跡だけで照らす（cd-admin）
MVV_TRAIL = TRAIL_APPROVAL - HUMAN_APPROVAL  # MVV 判定が証跡を書ける（cd-no-review）


class Case(NamedTuple):
    case: str
    approval_place: str
    merger: str
    reason: str

    @property
    def has_cd(self) -> bool:
        """CD のある場合（cd-no-review・cd-review・cd-admin）か。"""
        return self.case != NO_CD

    @property
    def trail_approval(self) -> bool:
        """承認を PR の上の証跡で取る場合か。"""
        return self.case in TRAIL_APPROVAL

    @property
    def admin_merge(self) -> bool:
        """本番の PR を管理者の迂回でマージする場合か。"""
        return self.case in ADMIN_MERGE

    @property
    def human_approval(self) -> bool:
        """MVV 判定で承認ゲートを代えず、利用者の承認が要る場合か。"""
        return self.case in HUMAN_APPROVAL

    @property
    def user_trail_only(self) -> bool:
        """利用者が書いた証跡だけで承認を照らす場合か。"""
        return self.case in USER_TRAIL

    @property
    def mvv_may_trail(self) -> bool:
        """MVV 判定が承認の証跡を書ける場合か。"""
        return self.case in MVV_TRAIL


class CaseUnreadable(Exception):
    """宣言（`.ndf/project.json` など）を読めず、場合を決められない。"""


def required_approvals(rules: list[dict]) -> int | None:
    """規則の配列の `pull_request` 規則の承認数（複数あれば最大）。規則が無ければ None。"""
    found = [
        int((r.get("parameters") or {}).get("required_approving_review_count") or 0)
        for r in rules or []
        if isinstance(r, dict) and r.get("type") == "pull_request"
    ]
    return max(found) if found else None


def decide_case(has_cd: bool, approvals: int | None, why: str, admin: bool = False) -> Case:
    """承認数が 0 か読めないときは、迂回の宣言があっても迂回しない（#3506 の I2a）。"""
    if not has_cd:
        return Case(NO_CD, "手元の承認資料", "NDF", why or "本番チャネルへのマージで届く CD の行が無い")
    reviewed = approvals is not None and approvals >= 1
    if reviewed and admin:
        return Case(
            CD_ADMIN,
            "リリースの PR の証跡のコメント",
            "利用者のアカウント（管理者の迂回）",
            why or f"本番チャネルの承認数が {approvals} で、迂回の宣言がある",
        )
    if reviewed:
        return Case(CD_REVIEW, "PR のレビューの承認", "利用者のアカウント", why or f"本番チャネルの承認数が {approvals}")
    return Case(CD_NO_REVIEW, "リリースの PR の証跡のコメント", "利用者のアカウント", why or "本番チャネルの承認数が 0")


def case_or_stop(root) -> Case:
    """`case_of` の場合。宣言を読めなければ StepError（前提が無い。終了コード 3）で止める。"""
    try:
        return case_of(root)
    except CaseUnreadable as e:
        raise StepError(f"リリースの場合を決められない: {e}", EXIT_PRECONDITION) from e


def _gh_read(root, path: str):
    r = gh_call.gh(["api", path], cwd=str(root))
    if r.returncode != 0:
        missing = "404" in (r.stderr + r.stdout) or "Not Found" in (r.stderr + r.stdout)
        return None, ("missing" if missing else (r.stderr or r.stdout).strip()[:200] or "gh api が失敗")
    try:
        return json.loads(r.stdout or "null"), ""
    except ValueError:
        return None, "応答を読めない"


def read_approvals(root, branch: str) -> tuple[int | None, str]:
    """本番チャネルの承認数。ruleset と従来のブランチ保護を重ねた事実（`read_rule_facts`）から引く。読めなければ (None, 理由)。"""
    facts = read_rule_facts(root, branch, bypass=False)  # 迂回の可否は承認数に効かないため読まない
    if facts is None:
        return None, f"本番チャネル {branch} の承認数を読めない（ruleset か従来のブランチ保護を読めない）"
    return facts.approvals, ""


def case_of(root) -> Case:
    """宣言と GitHub の設定から場合を決める。CD の行が無ければネットワークを使わない。"""
    decl = delivery.load_delivery(root)
    if decl.problems:
        raise CaseUnreadable("; ".join(decl.problems))
    if not delivery.merge_rows(decl):
        return decide_case(False, None, "")
    approvals, why = read_approvals(root, decl.production)
    return decide_case(True, approvals, why, repo.branch_strategy_of(root).section["merge"]["admin"] is True)


def case_at(root, strict: bool = False) -> Case:
    """プランを組むときのリリースの場合。リポジトリ（`root`。git の木でないディレクトリを含む）が無ければ CD の無い場合
    （今のプランのまま）。宣言を読めなければ、`strict` なら CaseUnreadable、そうでなければ CD の無い場合として返す。"""
    if not root or repo.first_git_tree([root]) is None:
        return decide_case(False, None, "リポジトリが無い")
    try:
        return case_of(root)
    except CaseUnreadable:
        if strict:
            raise
        return decide_case(False, None, "宣言を読めない")


class RuleFacts(NamedTuple):
    """本番ブランチの ruleset の事実（#3672 の I10）。`bypass` は当たった ruleset の `current_user_can_bypass`
    （どれか 1 つでも越えられない値なら、その値。ruleset が無ければ None）。"""

    present: bool
    approvals: int
    queue: bool
    up_to_date: bool
    bypass: str | None
    queue_merge_method: str | None = None  # merge queue の規則の parameters.merge_method（MERGE / SQUASH / REBASE）
    # 必須のチェックの（名前, 発行元のアプリの ID か None）。管理者の迂回の前に発行元まで照らす（#3506）
    required: tuple[tuple[str, int | None], ...] = ()


# 迂回の可否の強さ（弱いほうを全体の値にする）。`always`・`pull_requests_only` だけが本番の PR を越えられる
_BYPASS_ORDER = ("never", "exempt", "pull_requests_only", "always")


def rule_facts_of(rules: list, bypasses: list[str | None]) -> RuleFacts:
    """`rules/branches/<branch>` の規則の配列と、当たった ruleset ごとの迂回の可否から事実を組む。"""
    if not rules:
        return RuleFacts(False, 0, False, False, None)
    kinds = {r.get("type") for r in rules if isinstance(r, dict)}
    up_to_date = any(
        isinstance(r, dict)
        and r.get("type") == "required_status_checks"
        and (r.get("parameters") or {}).get("strict_required_status_checks_policy")
        for r in rules
    )
    known = [b for b in bypasses if b in _BYPASS_ORDER]
    bypass = min(known, key=_BYPASS_ORDER.index) if known else None
    queue_method = next(
        ((r.get("parameters") or {}).get("merge_method") for r in rules if isinstance(r, dict) and r.get("type") == "merge_queue"), None
    )
    required = tuple(check_sources.rule_pairs(rules))
    return RuleFacts(True, required_approvals(rules) or 0, "merge_queue" in kinds, bool(up_to_date), bypass, queue_method, required)


def classic_facts_of(protection: dict) -> RuleFacts:
    """従来のブランチ保護（`branches/<branch>/protection`）から事実を組む。保護を読めるのは管理者だけなので、
    管理者に保護を課さない（`enforce_admins` が偽）ときは迂回できる（`always`）とみなす。"""
    reviews = protection.get("required_pull_request_reviews") or {}
    up_to_date = bool((protection.get("required_status_checks") or {}).get("strict"))
    enforced = bool((protection.get("enforce_admins") or {}).get("enabled"))
    return RuleFacts(True, int(reviews.get("required_approving_review_count") or 0), False, up_to_date, "never" if enforced else "always")


def merge_facts(a: RuleFacts, b: RuleFacts) -> RuleFacts:
    """ruleset と従来のブランチ保護の事実を重ねる。GitHub は両方を効かせるため、厳しいほうを採る（迂回は弱いほう）。"""
    if not a.present or not b.present:
        return a if a.present else b
    known = [x for x in (a.bypass, b.bypass) if x in _BYPASS_ORDER]
    return RuleFacts(
        True,
        max(a.approvals, b.approvals),
        a.queue or b.queue,
        a.up_to_date or b.up_to_date,
        min(known, key=_BYPASS_ORDER.index) if known else None,
        a.queue_merge_method or b.queue_merge_method,
        a.required + b.required,
    )


def classic_facts_from(read, branch: str) -> RuleFacts | None:
    """従来のブランチ保護の事実。保護が無ければ `present` が偽、読めなければ None。
    先に読む権限で読める `branches/<b>` の `protection.enabled`（ruleset を含まない、従来の保護だけの有無）を見て、
    偽なら保護の照会を省く。保護の照会は管理者でない利用者に 404 を返しうるため、保護がある（か有無を読めない）のに詳細を読めないときは None。"""
    info, _ = read(f"repos/{{owner}}/{{repo}}/branches/{branch}")
    enabled = (info.get("protection") or {}).get("enabled") if isinstance(info, dict) else None
    if enabled is False:
        return RuleFacts(False, 0, False, False, None)
    protection, _ = read(f"repos/{{owner}}/{{repo}}/branches/{branch}/protection")
    # 必須のチェックは発行元（`app_id`）まで載る `branches/<b>` の応答から引く（迂回の前に読み直さない）
    required = tuple(check_sources.protection_pairs(info))
    return classic_facts_of(protection)._replace(required=required) if isinstance(protection, dict) else None


# GitHub の REST の既定の 1 ページの件数（`per_page` を付けないときの値）
_DEFAULT_PER_PAGE = 30


def _all_rules(read, path: str) -> list | None:
    """`rules/branches/<b>` の規則を全ページ読む。1 ページ目は `path` のまま（既定の件数）読み、満ちたページが続く間
    `?page=<n>` で次を読む。1 ページ目が 404 なら規則なし（空）、途中のページを読めなければ None（必須のチェックを
    欠いたまま迂回へ進めない）。"""
    rules, why = read(path)
    if why == "missing":
        return []
    if not isinstance(rules, list):
        return None
    got, page = rules, 1
    while len(got) >= _DEFAULT_PER_PAGE:
        page += 1
        got, _ = read(f"{path}?page={page}")
        if not isinstance(got, list):
            return None
        rules = rules + got
    return rules


def rule_facts_from(read, branch: str, bypass: bool = True) -> RuleFacts | None:
    """本番ブランチの ruleset と従来のブランチ保護から、有無・承認の数・merge queue の有無・最新の取り込みの要否・迂回の可否を読む。
    GitHub は両方を重ねて効かせるため、両方を読んで重ねる（保護のある本番ブランチを「保護なし」の早送りの経路へ入れない）。読めなければ None。
    `read` は `path → (値, 読めない理由)`（`_gh_read` の形）。宣言を書く測定・宣言を照らす land・承認数（`read_approvals`）が同じ事実を読むための正本。
    `bypass` が偽なら ruleset ごとの迂回の可否（`rulesets/<id>`）を読まない（`bypass` は None。承認数だけが要る経路で照会を減らす）。"""
    rules = _all_rules(read, f"repos/{{owner}}/{{repo}}/rules/branches/{branch}")
    if rules is None:
        return None
    bypasses = []
    for rid in sorted({r.get("ruleset_id") for r in rules if isinstance(r, dict) and r.get("ruleset_id")}) if bypass else ():
        got, _ = read(f"repos/{{owner}}/{{repo}}/rulesets/{rid}")
        if not isinstance(got, dict):
            return None
        bypasses.append(got.get("current_user_can_bypass"))
    classic = classic_facts_from(read, branch)
    return None if classic is None else merge_facts(rule_facts_of(rules, bypasses), classic)


def read_rule_facts(root, branch: str, bypass: bool = True) -> RuleFacts | None:
    """`rule_facts_from` を gh で読む。"""
    return rule_facts_from(lambda path: _gh_read(root, path), branch, bypass)
