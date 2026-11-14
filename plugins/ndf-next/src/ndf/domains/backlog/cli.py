"""`ndf issues` の副命令（課題の棚卸しと、解消する Issue を閉じる操作）。

    ndf issues candidates [--add 12,34] [--limit 10] [--depth diff|review] [--since-ref <ref>]
    ndf issues rank --scores <JSON> [--sizes <JSON>] [--capacity N] [--milestones <題>,<題>] [--approved 12,34] [--depth diff|review]
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
    actions,
    actions_decide,
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
    table_decide,
)
from ndf.domains.backlog import tables as tables_mod
from ndf.domains.backlog import selection_decide as sel

TOOL = top.tool_of(__name__)
DEFAULT_LIMIT = 10
PLAN_KEYS = {"rank", "rejected", "reschedule", "reparent", "actions", "create"}


class Stop(Exception):
    """副命令を止める。`kind` は止まった理由の種類。`incomplete` は失敗した行（`failed`）を欠け（gaps）として持つ。"""

    def __init__(self, kind: str, detail: str, items: Optional[list] = None, failed: Optional[list] = None) -> None:
        super().__init__(detail)
        self.kind = kind
        self.items = items
        self.failed = failed if failed is not None else []

    def gaps(self) -> list[dict[str, str]]:
        return [{"kind": "write", "name": _row_name(f), "reason": str(f["error"]) if "error" in f else "失敗した"} for f in self.failed]


def _row_name(row: dict[str, Any]) -> str:
    for key, fmt in (("number", "#{}"), ("title", "{}"), ("milestone", "{}")):
        if key in row and row[key] is not None:
            return fmt.format(row[key])
    return "?"


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
    gh = hub(repo)
    issues = issues_gh.open_issues(gh)
    by_num = {i["number"]: i for i in issues}
    stored = state.read(sd, "backlog-estimates", {})
    closed = tuple(issues_gh.closed_since(gh, collect.ref_time(Git(Path.cwd()), a.since_ref))) if a.since_ref else ()
    found = collect.collect(issues, Path.cwd(), collect.Ask(a.since_ref, a.add, carried, a.depth, closed), stored)
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
        raise Stop("invalid", f'{path} は {{"issues": [...]}} の形で書く')
    return {r["number"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("number"), int)}


def _sizes(path: Optional[str]) -> dict[int, Any]:
    """見積もりの記録から決めた Size（{"issues": [{"number", "size"}]}）。"""
    return {n: r["size"] for n, r in _given(path).items() if "size" in r}


def _previous_rows(rank: Any) -> dict[int, Any]:
    tables = rank["milestones"] if isinstance(rank, dict) and isinstance(rank.get("milestones"), list) else []
    out: dict[int, Any] = {}
    for t in tables:
        for r in t["rows"]:
            out.setdefault(r["number"], r)
    return out


def _edges(raws: list[Any], issues: list[dict[str, Any]]) -> frozenset[tuple[int, int]]:
    """open の課題どうしの依存の辺。見積の `depends_on`（番号の列）と、親が open の子を待つ辺（親 → 子）。

    見積が子 → 親の依存を書いていれば、輪にしないため親 → 子の辺を足さない。
    """
    open_numbers = {i["number"] for i in issues}
    out = set()
    for raw in raws:
        deps = raw["depends_on"] if isinstance(raw, dict) and isinstance(raw.get("depends_on"), list) else []
        out |= {(raw["number"], b) for b in deps if b in open_numbers and raw["number"] in open_numbers}
    subs = {(i["parent"], i["number"]) for i in issues if i["parent"] in open_numbers}
    return frozenset(out | {(p, k) for p, k in subs if (k, p) not in out})


def _sub_issues(issues: list[dict[str, Any]]) -> dict[int, list[int]]:
    """open の親 → open の子の番号の列（大きい課題の枠が子を切り出しに当てる）。"""
    open_numbers = {i["number"] for i in issues}
    out: dict[int, list[int]] = {}
    for i in issues:
        if i["parent"] in open_numbers:
            out.setdefault(i["parent"], []).append(i["number"])
    return out


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
) -> tuple[rank_decide.Board, list[dict[str, Any]], dict[str, Any], list[int]]:
    cfg = _config(a)
    given = _given(a.scores)
    stored = state.read(sd, "backlog-estimates", {})
    previous = _previous_rows(state.read(sd, "backlog-rank", None))
    digests = {i["number"]: model.snapshot_digest(i) for i in issues}
    review = digests if a.depth == model.REVIEW else None
    ests, excluded = estimates_decide.estimates(list(digests), given, stored, previous, cfg, review)
    try:
        ests, sized = estimates_decide.overlay_sizes(ests, _sizes(a.sizes), cfg)
    except model.EstimateError as exc:
        raise Stop("invalid", str(exc)) from exc
    by_num = {i["number"]: i for i in issues}
    order = rank_decide.milestone_order({i["milestone"] for i in issues if i["milestone"]}, a.milestones)
    raws = [*given.values(), *(v for v in stored.values() if isinstance(v, dict))]
    board = rank_decide.Board(by_num, ests, cfg, order, sub_issues=_sub_issues(issues), edges=_edges(raws, issues),
                              rejected=tuple(state.read(sd, "backlog-rejected", [])), approved=frozenset(a.approved))  # fmt: skip
    kept = {**stored, **{str(n): {**given[n], "number": n} for n in ests if n in given}}
    return board, excluded, kept, sized


def cmd_rank(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    sd = _sd(a, repo)
    issues = issues_gh.open_issues(hub(repo))
    board, excluded, kept, sized = _rank_board(a, sd, issues)
    try:
        got = rank_decide.compute(board)
    except rank_decide.CycleError as exc:
        raise Stop("invalid", str(exc), [{"number": n} for n in exc.numbers]) from exc
    for row in (r for t in got["milestones"] for r in t["rows"]):
        row["sources"]["size"] = table_decide.SIZE_ESTIMATE if row["number"] in sized else "llm"
    metrics = {"repo": repo, "depth": a.depth, "order": board.order, "forward": got["forward"], "backward": got["backward"],
               "dropped": got["dropped"], "rejected_stale": got["rejected_stale"], "excluded": excluded, "sized": sized}  # fmt: skip
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
    """plan が照らす rank.json の metrics。順位（rank）・却下・移動を持つ plan は rank.json と同じ digest を要る。"""
    if not (plan.get("rank") or plan.get("rejected") or plan.get("reschedule")):
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
    unknown = sorted(set(plan) - PLAN_KEYS)
    if unknown:
        raise Stop("invalid", f"plan の知らない鍵（1 件も書いていない）: {', '.join(unknown)}")
    wrong = actions_decide.plan_errors(plan)
    if wrong:
        raise Stop("invalid", "plan の誤り（1 件も書いていない）: " + " / ".join(wrong))
    repo = _repo(a)
    sd = _sd(a, repo)
    reparent.entries_of(plan)
    metrics = _checked_rank(sd, plan)
    holds = plan_decide.check(plan, metrics).holds if metrics else set()
    ledger = actions.Ledger(sd)
    if metrics:
        state.write(sd, "backlog-rejected", plan_decide.rejected_after(state.read(sd, "backlog-rejected", []), plan, metrics))
    gh = hub(repo)
    acted = actions.apply_actions(gh, ledger, plan)
    made = actions.create(gh, ledger, plan)
    moved, move_failed = _reschedule(gh, plan, holds)
    run_id = a.run_id if a.run_id else uuid.uuid4().hex
    parents = reparent.apply(gh, sd, plan, run_id)
    tables = tables_mod.write(gh, state.read(sd, "backlog-rank", {})["milestones"]) if plan.get("rank") else {}
    return _apply_outcome(repo, run_id, str(Path(a.plan).resolve()), {"acted": acted, "made": made, "moved": moved, "move_failed": move_failed,
                                         "holds": holds, "parents": parents, "tables": tables})  # fmt: skip


def _apply_outcome(repo: str, run_id: str, plan_path: str, got: dict[str, Any]) -> dict[str, Any]:
    """反映の結果から status・要約・metrics を作る。失敗は止め、承認待ちと照合で飛ばした課題は gate で返す。"""
    acted, made, parents, tables = got["acted"], got["made"], got["parents"], got["tables"]
    buckets = acted["buckets"]
    failed = [*got["move_failed"], *parents["failed"], *acted["failed"],
              *({"title": f["title"], "error": f["reason"]} for f in made["failed"]),
              *({"milestone": t, "error": "順位の表を書けない"} for t in (tables["failed"] if tables else []))]  # fmt: skip
    out = {"repo": repo, **buckets, "closed": acted["closed"], "created": made["created"], "create_rejected": made["rejected"],
           "moved": got["moved"], "held": sorted(got["holds"]), "reparented": parents["applied"], "failed": failed,
           "before": parents["before"], "tables": tables}  # fmt: skip
    summary = (f"反映した: 判定の反映 {len(buckets[actions_decide.APPLIED])} 件（閉じた {len(acted['closed'])} 件）・"
               f"起票 {len(made['created'])} 件・移動 {len(got['moved'])} 件・保留 {len(got['holds'])} 件・"
               f"付け直し {len(parents['applied'])} 件・承認待ち {len(buckets[actions_decide.WAITING])} 件・"
               f"人へ返した {len(buckets[actions_decide.RETURNED_BUCKET])} 件")  # fmt: skip
    if failed:
        raise Stop("incomplete", f"{summary}。{len(failed)} 件が失敗した（同じ plan で打ち直せば済んだものは記録で飛ぶ。"
                   f"付け直しを戻すは ndf issues revert --run {run_id}）", acted["items"], failed)  # fmt: skip
    waiting, skipped = buckets[actions_decide.WAITING], buckets[actions_decide.SKIPPED]
    if waiting or skipped:
        nxt = []
        if waiting:
            nxt.append("承認を得た課題に \"approved\": true を付けて同じ plan で apply を打ち直す")
        if skipped:
            nxt.append("plan の後に書き換わった " + " ".join(f"#{n}" for n in skipped) + " を判定し直す")
        again = result.command(TOOL, "apply", "--repo", repo, "--plan", plan_path, "--run-id", run_id)
        out["next_steps"] = nxt
        return result.result(TOOL, result.GATE, summary + "。" + "。".join(nxt), acted["items"], out, run_id=run_id,
                             routes={"approved": again, "finish": None})  # fmt: skip
    return result.result(TOOL, result.OK, summary, acted["items"], out, run_id=run_id)


def cmd_revert(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    got = reparent.revert(hub(repo), _sd(a, repo), a.run)
    if got["failed"]:
        raise Stop("incomplete", f"戻しの {len(got['failed'])} 件が失敗した", got["failed"], got["failed"])
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
    r.add_argument("--sizes", help='見積もりの記録から決めた Size（{"issues": [{"number", "size"}]}。size は尺度の値か "over"）')
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
        extra = {"gaps": exc.gaps() or [{"kind": "write", "name": TOOL, "reason": str(exc)}]} if exc.kind == "incomplete" else {}
        return result.stopped(TOOL, exc.kind, str(exc), items=exc.items, **extra)
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
