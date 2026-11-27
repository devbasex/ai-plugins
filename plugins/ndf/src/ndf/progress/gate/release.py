"""レーンのリリースのリクエスト（リリース承認ゲート。`ndf.progress.gate` の部品）。

リクエストは外のオーガナイザーの下ならファイル（`external`）、計画のステートマシンのレーンならリクエストの事象（`plan.open_request`）で出し、
リクエストへの回答を承認ゲートの回答（`organizer`）として書く。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf.contracts.worker_kinds import DELIVERY, PRODUCT_VERSION, store_kind_of
from ndf.core import production_merge
from ndf.progress import lane, model, plan, run, store
from ndf.progress.gate.answering import Answer, Observe, Post, answer, no_observe, no_post
from ndf.progress.gate.asking import (
    ORGANIZER_ANSWERS,
    RELEASE_GATE,
    RELEASE_KIND,
    Question,
    Target,
    material_path,
    presented_id,
    read_presented,
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
    """リクエストを出したときに見た対象の保存先（提示ごと。`q.target` は提示した承認の対象）。"""
    return material_path(root, q).with_name(presented_id(q) + SEEN_SUFFIX)


def _as_presented(root: Path, q: Question) -> Question:
    """待っている質問に、提示した承認の対象（`read_presented`）を持たせる（リクエストの ID と見た対象を提示ごとに分ける）。"""
    return replace(q, target=read_presented(root, q))


def request_release(st: store.Store, v: run.View, q: Question, env: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """リリース承認ゲートの待ちで、レーンのリリースのリクエストを出す（同じ質問には 1 回だけ）。出した先を返す。

    外のオーガナイザーの下（`NDF_ORGANIZER_DIR`）なら `requests/<id>.json`（`external.write_request`）、計画のステートマシンの
    レーンならリクエストの事象（`plan.open_request`）。どちらでもなければ出さない（人が `ndf answer` で回答する）。
    リクエストを出したときに見た対象は承認資料の隣に残し、回答を書くときの見た対象にする。`q.target` は提示した承認の
    対象（`present` が `write_material` の後に渡す）で、リクエストの ID と見た対象の保存先は提示ごとに分かれる。
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
            _close_earlier(conn, plan_id, q)
            body = {**_request_fields(v, q, name), "kind": RELEASE_KIND}
            key = plan.open_request(conn, plan_id, body, f"レーン {name} のリリースのリクエスト", run.ACTOR)
    return {"kind": "plan", "id": request_id(q), "plan": plan_id, "request": key}


def _close_earlier(conn: Any, plan_id: str, q: Question) -> None:
    """同じ質問の、出し直す前の提示へのリクエストで回答の無いものを閉じる（古い資料への回答を新しい提示に使わない）。"""
    view, _ = lane.read_plan(conn, plan_id)
    prefix, now = f"{RELEASE_KIND}-{q.id}", request_id(q)
    for r in view.requests.values():
        ident = r.body.get("id")
        if r.body.get("kind") != RELEASE_KIND or not isinstance(ident, str) or ident == now or not ident.startswith(prefix):
            continue
        if r.closed is None and r.release is None:
            plan.close_request(conn, plan_id, r.key, "待ちの間に承認の対象が変わり、資料を出し直した", run.ACTOR)


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
RELEASED_OUTPUTS = {"version": PRODUCT_VERSION, "release": "release_pr"}
# 回答に残せるバージョンの保存先の種類（`ndf lanes release` が記録し、外のオーガナイザーは回答のファイルに書ける）
RELEASE_STORES = (store_kind_of(False), store_kind_of(True), store_kind_of(None))


def _recorded_where(reply: Mapping[str, Any]) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    """オーガナイザーがリリース記録に残した、バージョンの保存先の種類と本番の届け先の 1 行（#4513）。

    レーンが回答を受け取る時点の設定で決め直さない（その間に設定が変わると、リリースしていない届け先を記録する）。種類が 3 つの語の
    どれでもなければ記録されていないもの（None）とする。届け先は、種類が `none` で、本番に届く行（`production_delivery` と同じ
    `on_production_channel` の条件）で、文脈の `delivery` の形に合うときだけ返す。外のオーガナイザーがベースブランチ向けの行を書いても、
    リリースの実績にしない。
    """
    kind = reply.get("store")
    known = kind if kind in RELEASE_STORES else None
    row = reply.get("delivery")
    production = production_merge.on_production_channel(row)
    if known != store_kind_of(False) or not production or not isinstance(row, Mapping) or not production_merge.fits_context(row):
        return known, None
    return known, dict(row)


def _released(reply: Mapping[str, Any], value: str, row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """出した回答が持つバージョンとリリース記録の PR（文脈の `product_version`・`release_pr`）。後の工程（閉じ・リリース後テスト）は
    文脈から読み、リリースの PR を探さない（#2783）。承認しない回答や、値の無いキーは書かない。

    オーガナイザーがリリース記録に残した届け先 `row`（`_recorded_where` が返したもの）があれば `delivery` として足す（バージョン番号を
    持たないリポジトリで、本番に届く行が 1 つに決まったときだけ残る）。デプロイの工程はこの記録だけから `delivery` を返し、worker を起動しない（#4513）。
    """
    if value != run.APPROVED:
        return {}
    out: dict[str, Any] = {name: str(reply[key]) for key, name in RELEASED_OUTPUTS.items() if reply.get(key)}
    if row is not None:
        out[DELIVERY] = row
    return out


def take_reply(
    st: store.Store, machine_id: str, env: Mapping[str, str], observe: Observe = no_observe, post: Post = no_post
) -> Optional[dict[str, Any]]:
    """リリース承認ゲートが待つリクエストに回答があれば、承認ゲートの回答（`organizer`）として書く。書いた結果を返す。

    見た対象はリクエストを出したときの対象で、その後に PR の head が変わっていれば回答は断られる（今の対象との検証は `answer`）。
    読むのは今の提示へのリクエストの回答だけで、資料を出し直す前のリクエストへの回答は読まない。
    """
    q = waiting(run.read(st, machine_id))
    if q is None or q.gate != RELEASE_GATE:
        return None
    q = _as_presented(st.root, q)
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
    # 回答の種類と届け先は承認ごとに 1 回だけ解釈し、出力と release_store の両方に使う
    store_kind, row = _recorded_where(reply) if value == run.APPROVED else (None, None)
    given = Answer(
        q.id,
        value,
        reason,
        "organizer",
        f"organizer:{request_id(q)}",
        seen=target,
        outputs=_released(reply, value, row),
        release_store=store_kind,
    )
    return answer(st, machine_id, given, observe, post)
