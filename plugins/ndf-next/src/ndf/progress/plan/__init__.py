"""計画の進行表（#3097）: 開発計画のレーンの配列とレーンをまたぐ決まりを持つ。

進行表は 3 段ある。計画の進行表（このパッケージ。オーガナイザーが持つ）→ レーンの進行表（`ndf.progress.lane`。
レーンの conductor が持つ）→ スプリントの進行表（`ndf progress`）。

- 計画の進行表へ書くのはこのモジュールの関数だけである。書き手はオーガナイザー（作成・レーンをまたぐ変更・中止・
  依頼を閉じる・答えの記録）と、耐久ワークフロー（完了。`ndf.progress.plan.run`）になる
- `plan.json` は入力として読むだけで、計画の状態は書かない
- 副命令 `ndf lanes` は `ndf.progress.plan.cli`、計画を進める耐久ワークフローは `ndf.progress.plan.run`、
  オーガナイザーが計画の知らせを待つ `wait` は `ndf.progress.plan.wait` にある
- 外のオーガナイザー（`NDF_ORGANIZER_DIR`）とのファイルのやり取りは `ndf.progress.plan.external`、
  計画の記録の置き場（`.ndf/parallel/<計画>/`）は `ndf.progress.plan.run.record_dir` が決める
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import sqlalchemy as sa

from ndf import cli as top
from ndf import contracts
from ndf.contracts import limits, result
from ndf.progress import lane, model, store
from ndf.progress.lane import liveness

# 結果 JSON の tool の名前（副命令のモジュール `cli` の名前から引く）。`ndf.cli` を `cli` の名で束ねると、
# 副モジュール `ndf.progress.plan.cli` を読む前の `from ndf.progress.plan import cli` が `ndf.cli` を返す
TOOL = top.tool_of(f"{__name__}.cli")
# 計画の宣言とレーンの宣言の形（レーンは同じファイルの $defs）
PLAN_SCHEMA = "workflow/parallel-plan.schema.json"
LANE_SCHEMA = PLAN_SCHEMA + "#/$defs/lane"
# 同時に動くレーンの上限とレーンごとのプランの本数の上限の既定（計画の宣言の max_lanes・max_plans で変える）
LIMIT_POLICY = ("relay.max-named", "count")
# 答えが載ってから conductor が next を打たないまま、止まりとして知らせるまでの秒
ANSWER_NEXT_GRACE_SECONDS = 600


class PlanInputError(contracts.ContractError):
    """計画の宣言（plan.json）かレーンの宣言が読めない・形が違う。"""


# --- 宣言の読み込み ------------------------------------------------------------------------------


def read_doc(path: Path, schema: str, label: str) -> dict[str, Any]:
    """宣言の JSON を読み、形を確かめて返す。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanInputError(f"{label} {path} を JSON として読めない: {exc}") from exc
    errs = contracts.schema_errors(doc, schema)
    if errs:
        raise PlanInputError(f"{label} {path} の形が違う: {'; '.join(errs)}")
    return doc


def lane_inputs(decl: dict[str, Any]) -> list[dict[str, Any]]:
    """レーンの宣言の最初の順番表（`rows`、`input` だけなら 1 行）。"""
    return list(decl["rows"]) if "rows" in decl else [decl["input"]]


def _lane_kind(decl: dict[str, Any]) -> str:
    return lane_inputs(decl)[0]["kind"]


def lane_payload(decl: dict[str, Any], table_id: str) -> dict[str, Any]:
    """計画の進行表のレーンの行（`lane_added` の値）。"""
    body = {"table_id": table_id, "kind": _lane_kind(decl), "plans": decl["plans"], "touches": decl["touches"]}
    body["after"] = list(decl["after"]) if "after" in decl else []
    if "repo" in decl:
        body["repo"] = decl["repo"]
    return body


def _default_limit() -> int:
    return int(limits.value(*LIMIT_POLICY))


def plan_payload(doc: dict[str, Any]) -> dict[str, Any]:
    """計画そのものの行（規則・上限・形の番号・NDF の版）。"""
    default = _default_limit()
    return {
        "name": doc["name"],
        "rules": doc["rules"],
        "max_lanes": doc["max_lanes"] if "max_lanes" in doc else default,
        "max_plans": doc["max_plans"] if "max_plans" in doc else default,
        "formats": lane.current_formats(),
        "ndf_version": lane.ndf_version(),
    }


# --- 計画の進行表の読み書き（オーガナイザーと耐久ワークフロー） -----------------------------------------


def plan_id(repo: str, name: str) -> str:
    return model.table_id("plan", repo, name)


def _append(conn: sa.Connection, pid: str, drafts: list[model.EventDraft]) -> list[int]:
    events = store.read_events(conn, pid)
    return store.append_events(conn, pid, drafts, events[-1].seq if events else 0)


def _refuse_if(errs: list[str]) -> None:
    if errs:
        raise model.Refused("; ".join(errs))


def _plan_errors(conn: sa.Connection, pid: str) -> list[str]:
    view, rows = lane.read_view(conn, pid)
    return lane.plan_errors(view, rows)


def _new_errors(conn: sa.Connection, pid: str, before: set[str]) -> list[str]:
    return [e for e in _plan_errors(conn, pid) if e not in before]


def _errors_now(conn: sa.Connection, pid: str) -> set[str]:
    return set(_plan_errors(conn, pid))


def create_plan(conn: sa.Connection, repo: str, doc: dict[str, Any], start: bool) -> str:
    """計画の進行表と各レーンの進行表（最初の順番表）を 1 つのトランザクションで作る。決まりに当たれば何も残さない。"""
    name = doc["name"]
    pid = plan_id(repo, name)
    names = [d["name"] for d in doc["lanes"]]
    dup = sorted({n for n in names if names.count(n) > 1})
    _refuse_if([f"レーン名が重なる: {', '.join(dup)}"] if dup else [])
    lane_ids = {d["name"]: model.table_id("lane", repo, lane.lane_table_name(name, d["name"])) for d in doc["lanes"]}
    drafts = [model.EventDraft("created", "organizer", lane.PLAN_ROW, payload=plan_payload(doc))]
    drafts += [model.EventDraft("lane_added", "organizer", d["name"], payload=lane_payload(d, lane_ids[d["name"]])) for d in doc["lanes"]]
    drafts += [model.EventDraft("plan_started", "organizer")] if start else []
    store.create_table(conn, ("plan", repo, name), None, None, drafts)
    for d in doc["lanes"]:
        lane.create_lane(conn, (repo, name, d["name"]), pid, lane_inputs(d))
    _refuse_if(_new_errors(conn, pid, set()))
    return pid


def start_plan(conn: sa.Connection, pid: str) -> None:
    """計画を始める（オーガナイザー）。区切りで止めた計画は、同じ名前のまま止めた所から再開する。"""
    view, _ = lane.read_plan(conn, pid)
    if view.state not in (lane.PENDING, lane.PAUSED):
        raise model.Refused(f"計画 {view.name} は{model.label(view.state)}で、始められない", current_id=pid)
    _append(conn, pid, [model.EventDraft("plan_started", "organizer")])


def _close_request(request: Optional[str], reason: str) -> list[model.EventDraft]:
    return [model.EventDraft("request_closed", "organizer", request, reason=reason)] if request else []


def _check_request(conn: sa.Connection, pid: str, request: Optional[str]) -> None:
    if request is None:
        return
    view, _ = lane.read_plan(conn, pid)
    found = view.requests.get(request)
    if found is None or found.closed is not None:
        raise model.Refused(f"計画 {view.name} に開いている依頼 {request} は無い")


def add_lane(conn: sa.Connection, pid: str, decl: dict[str, Any], request: Optional[str] = None) -> str:
    """動いている計画へレーンを足す（オーガナイザー）。応えた依頼があれば同じトランザクションで閉じる。"""
    lane.lock_plan(conn, pid)
    view, _ = lane.read_plan(conn, pid)
    meta = store.read_table(conn, pid)
    if decl["name"] in view.lanes:
        raise model.Refused(f"計画 {view.name} にレーン {decl['name']} は既にある")
    _check_request(conn, pid, request)
    before = _errors_now(conn, pid)
    tid = model.table_id("lane", meta["repo"], lane.lane_table_name(view.name, decl["name"]))
    drafts = [model.EventDraft("lane_added", "organizer", decl["name"], payload=lane_payload(decl, tid))]
    _append(conn, pid, drafts + _close_request(request, f"レーン {decl['name']} を足した"))
    lane.create_lane(conn, (meta["repo"], view.name, decl["name"]), pid, lane_inputs(decl))
    _refuse_if(_new_errors(conn, pid, before))
    return tid


def change_lane(conn: sa.Connection, pid: str, name: str, changes: dict[str, Any], reason: str, request: Optional[str] = None) -> None:
    """レーンの触る場所・プランの本数・after を変える（name が計画の行なら上限を変える）。決まりで新しく当たれば断る。"""
    lane.lock_plan(conn, pid)
    view, _ = lane.read_plan(conn, pid)
    if name != lane.PLAN_ROW and name not in view.lanes:
        raise model.Refused(f"計画 {view.name} にレーン {name} は無い")
    if not changes:
        raise model.Refused("変える値が無い")
    _check_request(conn, pid, request)
    before = _errors_now(conn, pid)
    drafts = [model.EventDraft("lane_changed", "organizer", name, reason=reason, payload=changes)]
    _append(conn, pid, drafts + _close_request(request, reason))
    _refuse_if(_new_errors(conn, pid, before))


def move_row(conn: sa.Connection, pid: str, src: tuple[str, str], dest: str, reason: str) -> str:
    """課題の行を別のレーンへ移す（オーガナイザー）。移す先へはレーンの進行表の関数で行を足す。足した行の鍵を返す。"""
    from_lane, key = src
    view, rows = lane.read_view(conn, pid)
    if from_lane not in rows or dest not in rows:
        raise model.Refused(f"計画 {view.name} にレーン {from_lane} か {dest} は無い")
    row = next((r for r in rows[from_lane] if r.key == key), None)
    if row is None:
        raise model.Refused(f"レーン {from_lane} に行 {key} は無い")
    lane.cancel_row(conn, pid, from_lane, key, lane.RowChange("organizer", f"レーン {dest} へ移す: {reason}"))
    return lane.add_row(conn, pid, dest, row.input, lane.RowChange("organizer", f"レーン {from_lane} の行 {key} から移した: {reason}"))


def pause_plan(conn: sa.Connection, pid: str, reason: str) -> list[str]:
    """計画を区切りで止める（オーガナイザー）。実行中の行のスプリントの ID を返す。

    実行中の行は終わりまで流し、耐久ワークフローは次の行を起こさない。行は中止にせず順番表に残すので、
    `ndf lanes start` を打ち直せば同じ名前の計画が止めた所から続く。動いている計画だけを止められる。
    """
    view, rows = lane.read_view(conn, pid)
    if view.state != lane.RUNNING:
        raise model.Refused(f"計画 {view.name} は{model.label(view.state)}で、区切りで止められない（すぐ止めるのは --now）")
    _append(conn, pid, [model.EventDraft("plan_paused", "organizer", reason=reason)])
    return [r.sprint_id for name in view.lanes for r in rows[name] if r.state == lane.RUNNING and r.sprint_id]


def stop_plan(conn: sa.Connection, pid: str, reason: str) -> list[str]:
    """計画を中止する（オーガナイザー）。未着手の行を中止にし、実行中の行のスプリントの ID を返す。

    実行中のスプリントはオーガナイザーが書かず、レーンの conductor が `ndf progress cancel` で中止にする。
    """
    view, _ = lane.read_plan(conn, pid)
    if model.is_terminal(view.state):
        raise model.Refused(f"計画 {view.name} は{model.label(view.state)}で、止められない")
    running = lane.cancel_pending(conn, view, reason)
    _append(conn, pid, [model.EventDraft("plan_cancelled", "organizer", reason=reason)])
    return running


def open_request(conn: sa.Connection, pid: str, body: dict[str, Any], reason: str, actor: str) -> str:
    """計画の進行表へ依頼を開く（`request_opened`）。依頼の鍵を返す。"""
    view, _ = lane.read_plan(conn, pid)
    key = f"{lane.REQUEST_PREFIX}{len(view.requests) + 1}"
    payload = {"lane": body.get("lane"), "body": body}
    _append(conn, pid, [model.EventDraft("request_opened", actor, key, reason=reason, payload=payload)])
    return key


def close_request(conn: sa.Connection, pid: str, key: str, reason: str) -> None:
    """答えていない依頼を採らずに閉じる（オーガナイザー）。"""
    _check_request(conn, pid, key)
    _append(conn, pid, [model.EventDraft("request_closed", "organizer", key, reason=reason)])


# まとめたリリースの答え
RELEASED, REJECTED = "released", "rejected"
RELEASE_RESULTS = (RELEASED, REJECTED)


@dataclass(frozen=True)
class ReleaseAnswer:
    """まとめたリリースの答え。`result` は released か rejected、`version` と `release` は出した版とリリース記録の PR。"""

    result: str
    reason: str
    version: Optional[str] = None
    release: Optional[str] = None


def record_release(conn: sa.Connection, pid: str, keys: list[str], answer: ReleaseAnswer) -> None:
    """まとめたリリースの答えを計画の進行表へ記録する（オーガナイザー）。

    まとめた依頼 `keys` を同じトランザクションで閉じ、閉じる事象に答え（released か rejected・版・リリース記録の PR・
    まとめた依頼の鍵）を載せる。各スプリントのリリース記録と差し戻しの数はこの記録から読む
    （差し戻しを承認ゲートの記録 gate-rejections へ写すのは承認ゲート P2 #3295）。
    """
    if answer.result not in RELEASE_RESULTS:
        raise model.Refused(f"まとめたリリースの答え {answer.result} は {' / '.join(RELEASE_RESULTS)} のどれでもない")
    if not keys or len(set(keys)) != len(keys):
        raise model.Refused("まとめたリリースの依頼が無いか、同じ依頼が重なっている")
    if answer.result == RELEASED and not (answer.version and answer.release):
        raise model.Refused("リリースした答えには版とリリース記録の PR が要る")
    for key in keys:
        _check_request(conn, pid, key)
    done = {"result": answer.result, "version": answer.version, "release": answer.release, "batch": list(keys)}
    drafts = [model.EventDraft("request_closed", "organizer", key, reason=answer.reason, payload={"release": done}) for key in keys]
    _append(conn, pid, drafts)


def release_counts(view: lane.PlanView) -> dict[str, int]:
    """まとめたリリースの答えの数（依頼ごと。released・rejected）。"""
    got = [r.release["result"] for r in view.requests.values() if r.release]
    return {k: got.count(k) for k in RELEASE_RESULTS}


def complete_plan(conn: sa.Connection, pid: str) -> bool:
    """計画を完了にする（耐久ワークフロー）。全レーンの行が終わり、答えていない依頼が無いときだけ書く（I16）。"""
    view, rows = lane.read_view(conn, pid)
    if view.state != lane.RUNNING or not lane.plan_complete(view, rows):
        return False
    _append(conn, pid, [model.EventDraft("plan_completed", "durable")])
    return True


# --- 状態の表示 -----------------------------------------------------------------------------------


def _row_item(row: lane.LaneRow, now: datetime) -> dict[str, Any]:
    item = {"row": row.key, "state": row.state, "label": model.label(row.state), "input": row.input, "sprint_id": row.sprint_id}
    if row.awaiting_next:
        item["awaiting_next"] = {
            "since": row.answered_at.isoformat() if row.answered_at else None,
            "stalled": row.stalled(now, ANSWER_NEXT_GRACE_SECONDS),
            "actor": "conductor",
            "next": result.command("progress", "next", row.sprint_id) if row.sprint_id else None,
        }
    return item


def status(conn: sa.Connection, pid: str, now: datetime, probes: Optional[liveness.Probes] = None) -> dict[str, Any]:
    """計画の状態を、計画の進行表とレーンの進行表の 2 つから作る（受け入れ条件 4）。

    `probes` があれば、実行中の行を持つレーンの生死（`liveness`）を載せ、止まり（`stalled`・`ended`）を `warnings` に
    足す。承認ゲートや問いの答えを待つ行は正常な待ちで、止まりにしない。
    """
    view, rows = lane.read_view(conn, pid)
    verdicts = liveness.judge_lanes(rows, probes) if probes is not None else {}
    items = []
    for name, spec in view.lanes.items():
        lane_rows = [_row_item(r, now) for r in rows[name]]
        item = {"lane": name, "state": lane.lane_state(view, rows, name), "after": list(spec.after), "rows": lane_rows}
        if name in verdicts:
            item["liveness"] = verdicts[name].as_dict()
        items.append(item)
    stalls = [
        f"レーン {i['lane']} の行 {r['row']}: 答えが載った後に conductor が next を打っていない（{r['awaiting_next']['since']} から）"
        for i in items
        for r in i["rows"]
        if "awaiting_next" in r and r["awaiting_next"]["stalled"]
    ]
    stalls += [f"レーン {name} の行 {v.row}: {v.reason}" for name, v in verdicts.items() if v.stall]
    metrics = {
        "plan_id": pid,
        "plan": view.name,
        "state": view.state,
        "max_lanes": view.max_lanes,
        "max_plans": view.max_plans,
        "wakeable": [list(w) for w in lane.wakeable_lanes(view, rows)],
        "open_requests": [{"request": r.key, "lane": r.lane, "reason": r.reason} for r in view.open_requests()],
        "complete": lane.plan_complete(view, rows),
        "stalled": len(stalls),
        "releases": release_counts(view),
    }
    summary = f"計画 {view.name} は{model.label(view.state)}（レーン {len(items)}・止まり {len(stalls)}）"
    return result.result(TOOL, result.OK, summary, items=items, metrics=metrics, warnings=stalls)
