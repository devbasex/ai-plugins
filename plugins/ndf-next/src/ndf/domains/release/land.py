"""承認の証跡の後に、リリースの PR をマージする（GitHub Actions の workflow が打つ）。

1. 本番ブランチ宛ての PR は、証跡を読み直して今の先端が承認されていることを確かめる（commit status も付け直す）
2. 宛先のブランチの規則（ruleset）の必須のチェックが PR の先端で通るまで待つ。落ちたら打ち直さずに止まった理由で
   返す（同じ CI の失敗を読み直し続けない。#1983）
3. 先端の SHA を添えてマージする（承認の後に先端が動いていれば GitHub が断る）
4. 本番ブランチへ入れたら、同じリリースのブランチからベースブランチ宛ての PR を作り、必須のチェックの後にマージする
   （版上げのコミットをベースブランチへ戻す）
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from ndf.adapters import ci
from ndf.adapters.github import GitHub, GitHubError, MergeOutcome, Pull, text_of
from ndf.domains.release import approval_decide, trail, version_decide

# 規則の種類（必須のチェック）
REQUIRED_RULE = "required_status_checks"


class Landed(NamedTuple):
    """マージの結果。止まったら `kind` に止まった理由の種類。"""

    merged: bool
    kind: str | None
    detail: str
    sync_pr: int | None = None


@dataclass(frozen=True)
class Lander:
    """1 つのリポジトリのリリースの PR をマージする。`wait` は必須のチェックの待ちを組む関数（テストが差し替える）。"""

    hub: GitHub
    base: str
    production: str
    budget: float
    wait: Callable[[Callable[[], ci.Reading], float], ci.WatchResult] | None = None

    def required(self, branch: str) -> list[str]:
        """宛先のブランチの規則の必須のチェックの名前（承認の commit status を除く）。"""
        rules = self.hub.rest("GET", f"/repos/{self.hub.repo}/rules/branches/{branch}")
        names = [
            text_of(check["context"])
            for rule in (rules if isinstance(rules, list) else [])
            if isinstance(rule, dict) and rule.get("type") == REQUIRED_RULE
            for check in rule["parameters"]["required_status_checks"]
        ]
        return sorted({n for n in names if n != approval_decide.STATUS_CONTEXT})

    def _watch(self, pull: Pull) -> str | None:
        """必須のチェックを待ち、止まった理由の種類（通れば None）。"""
        reader = ci.watch_head(self.hub, pull.head_sha, self.required(pull.base_ref), [])
        watched = self.wait(reader, self.budget) if self.wait is not None else ci.Watch(reader, self.budget).run()
        return watched.cause([])

    def _merge(self, pull: Pull) -> Landed:
        kind = self._watch(pull)
        if kind is not None:
            return Landed(False, kind, f"#{pull.number} の必須のチェックが通らない（{kind}。打ち直さずに人へ戻す）")
        outcome: MergeOutcome = self.hub.merge(pull.number, expect_head=pull.head_sha)
        return Landed(outcome.merged, outcome.kind, outcome.detail)

    def _sync(self, pull: Pull) -> Landed:
        """本番へ入れたリリースのブランチを、ベースブランチへ戻す PR を作ってマージする。"""
        try:
            made = self.hub.rest(
                "POST",
                f"/repos/{self.hub.repo}/pulls",
                {"title": f"Sync: {pull.head_ref} を {self.base} へ戻す", "head": pull.head_ref, "base": self.base, "body": ""},
            )
        except GitHubError as exc:
            if exc.status != 422:
                raise
            return Landed(True, None, f"#{pull.number} をマージした（{self.base} へ戻す差分は無い）")
        synced = self._merge(self.hub.pull(int(made["number"])))
        return synced._replace(sync_pr=int(made["number"]), detail=f"#{pull.number} をマージした。戻す PR: {synced.detail}")

    def land(self, number: int) -> Landed:
        """リリースの PR をマージする。"""
        pull = self.hub.pull(number)
        if pull.merged:
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
