"""`pr-info` の組み立て（#1142 の L0 で gh_parts から分けた。#849）。

メタ・本文・差分の統計・checks（名前ごとの最新の実行へ畳んだもの）・未解決のスレッドを 1 つの結果にまとめる。
GraphQL が上限のときは REST へ退避する（#271）。
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any

import gh_call
import gh_checks
import gh_graphql
import gh_quota
import step_result

PR_VIEW_FIELDS = ("number,title,body,state,isDraft,author,headRefName,headRefOid,"
                  "baseRefName,url,additions,deletions,changedFiles,labels,isCrossRepository")
WITH_PARTS = ("checks", "threads", "diff", "logs")


def _meta_from_graphql(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": d.get("number"), "title": d.get("title") or "", "body": d.get("body") or "",
        "state": str(d.get("state") or "").lower(), "draft": bool(d.get("isDraft")),
        "author": str((d.get("author") or {}).get("login") or ""),
        "head_branch": d.get("headRefName") or "", "head_sha": d.get("headRefOid") or "",
        "base_branch": d.get("baseRefName") or "", "url": d.get("url") or "",
        "is_fork": bool(d.get("isCrossRepository")),
        "labels": [str(x.get("name")) for x in d.get("labels") or [] if isinstance(x, dict)],
        "additions": d.get("additions"), "deletions": d.get("deletions"),
        "changed_files": d.get("changedFiles"),
    }


def _meta_from_rest(repo: str, d: dict[str, Any]) -> dict[str, Any]:
    head = d.get("head") or {}
    head_repo = (head.get("repo") or {}).get("full_name") or ""
    state = "merged" if d.get("merged") or d.get("merged_at") else str(d.get("state") or "")
    return {
        "number": d.get("number"), "title": d.get("title") or "", "body": d.get("body") or "",
        "state": state.lower(), "draft": bool(d.get("draft")),
        "author": str((d.get("user") or {}).get("login") or ""),
        "head_branch": head.get("ref") or "", "head_sha": head.get("sha") or "",
        "base_branch": (d.get("base") or {}).get("ref") or "", "url": d.get("html_url") or "",
        "is_fork": bool(head_repo) and head_repo != repo,
        "labels": [str(x.get("name")) for x in d.get("labels") or [] if isinstance(x, dict)],
        "additions": d.get("additions"), "deletions": d.get("deletions"),
        "changed_files": d.get("changed_files"),
    }


def fetch_pr_meta(repo: str, pr: int) -> tuple[dict[str, Any] | None, str, str]:
    """PR のメタと本文を返す。`(meta, 取得元, 失敗の理由)`。GraphQL が上限なら REST へ退避する。"""
    r = gh_call.gh(["pr", "view", str(int(pr)), "--repo", repo, "--json", PR_VIEW_FIELDS])
    if r.returncode == 0:
        try:
            return _meta_from_graphql(json.loads(r.stdout)), "graphql", ""
        except (ValueError, TypeError, AttributeError):
            return None, "graphql", "gh pr view の出力を読めない"
    if not gh_quota.is_rate_limited(r.stderr + r.stdout):
        return None, "graphql", f"gh pr view が失敗: {r.stderr.strip()[:200]}"
    resp = gh_call.rest(f"repos/{repo}/pulls/{int(pr)}")
    if resp is None or not isinstance(resp.body, dict) or not resp.body.get("number"):
        return None, "rest", "GraphQL が上限で、REST でも取得できない"
    return _meta_from_rest(repo, resp.body), "rest", ""


def default_out_dir(repo: str, pr: int) -> pathlib.Path:
    return pathlib.Path(tempfile.gettempdir()) / "ndf" / f"pr-info-{repo.replace('/', '--')}-{int(pr)}"


def _save_diff(repo: str, pr: int, out_dir: pathlib.Path) -> tuple[str | None, str]:
    r = gh_call.gh(["api", "-H", "Accept: application/vnd.github.v3.diff", f"repos/{repo}/pulls/{int(pr)}"])
    if r.returncode != 0:
        return None, f"差分を取得できない: {r.stderr.strip()[:200]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "pr.diff"
    path.write_text(r.stdout, encoding="utf-8")
    return str(path), ""


def pr_info(pr: int, repo: str | None = None, with_parts: set[str] | None = None,
            out_dir: pathlib.Path | None = None) -> tuple[dict[str, Any], int]:
    """PR の取得を 1 つの結果にまとめる。`(結果, 終了コード)`。

    items の `kind` は `pr` / `check` / `thread` / `diff`。checks は名前ごとの最新の実行へ
    畳んでから返す。失敗したチェックのログは `--with logs` のときだけファイルへ書く。
    """
    parts = set(with_parts or ())
    slug = gh_call.resolve_repo(repo)
    if not slug:
        return step_result.result("pr-info", "stopped", "リポジトリを決められない"), \
            step_result.EXIT_PRECONDITION
    meta, source, reason = fetch_pr_meta(slug, pr)
    if meta is None:
        return step_result.result("pr-info", "stopped", f"PR #{pr} を取得できない: {reason}",
                                  metrics={"source": source}), step_result.EXIT_UNREADABLE
    out = out_dir or default_out_dir(slug, pr)
    items: list[dict[str, Any]] = [{"kind": "pr", "name": f"#{pr}", "result": meta["state"],
                                    "repo": slug, **meta}]
    metrics: dict[str, Any] = {"source": source, "additions": meta["additions"],
                               "deletions": meta["deletions"],
                               "changed_files": meta["changed_files"]}
    unavailable: list[str] = []
    notes: list[str] = []

    if "diff" in parts:
        path, why = _save_diff(slug, pr, out)
        items.append({"kind": "diff", "name": "diff", "result": "saved" if path else "unavailable",
                      "path": path, **({"reason": why} if why else {})})
        if not path:
            unavailable.append("diff")

    if "checks" in parts or "logs" in parts:
        raw = gh_checks.fetch_check_runs(slug, meta["head_sha"])
        if raw is None:
            unavailable.append("checks")
            metrics.update(failed_checks=None, pending_checks=None)
        else:
            folded = gh_checks.fold_check_runs(raw)
            failed = pending = 0
            for run in folded:
                res = gh_checks.run_result(run)
                item = {"kind": "check", "name": str(run.get("name") or ""), "result": res,
                        "url": run.get("details_url") or run.get("html_url") or ""}
                if res in gh_checks.FAILED_CONCLUSIONS:
                    failed += 1
                    if "logs" in parts:
                        path, why = gh_checks.save_failed_log(slug, run, out)
                        item.update({"log_path": path} if path else {"log_error": why})
                elif res == "pending":
                    pending += 1
                items.append(item)
            metrics.update(failed_checks=failed, pending_checks=pending,
                           superseded_checks=len(raw) - len(folded))
            notes.append(f"checks 失敗 {failed} / 保留 {pending}")

    if "threads" in parts:
        threads = gh_graphql.unresolved_threads(slug, pr)
        if threads is None:
            unavailable.append("threads")
            metrics["unresolved_threads"] = None
        else:
            for t in threads:
                line: Any = t["line"]
                try:
                    line = int(line)
                except (TypeError, ValueError):
                    line = None
                items.append({"kind": "thread", "name": t["thread_id"], "result": "unresolved",
                              "thread_id": t["thread_id"], "path": t["path"], "line": line})
            metrics["unresolved_threads"] = len(threads)
            notes.append(f"未解決 {len(threads)}")

    if unavailable:
        metrics["unavailable"] = unavailable
        notes.append("取得できない: " + ", ".join(unavailable))
    summary = " / ".join([f"PR #{pr} {meta['state']}（{source}）", *notes])
    return step_result.result("pr-info", "ok", summary, items, metrics), step_result.EXIT_OK
