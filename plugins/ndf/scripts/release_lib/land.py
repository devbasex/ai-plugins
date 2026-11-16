"""land.py: 承認した head だけを、ブランチ戦略の宣言の主体と方式で本番ブランチへ入れる（#3672 の I5・I7・I10）。

- `ruleset_mismatch`: セクションの値と本番ブランチの ruleset の食い違い（I10）
- `check_production_pr`: 本番の PR のマージの方式ごとの確かめ（I7）
- `fast_forward_land`: ruleset の無い `strict` で、確かめた先端から本番ブランチが進んでいないときだけ入れる
- `land_approved`: 上を組み、マージかキューへの投入をする。承認した head と今の head が違えば止まる（I5・E10）

GitHub との出入りは `Port` に閉じる（テストは差し替える）。NDF はロックを取らない（決定 7）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

TRAILER_PR = "NDF-Landed-PR"
TRAILER_HEAD = "NDF-Approved-Head"
TRAILER_TIP = "NDF-Tip"
BYPASS_OK = ("always", "pull_requests_only")
REAPPROVE = "承認を取り直してください"


def ruleset_mismatch(strategy, facts) -> list[dict]:
    """セクションの値と ruleset の食い違いを I10 の比べ方で並べる。空なら一致。"""
    sec = strategy.section
    method, via, admin = sec["merge"]["method"], sec["approval"]["via"], sec["merge"]["admin"]
    out = []
    if not facts.present:
        if method == "merge-queue":
            out.append({"key": "merge.method", "declared": method, "ruleset": "ruleset が無い（merge queue が無い）"})
        return out
    if (method == "merge-queue") != facts.queue:
        out.append({"key": "merge.method", "declared": method, "ruleset": "merge queue あり" if facts.queue else "merge queue なし"})
    if method == "strict" and not facts.up_to_date:
        out.append({"key": "merge.method", "declared": method, "ruleset": "最新の取り込みを求めない"})
    if admin:
        if facts.bypass not in BYPASS_OK:
            out.append({"key": "merge.admin", "declared": True, "ruleset": f"迂回の可否 {facts.bypass}"})
    elif via == "gate" and facts.approvals > 0:
        out.append({"key": "approval.via", "declared": via, "ruleset": f"承認の数 {facts.approvals}"})
    elif via == "review" and facts.approvals < 1:
        out.append({"key": "approval.via", "declared": via, "ruleset": "承認の数 0"})
    return out


def check_production_pr(strategy, pr: dict, prod: dict, queue: list[dict]) -> str | None:
    """本番の PR がマージの方式ごとの確かめを通るか。通れば None、落ちれば理由の文。

    `pr` は `head`・`bump`（版上げの PR か）・`cut_version`（切った時点の本番の版数）・`contains_tip`（head が本番の先端を含むか）、
    `prod` は `tip`・`version`、`queue` はキューの PR（各 `number`・`bump`）。
    """
    method = strategy.section["merge"]["method"]
    if method == "direct":
        return None
    if method == "strict":
        if not pr.get("contains_tip"):
            return f"head が本番ブランチの先端 {prod.get('tip', '')[:12]} を含まない。本番の PR を作り直し、{REAPPROVE}"
        return None
    if not pr.get("bump"):
        return None
    if pr.get("cut_version") != prod.get("version"):
        return f"本番ブランチの版数 {prod.get('version')} が、版上げの PR を切った時点の {pr.get('cut_version')} と違う。作り直し、{REAPPROVE}"
    others = [q for q in queue if q.get("bump") and q.get("number") != pr.get("number")]
    if others:
        return f"キューに別の版上げの PR（#{others[0]['number']}）がある。作り直し、{REAPPROVE}"
    return None


class Port(Protocol):
    """GitHub との出入り（`land_approved` が使う分だけ）。"""

    def pull(self, number: int) -> dict: ...  # {"state", "merged", "head", "base", "bump", "cut_version", "contains_tip"}
    def production(self, branch: str) -> dict: ...  # {"tip", "version"}
    def queue(self, branch: str) -> list[dict]: ...
    def rule_facts(self, branch: str) -> Any: ...
    def merge(self, number: int, sha: str, commit: str, admin: bool) -> tuple[bool, str]: ...
    def enqueue(self, number: int, head: str, jump: bool) -> tuple[bool, str]: ...
    def create_commit(self, message: str, tree_of: str, parents: list[str]) -> str | None: ...
    def update_ref(self, branch: str, sha: str) -> bool: ...
    def close(self, number: int, comment: str) -> bool: ...
    def comment(self, number: int, body: str) -> bool: ...
    def landed_record(self, branch: str, number: int, since: str | None) -> dict | None: ...
    def has_human_record(self, number: int) -> bool: ...


def trailers(pr: int, head: str, tip: str) -> str:
    return f"{TRAILER_PR}: {pr}\n{TRAILER_HEAD}: {head}\n{TRAILER_TIP}: {tip}"


def fast_forward_land(port: Port, production: str, pr: int, tip: str, head: str, commit: str, title: str = "") -> dict:
    """確かめた先端 `tip` から本番ブランチが進んでいないときだけ承認した head を入れる（早送りだけの参照の更新）。"""
    if commit == "rebase":
        sha = head
    else:
        parents = [tip, head] if commit == "merge" else [tip]
        message = f"{title or f'Land #{pr}'}\n\n{trailers(pr, head, tip)}\n"
        sha = port.create_commit(message, head, parents)
        if not sha:
            return {"result": "stopped", "commit": None, "reason": "取り込みのコミットを作れない"}
    if not port.update_ref(production, sha):
        return {"result": "stopped", "commit": sha, "reason": f"本番ブランチ {production} が確かめた先端から進んだ（早送りでない）。本番の PR を作り直し、{REAPPROVE}"}
    if commit == "squash" and not port.close(pr, f"{sha} で本番ブランチへ入れた（squash）"):
        return {"result": "stopped", "commit": sha, "reason": f"#{pr} を閉じられない。打ち直すと記録から閉じるところへ進む"}
    return {"result": "merged", "commit": sha, "reason": ""}


@dataclass
class Approval:
    """承認の証跡が持つ値。"""

    head: str
    at: str | None = None


def land_approved(port: Port, strategy, pr: int, approval: Approval) -> dict:
    """承認した head を、セクションの主体と方式で本番ブランチへ入れる。"""
    sec = strategy.section
    prod_branch = sec["branches"]["production"]
    merge = sec["merge"]
    pull = port.pull(pr)
    out = {"result": "stopped", "head": pull.get("head"), "reason": ""}
    if pull.get("merged"):
        if not port.has_human_record(pr):
            port.comment(pr, f"<!-- ndf:human-merge -->\n人がマージした先端: {pull.get('head')}")
        return {**out, "result": "human"}
    if merge["actor"] == "human":
        return {**out, "result": "human", "reason": "人のマージを待つ"}
    if pull.get("head") != approval.head:
        return {**out, "reason": f"承認した head {approval.head[:12]} と今の head {str(pull.get('head'))[:12]} が違う。{REAPPROVE}"}
    facts = port.rule_facts(prod_branch)
    if facts is None:
        return {**out, "reason": f"本番ブランチ {prod_branch} の ruleset を読めない"}
    if merge["method"] == "strict" and not facts.present:
        rec = port.landed_record(prod_branch, pr, approval.at)
        if rec and rec.get("head") == approval.head and rec.get("tree_ok") and rec.get("first_parent_is_tip"):
            if pull.get("state") == "closed" or port.close(pr, f"{rec.get('commit')} で本番ブランチへ入れた"):
                return {**out, "result": "merged"}
    mism = ruleset_mismatch(strategy, facts)
    if mism:
        return {**out, "reason": "セクションと ruleset が食い違う: " + json.dumps(mism, ensure_ascii=False)}
    is_prod_pr = pull.get("base") == prod_branch
    if is_prod_pr:
        why = check_production_pr(strategy, pull, port.production(prod_branch), port.queue(prod_branch))
        if why:
            return {**out, "reason": why}
    if merge["method"] == "merge-queue":
        ok, why = port.enqueue(pr, approval.head, bool(pull.get("bump")))
        return {**out, "result": "queued" if ok else "stopped", "reason": why}
    if merge["method"] == "strict" and not facts.present:
        tip = port.production(prod_branch)["tip"]
        got = fast_forward_land(port, prod_branch, pr, tip, approval.head, merge["commit"], pull.get("title", ""))
        return {**out, "result": got["result"], "reason": got["reason"]}
    ok, why = port.merge(pr, approval.head, merge["commit"], bool(merge["admin"] and merge["method"] == "direct"))
    return {**out, "result": "merged" if ok else "stopped", "reason": why}
