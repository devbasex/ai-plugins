"""溜めた依頼（#2913）: 業務時間外にオーガナイザーへ届いた設計の承認と問い（`gate1`・`ask`）を `parallel.py wait` が返さずに控え、
業務時間に入った後の最初の `wait` が答えていない依頼の全件を 1 回でまとめて返す。

控え（`.ndf/parallel/<計画名>/deferred.json`。コミットしない）は依頼の一覧を id で参照するだけで、依頼そのものを持たない:

    {"version": 1, "ids": [...], "offered": [...], "shown": [...], "since": "<UTC>", "until": "<UTC>"}

- `ids`: 溜めた依頼。業務時間外に 1 回だけ返した MVV 判定を試せる依頼（`offered`）も入れる（I6）
- `shown`: 業務時間内の `wait` が利用者へ示した `gate1`・`ask`。業務時間外の `wait` は溜めも返しもしない（I5）
- 答えた依頼（一覧に無い id）は書くたびに外す。読めなければ空と読み、依頼の一覧から作り直す（問う方へは倒さない）

業務時間の宣言が無い・不正なら、控えを読み書きせず `wait` は今と同じに動く。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import clock
import gh_call
import jsonio
import pace
import parallel_answer as pa
import parallel_hold as ph
import parallel_plan as pp

FILE = "deferred.json"
DEFER_KINDS = ph.LANE_KINDS  # 溜めるのはレーンが送った依頼だけ（決定 7）
KEYS = ("ids", "offered", "shown")


def control_path(main: Path, name: str) -> Path:
    return pp.plan_dir(main, name) / FILE


def read_control(main: Path, name: str) -> dict:
    """控えを読む。無い・読めなければ空の控え（I5 の読めないとき）。"""
    got = jsonio.read(pp.plan_dir(main, name) / FILE, missing={}, broken={}, want=dict)
    out = {k: [str(x) for x in got.get(k) or [] if isinstance(x, str)] if isinstance(got.get(k), list) else [] for k in KEYS}
    return {"version": 1, **out, "since": got.get("since"), "until": got.get("until")}


def write_control(main: Path, name: str, d: dict, items: list[dict]) -> None:
    """控えを書く。答えた依頼（今の一覧に無い id）を `ids`・`offered`・`shown` から外す。"""
    live = {i for q in items for i in (q.get("id"), *(q.get("also") or [])) if i}
    out = {**d, **{k: sorted(set(d.get(k) or []) & live) for k in KEYS}}
    if not out["ids"]:
        out.update(since=None, until=None)
    jsonio.write_atomic(pp.plan_dir(main, name) / FILE, out)


def hours_of(main: Path, now: _dt.datetime | None = None) -> dict:
    return pace.hours_now(main, now or clock.now(utc=True))


def clip_timeout(timeout: float, hours: dict, now: _dt.datetime | None = None) -> float:
    """業務時間外の待ちの上限を、次の業務時間の始まりで切る（始まりの後の最初の `wait` がまとめて返すため）。"""
    if not hours["declared"] or hours["inside"] or not hours.get("next_start"):
        return timeout
    left = (clock.parse(hours["next_start"], naive="utc") - (now or clock.now(utc=True))).total_seconds()
    return max(1.0, min(timeout, left))


def _gh_text(args: list[str], cwd: Path) -> str | None:
    got = gh_call.gh(args, cwd=str(cwd))
    return got.stdout.strip() or None if got.returncode == 0 else None


def annotate(main: Path, name: str, items: list[dict]) -> tuple[list[dict], int]:
    """まとめて示す依頼の設計の承認へ課題の題（`title`）・設計 PR の URL（`url`）・`verdict`・`mvv` を添える。
    引けなければ null にし、引けなかった件数を返す。"""
    out, errors = [], 0
    for q in items:
        if q.get("kind") != "gate1":
            out.append(q)
            continue
        repo = pa.lane_repo(main, name, pa.repo_lane(q))
        title = _gh_text(["issue", "view", str(q["issue"]), "--json", "title", "-q", ".title"], repo) if q.get("issue") else None
        url = _gh_text(["pr", "view", str(q["pr"]), "--json", "url", "-q", ".url"], repo) if q.get("pr") else None
        errors += (title is None) + (url is None)
        out.append({**q, "title": title, "url": url, "verdict": q.get("verdict"), "mvv": pa.mvv_mark(q)})
    return out, errors


def due_batch(main: Path, name: str, hours: dict) -> tuple[list[dict], dict] | None:
    """業務時間内で控えの `ids` が空でなければ、答えていない依頼の全件と `metrics` を返し、控えの `ids` を空にして示した id を
    `shown` へ足す（E7）。まとめるものが無ければ None。"""
    if not hours["declared"] or not hours["inside"]:
        return None
    d = read_control(main, name)
    if not d["ids"]:
        return None
    items = pa.with_repo(main, name, pa.questions(main, name)[1])
    live = {i for q in items for i in (q.get("id"), *(q.get("also") or []))}
    if not set(d["ids"]) & live:
        write_control(main, name, d, items)
        return None
    out, errors = annotate(main, name, items)
    shown = set(d["shown"]) | {q["id"] for q in items if q.get("kind") in DEFER_KINDS}
    write_control(main, name, {**d, "ids": [], "offered": [], "shown": sorted(shown)}, items)
    return out, {"batch": True, **({"annotate_errors": errors} if errors else {})}


def route(main: Path, name: str, items: list[dict], hours: dict) -> tuple[list[dict], dict]:
    """`wait` が受けた依頼の一覧 `items` から、今オーガナイザーへ返す依頼と `metrics` を決める。宣言が無ければそのまま返す。

    業務時間内は全件を返し、`gate1`・`ask` の id を `shown` へ足す。業務時間外は、`gate1`・`ask` 以外と、まだ返していない
    MVV 判定を試せる設計の承認だけを返し（後者は `offered` と `ids` へ入れる）、ほかの `gate1`・`ask` は `shown` に無ければ溜める。"""
    if not hours["declared"]:
        return items, {}
    d = read_control(main, name)
    if hours["inside"]:
        shown = set(d["shown"]) | {q["id"] for q in items if q.get("kind") in DEFER_KINDS}
        write_control(main, name, {**d, "shown": sorted(shown)}, items)
        return items, {}
    ids, offered, shown, now_ask = set(d["ids"]), set(d["offered"]), set(d["shown"]), []
    for q in items:
        qid = q.get("id")
        if q.get("kind") not in DEFER_KINDS:
            now_ask.append(q)
        elif qid in shown:
            continue
        elif pa.mvv_mark(q)["eligible"] and qid not in offered:
            now_ask.append(q)
            offered.add(qid)
            ids.add(qid)
        else:
            ids.add(qid)
    since = d["since"] or (clock.now_iso("utc") if ids else None)
    new = {**d, "ids": sorted(ids), "offered": sorted(offered), "since": since, "until": hours.get("next_start")}
    write_control(main, name, new, items)
    kept = read_control(main, name)["ids"]
    return now_ask, {"deferred": {"count": len(kept), "until": hours.get("next_start")}}
