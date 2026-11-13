#!/usr/bin/env python3
"""progress.py: 工程の進行表のスクリプト（#3092）。スプリントの進行表とレーンの進行表を作り、流し、変える。

    python3 progress.py new <スプリント名> --mode M --issues N... [--pace P] [--lane <レーンの進行表の ID> --row N]
                            [--disable <工程>=<理由>]... [--fact <条件>=true|false]... [--requirements F] [--judgment F]
    python3 progress.py next|show <進行表の ID>
    python3 progress.py disable|enable|retry <ID> <工程> --reason R      # finish は --output F も
    python3 progress.py scope <ID> (--add N | --drop N | --mode M) --reason R    # cancel は <ID> --reason R
    python3 progress.py answer <ID> <行> (--approved | --rejected | --release-result F) [--by organizer]
    python3 progress.py lane add-row|move-row|cancel-row <レーンの進行表の ID> ...
    python3 progress.py adopt (--sprint <sprint.json> | --plan <計画名>)

工程の並びは工程の定義（`contracts/workflow/`）から組み、次の工程と終了の印は進行表の事象だけから決める。結果は
統一 JSON レポートの 1 行。終了コードは 0 = ok / 1 = 工程の失敗 / 2 = 引数・定義の誤りか読めない DB /
3 = 不変条件・状態・排他で断った操作 / 10 = 承認ゲートかリリースの結果の待ちに入った。置き場は `NDF_PROGRESS_DB`。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("durable", "workflow", "schema", "md", "waits")
import progress_adopt  # noqa: E402
import progress_db as pdb  # noqa: E402
import run_core  # noqa: E402
import progress_lanes as plr  # noqa: E402
import progress_model as pm  # noqa: E402
import workflow_def as wd  # noqa: E402
from exit_codes import EXIT_PRECONDITION, EXIT_UNREADABLE  # noqa: E402
from step_result import emit, result  # noqa: E402

TOOL = "progress"


def _cli_report(status: str, summary: str, items=None, **metrics) -> dict:
    return result(TOOL, status, summary, items, metrics)


def _project(root: Path) -> dict:
    import decl

    data, _ = decl.read_json_setting(root, "project.json")
    return data or {}


def facts_of(project: dict, given: dict[str, bool]) -> dict[str, bool]:
    """`metadata.ndf.when` の条件の事実。CI テストの宣言はプロジェクトの宣言から読み、ほかは引数（既定は真）。"""
    out = {w: True for w in wd.VOCABULARY["when"]}
    out["ci_declared"] = bool(((project.get("test") or {}).get("ci") or {}).get("only"))
    out.update(given)
    return out


def _pairs(values: list[str], what: str) -> dict[str, str]:
    out = {}
    for v in values or []:
        k, sep, rest = v.partition("=")
        if not sep or not k.strip():
            raise SystemExit(f"{what} は <名前>=<値> の形: {v}")
        out[k.strip()] = rest.strip()
    return out


def _snapshot_doc(snap, enabled, issues, pace, root: Path, extra: dict | None = None) -> dict:
    return {
        "snapshot": snap.to_dict(),
        "enabled": {k: vars(v) for k, v in enabled.items()},
        "issues": issues,
        "pace": pace,
        "cwd": str(root),
        **(extra or {}),
    }


def _read_input_json(path: str | None, what: str) -> dict | None:
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise pdb.Unreadable(f"{what} {path} を読めない（{e}）") from None


def cmd_new(a, store: pdb.Store) -> tuple[dict, int]:
    """スプリントの進行表を作り、要求とモード判定に終了の印を付ける（F2）。"""
    root = Path.cwd()
    snap = wd.load_mode_definition(a.mode)
    project = _project(root)
    declared = (project.get("workflow") or {}).get("stages") or {}
    disables = _pairs(a.disable, "--disable")
    facts = facts_of(project, {k: v.lower() == "true" for k, v in _pairs(a.fact, "--fact").items()})
    enabled = wd.resolve_enabled(snap, facts, declared, disables)
    drafts = [pm.EventDraft("created", "conductor", None, 1, payload={"lane_row": a.row} if a.lane else None)]
    for s in snap.stages:
        drafts.append(pm.EventDraft("created", "conductor", s.name, 1))
    done_inputs = {"要求と受け入れ条件": _read_input_json(a.requirements, "要求の出力")}
    judged = _read_input_json(a.judgment, "モード判定の出力")
    for name, out in done_inputs.items():
        if out is None or name not in snap.stage_names:
            continue
        stage = snap.stage(name)
        errs = wd.validate_io(snap, stage.output_ref, out)
        if errs:
            return _cli_report("stopped", f"{name} の出力が規約に合わない: {'; '.join(errs)}"), EXIT_UNREADABLE
        drafts.append(pm.EventDraft("manual_finished", "conductor", name, 1, reason="作成の前に済んだ", payload={"output": out}))
    for s in snap.stages:
        if not enabled[s.name].enabled:
            drafts.append(pm.EventDraft("disabled", "conductor", s.name, 1, reason=enabled[s.name].reason or "無効"))
    doc = _snapshot_doc(snap, enabled, a.issues, a.pace, root, {"judgment": judged})
    with pdb.transaction(store) as conn:
        tid = pdb.create_table(conn, "sprint", pdb.progress_repo_key(root), a.name, a.lane, doc, drafts)
        if a.lane:
            pdb.lane_write(conn, a.lane, pm.EventDraft("row_started", "conductor", str(a.row), payload={"row": a.row, "sprint": tid}))
    off = [{"stage": k, "reason": v.reason, "source": v.source} for k, v in enabled.items() if not v.enabled]
    return _cli_report(
        "ok", f"スプリントの進行表を作った（{len(snap.stages)} 工程・無効 {len(off)}）", off, table=tid, rows=len(snap.stages)
    ), 0


def cmd_next(a, store: pdb.Store) -> tuple[dict, int]:
    import progress_run

    return progress_run.run_next(store, a.id)


def _state_rows(snap, rows: dict) -> list[dict]:
    out = []
    for s in snap.stages:
        r = rows.get(s.name)
        state = r.state if r else pm.PENDING
        out.append(
            {
                "stage": s.name,
                "state": state,
                "label": pm.LABELS.get(state, state),
                "at": r.at if r else None,
                "reason": r.reason if r else None,
                "summary": (r.payload or {}).get("summary") if r else None,
            }
        )
    return out


def cmd_show(a, store: pdb.Store) -> tuple[dict, int]:
    """進行表の行・時刻・出力の要約・無効の理由・事象の履歴を返す（F6）。"""
    with pdb.transaction(store) as conn:
        row = pdb.progress_table(conn, a.id)
        if row is None:
            return _cli_report("stopped", f"進行表 {a.id} が無い"), EXIT_UNREADABLE
        events = pdb.read_progress_events(conn, a.id)
        history = [
            {
                "seq": e.seq,
                "rev": e.rev,
                "row": e.row_key,
                "kind": e.kind,
                "state": e.state,
                "actor": e.actor,
                "reason": e.reason,
                "at": e.at,
            }
            for e in events
        ]
        if row.level == "sprint":
            rev, raw = pdb.definition(conn, a.id)
            rows = pm.fold(events, rev)
            items = _state_rows(wd.Snapshot.from_dict(raw["snapshot"]), rows)
            return _cli_report(
                "ok",
                f"スプリントの進行表（改訂 {rev}・{pm.LABELS.get(pm.table_state(rows) or '', '実行中')}）",
                items,
                table=a.id,
                rev=rev,
                history=history,
            ), 0
        if row.level == "lane":
            return _cli_report("ok", "レーンの進行表", plr.lane_order_rows(events), table=a.id, history=history), 0
        plan, lanes, ids = pdb.plan_and_lanes(conn, a.id)
        return _cli_report(
            "ok",
            f"計画の進行表（{pm.LABELS.get(plan['state'], plan['state'])}）",
            [{"lane": k, "rows": v, "table": ids.get(k)} for k, v in lanes.items()],
            table=a.id,
            requests=list(plan["requests"].values()),
            history=history,
        ), 0


KIND_OF = {"disable": "disabled", "enable": "enabled", "retry": "retry", "finish": "manual_finished"}


def cmd_change(a, store: pdb.Store) -> tuple[dict, int]:
    """`disable`・`enable`・`retry`・`finish` の事象を書く（F4）。`retry` は排他を取れたときだけ書く。"""
    kind = KIND_OF[a.cmd]
    with pdb.transaction(store) as conn:
        rev, raw = pdb.definition(conn, a.id)
    payload = None
    if a.cmd == "finish":
        out = _read_input_json(a.output, "出力")
        snap = wd.Snapshot.from_dict(raw["snapshot"])
        if a.stage not in snap.stage_names:
            return _cli_report("stopped", f"工程 {a.stage} が定義に無い"), EXIT_PRECONDITION
        errs = wd.validate_io(snap, snap.stage(a.stage).output_ref, out)
        if errs:
            return _cli_report("stopped", f"出力が規約に合わない（I17）: {'; '.join(errs)}"), EXIT_PRECONDITION
        payload = {"output": out, "output_path": a.output}
    draft = pm.EventDraft(kind, "conductor", a.stage, rev, reason=a.reason, payload=payload)

    def write():
        with pdb.transaction(store) as conn:
            return pdb.append_events(conn, a.id, [draft])

    if a.cmd == "retry":
        with pdb.run_lock(store, a.id):
            seqs = write()
    else:
        seqs = write()
    return _cli_report("ok", f"{a.stage} に {kind} を書いた", [{"seq": s, "kind": kind, "row": a.stage} for s in seqs], table=a.id), 0


def _cancel_pending(store: pdb.Store, table_id: str) -> list[str]:
    import durable

    with pdb.transaction(store) as conn:
        wids = [e.payload.get("wid") for e in pdb.read_progress_events(conn, table_id) if e.kind == "execution" and e.payload]
    return [w for w in wids[-1:] if durable.cancel_from(store.url, w)]


def cmd_scope(a, store: pdb.Store) -> tuple[dict, int]:
    """範囲とモードの変更で改訂を上げ、待ちの実行を取り消す（決定 16）。外した課題は同じレーンの次の行へ入れる。"""
    with pdb.run_lock(store, a.id):
        with pdb.transaction(store) as conn:
            rev, raw = pdb.definition(conn, a.id)
            old_rows = pm.fold(pdb.read_progress_events(conn, a.id), rev)
        old = wd.Snapshot.from_dict(raw["snapshot"])
        issues = list(raw.get("issues") or [])
        mode = a.mode or old.mode
        if a.add:
            issues = sorted(set(issues) | set(a.add))
        if a.drop:
            issues = [n for n in issues if n not in a.drop]
        snap = wd.load_mode_definition(mode) if mode != old.mode else old
        # 影響する最初の工程: 前の改訂で終了か無効でない最初の工程（モードを上げて足された工程を含む）
        first = next(
            (n for n in snap.stage_names if old_rows.get(n) is None or old_rows[n].state not in (pm.FINISHED, pm.DISABLED)),
            snap.stage_names[-1],
        )
        cancelled = _cancel_pending(store, a.id)
        enabled = {k: wd.Enablement(**v) for k, v in (raw.get("enabled") or {}).items()}
        if mode != old.mode:
            project = _project(Path.cwd())
            enabled = wd.resolve_enabled(snap, facts_of(project, {}), (project.get("workflow") or {}).get("stages") or {}, {})
        doc = {**raw, "snapshot": snap.to_dict(), "issues": issues, "enabled": {k: vars(v) for k, v in enabled.items()}}
        head = (
            pm.EventDraft("mode_raised", "conductor", None, reason=a.reason, payload={"from": old.mode, "to": mode, "first": first})
            if mode != old.mode
            else pm.EventDraft(
                "scope_changed", "conductor", None, reason=a.reason, payload={"add": a.add or [], "drop": a.drop or [], "first": first}
            )
        )
        drafts = [head] + pm.carry_rows(old_rows, snap, first, rev + 1)
        drafts += [
            pm.EventDraft("disabled", "conductor", k, rev + 1, reason=v.reason or "無効")
            for k, v in enabled.items()
            if not v.enabled and not any(d.row_key == k and d.kind == "carried" for d in drafts)
        ]
        refused, request = None, None
        with pdb.transaction(store) as conn:
            new = pdb.add_revision(conn, a.id, doc, drafts, wd.mode_heights())
        if a.drop:
            refused, request = pdb.carry_dropped(store, a.id, a.drop, a.reason)
    metrics = {"table": a.id, "rev": new, "first": first, "cancelled": cancelled}
    if refused:
        return _cli_report(
            "stopped", f"改訂 {new} へ上げたが、外した課題を次の行へ入れられない: {refused}", [], request=request, **metrics
        ), EXIT_PRECONDITION
    return _cli_report("ok", f"改訂 {new} へ上げた（{first} から流し直す）", **metrics), 0


def cmd_cancel(a, store: pdb.Store) -> tuple[dict, int]:
    """スプリントを中止し、レーンの行を同じトランザクションで終える（決定 17）。"""
    with pdb.run_lock(store, a.id):
        cancelled = _cancel_pending(store, a.id)
        with pdb.transaction(store) as conn:
            rev, _ = pdb.definition(conn, a.id)
            pdb.append_events(conn, a.id, [pm.EventDraft("cancelled", "conductor", None, rev, reason=a.reason)])
            got = pdb.end_lane_row(conn, a.id, "cancelled", "conductor")
            rows = pm.fold(pdb.read_progress_events(conn, a.id), rev)
    n = sum(1 for k, r in rows.items() if k != pm.TABLE_KEY and r.state == pm.CANCELLED)
    return _cli_report(
        "ok", f"スプリントを中止した（中止の行 {n}）", table=a.id, cancelled=cancelled, next_row=(got or {}).get("next_row")
    ), 0


def cmd_answer(a, store: pdb.Store) -> tuple[dict, int]:
    """承認ゲートの答えかリリースの結果を `answered` と DBOS の送信で届ける（F5）。"""
    import progress_run

    value = {"answer": "approved" if a.approved else "rejected"}
    if a.release_result:
        value = {"answer": "released", "release": _read_input_json(a.release_result, "リリースの結果")}
    with pdb.transaction(store) as conn:
        rev, _ = pdb.definition(conn, a.id)
        pdb.append_events(conn, a.id, [pm.EventDraft("answered", a.by, a.row, rev, payload=value)])
    sent = progress_run.send_progress_answer(store, a.id, a.row, value)
    return _cli_report("ok", f"{a.row} へ答え（{value['answer']}）を書いた", table=a.id, sent=sent), 0


def cmd_lane(a, store: pdb.Store) -> tuple[dict, int]:
    """レーンの順番表の行を足す・動かす・取り消す（F9）。計画の決まりで断られたら 3。"""
    with pdb.transaction(store) as conn:
        rows = plr.lane_order_rows(pdb.read_progress_events(conn, a.id))
        if a.op == "add-row":
            n = max((r["row"] for r in rows), default=0) + 1
            d = pm.EventDraft(
                "row_added",
                a.by,
                str(n),
                reason=a.reason,
                payload={"row": n, "issues": a.issues or [], "touches": a.touches or [], "after": a.after or [], "position": a.position},
            )
        elif a.op == "move-row":
            d = pm.EventDraft("row_moved", a.by, str(a.row), reason=a.reason, payload={"row": a.row, "to": a.to})
        else:
            d = pm.EventDraft("row_cancelled", a.by, str(a.row), reason=a.reason, payload={"row": a.row})
        seqs = pdb.lane_write(conn, a.id, d)
    return _cli_report(
        "ok", f"レーンの進行表へ {d.kind} を書いた", [{"seq": s, "kind": d.kind, "row": d.row_key} for s in seqs], table=a.id
    ), 0


def cmd_adopt(a, store: pdb.Store) -> tuple[dict, int]:
    """動いているスプリントか計画を 1 つのトランザクションで進行表へ取り込む（F11・決定 19）。元のファイルは消さない。"""
    root = Path.cwd()
    if a.sprint:
        path = Path(a.sprint)
        m = _read_input_json(str(path), "目録")
        snap = wd.load_mode_definition(m.get("モード") or "standard")
        drafts, issues = progress_adopt.sprint_drafts(m, path, snap)
        enabled = {s.name: wd.Enablement(True) for s in snap.stages}
        doc = _snapshot_doc(snap, enabled, issues, m.get("進め方") or "normal", root, {"adopted_from": str(path)})
        with pdb.transaction(store) as conn:
            tid = pdb.create_table(conn, "sprint", pdb.progress_repo_key(root), m.get("スプリント") or path.parent.name, None, doc, drafts)
        failed = [{"stage": d.row_key, "reason": d.reason} for d in drafts if d.kind == "adopted" and d.resolved_state() == pm.FAILED]
        return _cli_report("ok", f"スプリントを取り込んだ（失敗にした工程 {len(failed)}）", failed, table=tid), 0
    head, lane_rows = progress_adopt.plan_drafts(root, a.plan)
    repo, lanes = pdb.progress_repo_key(root), {}
    with pdb.transaction(store) as conn:
        pid = pdb.create_table(conn, "plan", repo, a.plan, None, None, head)
        for name, rows in lane_rows.items():
            lanes[name] = pdb.create_table(conn, "lane", repo, f"{a.plan}/{name}", pid, None, rows)
    return _cli_report("ok", f"計画を取り込んだ（レーン {len(lanes)}）", [{"lane": k, "table": v} for k, v in lanes.items()], table=pid), 0


COMMANDS = {
    "new": cmd_new,
    "next": cmd_next,
    "show": cmd_show,
    "disable": cmd_change,
    "enable": cmd_change,
    "retry": cmd_change,
    "finish": cmd_change,
    "scope": cmd_scope,
    "cancel": cmd_cancel,
    "answer": cmd_answer,
    "lane": cmd_lane,
    "adopt": cmd_adopt,
}


def main(argv: list[str] | None = None) -> "NoReturn":  # noqa: F821
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="スプリントの進行表を作る")
    n.add_argument("name")
    n.add_argument("--mode", required=True, choices=wd.MODES)
    n.add_argument("--issues", type=int, nargs="+", required=True)
    n.add_argument("--pace", default="normal")
    n.add_argument("--lane")
    n.add_argument("--row", type=int)
    n.add_argument("--disable", action="append", default=[])
    n.add_argument("--fact", action="append", default=[])
    n.add_argument("--requirements")
    n.add_argument("--judgment")
    nx = sub.add_parser("next")
    nx.add_argument("id")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    c = sub.add_parser("disable")
    c.add_argument("id")
    c.add_argument("stage")
    c.add_argument("--reason", required=True)
    c = sub.add_parser("enable")
    c.add_argument("id")
    c.add_argument("stage")
    c.add_argument("--reason", required=True)
    c = sub.add_parser("retry")
    c.add_argument("id")
    c.add_argument("stage")
    c.add_argument("--reason", required=True)
    c = sub.add_parser("finish")
    c.add_argument("id")
    c.add_argument("stage")
    c.add_argument("--reason", required=True)
    c.add_argument("--output", required=True)
    s = sub.add_parser("scope")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--add", type=int, nargs="+")
    g.add_argument("--drop", type=int, nargs="+")
    g.add_argument("--mode", choices=wd.MODES)
    s.add_argument("--reason", required=True)
    c = sub.add_parser("cancel")
    c.add_argument("id")
    c.add_argument("--reason", required=True)
    w = sub.add_parser("answer")
    w.add_argument("id")
    w.add_argument("row")
    g = w.add_mutually_exclusive_group(required=True)
    g.add_argument("--approved", action="store_true")
    g.add_argument("--rejected", action="store_true")
    g.add_argument("--release-result")
    w.add_argument("--by", choices=("organizer", "conductor"), default="conductor")
    ln = sub.add_parser("lane")
    ln.add_argument("op", choices=("add-row", "move-row", "cancel-row"))
    ln.add_argument("id")
    ln.add_argument("--issues", type=int, nargs="+")
    ln.add_argument("--touches", nargs="+")
    ln.add_argument("--after", nargs="+")
    ln.add_argument("--position", type=int)
    ln.add_argument("--row", type=int)
    ln.add_argument("--to", type=int)
    ln.add_argument("--reason", required=True)
    ln.add_argument("--by", choices=("conductor", "organizer"), default="conductor")
    ad = sub.add_parser("adopt")
    g = ad.add_mutually_exclusive_group(required=True)
    g.add_argument("--sprint")
    g.add_argument("--plan")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        emit(_cli_report("stopped", "引数の誤り"), EXIT_UNREADABLE if e.code else 0)
    try:
        store = pdb.connect(pdb.default_url(Path.cwd()))
        rep, code = COMMANDS[a.cmd](a, store)
    except pm.Refused as e:
        rep, code = _cli_report("stopped", e.reason, current=e.current), EXIT_PRECONDITION
    except (pdb.Unreadable, wd.WorkflowDefError, ValueError) as e:
        rep, code = _cli_report("stopped", str(e)), EXIT_UNREADABLE
    except SystemExit as e:  # 引数の形の誤り（_pairs）
        rep, code = _cli_report("stopped", str(e.code)), EXIT_UNREADABLE
    emit(rep, code)


if __name__ == "__main__":
    run_core.main("scripts/progress.py", main)
