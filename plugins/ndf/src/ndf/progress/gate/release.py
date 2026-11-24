"""レーンのリリースのリクエスト（リリース承認ゲート。`ndf.progress.gate` の部品）。

リクエストは外のオーガナイザーの下ならファイル（`external`）、計画のステートマシンのレーンならリクエストの事象（`plan.open_request`）で出し、
リクエストへの回答を承認ゲートの回答（`organizer`）として書く。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf.progress import lane, model, plan, run, store
from ndf.progress.gate.answering import Answer, Observe, Post, answer, no_observe, no_post
from ndf.progress.gate.asking import (
    ORGANIZER_ANSWERS,
    RELEASE_GATE,
    RELEASE_KIND,
    Question,
    Target,
    material_path,
    release_request_of,
    request_id,
    sprint_issues,
    waiting,
)
from ndf.progress.plan import external

# 外のオーガナイザーの回答 → 承認ゲートの回答
# リクエストを出したときに見た対象の保存先（承認資料の隣）
SEEN_SUFFIX = ".seen.json"


def _request_fields(v: run.View, q: Question, lane_name: Optional[str]) -> dict[str, Any]:
    issues = sprint_issues(v)
    return {
        "id": request_id(q),
        "lane": lane_name,
        "prs": [q.pr] if q.pr is not None else [],
        "issues": issues,
        "sprint_state": v.machine_id,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _seen_path(root: Path, q: Question) -> Path:
    return material_path(root, q).with_name(q.id + SEEN_SUFFIX)


def request_release(st: store.Store, v: run.View, q: Question, env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """リリース承認ゲートの待ちで、レーンのリリースのリクエストを出す（同じ質問には 1 回だけ）。出した先を返す。

    外のオーガナイザーの下（`NDF_ORGANIZER_DIR`）なら `requests/<id>.json`（`external.write_request`）、計画のステートマシンの
    レーンならリクエストの事象（`plan.open_request`）。どちらでもなければ出さない（人が `ndf answer` で回答する）。
    リクエストを出したときに見た対象は承認資料の隣に残し、回答を書くときの見た対象にする。
    """
    if q.gate != RELEASE_GATE:
        return None
    seen = _seen_path(st.root, q)
    if not seen.exists():
        seen.parent.mkdir(parents=True, exist_ok=True)
        seen.write_text(json.dumps(q.target.as_dict()) + "\n", encoding="utf-8")
    root = external.organizer_dir(env)
    if root is not None:
        return _file_request(v, q, env, root)
    return _plan_request(st, v, q)


def _file_request(v: run.View, q: Question, env: Mapping[str, str], root: Path) -> dict[str, Any]:
    """外のオーガナイザーの下のリクエストのファイルを書き、出した先を返す。バージョンは `NDF_VERSION`、無ければプラグインのバージョン。"""
    version = env["NDF_VERSION"] if env.get("NDF_VERSION") else lane.ndf_version()
    if version is None:
        raise model.Refused("NDF のバージョンが読めず、リリースのリクエストの ndf_version を書けない")
    body = external.request_body(_request_fields(v, q, env.get("NDF_ORGANIZER_LANE")), version)
    return {"kind": "file", "id": request_id(q), "path": str(external.write_request(root, body))}


def _plan_request(st: store.Store, v: run.View, q: Question) -> Optional[dict[str, Any]]:
    """計画のステートマシンへリクエストの事象を書き（同じ質問のリクエストがあれば使い直す）、出した先を返す。レーンの外なら None。"""
    with store.transaction(st) as conn:
        found = release_request_of(conn, q)
        if found is None:
            return None
        plan_id, name, held = found
        key = held.key if held is not None else None
        if key is None:
            body = {**_request_fields(v, q, name), "kind": RELEASE_KIND}
            key = plan.open_request(conn, plan_id, body, f"レーン {name} のリリースのリクエスト", run.ACTOR)
    return {"kind": "plan", "id": request_id(q), "plan": plan_id, "request": key}


def _organizer_reply(st: store.Store, q: Question, env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """リクエストへの回答（外のオーガナイザーのファイルか、計画のステートマシンのまとめたリリースの回答）。無ければ None。"""
    root = external.organizer_dir(env)
    if root is not None:
        return external.read_answer(root, request_id(q))
    with store.transaction(st) as conn:
        found = release_request_of(conn, q)
    if found is None:
        return None
    held = found[2]
    return dict(held.release) if held is not None and held.release is not None else None


# まとめたリリースの回答のキー → スプリントの文脈のキー（デプロイの出力）
RELEASED_OUTPUTS = {"version": "product_version", "release": "release_pr"}


def _released(reply: Mapping[str, Any], value: str) -> dict[str, Any]:
    """出した回答が持つバージョンとリリース記録の PR（文脈の `product_version`・`release_pr`）。後の工程（閉じ・リリース後テスト）は
    文脈から読み、リリースの PR を探さない（#2783）。承認しない回答や、値の無いキーは書かない。"""
    if value != run.APPROVED:
        return {}
    return {name: str(reply[key]) for key, name in RELEASED_OUTPUTS.items() if reply.get(key)}


def take_reply(
    st: store.Store, machine_id: str, env: Mapping[str, str], observe: Observe = no_observe, post: Post = no_post
) -> Optional[dict[str, Any]]:
    """リリース承認ゲートが待つリクエストに回答があれば、承認ゲートの回答（`organizer`）として書く。書いた結果を返す。

    見た対象はリクエストを出したときの対象で、その後に PR の head が変わっていれば回答は断られる（今の対象との検証は `answer`）。
    """
    q = waiting(run.read(st, machine_id))
    if q is None or q.gate != RELEASE_GATE:
        return None
    reply = _organizer_reply(st, q, env)
    if reply is None or reply.get("result") not in ORGANIZER_ANSWERS:
        return None
    value = ORGANIZER_ANSWERS[str(reply["result"])]
    if value not in q.choices:
        return None
    seen_file = _seen_path(st.root, q)
    seen = json.loads(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else None
    reason = str(reply["reason"]) if reply.get("reason") else f"オーガナイザーのリリースの回答（{reply['result']}）"
    target = Target.from_dict(seen)
    given = Answer(q.id, value, reason, "organizer", f"organizer:{request_id(q)}", seen=target, outputs=_released(reply, value))
    return answer(st, machine_id, given, observe, post)
