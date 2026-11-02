"""upkeep_actions.py: apply の深さの検査と、起票（create）・付け直し（reparent）・戻し（revert）（#2315 の決定 8・16）。

plan に足すキー:

    {"depth": "diff" | "review" | "overhaul",      # 省くと diff（既存の plan）
     "owner": "<owner claim の run>",                # review・overhaul で要る（I3）
     "create": [{"kind": "needs" | "seeds" | "policy", "title": "...", "body": "...",
                 "sources": [{"kind": "url|path|command", "value": "..."}], "mvv_basis": "Value 3 など",
                 "labels": ["..."], "mvv_verdict": "follow" | "suspect" | "unknown"}],
     "reparent": [{"number": 12, "parent": 2335, "milestone": "<題名>" | null}],
     "policy_verdict": "follow"}                     # 大方針を前の回に起票したときの MVV 判定（reparent の前提）

- 差分の棚卸しは順位の表・前倒しと後ろ倒し・マイルストーンの設定・やらない・起票・付け直しを反映しない（I12。`wrong_depth`）
- 見直しの起票は `depths.review.issue_limit` 件まで（I4。超えた分は `not_created`）。出典と MVV の当たる所が無い起票は
  `rejected_create`（I5）。区別のラベル（`depths.labels`）を付け、MVV 判定が follow でなければ `needs-decision` を付ける（I6）
- 付け直しは、大方針の MVV 判定が follow のときだけ行う（受け入れ条件 21）。最初の書き込みの前に対象の全件の親と
  マイルストーンを `.ndf/backlog/overhaul/<run>/before.json` に書く（I9）。途中で失敗したら `revert_command` を返す
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import clock
import jsonio
import run_owner
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, git_root, result
import upkeep_depth as D
import upkeep_owner as O
from upkeep_gh import Gh, Milestones, Partial, target_repo

TOOL = "backlog-refinement"
DECISION = "needs-decision"
KINDS = ("needs", "seeds", "policy")


def _stop_apply(why: str, code: int = EXIT_PRECONDITION):
    emit(
        result(TOOL, "stopped", f"{why}。1 件も書かない", [{"kind": "plan", "name": "depth", "result": "stopped", "reason": why}], {}), code
    )


def precheck_depth(root, plan: dict) -> tuple[dict, list[dict]]:
    """深さと持ち主を確かめ、差分の棚卸しで反映しない action を外す。(直した plan, wrong_depth の行)。"""
    depth = plan.get("depth") or "diff"
    if depth not in D.DEPTHS:
        _stop_apply(f"plan の depth は {' / '.join(D.DEPTHS)} のどれか: {depth}", EXIT_UNREADABLE)
    plan["depth"] = depth
    if depth != "diff":
        path = O.owner_file(root)
        cur, _ = run_owner.read_owner(path)
        ttl = D.settings(root)["owner"]["ttl"]
        if not plan.get("owner") or (cur or {}).get("run") != plan["owner"] or not O.valid(path, cur, ttl):
            _stop_apply(
                f"深さ {depth} の apply は見直しの持ち主の実行（owner）だけが書く（宣言 {(cur or {}).get('run')}・plan {plan.get('owner')}）"
            )
        return plan, []
    wrong, keep = [], []
    for act in plan["actions"]:
        ch = act.get("changes") or {}
        why = None
        if act.get("reschedule"):
            why = "前倒し・後ろ倒しは見直しで反映する"
        elif act.get("verdict") == "やらない":
            why = "やらないの判断は見直しで反映する"
        elif "milestone" in ch:
            why = "マイルストーンの設定は見直しで行う"
        if why:
            wrong.append({"kind": "issue", "name": f"#{act['number']}", "result": "wrong_depth", "reason": why})
        else:
            keep.append(act)
    plan["actions"] = keep
    for key, why in (("rank", "順位の表は見直しで書く"), ("rejected", "退けた移動は見直しで扱う")):
        if plan.pop(key, None):
            wrong.append({"kind": "plan", "name": key, "result": "wrong_depth", "reason": why})
    for key in ("create", "reparent"):
        for x in plan.pop(key, None) or []:
            wrong.append(
                {
                    "kind": key,
                    "name": str(x.get("title") or x.get("number")),
                    "result": "wrong_depth",
                    "reason": "差分の棚卸しは起票・付け直しをしない",
                }
            )
    return plan, wrong


def compose_body(c: dict) -> str:
    """本文に出典と MVV の当たる所を足す（I5）。"""
    src = "\n".join(f"- {s.get('kind')}: {s.get('value')}" for s in c.get("sources") or [])
    return f"{(c.get('body') or '').rstrip()}\n\n## 出典\n\n{src}\n\n## MVV の当たる所\n\n{c.get('mvv_basis')}\n"


def _create_key(c: dict) -> str:
    return "create:" + hashlib.sha256(json.dumps([c.get("kind"), c.get("title")], ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def create(gh: Gh, repo: str, plan: dict, cfg: dict, ledger: dict, record) -> dict:
    """起票（E9・E10）。{created, not_created, rejected_create, create_failed}。"""
    out = {"created": [], "not_created": [], "rejected_create": [], "create_failed": []}
    made = 0
    for c in plan.get("create") or []:
        title = c.get("title") or ""
        if c.get("kind") not in KINDS or not title or not c.get("sources") or not c.get("mvv_basis"):
            out["rejected_create"].append({"title": title, "reason": "kind・題名・出典・MVV の当たる所のどれかが無い"})
            continue
        if plan["depth"] == "review" and c["kind"] != "policy" and made >= cfg["review"]["issue_limit"]:
            out["not_created"].append({"title": title, "reason": f"上限 {cfg['review']['issue_limit']} 件を超えた"})
            continue
        key = _create_key(c)
        if key in ledger:  # 打ち直しで 2 度作らない
            out["created"].append({**ledger[key], "already": True})
            made += c["kind"] != "policy"
            continue
        labels = list(dict.fromkeys([*(c.get("labels") or []), cfg["labels"][c["kind"]]]))
        if c.get("mvv_verdict") != "follow":
            labels.append(DECISION)
        try:
            got = gh.call(
                [f"repos/{repo}/issues", "-X", "POST", "--input", "-"],
                stdin=json.dumps({"title": title, "body": compose_body(c), "labels": labels}, ensure_ascii=False),
            )
        except (StepError, Partial) as e:
            out["create_failed"].append({"title": title, "reason": str(e)})
            continue
        row = {"number": got.get("number"), "title": title, "kind": c["kind"], "labels": labels}
        record(key, row)
        out["created"].append(row)
        made += c["kind"] != "policy"
    return out


def _policy_follows(plan: dict) -> bool:
    pol = [c for c in plan.get("create") or [] if c.get("kind") == "policy"]
    if pol:
        return all(c.get("mvv_verdict") == "follow" for c in pol)
    return plan.get("policy_verdict") == "follow"


def _parent_of(gh: Gh, repo: str, n: int) -> int | None:
    try:
        got = gh.call([f"repos/{repo}/issues/{n}/parent"], target=n)
    except StepError as e:
        if "404" in str(e):
            return None
        raise
    return (got or {}).get("number")


def _set_parent(gh: Gh, repo: str, n: int, parent: int | None, cur_parent: int | None) -> None:
    issue = gh.call([f"repos/{repo}/issues/{n}"], target=n)
    if parent is None:
        if cur_parent is not None:
            gh.call(
                [f"repos/{repo}/issues/{cur_parent}/sub_issue", "-X", "DELETE", "--input", "-"],
                stdin=json.dumps({"sub_issue_id": issue["id"]}),
                target=n,
            )
        return
    gh.call(
        [f"repos/{repo}/issues/{parent}/sub_issues", "-X", "POST", "--input", "-"],
        stdin=json.dumps({"sub_issue_id": issue["id"], "replace_parent": True}),
        target=n,
    )


def _set_milestone(gh: Gh, ms: Milestones, repo: str, n: int, title: str | None) -> None:
    num = None if title is None else ms.number(title, n)
    gh.call([f"repos/{repo}/issues/{n}", "-X", "PATCH", "--input", "-"], stdin=json.dumps({"milestone": num}), target=n)


def before_path(root, run: str) -> Path:
    return D.backlog_dir(root) / "overhaul" / run / "before.json"


def revert_command(run: str) -> str:
    return f"python3 {Path(__file__).resolve().parent / 'upkeep.py'} revert --run {run}"


def reparent(root, gh: Gh, ms: Milestones, repo: str, plan: dict) -> dict:
    """付け直し（E15）。{reparented, not_reparented, reparent_failed, revert_command}。"""
    rows = plan.get("reparent") or []
    out = {"reparented": [], "not_reparented": [], "reparent_failed": [], "revert_command": None}
    if not rows:
        return out
    if plan["depth"] != "overhaul" or not _policy_follows(plan):
        out["not_reparented"] = [r.get("number") for r in rows]
        out["reason"] = "大方針の MVV 判定が follow でない（人の判断待ち）か、深さが大整理でない"
        return out
    run = plan["owner"]
    try:  # I9: 最初の書き込みの前に全件の前の状態を書く
        before = []
        for r in rows:
            cur = gh.call([f"repos/{repo}/issues/{r['number']}"], target=r["number"])
            before.append(
                {
                    "number": r["number"],
                    "parent": _parent_of(gh, repo, r["number"]),
                    "milestone": (cur.get("milestone") or {}).get("title"),
                    "at": clock.now_iso("utc"),
                }
            )
        run_dir = before_path(root, run).parent
        run_dir.mkdir(parents=True, exist_ok=True)
        jsonio.write_atomic(run_dir / "before.json", before, indent=1)
    except (StepError, Partial, OSError) as e:
        out["reparent_failed"] = [r.get("number") for r in rows]
        out["reason"] = f"前の状態を書けないため 1 件も付け直さない: {e}"
        return out
    prev = {b["number"]: b for b in before}
    for r in rows:
        n = r["number"]
        try:
            if "parent" in r:
                _set_parent(gh, repo, n, r["parent"], prev[n]["parent"])
            if "milestone" in r:
                _set_milestone(gh, ms, repo, n, r["milestone"])
        except (StepError, Partial) as e:
            out["reparent_failed"].append(n)
            out["reason"] = f"#{n} の付け直しに失敗した: {e}"
            out["revert_command"] = revert_command(run)
            break
        out["reparented"].append(n)
    return out


def write_extra(root, plan: dict, gh: Gh, ms: Milestones, repo: str, ledger: dict, write_ledger) -> dict:
    """起票と付け直し。apply の本体（既存の action）の後に呼ぶ。"""
    cfg = D.settings(root)

    def record(key, value):
        ledger[key] = value
        write_ledger()

    got = create(gh, repo, plan, cfg, ledger, record)
    got.update(
        reparent(root, gh, ms, repo, plan) if not got["create_failed"] else {"reparented": [], "not_reparented": [], "revert_command": None}
    )
    got["depth"] = plan["depth"]
    return got


def merge_outcome(extra: dict, wrong: list[dict], status: str, code, nxt, metrics: dict, items: list) -> tuple[str, object, object]:
    """apply の結果へ起票・付け直し・wrong_depth を足す。付け直しの失敗は stopped・1 で戻すコマンドを必ず載せる。"""
    metrics.update(extra)
    metrics["wrong_depth"] = wrong
    items.extend(wrong)
    if extra.get("create_failed") or extra.get("reparent_failed") or extra.get("revert_command"):
        nxt = f"付け直しを戻すときは {extra['revert_command']} を打つ" if extra.get("revert_command") else nxt
        return "stopped", 1, nxt
    return status, code, nxt


def cmd_revert(a):
    """付け直しの記録から親とマイルストーンを戻す（E16）。"""
    root = git_root(a.root)
    rows = jsonio.read(before_path(root, a.run), missing=None, broken=None)
    if not isinstance(rows, list):
        emit(result(TOOL, "stopped", f"付け直しの記録が無いか読めない: {before_path(root, a.run)}"), EXIT_PRECONDITION)
    repo = target_repo(root, getattr(a, "repo", None))
    gh = Gh(repo)
    ms = Milestones(gh)
    done, bad = [], []
    for b in rows:
        n = b["number"]
        try:
            _set_parent(gh, repo, n, b.get("parent"), _parent_of(gh, repo, n))
            _set_milestone(gh, ms, repo, n, b.get("milestone"))
            done.append(n)
        except (StepError, Partial) as e:
            bad.append({"kind": "issue", "name": f"#{n}", "result": "failed", "reason": str(e)})
    if bad:
        emit(
            result(
                TOOL,
                "stopped",
                f"戻した {len(done)} 件・戻せなかった {len(bad)} 件",
                bad,
                {"reverted": done, "failed": [x["name"] for x in bad]},
            ),
            1,
        )
    emit(result(TOOL, "ok", f"付け直しを {len(done)} 件戻した", [], {"reverted": done}))


def add_revert_parser(sub, common) -> None:
    r = sub.add_parser("revert", parents=[common])
    r.add_argument("--run", required=True, help="付け直した大整理の実行（owner claim の run）")
    r.set_defaults(func=cmd_revert)
