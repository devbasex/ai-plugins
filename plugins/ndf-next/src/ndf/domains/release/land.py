"""承認の証跡の後に、リリースの PR をマージする（GitHub Actions の workflow が打つ）。

1. 本番ブランチ宛ての PR は、証跡を読み直して今の先端が承認されていることを確かめる（commit status も付け直す）
2. 宛先のブランチの規則（ruleset）と従来の保護の必須のチェック（check run と commit status）が PR の先端で通るまで待つ。落ちたら打ち直さずに止まった理由で
   返す（同じ CI の失敗を読み直し続けない。#1983）
3. 先端の SHA を添えてマージする（承認の後に先端が動いていれば GitHub が断る）
4. 本番ブランチへ入れたら、本番ブランチからベースブランチ宛ての PR を作り（開いたものがあれば使い直す）、必須のチェックの
   後にマージする（版上げのコミットをベースブランチへ戻す）。マージ済みの PR で打ち直しても、ここから再開する
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import NamedTuple

from ndf.adapters import ci
from ndf.adapters.github import GitHub, GitHubError, MergeOutcome, Pull, text_of
from ndf.contracts import facts
from ndf.domains.release import approval_decide, trail, version_decide

# 規則の種類（必須のチェック）
REQUIRED_RULE = "required_status_checks"
# GitHub が PR の作成を断る状態と、そのうち差分が無いことを表す文言（ベースブランチへ戻す差分が無い）
UNPROCESSABLE = HTTPStatus.UNPROCESSABLE_ENTITY
NOTHING_TO_SYNC_TEXT = "No commits between"
# 従来の保護（branch protection）が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 結論の出ていない状態（事実の表 ci-outcomes の pending。commit status の `pending` を含む）
PENDING_STATES = frozenset(facts.ids("ci-outcomes", outcome="pending"))


class Landed(NamedTuple):
    """マージの結果。止まったら `kind` に止まった理由の種類。"""

    merged: bool
    kind: str | None
    detail: str
    sync_pr: int | None = None
    # 本番ブランチへ入ったコミット（タグと GitHub Release を作る版。本番ブランチへ入れていなければ None）
    production_sha: str | None = None


def _status_run(status: dict) -> dict:
    """commit status 1 件を check run の形にする（`ci.read` に渡す）。"""
    state = text_of(status["state"]).lower()
    conclusion = "" if state in PENDING_STATES else state
    return {
        "name": text_of(status["context"]),
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "completed_at": text_of(status["updated_at"]) if conclusion else "",
        "started_at": text_of(status["created_at"]),
        "id": int(status["id"]),
    }


def _list(raw: dict, key: str) -> list:
    """`raw[key]` が配列ならそれ、ほかは空の配列。"""
    got = raw.get(key)
    return got if isinstance(got, list) else []


def _protection_checks(branch: dict) -> list[str]:
    """`GET /branches/<名前>` の従来の保護の必須のチェックの名前。"""
    protection = branch.get("protection")
    checks = protection.get("required_status_checks") if isinstance(protection, dict) else None
    if not isinstance(checks, dict):
        return []
    contexts = [text_of(c) for c in _list(checks, "contexts")]
    return contexts + [text_of(c["context"]) for c in _list(checks, "checks") if isinstance(c, dict)]


@dataclass(frozen=True)
class Lander:
    """1 つのリポジトリのリリースの PR をマージする。`wait` は必須のチェックの待ちを組む関数（テストが差し替える）。"""

    hub: GitHub
    base: str
    production: str
    budget: float
    wait: Callable[[Callable[[], ci.Reading], float], ci.WatchResult] | None = None

    def required(self, branch: str) -> list[str]:
        """宛先のブランチの規則（ruleset）と従来の保護の必須のチェックの名前（承認の commit status を除く）。"""
        rules = self.hub.rest("GET", f"/repos/{self.hub.repo}/rules/branches/{branch}")
        names = [
            text_of(check["context"])
            for rule in (rules if isinstance(rules, list) else [])
            if isinstance(rule, dict) and rule.get("type") == REQUIRED_RULE
            for check in rule["parameters"]["required_status_checks"]
        ]
        try:
            got = self.hub.rest("GET", f"/repos/{self.hub.repo}/branches/{branch}")
        except GitHubError as exc:
            if exc.status != NOT_FOUND:
                raise
            got = {}
        names += _protection_checks(got if isinstance(got, dict) else {})
        return sorted({n for n in names if n != approval_decide.STATUS_CONTEXT})

    def _runs(self, sha: str) -> list[dict]:
        """コミットの check run と commit status（check run の形にしたもの）の一覧。"""
        combined = self.hub.rest("GET", f"/repos/{self.hub.repo}/commits/{sha}/status", params={"per_page": 100})
        statuses = _list(combined, "statuses") if isinstance(combined, dict) else []
        return self.hub.check_runs(sha) + [_status_run(s) for s in statuses if isinstance(s, dict)]

    def _watch(self, pull: Pull) -> str | None:
        """必須のチェック（check run と commit status）を待ち、止まった理由の種類（通れば None）。"""
        required = self.required(pull.base_ref)

        def reader() -> ci.Reading:
            return ci.read(self._runs(pull.head_sha), required, [])

        watched = self.wait(reader, self.budget) if self.wait is not None else ci.Watch(reader, self.budget).run()
        return watched.cause([])

    def _merge(self, pull: Pull) -> Landed:
        kind = self._watch(pull)
        if kind is not None:
            return Landed(False, kind, f"#{pull.number} の必須のチェックが通らない（{kind}。打ち直さずに人へ戻す）")
        outcome: MergeOutcome = self.hub.merge(pull.number, expect_head=pull.head_sha)
        return Landed(outcome.merged, outcome.kind, outcome.detail)

    def _open_sync(self) -> int | None:
        """本番ブランチからベースブランチへ戻す PR の番号（開いたものがあれば使い直し、無ければ作る。差分が無ければ None）。

        head は本番ブランチにする（リリースのブランチはマージの後に自動で消えることがある）。
        """
        owner = self.hub.repo.partition("/")[0]
        found = self.hub.rest(
            "GET",
            f"/repos/{self.hub.repo}/pulls",
            params={"state": "open", "head": f"{owner}:{self.production}", "base": self.base},
        )
        if isinstance(found, list) and found:
            return int(found[0]["number"])
        try:
            made = self.hub.rest(
                "POST",
                f"/repos/{self.hub.repo}/pulls",
                {"title": f"Sync: {self.production} を {self.base} へ戻す", "head": self.production, "base": self.base, "body": ""},
            )
        except GitHubError as exc:
            if exc.status != UNPROCESSABLE or NOTHING_TO_SYNC_TEXT not in str(exc):
                raise
            return None
        return int(made["number"])

    def _sync(self, pull: Pull) -> Landed:
        """本番ブランチへ入れた版上げを、ベースブランチへ戻す PR でマージする（マージ済みからの再開でも打つ）。"""
        merged_sha = self.hub.pull(pull.number).merge_sha
        landed = merged_sha if merged_sha else None
        number = self._open_sync()
        if number is None:
            return Landed(True, None, f"#{pull.number} をマージした（{self.base} へ戻す差分は無い）", production_sha=landed)
        synced = self._merge(self.hub.pull(number))
        detail = f"#{pull.number} をマージした。戻す PR: {synced.detail}"
        return synced._replace(sync_pr=number, detail=detail, production_sha=landed)

    def land(self, number: int) -> Landed:
        """リリースの PR をマージする。本番ブランチへ入れた後（マージ済みからの再開を含む）は、ベースブランチへ戻す。"""
        pull = self.hub.pull(number)
        if pull.merged:
            if pull.base_ref == self.production:
                return self._sync(pull)
            return Landed(True, None, f"#{number} は既にマージされている")
        version_decide.version_of_branch(pull.head_ref)
        if pull.base_ref == self.production:
            got = trail.check(self.hub, number)
            if not got.judgment.approved:
                return Landed(False, "refused", f"#{number} は承認されていない: {got.judgment.detail}")
        done = self._merge(pull)
        if not done.merged or pull.base_ref != self.production:
            return done
        return self._sync(pull)
