"""組み換え（#3092 の F10・決定 11・受け入れ条件 19〜21。#3098 から取り込んだ）。

振り返りの直後に、次以降のスプリントの順番表を必要なときだけ変える。要否の判定（`needs_reshuffle`）は純粋な
処理で、持ち越す課題も組を変える理由も無ければ Claude を起動しない。変更案（`propose`）は Claude が作り、
自分のレーンの中の案はレーンの進行表のスクリプトの検査を通して反映し、レーンをまたぐ案と断られた案は
オーガナイザーへの依頼（計画の進行表の `request_opened`）にする（`apply_changes`）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Optional

import limits
import progress_lanes as plr
import progress_model as pm

INPUT_KINDS = ("carry", "dropped", "held", "filed")  # 振り返りの持ち越し・外した課題・保留した受け入れ条件・起票した課題
PROPOSE_TIMEOUT = limits.seconds("progress.reshuffle-propose")
TOOL = "reshuffle"


def _own_overlaps(lane: Optional[str], plan: Optional[dict], lanes: dict) -> list[str]:
    if not plan or not lane:
        return []
    return [why for rule, why in plr._violations(plan, lanes) if rule == "I13" and f"{lane}#" in why]


def needs_reshuffle(inp: dict, plan: Optional[dict], lanes: dict) -> bool:
    """入力の 4 種類がすべて空で、自分のレーンと他のレーンの触る場所の重なりが無いときだけ偽。"""
    if any(inp.get(k) for k in INPUT_KINDS):
        return True
    return bool(_own_overlaps(inp.get("lane"), plan, lanes))


def unchanged(summary: str = "持ち越す課題も組を変える理由も無い") -> dict:
    return {
        "tool": TOOL,
        "status": "ok",
        "summary": f"変更なし（{summary}）",
        "decision": "unchanged",
        "lane_changes": [],
        "requests": [],
        "refused": [],
    }


PROMPT = """あなたは並列の計画の 1 レーンの組み換えを考える。次の入力から、自分のレーンの順番表への変更案を JSON 1 つで返す。
- 変更は op（add / move / cancel）・issues（課題番号の配列。add のとき）・row（move・cancel の行）・to（move の位置）・reason を持つ
- 自分のレーンの中の変更は lane_changes、別のレーンへ移す・レーンを足すものは requests（lane に移す先）に入れる
- 実行中と完了の行は動かさない。変える理由が無ければ両方を空にする
返す形: {{"lane_changes": [...], "requests": [...]}}

入力: {inp}
自分のレーンの順番表: {lane}
"""


def propose(inp: dict, lane: list[dict], claude: Optional[str] = None) -> dict:
    """Claude（`claude -p`）で変更案を作る。出力の最後の JSON の辞書を返す。起動の失敗は RuntimeError。"""
    exe = claude or shutil.which("claude")
    if not exe:
        raise RuntimeError("claude が無い")
    text = PROMPT.format(inp=json.dumps(inp, ensure_ascii=False), lane=json.dumps(lane, ensure_ascii=False))
    try:
        p = subprocess.run([exe, "-p", text], capture_output=True, text=True, timeout=PROPOSE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude が {PROPOSE_TIMEOUT} 秒で終わらない") from None
    if p.returncode != 0:
        raise RuntimeError(f"claude が {p.returncode} で終わった")
    for m in reversed(list(re.finditer(r"\{.*\}", p.stdout, re.S))):
        try:
            got = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(got, dict):
            return got
    raise RuntimeError("claude の出力に JSON の変更案が無い")


def _change_draft(change: dict, actor: str, next_row: int) -> pm.EventDraft:
    op = change["op"]
    if op == "add":
        return pm.EventDraft(
            "row_added",
            actor,
            str(next_row),
            reason=change["reason"],
            payload={"row": next_row, "issues": change.get("issues") or [], "touches": change.get("touches") or []},
        )
    if op == "move":
        return pm.EventDraft(
            "row_moved", actor, str(change["row"]), reason=change["reason"], payload={"row": change["row"], "to": change["to"]}
        )
    return pm.EventDraft("row_cancelled", actor, str(change["row"]), reason=change["reason"], payload={"row": change["row"]})


def apply_changes(store, sprint_id: str, proposal: dict, actor: str = "workflow") -> dict:
    """自分のレーンの中の案を反映し、レーンをまたぐ案と断られた案を依頼にする。すべての案がどれかに入る。"""
    import progress_db as pdb

    applied, requests, refused = [], [], []
    with pdb.transaction(store) as conn:
        link = pdb.sprint_link(conn, sprint_id)
        lane_id = link[0] if link else None
        lane_row = pdb.progress_table(conn, lane_id) if lane_id else None
        plan_id = lane_row.parent_id if lane_row else None
        for ch in proposal.get("lane_changes") or []:
            if lane_id is None:
                refused.append({**ch, "reason": f"{ch.get('reason', '')}（計画の外のスプリント）"})
                continue
            rows = plr.lane_order_rows(pdb.read_progress_events(conn, lane_id))
            n = max((r["row"] for r in rows), default=0) + 1
            try:
                pdb.lane_write(conn, lane_id, _change_draft(ch, actor, n))
                applied.append(ch)
            except pm.Refused as e:
                refused.append({**ch, "reason": f"{ch.get('reason', '')}（断られた: {e.reason}）"})
        for item in (proposal.get("requests") or []) + refused:
            if plan_id is not None:
                rid = pdb.open_request(conn, plan_id, {"change": item, "from": sprint_id}, item.get("reason") or "組み換えの依頼", actor)
                item.setdefault("request", rid)
            if item not in refused:
                requests.append(item)
    decision = "changed" if applied or requests or refused else "unchanged"
    summary = f"反映 {len(applied)} 件・依頼 {len(requests)} 件・断られた {len(refused)} 件"
    return {
        "tool": TOOL,
        "status": "ok",
        "summary": summary,
        "decision": decision,
        "lane_changes": applied,
        "requests": requests,
        "refused": refused,
    }
