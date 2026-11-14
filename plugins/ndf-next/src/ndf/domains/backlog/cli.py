"""`ndf issues` の副命令（課題の棚卸しと、解消する Issue を閉じる操作）。

    ndf issues candidates [--add 12,34] [--limit 10] [--depth diff|review] [--since-ref <ref>]
    ndf issues rank --scores <JSON> [--sizes <JSON>] [--capacity N] [--milestones <題>,<題>] [--approved 12,34] [--depth diff|review]
    ndf issues apply --plan <JSON> [--run-id <id>]
    ndf issues revert --run <id>
    ndf issues depth-limits <review|overhaul>
    ndf issues depth-record --depth <review|overhaul> --event <timed_out|ran> [--left 12,34]
    ndf issues due <review|overhaul> [--manual]
    ndf issues settle --file <JSON> [--base <ブランチ>] [--dry-run]

どの副命令も `--repo`（省けば origin のリモート）と `--state-dir`（省けばメインディレクトリの置き場）を受ける。
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from ndf import cli as top
from ndf.adapters import github
from ndf.adapters.git import Git, GitError
from ndf.contracts import causes, result
from ndf.domains.backlog import (
    actions,
    actions_decide,
    collect,
    depth,
    due_decide,
    issues_gh,
    model,
    plan_decide,
    rank_decide,
    reparent,
    settle,
    settle_decide,
    state,
)
from ndf.domains.backlog import tables as tables_mod
from ndf.domains.backlog import selection_decide as sel
from ndf.domains.backlog.cli_input import Stop, declaration, rank_board, read_json

TOOL = top.tool_of(__name__)
DEFAULT_LIMIT = 10
PLAN_KEYS = {"rank", "rejected", "reschedule", "reparent", "actions", "create"}


def hub(repo: str) -> github.GitHub:
    """`repo` への GitHub の読み書き（テストが差し替える）。"""
    return github.GitHub(repo, github.client())


def numbers(text: str) -> list[int]:
    try:
        return [int(x) for x in text.replace(" ", "").split(",") if x]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"番号の並びはカンマ区切りの整数で書く: {text!r}") from exc


def _repo(a: argparse.Namespace) -> str:
    return a.repo if a.repo else Git(Path.cwd()).remote_repo()


def _sd(a: argparse.Namespace, repo: str) -> Path:
    return state.state_dir(Path.cwd(), repo, a.state_dir)


def _previous_metrics(sd: Path) -> dict[str, Any]:
    """前回の候補の収集の metrics（形が違えば空）。"""
    prev = state.read(sd, "backlog-candidates", {})
    return prev["metrics"] if isinstance(prev, dict) and isinstance(prev.get("metrics"), dict) else {}


# ---------------- candidates ----------------


def _carried(sd: Path) -> tuple[list[int], frozenset[int]]:
    """前回の候補の収集の持ち越しと、前回の深さの打ち切りの残り。と、そのうち見積の欠けだけで持ち越した課題。"""
    metrics = _previous_metrics(sd)
    deferred = metrics["deferred"] if isinstance(metrics.get("deferred"), list) else []
    weak = metrics["deferred_unscored"] if isinstance(metrics.get("deferred_unscored"), list) else []
    left = set(depth.left_over(sd))
    return sorted({n for n in deferred if isinstance(n, int)} | left), frozenset(n for n in weak if isinstance(n, int) and n not in left)


def _candidate_item(issue: dict[str, Any], routes: set[str]) -> dict[str, Any]:
    n = issue["number"]
    return {"kind": "issue", "name": f"#{n}", "result": "candidate", "number": n, "title": issue["title"],
            "routes": [r for r in sel.ROUTES if r in routes], "updated_at": issue["updated_at"],
            "digest": model.snapshot_digest(issue)}  # fmt: skip


def cmd_candidates(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    sd = _sd(a, repo)
    carried, weak_carry = _carried(sd)
    gh = hub(repo)
    issues = issues_gh.open_issues(gh)
    by_num = {i["number"]: i for i in issues}
    stored = state.read(sd, "backlog-estimates", {})
    closed = tuple(issues_gh.closed_since(gh, collect.ref_time(Git(Path.cwd()), a.since_ref))) if a.since_ref else ()
    found = collect.collect(issues, Path.cwd(), collect.Ask(a.since_ref, a.add, carried, a.depth, closed), stored)
    picked = sel.select(found.routes, a.limit, a.depth, {n: i["updated_at"] for n, i in by_num.items()}, weak_carry)
    items = [_candidate_item(by_num[n], found.routes[n]) for n in picked.keep]
    counts = sel.by_route(found.routes, picked.keep)
    notes = found.notes + (
        [f"--add の {picked.manual_over} 件が上限 {a.limit} 件を超えたが、すべて候補に残した"] if picked.manual_over else []
    )
    metrics = {"repo": repo, "open": len(issues), "candidates": len(picked.keep), "limit": a.limit, "depth": a.depth,
               "since_ref": a.since_ref, "deferred": picked.deferred, "carry": picked.carry, "deferred_unscored": picked.unscored_only,
               "carried": sorted(set(carried) & set(by_num)), "by_route": counts, "notes": notes}  # fmt: skip
    state.write(sd, "backlog-candidates", {"metrics": metrics, "items": items})
    summary = f"候補 {len(picked.keep)} 件（open {len(issues)} 件中）: " + "・".join(f"{r} {c}" for r, c in counts.items() if c)
    if picked.deferred:
        summary += f"。上限 {a.limit} 件を超えた {len(picked.deferred)} 件は次の回が持ち越しとして拾う"
    return result.result(TOOL, result.OK, summary, items, metrics)


# ---------------- rank ----------------


def _rank_metrics(repo: str, a: argparse.Namespace, board: rank_decide.Board, got: dict[str, Any],
                  excluded: list[dict[str, Any]], sized: list[int]) -> dict[str, Any]:  # fmt: skip
    """順位の metrics と、metrics と表を合わせた digest（plan が照らす値）。"""
    metrics = {"repo": repo, "depth": a.depth, "order": board.order, "forward": got["forward"], "backward": got["backward"],
               "dropped": got["dropped"], "rejected_stale": got["rejected_stale"], "excluded": excluded, "sized": sized}  # fmt: skip
    metrics["digest"] = model.stable_digest({"metrics": metrics, "milestones": got["milestones"]})
    return metrics


def _save_rank(sd: Path, metrics: dict[str, Any], got: dict[str, Any], kept: dict[str, Any]) -> None:
    state.write(sd, "backlog-rank", {"metrics": metrics, "milestones": got["milestones"], "board": got["board"]})
    state.write(sd, "backlog-estimates", kept)


def cmd_rank(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    sd = _sd(a, repo)
    issues = issues_gh.open_issues(hub(repo))
    board, excluded, kept, sized = rank_board(a, sd, issues)
    try:
        got = rank_decide.compute(board)
    except rank_decide.CycleError as exc:
        raise Stop("invalid", str(exc), [{"number": n} for n in exc.numbers]) from exc
    metrics = _rank_metrics(repo, a, board, got, excluded, sized)
    _save_rank(sd, metrics, got, kept)
    summary = f"順位を {len(board.estimates)} 件で算出した: 前倒し {len(got['forward'])} 件・後ろ倒し {len(got['backward'])} 件・外した {len(excluded)} 件"
    if a.depth == model.REVIEW and excluded:
        gaps = [{"kind": "estimate", "name": f"#{e['number']}", "reason": e["reason"]} for e in excluded]
        return result.stopped(TOOL, "incomplete", summary + "。見直しは外した課題を採点し直して打ち直す", gaps=gaps, metrics=metrics)
    return result.result(TOOL, result.OK, summary, got["milestones"], metrics)


# ---------------- apply / revert ----------------


class _CheckedRank(NamedTuple):
    """plan が照らす rank.json の metrics と、照合で保留にした番号（移動を承認待ちで止める課題）と、表を組み直す値。"""

    metrics: dict[str, Any]
    holds: set[int]
    board: dict[str, Any]


def _checked_rank(sd: Path, plan: dict[str, Any]) -> _CheckedRank:
    """plan が照らす rank.json の metrics と保留。順位（rank）・却下・移動を持つ plan は rank.json と同じ digest を要る。"""
    if not (plan.get("rank") or plan.get("rejected") or plan.get("reschedule")):
        return _CheckedRank({}, set(), {})
    rank = state.read(sd, "backlog-rank", None)
    if not isinstance(rank, dict) or not isinstance(rank.get("metrics"), dict):
        raise Stop("precondition", "rank.json が無い（rank を打ってから plan を作る）")
    if not isinstance(rank.get("board"), dict):
        raise Stop("precondition", "rank.json に表を組み直す値が無い（rank を打ち直して plan を作り直す）")
    if plan.get("rank") != rank["metrics"].get("digest"):
        raise Stop("invalid", "plan の rank が rank.json の digest と違う（rank を打ち直して plan を作り直す）")
    checked = plan_decide.check(plan, rank["metrics"])
    if checked.errors:
        raise Stop("invalid", "plan の誤り（1 件も書いていない）: " + " / ".join(checked.errors))
    metrics = rank["metrics"]
    return _CheckedRank(metrics, checked.holds if metrics else set(), rank["board"])


def _read_plan(path: str) -> dict[str, Any]:
    """plan を読み、形を確かめる（オブジェクトであること・知らない鍵・actions と create の形）。"""
    plan = read_json(path)
    if not isinstance(plan, dict):
        raise Stop("invalid", "plan は JSON のオブジェクトで書く")
    unknown = sorted(set(plan) - PLAN_KEYS)
    if unknown:
        raise Stop("invalid", f"plan の知らない鍵（1 件も書いていない）: {', '.join(unknown)}")
    wrong = actions_decide.plan_errors(plan)
    if wrong:
        raise Stop("invalid", "plan の誤り（1 件も書いていない）: " + " / ".join(wrong))
    return plan


def cmd_apply(a: argparse.Namespace) -> dict[str, Any]:
    plan = _read_plan(a.plan)
    repo = _repo(a)
    sd = _sd(a, repo)
    reparent.entries_of(plan)
    metrics, holds, board = _checked_rank(sd, plan)
    ledger = actions.Ledger(sd)
    if metrics:
        state.write(sd, "backlog-rejected", plan_decide.rejected_after(state.read(sd, "backlog-rejected", []), plan, metrics))
    gh = hub(repo)
    acted = actions.apply_actions(gh, ledger, plan)
    made = actions.create(gh, ledger, plan)
    moves = actions.reschedule(gh, plan, holds, rank_decide.origin(board) if board else {})
    run_id = a.run_id if a.run_id else uuid.uuid4().hex
    parents = reparent.apply(gh, sd, plan, run_id)
    # 表は rank の時点の所属に、実際に済んだ移動を重ね、閉じた課題を除いて組み直す（退けた・保留の移動は動かさない）
    tables = tables_mod.write(gh, rank_decide.retable(board, moves.placed, set(acted["gone"]))) if plan.get("rank") else {}
    # sd は <置き場>/<repo> なので、打ち直しには置き場（親）を渡す
    again = ["--repo", repo, "--plan", str(Path(a.plan).resolve()), "--run-id", run_id, "--state-dir", str(sd.parent)]
    return _apply_outcome(repo, run_id, again, {"acted": acted, "made": made, "moved": moves.moved, "move_failed": moves.failed,
                                                "holds": holds, "parents": parents, "tables": tables})  # fmt: skip


def _apply_failures(got: dict[str, Any]) -> list[dict[str, Any]]:
    """移動・付け直し・判定・起票・表の失敗を 1 つの列にする。"""
    tables = got["tables"]
    return [*got["move_failed"], *got["parents"]["failed"], *got["acted"]["failed"],
            *({"title": f["title"], "error": f["reason"]} for f in got["made"]["failed"]),
            *({"milestone": t, "error": "順位の表を書けない"} for t in (tables["failed"] if tables else []))]  # fmt: skip


def _apply_summary(got: dict[str, Any]) -> str:
    acted, made, parents = got["acted"], got["made"], got["parents"]
    buckets = acted["buckets"]
    return (f"反映した: 判定の反映 {len(buckets[actions_decide.APPLIED])} 件（閉じた {len(acted['closed'])} 件）・"
            f"起票 {len(made['created'])} 件・移動 {len(got['moved'])} 件・保留 {len(got['holds'])} 件・"
            f"付け直し {len(parents['applied'])} 件・承認待ち {len(buckets[actions_decide.WAITING])} 件・"
            f"人へ返した {len(buckets[actions_decide.RETURNED_BUCKET])} 件")  # fmt: skip


def _next_steps(waiting: list[int], skipped: list[int], holds: list[int]) -> list[str]:
    nxt = []
    if waiting:
        nxt.append('承認を得た課題に "approved": true を付けて同じ plan で apply を打ち直す')
    if holds:
        nxt.append("承認を待つ移動 " + " ".join(f"#{n}" for n in holds)
                   + " は、承認を得たら rank を --approved で打ち直し、その rank で plan を作り直して apply を打つ")  # fmt: skip
    if skipped:
        nxt.append("plan の後に書き換わった " + " ".join(f"#{n}" for n in skipped) + " を判定し直す")
    return nxt


def _apply_outcome(repo: str, run_id: str, again_args: list[str], got: dict[str, Any]) -> dict[str, Any]:
    """反映の結果から status・要約・metrics を作る。失敗は止め、承認待ちと照合で飛ばした課題は gate で返す。"""
    acted, made, parents, tables = got["acted"], got["made"], got["parents"], got["tables"]
    buckets = acted["buckets"]
    failed = _apply_failures(got)
    out = {"repo": repo, **buckets, "closed": acted["closed"], "created": made["created"], "create_rejected": made["rejected"],
           "moved": got["moved"], "held": sorted(got["holds"]), "reparented": parents["applied"], "failed": failed,
           "before": parents["before"], "tables": tables}  # fmt: skip
    summary = _apply_summary(got)
    if failed:
        raise Stop("incomplete", f"{summary}。{len(failed)} 件が失敗した（同じ plan で打ち直せば済んだものは記録で飛ぶ。"
                   f"付け直しを戻すは ndf issues revert --run {run_id}）", acted["items"], failed)  # fmt: skip
    waiting, skipped, holds = buckets[actions_decide.WAITING], buckets[actions_decide.SKIPPED], sorted(got["holds"])
    if waiting or skipped or holds:
        nxt = _next_steps(waiting, skipped, holds)
        again = result.command(TOOL, "apply", *again_args)
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
    seconds = depth.depth_seconds(declaration(), a.depth)
    return result.result(TOOL, result.OK, f"{a.depth} の 1 回の秒の上限は {seconds} 秒", metrics={"depth": a.depth, "seconds": seconds})


def cmd_depth_record(a: argparse.Namespace) -> dict[str, Any]:
    repo = _repo(a)
    row = depth.record(_sd(a, repo), a.depth, a.event, a.left)
    return result.result(TOOL, result.OK, f"{a.depth} の {a.event} を記録した（残り {len(row['left'])} 件）", metrics=row)


def cmd_due(a: argparse.Namespace) -> dict[str, Any]:
    """見直し・大整理を起動するか。起動しなければ not_applicable で止まる（読み手は status で分ける）。"""
    repo = _repo(a)
    sd = _sd(a, repo)
    decl = declaration()
    depth.depth_seconds(decl, a.depth)  # 宣言の形を確かめる
    try:
        cfg = due_decide.settings(a.depth, depth.depth_entry(decl, a.depth))
    except ValueError as exc:
        raise Stop("invalid", str(exc)) from exc
    metrics = _previous_metrics(sd)
    trig, values = due_decide.due(a.depth, state.read_lines(sd, "backlog-depths"), metrics, cfg, a.manual)
    out = {"depth": a.depth, "trigger": trig, "values": values}
    if not trig:
        return result.stopped(TOOL, "not_applicable", f"{a.depth} は起動しない（{json.dumps(values, ensure_ascii=False)}）", metrics=out)
    return result.result(TOOL, result.OK, f"{a.depth} を起動する（{'・'.join(trig)}）", metrics=out)


def cmd_settle(a: argparse.Namespace) -> dict[str, Any]:
    try:
        rows, bad = settle_decide.parse(read_json(a.file))
    except ValueError as exc:
        raise Stop("invalid", str(exc)) from exc
    repo = _repo(a)
    gh = hub(repo)
    base = a.base if a.base else settle.default_branch(gh)
    items = [{"kind": "issue", "name": f"#{b['issue']}", "result": settle_decide.UNVERIFIED, **b} for b in bad]
    items += settle.settle(gh, rows, base, a.dry_run)
    left = [i for i in items if i["result"] == settle_decide.UNVERIFIED]
    done = sum(1 for i in items if i["result"] in (settle_decide.CLOSED, settle_decide.VERIFIED))
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
    "due": cmd_due,
    "settle": cmd_settle,
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help="<所有者>/<名前>（省けば origin）")
    common.add_argument("--state-dir", help="状態の置き場（省けば環境変数の置き場かメインディレクトリの既定の置き場）")
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
    du = sub.add_parser("due", parents=[common], help="見直し・大整理を起動するか（起動しなければ not_applicable）")
    du.add_argument("depth", choices=depth.DEPTHS)
    du.add_argument("--manual", action="store_true", help="利用者が求めた")
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
