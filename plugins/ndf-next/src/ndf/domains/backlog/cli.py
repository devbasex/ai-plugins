"""`ndf issues` の副命令（課題の棚卸しと、解消する Issue を閉じる操作）。

    ndf issues candidates [--add 12,34] [--limit 10] [--depth diff|review] [--since-ref <ref>]
    ndf issues rank --scores <JSON> [--capacity N] [--milestones <題>,<題>] [--approved 12,34] [--depth diff|review]
    ndf issues apply --plan <JSON> [--run-id <id>]
    ndf issues revert --run <id>
    ndf issues depth-limits <review|overhaul>
    ndf issues depth-record --depth <review|overhaul> --event <timed_out|ran> [--left 12,34]
    ndf issues settle --file <JSON> [--base <ブランチ>] [--dry-run]

どの副命令も `--repo`（省けば origin のリモート）と `--state-dir`（省けばメインディレクトリの置き場）を受ける。
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from ndf import cli as top
from ndf.adapters import github
from ndf.adapters.git import Git, GitError
from ndf.contracts import causes, result
from ndf.domains.backlog import (
    collect,
    depth,
    estimates_decide,
    issues_gh,
    model,
    plan_decide,
    rank_decide,
    reparent,
    settle,
    settle_decide,
    state,
)
from ndf.domains.backlog import selection_decide as sel

TOOL = top.tool_of(__name__)
DEFAULT_LIMIT = 10


class Stop(Exception):
    """副命令を止める。`kind` は止まった理由の種類。"""

    def __init__(self, kind: str, detail: str, items: Optional[list] = None) -> None:
        super().__init__(detail)
        self.kind = kind
        self.items = items


def hub(repo: str) -> github.GitHub:
    """`repo` への GitHub の読み書き（テストが差し替える）。"""
    return github.GitHub(repo, github.client())


def numbers(text: str) -> list[int]:
    try:
        return [int(x) for x in text.replace(" ", "").split(",") if x]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"番号の並びはカンマ区切りの整数で書く: {text!r}") from exc


def _read_json(path: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Stop("invalid", f"{path} を JSON として読めない: {exc}") from exc


def _repo(a: argparse.Namespace) -> str:
    return a.repo if a.repo else Git(Path.cwd()).remote_repo()


def _sd(a: argparse.Namespace, repo: str) -> Path:
    return state.state_dir(Path.cwd(), repo, a.state_dir)


# ---------------- candidates ----------------


def _carried(sd: Path) -> list[int]:
    """前回の候補の収集の持ち越しと、前回の深さの打ち切りの残り。"""
    prev = state.read(sd, "backlog-candidates", {})
    metrics = prev["metrics"] if isinstance(prev, dict) and isinstance(prev.get("metrics"), dict) else {}
    deferred = metrics["deferred"] if isinstance(metrics.get("deferred"), list) else []
    return sorted({n for n in deferred if isinstance(n, int)} | set(depth.left_over(sd)))


def _candidate_item(issue: dict[str, Any], routes: set[str]) -> dict[str, Any]:
    n = issue["number"]
    return {"kind": "issue", "name": f"#{n}", "result": "candidate", "number": n, "title": issue["title"],
            "routes": [r for r in sel.ROUTES if r in routes], "updated_at": issue["updated_at"],
            "digest": model.snapshot_digest(issue)}  # fmt: skip


def cmd_candidates(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    sd = _sd(a, repo)
    carried = _carried(sd)
    issues = issues_gh.open_issues(hub(repo))
    by_num = {i["number"]: i for i in issues}
    stored = state.read(sd, "backlog-estimates", {})
    found = collect.collect(issues, Path.cwd(), collect.Ask(a.since_ref, a.add, carried, a.depth), stored)
    picked = sel.select(found.routes, a.limit, a.depth, {n: i["updated_at"] for n, i in by_num.items()})
    items = [_candidate_item(by_num[n], found.routes[n]) for n in picked.keep]
    counts = sel.by_route(found.routes, picked.keep)
    notes = found.notes + (
        [f"--add の {picked.manual_over} 件が上限 {a.limit} 件を超えたが、すべて候補に残した"] if picked.manual_over else []
    )
    metrics = {"repo": repo, "open": len(issues), "candidates": len(picked.keep), "limit": a.limit, "depth": a.depth,
               "since_ref": a.since_ref, "deferred": picked.deferred, "carry": picked.carry,
               "carried": sorted(set(carried) & set(by_num)), "by_route": counts, "notes": notes}  # fmt: skip
    state.write(sd, "backlog-candidates", {"metrics": metrics, "items": items})
    summary = f"候補 {len(picked.keep)} 件（open {len(issues)} 件中）: " + "・".join(f"{r} {c}" for r, c in counts.items() if c)
    if picked.deferred:
        summary += f"。上限 {a.limit} 件を超えた {len(picked.deferred)} 件は次の回が持ち越しとして拾う"
    return result.result(TOOL, result.OK, summary, items, metrics)


# ---------------- rank ----------------


def _given(path: Optional[str]) -> dict[int, Any]:
    if not path:
        return {}
    got = _read_json(path)
    rows = got["issues"] if isinstance(got, dict) and "issues" in got else None
    if not isinstance(rows, list):
        raise Stop("invalid", '見積の入力は {"issues": [...]} の形で書く')
    return {r["number"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("number"), int)}


def _previous_rows(rank: Any) -> dict[int, Any]:
    tables = rank["milestones"] if isinstance(rank, dict) and isinstance(rank.get("milestones"), list) else []
    out: dict[int, Any] = {}
    for t in tables:
        for r in t["rows"]:
            out.setdefault(r["number"], r)
    return out


def _edges(raws: list[Any], open_numbers: set[int]) -> frozenset[tuple[int, int]]:
    """見積の `depends_on`（番号の列）から、open の課題どうしの依存の辺。"""
    out = set()
    for raw in raws:
        deps = raw["depends_on"] if isinstance(raw, dict) and isinstance(raw.get("depends_on"), list) else []
        out |= {(raw["number"], b) for b in deps if b in open_numbers and raw["number"] in open_numbers}
    return frozenset(out)


def _config(a: argparse.Namespace) -> model.Config:
    try:
        decl = depth.declaration(depth.declaration_path(Path.cwd()))
    except depth.DepthError as exc:
        raise Stop("invalid", str(exc)) from exc
    quality = decl["quality"] if isinstance(decl.get("quality"), dict) else None
    if quality is not None:
        quality = {"label": "quality", "ratio": 0.2, "min": 1, **quality}
    return model.Config(capacity=a.capacity, quality=quality)


def _rank_board(
    a: argparse.Namespace, sd: Path, issues: list[dict[str, Any]]
) -> tuple[rank_decide.Board, list[dict[str, Any]], dict[str, Any]]:
    cfg = _config(a)
    given = _given(a.scores)
    stored = state.read(sd, "backlog-estimates", {})
    previous = _previous_rows(state.read(sd, "backlog-rank", None))
    digests = {i["number"]: model.snapshot_digest(i) for i in issues}
    review = digests if a.depth == model.REVIEW else None
    ests, excluded = estimates_decide.estimates(list(digests), given, stored, previous, cfg, review)
    by_num = {i["number"]: i for i in issues}
    order = rank_decide.milestone_order({i["milestone"] for i in issues if i["milestone"]}, a.milestones)
    raws = [*given.values(), *(v for v in stored.values() if isinstance(v, dict))]
    board = rank_decide.Board(by_num, ests, cfg, order, edges=_edges(raws, set(by_num)),
                              rejected=tuple(state.read(sd, "backlog-rejected", [])), approved=frozenset(a.approved))  # fmt: skip
    kept = {**stored, **{str(n): {**given[n], "number": n} for n in ests if n in given}}
    return board, excluded, kept


def cmd_rank(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    sd = _sd(a, repo)
    issues = issues_gh.open_issues(hub(repo))
    board, excluded, kept = _rank_board(a, sd, issues)
    try:
        got = rank_decide.compute(board)
    except rank_decide.CycleError as exc:
        raise Stop("invalid", str(exc), [{"number": n} for n in exc.numbers]) from exc
    metrics = {"repo": repo, "depth": a.depth, "order": board.order, "forward": got["forward"], "backward": got["backward"],
               "dropped": got["dropped"], "rejected_stale": got["rejected_stale"], "excluded": excluded}  # fmt: skip
    metrics["digest"] = model.stable_digest({"metrics": metrics, "milestones": got["milestones"]})
    state.write(sd, "backlog-rank", {"metrics": metrics, "milestones": got["milestones"]})
    state.write(sd, "backlog-estimates", kept)
    summary = f"順位を {len(board.estimates)} 件で算出した: 前倒し {len(got['forward'])} 件・後ろ倒し {len(got['backward'])} 件・外した {len(excluded)} 件"
    if a.depth == model.REVIEW and excluded:
        gaps = [{"kind": "estimate", "name": f"#{e['number']}", "reason": e["reason"]} for e in excluded]
        return result.stopped(TOOL, "incomplete", summary + "。見直しは外した課題を採点し直して打ち直す", gaps=gaps, metrics=metrics)
    return result.result(TOOL, result.OK, summary, got["milestones"], metrics)


# ---------------- apply / revert ----------------


def _checked_rank(sd: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """plan が照らす rank.json の metrics。却下か移動を持つ plan は rank.json と同じ digest を要る。"""
    if not (plan.get("rejected") or plan.get("reschedule")):
        return {}
    rank = state.read(sd, "backlog-rank", None)
    if not isinstance(rank, dict) or not isinstance(rank.get("metrics"), dict):
        raise Stop("precondition", "rank.json が無い（rank を打ってから plan を作る）")
    if plan.get("rank") != rank["metrics"].get("digest"):
        raise Stop("invalid", "plan の rank が rank.json の digest と違う（rank を打ち直して plan を作り直す）")
    checked = plan_decide.check(plan, rank["metrics"])
    if checked.errors:
        raise Stop("invalid", "plan の誤り（1 件も書いていない）: " + " / ".join(checked.errors))
    return rank["metrics"]


def _reschedule(gh: github.GitHub, plan: dict[str, Any], holds: set[int]) -> tuple[list[int], list[dict[str, Any]]]:
    moves = [e for e in plan["reschedule"] if e["number"] not in holds] if "reschedule" in plan else []
    if not moves:
        return [], []
    titles = issues_gh.milestones(gh)
    done: list[int] = []
    failed: list[dict[str, Any]] = []
    for e in moves:
        if e["milestone"] not in titles:
            failed.append({"number": e["number"], "error": f"マイルストーン {e['milestone']!r} が無い"})
            continue
        try:
            issues_gh.set_milestone(gh, e["number"], titles[e["milestone"]])
        except github.GitHubError as exc:
            failed.append({"number": e["number"], "error": str(exc)})
            continue
        done.append(e["number"])
    return done, failed


def cmd_apply(a: argparse.Namespace) -> dict[str, Any]:
    plan = _read_json(a.plan)
    if not isinstance(plan, dict):
        raise Stop("invalid", "plan は JSON のオブジェクトで書く")
    unknown = sorted(set(plan) - {"rank", "rejected", "reschedule", "reparent"})
    if unknown:
        raise Stop("invalid", f"plan の知らない鍵（1 件も書いていない）: {', '.join(unknown)}")
    repo = _repo(a)
    sd = _sd(a, repo)
    reparent.entries_of(plan)
    metrics = _checked_rank(sd, plan)
    holds = plan_decide.check(plan, metrics).holds if metrics else set()
    if metrics:
        state.write(sd, "backlog-rejected", plan_decide.rejected_after(state.read(sd, "backlog-rejected", []), plan, metrics))
    gh = hub(repo)
    moved, move_failed = _reschedule(gh, plan, holds)
    run_id = a.run_id if a.run_id else uuid.uuid4().hex
    parents = reparent.apply(gh, sd, plan, run_id)
    failed = move_failed + parents["failed"]
    out = {"repo": repo, "moved": moved, "held": sorted(holds), "reparented": parents["applied"], "failed": failed,
           "before": parents["before"]}  # fmt: skip
    if failed:
        raise Stop("incomplete", f"反映の {len(failed)} 件が失敗した（ほかは書いた。戻すは ndf issues revert --run {run_id}）", failed)
    summary = f"反映した: 移動 {len(moved)} 件・保留 {len(holds)} 件・付け直し {len(parents['applied'])} 件"
    return result.result(TOOL, result.OK, summary, metrics=out, run_id=run_id)


def cmd_revert(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    got = reparent.revert(hub(repo), _sd(a, repo), a.run)
    if got["failed"]:
        raise Stop("incomplete", f"戻しの {len(got['failed'])} 件が失敗した", got["failed"])
    return result.result(TOOL, result.OK, f"付け直しを {len(got['reverted'])} 件戻した", metrics=got, run_id=a.run)


# ---------------- depth / settle ----------------


def cmd_depth_limits(a: argparse.Namespace) -> dict[str, Any]:
    seconds = depth.depth_seconds(depth.declaration(depth.declaration_path(Path.cwd())), a.depth)
    return result.result(TOOL, result.OK, f"{a.depth} の 1 回の秒の上限は {seconds} 秒", metrics={"depth": a.depth, "seconds": seconds})


def cmd_depth_record(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    row = depth.record(_sd(a, repo), a.depth, a.event, a.left)
    return result.result(TOOL, result.OK, f"{a.depth} の {a.event} を記録した（残り {len(row['left'])} 件）", metrics=row)


def cmd_settle(a: argparse.Namespace) -> dict[str, Any]:
    try:
        rows, bad = settle_decide.parse(_read_json(a.file))
        for row in rows:
            if row.kind == settle_decide.IMPROVED:
                settle_decide.test_target(row.evidence["test"])
    except ValueError as exc:
        raise Stop("invalid", str(exc)) from exc
    repo = _repo(a)
    gh = hub(repo)
    base = a.base if a.base else settle.default_branch(gh)
    items = [{"kind": "issue", "name": f"#{b['issue']}", "result": "unverified", **b} for b in bad]
    items += settle.settle(gh, rows, base, Path.cwd(), a.dry_run)
    left = [i for i in items if i["result"] == "unverified"]
    done = sum(1 for i in items if i["result"] in ("closed", "verified"))
    summary = f"証拠を確かめた Issue {done} 件を{'確かめた（閉じていない）' if a.dry_run else '閉じた'}"
    if left:
        raise Stop("refused", f"{summary}。証拠を確かめられない {len(left)} 件は閉じていない（IY5）", items)
    return result.result(TOOL, result.OK, summary, items, {"repo": repo, "base": base})


HANDLERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "candidates": cmd_candidates,
    "rank": cmd_rank,
    "apply": cmd_apply,
    "revert": cmd_revert,
    "depth-limits": cmd_depth_limits,
    "depth-record": cmd_depth_record,
    "settle": cmd_settle,
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="<所有者>/<名前>（省けば origin）")
    common.add_argument("--state-dir", help="状態の置き場（省けば NDF_UPKEEP_STATE_DIR かメインディレクトリの .ndf/backlog/state）")
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="課題の棚卸しと、解消する Issue を閉じる操作")
    sub = parser.add_subparsers(dest="action", required=True)
    c = sub.add_parser("candidates", parents=[common], help="候補を集める（手順 1）")
    c.add_argument("--add", type=numbers, default=[], help="必ず候補に入れる番号（カンマ区切り。上限で切らない）")
    c.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    c.add_argument("--depth", choices=(model.DIFF, model.REVIEW), default=model.DIFF)
    c.add_argument("--since-ref", help="差分の起点（タグか SHA）")
    r = sub.add_parser("rank", parents=[common], help="順位を算出する")
    r.add_argument("--scores", help='見積の入力（{"issues": [...]}）')
    r.add_argument("--capacity", type=int)
    r.add_argument(
        "--milestones", type=lambda t: [x for x in t.split(",") if x], default=None, help="着手の順のマイルストーンの題（カンマ区切り）"
    )
    r.add_argument("--approved", type=numbers, default=[])
    r.add_argument("--depth", choices=(model.DIFF, model.REVIEW), default=model.DIFF)
    ap = sub.add_parser("apply", parents=[common], help="plan を反映する（手順 3）")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--run-id")
    rv = sub.add_parser("revert", parents=[common], help="付け直しを戻す")
    rv.add_argument("--run", required=True)
    dl = sub.add_parser("depth-limits", parents=[common], help="深さの 1 回の秒の上限")
    dl.add_argument("depth", choices=depth.DEPTHS)
    dr = sub.add_parser("depth-record", parents=[common], help="深さの実行を記録する（打ち切りは残りの番号を持つ）")
    dr.add_argument("--depth", choices=depth.DEPTHS, required=True)
    dr.add_argument("--event", choices=depth.EVENTS, required=True)
    dr.add_argument("--left", type=numbers, default=[])
    st = sub.add_parser("settle", parents=[common], help="解消する Issue を、閉じる証拠を確かめてから閉じる")
    st.add_argument("--file", required=True, help='入力（{"settle": [...]}）')
    st.add_argument("--base", help="基底のブランチ（省けば既定のブランチ）")
    st.add_argument("--dry-run", action="store_true", help="確かめるだけで閉じない")
    return parser


def _github_kind(exc: github.GitHubError) -> str:
    if exc.quota is not None:
        return "infra_wait"
    return "transient" if exc.status == 0 or exc.status >= 500 else "precondition"


def run(a: argparse.Namespace) -> dict[str, Any]:
    """副命令を打ち、結果を返す。分かっている失敗は止まった理由の種類へ写す。"""
    try:
        return HANDLERS[a.action](a)
    except Stop as exc:
        return result.stopped(TOOL, exc.kind, str(exc), items=exc.items)
    except state.StateError as exc:
        return result.stopped(TOOL, exc.kind, str(exc))
    except (depth.DepthError, reparent.ReparentError) as exc:
        return result.stopped(TOOL, "invalid", str(exc))
    except GitError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    except github.GitHubError as exc:
        return result.stopped(TOOL, _github_kind(exc), str(exc), retry_after=exc.retry_after)


def main(argv: list[str]) -> int:
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    return result.emit(run(a))
