"""state_get.py: スプリントの状態と開発ループの記録を読むだけの取り出し（#2909 の F1・F2。用語集の F3 は `glossary_lookup.py`）。

`sprint-state.py get <sprint.json> [--field <JSON Pointer>]...` と `sprint-state.py loop get <ループ名> [--field ...]` の中身。
どちらも状態ファイルを書き換えず（I2）、読めた限り `ok` で返す（決定 3）。次の操作は `next` に置くだけで、続けてよいかは
判断しない。`--field` の評価は `json_pick.py` が持つ。

| 入力 | `--field` なし | `--field` あり |
| --- | --- | --- |
| スプリントの状態 | `items` に計画ごとの `{kind: plan, name, result, pr, next}`、`metrics` に `{plans, done, open}`、`next` に最初の終わっていない計画の `<名>: <次の欄>` | `metrics` に `{<指し先>: <値>}`。値が無い指し先は `null` で、`items` に `{kind: field, name, result: missing}` |
| 開発ループの記録 | `metrics` は `loop status --json` と同じ鍵、`next` は止まった理由・人の判断待ち・答え待ちの順に最初に当たったもの | 同上（ループの記録を根にする） |

終了コード: 0 = 読めた、2 = 読めない・指し先の書き方の誤り、3 = 状態ファイル・ループの記録が無い。
"""

from __future__ import annotations

from pathlib import Path

import json_pick
import jsonio
import step_result

TOOL = "sprint-state"
NOT_DONE = "まだ"  # 計画の行の状態で「まだ終わっていない」を表す語（`sprint-state.py` も同じ値を使う）


def _out(status: str, summary: str, items=None, metrics=None, next_: str | None = None, code: int | None = None) -> dict:
    out = step_result.result(TOOL, status, summary, items, metrics, next=next_)
    if code is not None:
        out["exit"] = code  # `sprint-state.py` の main が終了コードに読む
    return out


def _fields(doc, pointers, what: str) -> dict:
    values, missing = json_pick.pick(doc, pointers)
    summary = f"{what}から {len(pointers) - len(missing)}/{len(pointers)} 件の値を取り出した"
    return _out("ok", summary, json_pick.missing_items(missing), values)


def _read(path: Path, what: str):
    """読めれば (文書, None)。無い・読めなければ (None, 失敗の結果)。"""
    if not path.is_file():
        return None, _out("stopped", f"{what}が無い: {path}", code=step_result.EXIT_PRECONDITION)
    try:
        return jsonio.read(path, want=dict), None
    except jsonio.JsonReadError as e:
        return None, _out("stopped", f"{what}を読めない: {path}（{e.detail or e}）", code=step_result.EXIT_UNREADABLE)


def _bad_pointer(pointers) -> dict | None:
    try:
        json_pick.validate(pointers or [])
    except json_pick.PointerError as e:
        return _out("stopped", str(e), code=step_result.EXIT_UNREADABLE)
    return None


def plan_rows(m: dict) -> list[dict]:
    rows = []
    for p in m.get("plans", []):
        row = p.get("row") or {}
        rows.append(
            {
                "kind": "plan",
                "name": p.get("label") or p.get("plan", ""),
                "result": row.get("result") or NOT_DONE,
                "pr": row.get("pr") or "",
                "next": p.get("next") or "",
            }
        )
    return rows


def sprint_get(path: str, fields=None) -> dict:
    """`sprint-state.py get`。"""
    if (bad := _bad_pointer(fields)) is not None:
        return bad
    m, err = _read(Path(path), "スプリントの状態")
    if err is not None:
        return err
    if fields:
        return _fields(m, fields, "スプリントの状態")
    rows = plan_rows(m)
    open_rows = [r for r in rows if r["result"] == NOT_DONE]
    first = open_rows[0] if open_rows else None
    next_ = f"{first['name']}: {first['next'] or '—'}" if first else None
    summary = f"計画 {len(rows)} 本: 終わった {len(rows) - len(open_rows)} / 終わっていない {len(open_rows)}"
    return _out("ok", summary, rows, {"plans": len(rows), "done": len(rows) - len(open_rows), "open": len(open_rows)}, next_)


def add_parser(sub) -> None:
    """`sprint-state.py get` の引数を足す。"""
    s = sub.add_parser("get", help="読むだけ。計画の状態と次の操作、--field の値を返す（#2909）")
    s.add_argument("sprint")
    s.add_argument("--field", action="append", help="JSON Pointer（状態ファイルを根にする。繰り返し）")


def cmd_get(a) -> dict:
    return sprint_get(a.sprint, a.field)


def loop_next(rec: dict, metrics: dict) -> str | None:
    """開発ループの次の操作（設計の「開発ループの次の操作」の表を上から当てる）。"""
    import sprint_loop_record as slr

    if rec.get("stop"):
        return f"止まった理由: {rec['stop'].get('condition')}（{slr.stop_line(rec, rec['stop'].get('condition'))}）。再開: {slr.resume_command(rec)}"
    if metrics.get("waiting"):
        nums = " ".join(f"#{n}" for n in metrics["waiting"])
        return f"人の判断待ち: {nums}。答えは sprint-state.py loop answer で書く"
    for h in metrics.get("held") or []:
        if not h.get("answer"):
            return f"ループの {h['lap']} 回目の答え待ち"
    return None


def loop_get(env, name: str, fields=None) -> dict:
    """`sprint-state.py loop get`。env は `sprint_loop.Env`。"""
    import sprint_loop as sl
    import sprint_loop_record as slr

    if (bad := _bad_pointer(fields)) is not None:
        return bad
    try:
        path = slr.loop_record_path(env, name)
    except sl.LoopError as e:
        return _out("stopped", str(e), code=step_result.EXIT_UNREADABLE)
    rec, err = _read(path, "開発ループの記録")
    if err is not None:
        return err
    if fields:
        return _fields(rec, fields, "開発ループの記録")
    try:
        status = sl.cmd_status(env, name)
    except sl.LoopError as e:
        return _out("stopped", str(e), code=step_result.EXIT_UNREADABLE)
    rec = slr.read_loop_record(env, name)
    return _out("ok", status["summary"], [], status["metrics"], loop_next(rec, status["metrics"]))
