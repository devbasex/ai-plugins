"""レーンのリリースの依頼（リリース承認ゲート。`ndf.progress.gate` の部品）。

依頼は外のオーガナイザーの下ならファイル（`external`）、計画の進行表のレーンなら依頼の事象（`plan.open_request`）で出し、
依頼への答えを承認ゲートの答え（`organizer`）として書く。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf.progress import lane, model, plan, run, store
from ndf.progress.gate.answering import Answer, Observe, answer, no_observe
from ndf.progress.gate.asking import RELEASE_KIND, Question, Target, material_path, plan_request, request_id, waiting
from ndf.progress.plan import external

# 外のオーガナイザーの答え → 承認ゲートの答え
ORGANIZER_ANSWERS = {plan.RELEASED: run.APPROVED, plan.REJECTED: "rejected"}
# 依頼を出したときに見た対象の置き場（承認資料の隣）
SEEN_SUFFIX = ".seen.json"


def _request_fields(v: run.View, q: Question, lane_name: Optional[str]) -> dict[str, Any]:
    ctx = v.context()
    issues = sorted(int(n) for n in ctx["issues"]) if isinstance(ctx.get("issues"), list) else []
    return {
        "id": request_id(q),
        "lane": lane_name,
        "prs": [q.pr] if q.pr is not None else [],
        "issues": issues,
        "sprint_state": v.table_id,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _seen_path(root: Path, q: Question) -> Path:
    return material_path(root, q).with_name(q.id + SEEN_SUFFIX)


def request_release(st: store.Store, v: run.View, q: Question, env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """リリース承認ゲートの待ちで、レーンのリリースの依頼を出す（同じ問いには 1 回だけ）。出した先を返す。

    外のオーガナイザーの下（`NDF_ORGANIZER_DIR`）なら `requests/<id>.json`（`external.write_request`）、計画の進行表の
    レーンなら依頼の事象（`plan.open_request`）。どちらでもなければ出さない（人が `ndf answer` で答える）。
    依頼を出したときに見た対象は承認資料の隣に残し、答えを書くときの見た対象にする。
    """
    if q.gate != "release":
        return None
    seen = _seen_path(st.root, q)
    if not seen.exists():
        seen.write_text(json.dumps({"head": q.target.head, "material_sha256": q.target.material}) + "\n", encoding="utf-8")
    root = external.organizer_dir(env)
    if root is not None:
        version = env["NDF_VERSION"] if env.get("NDF_VERSION") else lane.ndf_version()
        if version is None:
            raise model.Refused("NDF の版が読めず、リリースの依頼の ndf_version を書けない")
        body = external.request_body(_request_fields(v, q, env.get("NDF_ORGANIZER_LANE")), version)
        return {"kind": "file", "id": request_id(q), "path": str(external.write_request(root, body))}
    with store.transaction(st) as conn:
        found = lane.sprint_row(conn, v.table_id)
        if found is None:
            return None
        plan_id, name, _row = found
        view, _ = lane.read_plan(conn, plan_id)
        held = plan_request(view, request_id(q))
        key = held.key if held is not None else None
        if key is None:
            body = {**_request_fields(v, q, name), "kind": RELEASE_KIND}
            key = plan.open_request(conn, plan_id, body, f"レーン {name} のリリースの依頼", run.ACTOR)
    return {"kind": "plan", "id": request_id(q), "plan": plan_id, "request": key}


def _organizer_reply(st: store.Store, q: Question, env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """依頼への答え（外のオーガナイザーのファイルか、計画の進行表のまとめたリリースの答え）。無ければ None。"""
    root = external.organizer_dir(env)
    if root is not None:
        return external.read_answer(root, request_id(q))
    with store.transaction(st) as conn:
        found = lane.sprint_row(conn, q.table_id)
        if found is None:
            return None
        view, _ = lane.read_plan(conn, found[0])
    held = plan_request(view, request_id(q))
    return dict(held.release) if held is not None and held.release is not None else None


# まとめたリリースの答えの鍵 → スプリントの文脈の鍵（デプロイの出力）
RELEASED_OUTPUTS = {"version": "version", "release": "release_pr"}


def _released(reply: Mapping[str, Any], value: str) -> dict[str, Any]:
    """出した答えが持つ版とリリース記録の PR（文脈の `version`・`release_pr`）。後の工程（閉じ・リリース後テスト）は
    文脈から読み、リリースの PR を探さない（#2783）。承認しない答えや、値の無い鍵は書かない。"""
    if value != run.APPROVED:
        return {}
    return {name: str(reply[key]) for key, name in RELEASED_OUTPUTS.items() if reply.get(key)}


def take_reply(st: store.Store, table_id: str, env: Mapping[str, str], observe: Observe = no_observe) -> Optional[dict[str, Any]]:
    """リリース承認ゲートが待つ依頼に答えがあれば、承認ゲートの答え（`organizer`）として書く。書いた結果を返す。

    見た対象は依頼を出したときの対象で、その後に PR の head が変わっていれば答えは断られる（今の対象との照合は `answer`）。
    """
    q = waiting(run.read(st, table_id))
    if q is None or q.gate != "release":
        return None
    reply = _organizer_reply(st, q, env)
    if reply is None or reply.get("result") not in ORGANIZER_ANSWERS:
        return None
    value = ORGANIZER_ANSWERS[str(reply["result"])]
    if value not in q.choices:
        return None
    seen_file = _seen_path(st.root, q)
    seen = json.loads(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else {}
    reason = str(reply["reason"]) if reply.get("reason") else f"オーガナイザーのリリースの答え（{reply['result']}）"
    target = Target(seen.get("head"), seen.get("material_sha256"))
    given = Answer(q.id, value, reason, "organizer", f"organizer:{request_id(q)}", seen=target, outputs=_released(reply, value))
    return answer(st, table_id, given, observe)
