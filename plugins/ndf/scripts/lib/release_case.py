"""リリースの場合の判定（#2773）。CD の有無とレビュー必須の有無から、リリース承認を取る場所とマージの主体を決める。

| 場合 | 条件 | 承認を取る場所 | マージの主体 |
| --- | --- | --- | --- |
| `cd-no-review` | CD の行があり、本番チャネルの承認数が 0（読めないときもここ） | リリースの PR の証跡のコメント | 利用者のアカウント |
| `cd-review` | CD の行があり、承認数が 1 以上 | PR のレビューの承認 | 利用者のアカウント |
| `no-cd` | 本番チャネルへのマージで届く `kind: auto` の行が無い | 手元の承認資料 | NDF（今の工程） |

CD の行は `lib/delivery.py` の `merge_rows`。承認数は本番チャネルの ruleset（`rules/branches/<branch>`）の
`pull_request` 規則から読み、規則が無ければ従来のブランチ保護を読む。読めなければ `cd-no-review` へ倒す:
その経路は管理者の迂回を使わないため、レビューが本当は必須なら GitHub がマージを拒んで止まるだけである。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import delivery
import gh_call
from step_result import EXIT_PRECONDITION, StepError

CD_NO_REVIEW = "cd-no-review"
CD_REVIEW = "cd-review"
NO_CD = "no-cd"


class Case(NamedTuple):
    case: str
    approval_place: str
    merger: str
    reason: str

    @property
    def has_cd(self) -> bool:
        """CD のある場合（cd-no-review・cd-review）か。"""
        return self.case != NO_CD


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


def decide_case(has_cd: bool, approvals: int | None, why: str) -> Case:
    if not has_cd:
        return Case(NO_CD, "手元の承認資料", "NDF", why or "本番チャネルへのマージで届く CD の行が無い")
    if approvals is not None and approvals >= 1:
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
    """本番チャネルの承認数。規則が無ければ従来のブランチ保護を読む。読めなければ (None, 理由)。"""
    rules, why = _gh_read(root, f"repos/{{owner}}/{{repo}}/rules/branches/{branch}")
    if isinstance(rules, list) and (n := required_approvals(rules)) is not None:
        return n, ""
    path = f"repos/{{owner}}/{{repo}}/branches/{branch}/protection/required_pull_request_reviews"
    got, why2 = _gh_read(root, path)
    if isinstance(got, dict):
        return int(got.get("required_approving_review_count") or 0), ""
    if why2 == "missing" and isinstance(rules, list):
        return 0, ""  # 規則を読め、ブランチ保護が無い
    return None, f"本番チャネル {branch} の承認数を読めない（ruleset: {why or '規則が無い'}・ブランチ保護: {why2}）"


def case_of(root) -> Case:
    """宣言と GitHub の設定から場合を決める。CD の行が無ければネットワークを使わない。"""
    decl = delivery.load_delivery(root)
    if decl.problems:
        raise CaseUnreadable("; ".join(decl.problems))
    if not delivery.merge_rows(decl):
        return decide_case(False, None, "")
    approvals, why = read_approvals(root, decl.production)
    return decide_case(True, approvals, why)


def case_at(root, strict: bool = False) -> Case:
    """プランを組むときのリリースの場合。リポジトリ（`root`）が無ければ CD の無い場合（今のプランのまま）。
    宣言を読めなければ、`strict` なら CaseUnreadable、そうでなければ CD の無い場合として返す。"""
    if not root or not Path(root).is_dir():
        return decide_case(False, None, "リポジトリが無い")
    try:
        return case_of(root)
    except CaseUnreadable:
        if strict:
            raise
        return decide_case(False, None, "宣言を読めない")
