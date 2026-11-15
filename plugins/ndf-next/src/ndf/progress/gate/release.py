"""レーンのリリースの依頼（リリース承認ゲート。`ndf.progress.gate` の部品）。

依頼は外のオーガナイザーの下ならファイル（`external`）、計画の進行表のレーンなら依頼の事象（`plan.open_request`）で出し、
依頼への答えを承認ゲートの答え（`organizer`）として書く。

答えが無いまま利用者が GitHub で本番の PR をマージしたら、それを承認ゲートの承認にする（リリース承認とマージは等価。
#3572 の決定 4）。証跡はリリースの副命令 `ndf release-steps answer --route github-merge` を子プロセスで打って書かせ、
ここでは承認ゲートの答えの状態だけを書く（リリースの領域を import せず、本番の PR のコメントを直接書かない）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import GitError
from ndf.contracts import ContractError
from ndf.progress import command, invoke, lane, model, plan, run, store
from ndf.progress.gate.answering import Answer, Observe, answer, no_observe
from ndf.progress.gate.asking import (
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

# 外のオーガナイザーの答え → 承認ゲートの答え
ORGANIZER_ANSWERS = {plan.RELEASED: run.APPROVED, plan.REJECTED: run.REJECTED}
# 依頼を出したときに見た対象の置き場（承認資料の隣）
SEEN_SUFFIX = ".seen.json"
# 人の GitHub でのマージを承認ゲートの答えにするときの答えた者と冪等の鍵の頭（マージした人のログインは理由に書く）
MERGE_ACTOR = "service:github-merge"
MERGE_KEY = "github-merge"
# マージされていないときの副命令の止まった理由（待ちを残す）
NOT_MERGED_KIND = "not_applicable"
# 宣言の本番ブランチの鍵（`ndf.domains.release.declaration.PRODUCTION_KEY` と同じ 1 鍵。領域を import しない）
PRODUCTION_KEY = "production_branch"


def _reply_values(report: Mapping[str, Any]) -> dict[str, Any]:
    """副命令の結果の値の項目（種類 value。`ndf.domains.release.cli.VALUE_KIND`。領域を import しない）と outputs の版。"""
    items = report["items"] if isinstance(report.get("items"), list) else []
    found = {str(i["name"]): i["value"] for i in items if isinstance(i, dict) and i.get("kind") == "value" and "name" in i}
    outputs = report["outputs"] if isinstance(report.get("outputs"), dict) else {}
    return {**found, **({"version": outputs["version"]} if "version" in outputs else {})}


def _request_fields(v: run.View, q: Question, lane_name: Optional[str]) -> dict[str, Any]:
    issues = sprint_issues(v)
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
    """外のオーガナイザーの下の依頼のファイルを書き、出した先を返す。版は `NDF_VERSION`、無ければプラグインの版。"""
    version = env["NDF_VERSION"] if env.get("NDF_VERSION") else lane.ndf_version()
    if version is None:
        raise model.Refused("NDF の版が読めず、リリースの依頼の ndf_version を書けない")
    body = external.request_body(_request_fields(v, q, env.get("NDF_ORGANIZER_LANE")), version)
    return {"kind": "file", "id": request_id(q), "path": str(external.write_request(root, body))}


def _plan_request(st: store.Store, v: run.View, q: Question) -> Optional[dict[str, Any]]:
    """計画の進行表へ依頼の事象を書き（同じ問いの依頼があれば使い直す）、出した先を返す。レーンの外なら None。"""
    with store.transaction(st) as conn:
        found = release_request_of(conn, q)
        if found is None:
            return None
        plan_id, name, held = found
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
        found = release_request_of(conn, q)
    if found is None:
        return None
    held = found[2]
    return dict(held.release) if held is not None and held.release is not None else None


# まとめたリリースの答えの鍵 → スプリントの文脈の鍵（デプロイの出力）
RELEASED_OUTPUTS = {"version": "version", "release": "release_pr"}


def _released(reply: Mapping[str, Any], value: str) -> dict[str, Any]:
    """出した答えが持つ版とリリース記録の PR（文脈の `version`・`release_pr`）。後の工程（閉じ・リリース後テスト）は
    文脈から読み、リリースの PR を探さない（#2783）。承認しない答えや、値の無い鍵は書かない。"""
    if value != run.APPROVED:
        return {}
    return {name: str(reply[key]) for key, name in RELEASED_OUTPUTS.items() if reply.get(key)}


def _merged_reply(q: Question, runner: invoke.Runner, cwd: Path) -> Optional[dict[str, Any]]:
    """副命令 `answer --route github-merge` を打ち、本番の PR がマージ済みなら結果の値（`state`・`by`・`head`・`version`）を返す
    （まだなら None）。

    ほかの止まり方・結果 JSON を読めない・起動できないときは `model.Refused`（待ちは残る）。
    """
    argv = invoke.ndf_command("release-steps", "answer", "--root", str(cwd), "--pr", str(q.pr), "--route", MERGE_KEY)
    try:
        done = runner(argv, cwd, invoke.ndf_env(os.environ))
    except OSError as exc:
        raise model.Refused(f"人のマージを読む副命令を起動できない: {exc}") from None
    got = invoke.read_report(done.stdout)
    if got is None:
        first = done.stderr.strip().splitlines()[:1]
        raise model.Refused(f"人のマージを読む副命令の結果 JSON を読めない（終了コード {done.returncode}）: {''.join(first)}")
    if got["status"] == "ok":
        return _reply_values(got)
    cause = got["cause"] if isinstance(got.get("cause"), dict) else {}
    if cause.get("kind") == NOT_MERGED_KIND:
        return None
    raise model.Refused(f"人のマージを承認の証跡にできない: {got.get('summary')}")


def _off_production(now: Target, cwd: Path) -> bool:
    """読み直した PR の宛先が、宣言（`.ndf/worktree.json` の `production_branch`）の本番ブランチと違うか。

    リリース承認ゲートの問いの PR は多くがスプリントの PR で、develop へマージ済みでも本番の PR ではない。その待ちの
    読み直しの度に副命令を起動しないために使う（#3587）。宛先か本番ブランチが分からなければ偽（副命令が確かめて止まる）。
    """
    if now.base is None:
        return False
    try:
        production = command.declaration(cwd, command.WORKTREE_FILE).get(PRODUCTION_KEY)
    except (GitError, ContractError):  # noqa: NDF401 宣言を読めなければ省かず、副命令が理由を添えて止まる
        return False
    return isinstance(production, str) and bool(production) and now.base != production


def _merge_answer(
    st: store.Store, table_id: str, q: Question, observe: Observe, runner: invoke.Runner, cwd: Path
) -> Optional[dict[str, Any]]:
    """本番の PR がマージ済みなら、承認ゲートの答えを承認にして書いた結果を返す（マージされていなければ None）。"""
    merged = _merged_reply(q, runner, cwd)
    if merged is None or merged.get("state") != run.APPROVED or run.APPROVED not in q.choices:
        return None
    head = str(merged["head"]) if merged.get("head") else ""
    reason = f"利用者 {merged.get('by')} が GitHub で本番の PR #{q.pr} を {head[:12]} でマージした（リリース承認とみなす）"
    outputs = {"version": str(merged["version"])} if merged.get("version") else {}
    outputs["release_pr"] = q.pr_url if q.pr_url else str(q.pr)
    given = Answer(q.id, run.APPROVED, reason, MERGE_ACTOR, f"{MERGE_KEY}:{q.id}", seen=observe(q), outputs=outputs)
    return answer(st, table_id, given, observe)


def take_reply(
    st: store.Store,
    table_id: str,
    env: Mapping[str, str],
    observe: Observe = no_observe,
    runner: Optional[invoke.Runner] = None,
    cwd: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """リリース承認ゲートが待つ依頼に答えがあれば、承認ゲートの答え（`organizer`）として書く。書いた結果を返す。

    見た対象は依頼を出したときの対象で、その後に PR の head が変わっていれば答えは断られる（今の対象との照合は `answer`）。
    答えが無く、問いが PR を持ち、本番の PR がマージ済みなら、人のマージを承認の答えとして書く（E6）。GitHub を読まない
    呼び出し（`observe` が `no_observe` で `runner` も無い）は人のマージを読まない。読み直し（`observe`）が未マージか、
    本番ブランチ宛てでない（`_off_production`）と返せば副命令を起動しない（待ちの読み直しの度に CLI を起動しない）。
    """
    q = waiting(run.read(st, table_id))
    if q is None or q.gate != RELEASE_GATE:
        return None
    reply = _organizer_reply(st, q, env)
    if reply is None or reply.get("result") not in ORGANIZER_ANSWERS:
        if q.pr is None or (runner is None and observe is no_observe):
            return None
        now = observe(q)
        place = cwd if cwd is not None else Path.cwd()
        if now.merged is False or _off_production(now, place):  # 未マージか本番宛てでなければ、副命令を起動しない
            return None
        return _merge_answer(st, table_id, q, observe, runner if runner is not None else invoke.run_command, place)
    return _organizer_answer(st, table_id, q, reply, observe)


def _seen_target(root: Path, q: Question) -> Target:
    """依頼を出したときに見た対象（承認資料の隣のファイル。無ければ `Target.from_dict(None)`）。"""
    seen_file = _seen_path(root, q)
    seen = json.loads(seen_file.read_text(encoding="utf-8")) if seen_file.exists() else None
    return Target.from_dict(seen)


def _organizer_answer(st: store.Store, table_id: str, q: Question, reply: Mapping[str, Any], observe: Observe) -> Optional[dict[str, Any]]:
    """オーガナイザーの答え `reply` を承認ゲートの答え（`organizer`）として書いた結果を返す（問いが選べない答えなら None）。"""
    value = ORGANIZER_ANSWERS[str(reply["result"])]
    if value not in q.choices:
        return None
    target = _seen_target(st.root, q)
    reason = str(reply["reason"]) if reply.get("reason") else f"オーガナイザーのリリースの答え（{reply['result']}）"
    given = Answer(q.id, value, reason, "organizer", f"organizer:{request_id(q)}", seen=target, outputs=_released(reply, value))
    return answer(st, table_id, given, observe)
