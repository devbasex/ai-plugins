"""`gh_parts.py pr-status` の組み立て（#2909 の F4）。

PR の状態・チェック（名前ごとの最新の実行）・未解決のスレッド・マージの状態・PR が指す Issue の開閉を 1 回で読み、
次の 1 手を 4 つの語（待つ・直す・マージへ進める・人へ戻す）の 1 つへ訳して返す。GitHub の語（`MergeStateStatus`・
チェックの `status` と `conclusion`）は工程の側へ持ち込まない（腐敗防止層）。

取得の回数: メタは `gh pr view` 1 回（`pr-info` と同じ）、チェックは REST のチェックの実行（100 件ごとに 1 回）、
スレッド・マージの状態・PR が閉じる Issue は GraphQL 1 回。`--issue` で足した Issue だけ REST で 1 回ずつ読む。

判定（上から当て、最初に当たった行で決める。I7）:

| 順 | 条件 | 次の 1 手 | 終了コード |
| --- | --- | --- | --- |
| 1 | PR の状態が open でない | 人へ戻す（escalate） | 10 |
| 2 | マージの状態が DIRTY（衝突） | 人へ戻す | 10 |
| 3 | チェックかスレッドを照会できなかった（null。0 件と分ける） | 人へ戻す | 10 |
| 4 | 失敗したチェックがある、または未解決のスレッドが 1 件以上 | 直す（fix） | 20 |
| 5 | 実行中のチェックがある、またはチェックが 1 件も無い | 待つ（wait） | 21 |
| 6 | すべて成功で、未解決のスレッドが 0 件 | マージへ進める（merge） | 0 |

PR が指す Issue の開閉は判定に使わず、`items` と `metrics` に載せるだけにする。
"""

from __future__ import annotations

from typing import Any

import gh_call
import gh_checks
import gh_graphql
import gh_pr_info
import step_result

TOOL = "pr-status"
EXIT_FIX = step_result.EXIT_PAUSE  # 20: 直す（LLM の判断）
EXIT_WAIT = step_result.EXIT_PAUSE + 1  # 21: 待つ（LLM の判断）
EXIT_ESCALATE = step_result.EXIT_GATE  # 10: 人へ戻す（人の同意）
OK_RESULTS = ("success", "skipped", "neutral")


def _checks(slug: str, sha: str) -> list[dict[str, Any]] | None:
    """名前ごとの最新の実行へ畳んだチェック。照会できなければ None、1 件も無ければ空。"""
    raw = gh_checks.fetch_check_runs(slug, sha, empty_ok=True) if sha else None
    if raw is None:
        return None
    return [{"kind": "check", "name": str(r.get("name") or ""), "result": gh_checks.run_result(r)} for r in gh_checks.fold_check_runs(raw)]


def _issue_state(slug: str, number: int) -> str | None:
    resp = gh_call.rest(f"repos/{slug}/issues/{int(number)}")
    if resp is None or not isinstance(resp.body, dict):
        return None
    return str(resp.body.get("state") or "").lower() or None


def _check_failed(check: dict) -> bool:
    """成功・飛ばした・中立・実行中のどれでもない結果（failure・cancelled・timed_out など）を失敗と読む。"""
    return check["result"] not in (*OK_RESULTS, "pending")


def decide_action(state: str, merge_state: str | None, checks: list[dict] | None, threads: int | None, pr: int) -> tuple[str, str, int]:
    """判定の表を上から当て、`(action, next, 終了コード)` を返す。"""
    if state != "open":
        return "escalate", f"PR は {state}。後片付け（merged）へ進むかを人が決める", EXIT_ESCALATE
    if str(merge_state or "").upper() == "DIRTY":
        return "escalate", "ベースブランチと衝突している。merged-steps.py base-sync を打つかを人が決める", EXIT_ESCALATE
    if checks is None or threads is None:
        what = "・".join(w for w, v in (("チェック", checks), ("未解決のスレッド", threads)) if v is None)
        return "escalate", f"{what}を照会できない。gh_parts.py pr-status {pr} を打ち直す", EXIT_ESCALATE
    failed = [c["name"] for c in checks if _check_failed(c)]
    if failed or threads:
        parts = [f"失敗: {', '.join(failed)}" if failed else "", f"未解決のスレッド {threads} 件" if threads else ""]
        return "fix", "。".join(p for p in parts if p) + "。直してから打ち直す", EXIT_FIX
    pending = [c["name"] for c in checks if c["result"] == "pending"]
    if pending or not checks:
        what = f"実行中: {', '.join(pending)}" if pending else "チェックがまだ 1 件も無い"
        return "wait", f"{what}。merged-steps.py merge-when-green {pr} で待つ", EXIT_WAIT
    return "merge", f"merged-steps.py merge-when-green {pr}", step_result.EXIT_OK


def pr_status(pr: int, repo: str | None = None, extra_issues=()) -> tuple[dict[str, Any], int]:
    """PR を照合して `(結果, 終了コード)` を返す。"""
    slug = gh_call.resolve_repo(repo)
    if not slug:
        return step_result.result(TOOL, "stopped", "リポジトリを決められない（--repo か origin）"), step_result.EXIT_UNREADABLE
    meta, source, reason = gh_pr_info.fetch_pr_meta(slug, pr)
    if meta is None:
        return step_result.result(
            TOOL, "stopped", f"PR #{pr} を取得できない: {reason}", metrics={"source": source}
        ), step_result.EXIT_UNREADABLE
    checks = _checks(slug, meta["head_sha"])
    parts, why = gh_graphql.pr_status_parts(slug, pr)
    parts = parts or {"merge_state": None, "unresolved_threads": None, "issues": []}
    issues = {i["number"]: i["state"] for i in parts["issues"]}
    for n in extra_issues or ():
        if n not in issues:
            issues[n] = _issue_state(slug, n)
    action, next_, code = decide_action(meta["state"], parts["merge_state"], checks, parts["unresolved_threads"], pr)
    items = [*(checks or []), *({"kind": "issue", "name": f"#{n}", "result": s or "unknown"} for n, s in issues.items())]
    metrics = {
        "action": action,
        "state": meta["state"],
        "draft": meta["draft"],
        "merge_state": parts["merge_state"],
        "failed_checks": None if checks is None else sum(1 for c in checks if _check_failed(c)),
        "pending_checks": None if checks is None else sum(1 for c in checks if c["result"] == "pending"),
        "unresolved_threads": parts["unresolved_threads"],
        "issues_open": sum(1 for s in issues.values() if s == "open"),
        "issues_closed": sum(1 for s in issues.values() if s == "closed"),
    }
    gaps = [
        {"kind": "query", "name": w, "reason": why or "照会できない"}
        for w, v in (("checks", checks), ("threads", parts["unresolved_threads"]))
        if v is None
    ]
    status = "ok" if action == "merge" else "gate"
    summary = f"PR #{pr} {meta['state']}: {action}"
    out = step_result.result(TOOL, status, summary, items, metrics, next=next_)
    if gaps and status == "gate":
        out["gaps"] = gaps
    return out, code
