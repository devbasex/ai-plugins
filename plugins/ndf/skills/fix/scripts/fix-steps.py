#!/usr/bin/env python3
"""fix-steps.py: `/ndf:fix` の手順のうち、判断の要らない取得と集計を行う。

    fix-steps.py context  <PR> [--repo <所有者>/<リポジトリ>] [--root <作業ツリー>]
    fix-steps.py finalize --decisions <振り分けの JSON> [--pr <PR>] [--root <作業ツリー>]
    fix-steps.py remaining <PR> [--repo <所有者>/<リポジトリ>]

context   未解決のスレッド・3 種のコメント・CI の状態（失敗した check と失敗ログの保存先）・
          本文の除外の節と「指摘の基準」の節（`scripts/lib/review_criteria.py`）を 1 つのファイルへ書き、
          振り分けの雛形（decisions）を書き出す。レビューの重点は、環境変数 `CROSS_REVIEW_STATE` が
          cross-review の状態ファイルを指し `review_criteria` を持てばそれを写し、無ければ `--root`
          （既定はカレントの作業ツリーの根）の `.ndf/review.json` を読む
finalize  振り分けの JSON から件数と by_severity を数え、`cross-review` の `state.py merge-fix`
          が読む戻り値ファイル `$TMP_DIR/fix-pr<PR>-result.json` を組む。`waived` は見送りの返信を
          組んで `deferred` へ `resolve: true` で載せ、`fixed` も CI の修正（`ci_fixed`）も無ければ `fix_commit` を捨てる。設計 PR の本文の
          「決めたこと」の節も揃える（`pr-body-decisions.sh sync`）
remaining 返信と決着の後に未解決のスレッドを数え直し、見送り・却下で残したもの以外が
          残っていれば止まる

結果は 1 行の JSON（形は `scripts/lib/README.md`。`tool` は `fix`）。読み手は `status` を見る。
`$TMP_DIR` は環境変数 `CROSS_REVIEW_TMP_DIR` があればそれ、なければ /tmp。

振り分けの JSON（`context` が雛形を書き、LLM が `decision` と理由を埋める）:

    {"pr": 812, "fix_commit": "abc1234"（省くと HEAD）, "ci_fixed": false（CI の失敗を直したら true）, "ci_note": null,
     "review_focus": ["<重点の名前>"], "review_focus_status": "declared|none|unreadable",
     "decisions": [{"thread_id": "PRRT_...", "comment_id": 1, "path": "a.py", "line": 3,
                    "severity": "major", "category": "style", "summary": "...",
                    "decision": "fixed|waived|deferred|rejected|separate_pr", "reason": "...",
                    "criterion": 1（当たった基準の番号。任意。waived では持たない）,
                    "waive_kind": "wording"（waived のとき）,
                    "issue": "#123"（separate_pr のとき）}]}

振り分けの規則（破れば finalize が止まる）: fixed は critical / major だけ。waived は minor / nit だけで、
criterion を持たず、waive_kind が見送りの種類のどれか。criterion 3 は review_focus が空でないときだけ。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
import deps  # noqa: E402

deps.require("md")
import md  # noqa: E402
from step_result import (  # noqa: E402
    EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, gh_json, git, git_root, main_with, result, run,
)
import gh_parts  # noqa: E402
import review_criteria  # noqa: E402

TOOL = "fix"
SEVERITIES = ("critical", "major", "minor", "nit")
FIX_SEVERITIES = ("critical", "major")
WAIVE_SEVERITIES = ("minor", "nit")
DECISIONS = ("fixed", "waived", "deferred", "rejected", "separate_pr")
EXCLUDE_HEADINGS = ("やらないこと", "別 PR", "別PR", "対応しない", "スコープ外", "範囲外", "out of scope",
                    "non-goals", "not in scope")
CI_FAILED = ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE")
CI_PENDING = ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED", "EXPECTED")


def result_dir() -> Path:
    """結果とログの置き場（cross-review と同じ `CROSS_REVIEW_TMP_DIR`）。"""
    d = Path(os.environ.get("CROSS_REVIEW_TMP_DIR") or tempfile.gettempdir())
    d.mkdir(parents=True, exist_ok=True)
    return d


def repo_of(a) -> str:
    """`--repo` か、`gh repo view` の `owner/repo`（origin の URL より gh の既定のリポジトリを先に見る）。"""
    if getattr(a, "repo", None):
        return a.repo
    p = gh_parts.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    slug = p.stdout.strip()
    if p.returncode != 0 or "/" not in slug:
        raise StepError("リポジトリを決められない（--repo <所有者>/<リポジトリ> を渡す）", EXIT_PRECONDITION)
    return slug


# --- 取得 -----------------------------------------------------------------------

def pr_for_fix(pr: int) -> dict:
    return gh_json(None, ["pr", "view", str(pr), "--json", "number,url,body,headRefName,reviewDecision,state"],
                   f"PR #{pr} の取得")


def threads_with_first_comment(repo: str, pr: int) -> list[dict]:
    """未解決のスレッドを、最初のコメント（id・書き手・本文）つきで返す（`gh_parts.unresolved_threads` は id と位置だけ）。"""
    owner, name = repo.split("/", 1)
    query = ("query($owner:String!,$name:String!,$pr:Int!){repository(owner:$owner,name:$name){"
             "pullRequest(number:$pr){reviewThreads(first:100){nodes{id isResolved path line "
             "comments(first:1){nodes{databaseId body author{login}}}}}}}}")
    data = gh_json(None, ["api", "graphql", "-f", f"query={query}", "-F", f"owner={owner}",
                          "-F", f"name={name}", "-F", f"pr={pr}"], "未解決スレッドの取得")
    try:
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (KeyError, TypeError):
        raise StepError("未解決スレッドの応答を読めない", EXIT_UNREADABLE)
    out = []
    for n in nodes or []:
        if n.get("isResolved"):
            continue
        first = ((n.get("comments") or {}).get("nodes") or [{}])[0] or {}
        out.append({"thread_id": n.get("id"), "comment_id": first.get("databaseId"),
                    "path": n.get("path"), "line": n.get("line"),
                    "author": (first.get("author") or {}).get("login"), "body": first.get("body") or ""})
    return out


def fetch_comments(repo: str, pr: int) -> tuple[str, int]:
    p = run([str(HERE / "fetch-pr-comments.sh"), repo, str(pr)], check=False)
    if p.returncode != 0:
        raise StepError(f"コメントの取得に失敗: {p.stderr.strip()[:300]}")
    text = p.stdout
    return text, sum(1 for ln in text.splitlines() if ln.strip())


def ci_snapshot(pr: int) -> tuple[str, list[dict]]:
    """現時点のチェックの状態。待たない。"""
    p = gh_parts.gh(["pr", "checks", str(pr), "--json", "name,state,link"])
    try:
        checks = json.loads(p.stdout or "[]")
    except ValueError:
        checks = []
    if not isinstance(checks, list) or not checks:
        return "NONE", []
    failed = [c for c in checks if str(c.get("state", "")).upper() in CI_FAILED]
    if failed:
        return "FAILURE", failed
    if any(str(c.get("state", "")).upper() in CI_PENDING for c in checks):
        return "PENDING", []
    return "SUCCESS", []


def failed_log(branch: str, pr: int) -> str | None:
    p = gh_parts.gh(["run", "list", "--branch", branch, "--limit", "1", "--json", "databaseId",
                     "--jq", ".[0].databaseId // empty"])
    run_id = p.stdout.strip()
    if p.returncode != 0 or not run_id:
        return None
    p = gh_parts.gh(["run", "view", run_id, "--log-failed"])
    if p.returncode != 0 and not p.stdout.strip():
        return None
    path = result_dir() / f"fix-pr{pr}-ci-failed.log"
    path.write_text(p.stdout, encoding="utf-8")
    return str(path)


def exclusion_sections(body: str) -> list[tuple[str, str]]:
    """本文のうち、この PR で対応しない内容を書いた節（見出しと、次の見出しまでの本文）。見出しは lib/md.py で読む。"""
    text = body or ""
    lines = text.splitlines()
    marks = md.headings(text)
    out = []
    for k, h in enumerate(marks):
        if any(w.lower() in h.title.lower() for w in EXCLUDE_HEADINGS):
            end = marks[k + 1].line if k + 1 < len(marks) else len(lines)
            out.append((h.title, "\n".join(lines[h.line + 1:end]).strip()))
    return out


def head_line(s: str, n: int = 100) -> str:
    return (s or "").strip().splitlines()[0][:n] if (s or "").strip() else ""


def focus_for_context(root_arg) -> review_criteria.Focus:
    """cross-review の中なら状態ファイルに写した重点（I8）、単独なら作業ツリーの宣言。"""
    state = os.environ.get("CROSS_REVIEW_STATE")
    if state:
        try:
            crit = json.loads(Path(state).read_text(encoding="utf-8")).get("review_criteria")
        except (OSError, ValueError, AttributeError):
            crit = None
        if isinstance(crit, dict):
            return review_criteria.focus_from(crit.get("status"), crit.get("focus"), crit.get("error"))
    try:
        root = git_root(root_arg)
    except StepError:
        root = Path.cwd()
    return review_criteria.load_focus(root)


def cmd_context(a):
    pr = a.pr
    repo = repo_of(a)
    focus = focus_for_context(a.root)
    view = pr_for_fix(pr)
    threads = threads_with_first_comment(repo, pr)
    comments_text, comments_n = fetch_comments(repo, pr)
    ci_status, failed = ci_snapshot(pr)
    log_path = failed_log(view.get("headRefName") or "", pr) if failed else None
    excluded = exclusion_sections(view.get("body") or "")

    d = result_dir()
    ctx = d / f"fix-pr{pr}-context.md"
    lines = [f"# PR #{pr} の文脈", "", f"- URL: {view.get('url')}", f"- head: {view.get('headRefName')}",
             f"- reviewDecision: {view.get('reviewDecision')}", f"- CI: {ci_status}", ""]
    lines += ["## この PR で対応しない内容（本文の節）", ""]
    lines += [f"### {t}\n\n{b}\n" for t, b in excluded] or ["（無し）", ""]
    lines += [f"## 未解決のスレッド（{len(threads)} 件）", ""]
    for i, t in enumerate(threads, 1):
        lines.append(f"{i}. `{t['thread_id']}` comment_id={t['comment_id']} {t['path']}:{t['line']} "
                     f"[{t.get('author')}]")
        lines.append("   " + (t["body"].replace("\n", "\n   ")))
    lines += ["", f"## CI の失敗（{len(failed)} 件）", ""]
    lines += [f"- {c.get('name')}: {c.get('state')} {c.get('link') or ''}" for c in failed] or ["（無し）"]
    if log_path:
        lines += ["", f"失敗ログ: {log_path}"]
    lines += ["", f"## コメント（3 種、{comments_n} 行）", "", "```", comments_text.rstrip(), "```", ""]
    lines += [review_criteria.fixer_block(focus), ""]
    ctx.write_text("\n".join(lines), encoding="utf-8")

    dec = d / f"fix-pr{pr}-decisions.json"
    dec.write_text(json.dumps({
        "pr": pr, "fix_commit": None, "ci_fixed": False, "ci_note": None,
        "review_focus": list(focus.names), "review_focus_status": focus.status,
        "decisions": [{"thread_id": t["thread_id"], "comment_id": t["comment_id"], "path": t["path"],
                       "line": t["line"], "severity": "", "category": "", "summary": head_line(t["body"]),
                       "decision": "", "reason": ""} for t in threads],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    items = [{"name": "context", "path": str(ctx)}, {"name": "decisions", "path": str(dec)},
             {"name": "review-focus", "result": focus.status, "reason": focus.error}]
    items += [{"name": c.get("name"), "result": "ci_failed", "state": c.get("state"), "link": c.get("link")}
              for c in failed]
    if log_path:
        items.append({"name": "ci_log", "path": log_path})
    emit(result(TOOL, "ok",
                f"未解決 {len(threads)} 件 / コメント {comments_n} 行 / CI {ci_status}（失敗 {len(failed)} 件）",
                items, {"pr": pr, "repo": repo, "unresolved": len(threads), "comments": comments_n,
                        "ci_status": ci_status, "ci_failed": len(failed), "excluded_sections": len(excluded),
                        "review_focus": focus.status},
                next=f"{ctx} を読み、{dec} の decision を埋める"))


# --- 集計 -----------------------------------------------------------------------

def load_decisions(path: str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise StepError(f"振り分けの JSON が無い: {p}", EXIT_PRECONDITION)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise StepError(f"振り分けの JSON を読めない: {e}", EXIT_UNREADABLE)
    if not isinstance(d, dict) or not isinstance(d.get("decisions"), list):
        raise StepError("振り分けの JSON は {\"decisions\": [...]} を持つ", EXIT_UNREADABLE)
    return d


def check_decisions(decs: list, focus_names=()) -> list[dict]:
    """振り分けの誤りを items の形で返す。空なら通る（設計の不変条件 I1〜I3）。"""
    bad = []
    for i, e in enumerate(decs):
        if not isinstance(e, dict):
            bad.append({"index": i, "result": "invalid", "reason": "オブジェクトでない"})
            continue
        why = []
        if e.get("decision") not in DECISIONS:
            why.append(f"decision が {'/'.join(DECISIONS)} のどれでもない: {e.get('decision')!r}")
        if e.get("decision") == "rejected":
            for k in ("path", "line", "severity"):
                if e.get(k) in (None, ""):
                    why.append(f"rejected には {k} が要る")
        if e.get("decision") in ("deferred", "rejected") and not str(e.get("reason") or "").strip():
            why.append("deferred / rejected には reason が要る")
        if e.get("decision") == "separate_pr" and not str(e.get("issue") or "").strip():
            why.append("separate_pr には issue（起票した番号）が要る")
        if e.get("decision") == "fixed" and e.get("severity") not in FIX_SEVERITIES:
            why.append(f"fixed は severity が {'/'.join(FIX_SEVERITIES)} のときだけ（minor / nit は waived にする。"
                       f"基準に当たるなら重要度を判定し直して criterion を書く）: {e.get('severity')!r}")
        if e.get("decision") == "waived":
            if e.get("severity") not in WAIVE_SEVERITIES:
                why.append(f"waived は severity が {'/'.join(WAIVE_SEVERITIES)} のときだけ: {e.get('severity')!r}")
            if e.get("criterion") not in (None, ""):
                why.append("waived は criterion を持たない（基準に当たるなら major 以上で直す）")
            if e.get("waive_kind") not in review_criteria.WAIVE_KINDS:
                why.append(f"waived には waive_kind（{'/'.join(review_criteria.WAIVE_KINDS)}）が要る: "
                           f"{e.get('waive_kind')!r}")
        c = e.get("criterion")
        if c not in (None, "") and (isinstance(c, bool) or c not in review_criteria.CRITERION_NUMBERS):
            why.append(f"criterion は {'/'.join(map(str, review_criteria.CRITERION_NUMBERS))} のどれか: {c!r}")
        elif c == review_criteria.FOCUS_CRITERION and not focus_names:
            why.append("criterion 3 はレビューの重点の宣言があるとき（review_focus が空でない）だけ")
        if why:
            bad.append({"index": i, "thread_id": e.get("thread_id"), "path": e.get("path"),
                        "line": e.get("line"), "result": "invalid", "reason": "; ".join(why)})
    return bad


def focus_names(d: dict) -> list[str]:
    names = d.get("review_focus")
    return [n for n in names if isinstance(n, str) and n.strip()] if isinstance(names, list) else []


def pick(e: dict, *keys) -> dict:
    return {k: e.get(k) for k in keys}


def build_result(pr: int, d: dict, commit: str | None, ci_status: str, failed_names: list[str]) -> dict:
    decs = d["decisions"]
    fixed = [e for e in decs if e["decision"] == "fixed"]
    by = {s: 0 for s in SEVERITIES}
    for e in fixed:
        by[e["severity"]] += 1
    resolved = [pick(e, "thread_id", "comment_id", "path", "line") for e in fixed]
    deferred = []
    names = focus_names(d)
    for e in decs:
        if e["decision"] == "waived":
            reply = review_criteria.waiver_reply(e["waive_kind"], names)
            deferred.append({**pick(e, "comment_id", "thread_id", "path", "line", "severity", "category", "summary"),
                             "reason_for_deferral": reply, "reply": reply, "resolve": True,
                             "waived": e["waive_kind"]})
        elif e["decision"] == "deferred":
            deferred.append({**pick(e, "comment_id", "thread_id", "path", "line", "severity", "category", "summary"),
                             "reason_for_deferral": e.get("reason")})
        elif e["decision"] == "separate_pr":
            deferred.append({**pick(e, "comment_id", "thread_id", "path", "line", "severity", "category", "summary"),
                             "reason_for_deferral": f"別 PR で対応（{e['issue']}）" + (
                                 f": {e['reason']}" if e.get("reason") else ""),
                             "issue": e["issue"], "resolve": True})
    rejected = [{**pick(e, "comment_id", "thread_id", "path", "line", "severity", "category", "summary"),
                 "reason_for_rejection": e.get("reason")} for e in decs if e["decision"] == "rejected"]
    return {"pr": pr, "fix_commit": commit, "ci_status": ci_status, "ci_failed_checks": failed_names,
            "ci_note": d.get("ci_note"), "fixed_count": len(fixed), "by_severity": by,
            "resolved_threads": resolved, "deferred": deferred, "rejected": rejected}


def sync_pr_body(pr: int, script: str | None, repo: str | None) -> dict:
    """設計 PR の本文の節を揃える。終了コードの意味はスクリプトの冒頭が正本。"""
    path = Path(script) if script else PLUGIN_ROOT / "scripts" / "pr-body-decisions.sh"
    if not path.is_file():
        return {"name": "pr-body-decisions", "result": "missing", "code": None, "reason": f"無い: {path}"}
    cmd = ["bash", str(path), "sync", str(pr)] + (["--repo", repo] if repo else [])
    p = run(cmd, check=False)
    label = {0: "synced", 1: "mismatch", 2: "unreadable", 3: "invalid_call"}.get(p.returncode, "failed")
    return {"name": "pr-body-decisions", "result": label, "code": p.returncode,
            "reason": (p.stderr.strip() or p.stdout.strip())[:300]}


def cmd_finalize(a):
    d = load_decisions(a.decisions)
    pr = a.pr or d.get("pr")
    if not isinstance(pr, int):
        raise StepError("PR 番号が無い（--pr か JSON の pr）", EXIT_UNREADABLE)
    bad = check_decisions(d["decisions"], focus_names(d))
    if bad:
        emit(result(TOOL, "stopped", f"振り分けに誤りが {len(bad)} 件", bad, {"pr": pr},
                    next=f"{a.decisions} の decision / reason / severity / criterion / waive_kind を直して打ち直す"))
    commit = d.get("fix_commit")
    has_fixed = any(e["decision"] == "fixed" for e in d["decisions"])
    # CI の失敗を直したコミットは、指摘の fixed が 0 件でも送る
    keep = has_fixed or d.get("ci_fixed") is True
    dropped = None
    if not keep and commit:
        # 直したものが無ければ送るコミットも無い（I4）。push と CI を起こさない
        dropped = {"name": "fix-commit", "result": "dropped",
                   "reason": f"fixed が 0 件で CI の修正も無いため {commit} を捨てた"}
        commit = None
    if not commit and keep:
        root = git_root(a.root)
        commit = git(root, "rev-parse", "--short", "HEAD").stdout.strip()
    if d.get("ci_fixed") is True:
        # 照会できるのは送信前の head で、直した失敗が残って見える。送った後の CI を待つ
        ci_status, failed = "PENDING", []
    else:
        ci_status, failed = ci_snapshot(pr)
    res = build_result(pr, d, commit, ci_status, [str(c.get("name")) for c in failed])
    out = Path(a.out) if a.out else result_dir() / f"fix-pr{pr}-result.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    sync = {"name": "pr-body-decisions", "result": "skipped", "code": None} if a.no_sync else \
        sync_pr_body(pr, a.sync_script, getattr(a, "repo", None))
    items = [{"name": "result", "path": str(out)}, sync] + ([dropped] if dropped else [])
    items += [{"name": c.get("name"), "result": "ci_failed", "state": c.get("state")} for c in failed]
    waived = sum(1 for e in res["deferred"] if e.get("waived"))
    metrics = {"pr": pr, "fixed": res["fixed_count"], "deferred": len(res["deferred"]), "waived": waived,
               "rejected": len(res["rejected"]), "by_severity": res["by_severity"],
               "ci_status": ci_status, "pr_body_decisions_code": sync["code"]}
    summary = (f"修正 {res['fixed_count']} 件 / 見送り {len(res['deferred'])} 件（うち基準外 {waived} 件）/ "
               f"却下 {len(res['rejected'])} 件 / "
               f"CI {ci_status} / 本文の節 {sync['result']}")
    if sync["result"] == "invalid_call":
        emit(result(TOOL, "stopped", summary + "（pr-body-decisions.sh の呼び出しが誤り）", items, metrics,
                    next="fix-steps.py の sync_pr_body の呼び出しを直す"))
    emit(result(TOOL, "ok", summary, items, metrics,
                next=f"python3 {PLUGIN_ROOT / 'scripts' / 'lib' / 'result_posts.py'} fix --pr {pr} --result {out}"))


def cmd_remaining(a):
    pr = a.pr
    repo = repo_of(a)
    threads = threads_with_first_comment(repo, pr)
    kept = set()
    res_path = Path(a.result) if a.result else result_dir() / f"fix-pr{pr}-result.json"
    if res_path.is_file():
        try:
            res = json.loads(res_path.read_text(encoding="utf-8"))
        except ValueError:
            raise StepError(f"戻り値ファイルを読めない: {res_path}", EXIT_UNREADABLE)
        for e in (res.get("deferred") or []) + (res.get("rejected") or []):
            if isinstance(e, dict) and e.get("thread_id") and not e.get("resolve"):
                kept.add(e["thread_id"])
    leftover = [t for t in threads if t["thread_id"] not in kept]
    items = [{**pick(t, "thread_id", "comment_id", "path", "line"),
              "result": "kept" if t["thread_id"] in kept else "unresolved"} for t in threads]
    metrics = {"pr": pr, "unresolved": len(threads), "kept": len(kept), "leftover": len(leftover)}
    if leftover:
        emit(result(TOOL, "stopped", f"見送り・却下の外に未解決が {len(leftover)} 件残っている", items, metrics,
                    next="残ったスレッドへ対応するか、見送り・却下として戻り値へ入れて送り直す"))
    emit(result(TOOL, "ok", f"未解決 {len(threads)} 件（見送り・却下で残したもの {len(kept)} 件）", items, metrics))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("context", help="PR の文脈を 1 ファイルへ集め、振り分けの雛形を書く")
    s.add_argument("pr", type=int)
    s.add_argument("--repo")
    s.add_argument("--root", help="レビューの重点の宣言を読む作業ツリー（既定はカレントの作業ツリーの根）")
    s.set_defaults(func=cmd_context)
    s = sub.add_parser("finalize", help="振り分けから戻り値ファイルを組む")
    s.add_argument("--decisions", required=True)
    s.add_argument("--pr", type=int)
    s.add_argument("--root", help="修正をコミットした作業ツリー（fix_commit を HEAD から採るとき）")
    s.add_argument("--out", help="戻り値ファイルの置き場所（既定 $TMP_DIR/fix-pr<PR>-result.json）")
    s.add_argument("--repo")
    s.add_argument("--no-sync", action="store_true", help="本文の節の揃えを行わない")
    s.add_argument("--sync-script", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_finalize)
    s = sub.add_parser("remaining", help="返信と決着の後に未解決を数え直す")
    s.add_argument("pr", type=int)
    s.add_argument("--repo")
    s.add_argument("--result", help="戻り値ファイル（既定 $TMP_DIR/fix-pr<PR>-result.json）")
    s.set_defaults(func=cmd_remaining)
    main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
