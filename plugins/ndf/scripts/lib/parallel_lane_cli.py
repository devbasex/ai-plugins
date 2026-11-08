"""動いている並列の計画のレーンの追加と書き換え（`parallel.py lane`。#2733 の決定 9）。

`lane <計画名> add --from <JSON>` はレーン 1 本の宣言を、`lane <計画名> set --lane <名> --from <JSON>` は書き換えるキー
（`input`・`plans`・`touches`・`after`・`handoff`）を受ける。上限を含む計画の検査を通ったときだけ `plan.json`・`plan.md`・
引継ぎ文書を書き、計画の所在の一覧へ置き場を書き、耐久ワークフローへ `lanes_changed` を送る（I13）。レーンの名前と `repo` は
`set` で変えない（名前は答え待ちの停止・再開の印・依頼の持ち主のキーのため。決定 13）。

終了コード: 検査の誤り・使い方の誤り 1、計画が無い・読めない・書けない 2。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import parallel_lane as pl
import parallel_plan as pp

SET_KEYS = ("input", "plans", "touches", "after", "handoff")
FIXED_KEYS = ("name", "repo")


def _read_json(src: str) -> object:
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    return json.loads(text)


def limit_items(items: list[dict], lanes: int) -> list[dict]:
    """レーンの数が上限を超えた誤り（I2）があれば、上限の変え方の 1 行を足す（受け入れ条件 8）。"""
    if not any(it.get("rule") == "I2" and "レーンの数" in str(it.get("message")) for it in items):
        return items
    relay = Path(__file__).resolve().parent.parent / "relay.py"
    line = f"python3 {relay} cap --named {lanes}"
    return [
        *items,
        {"level": "info", "rule": "limit", "message": f"レーン数の上限を変えるには、人の指示を受けてから {line} を打つ", "next": line},
    ]


def updated(raw: dict, action: str, lane_name: str | None, body: object) -> tuple[dict | None, str, str | None]:
    """(書き換えた計画の JSON か None, 対象のレーン名, 誤りの 1 文か None)。"""
    if not isinstance(body, dict):
        return None, "", "--from の中身が JSON のオブジェクトでない"
    lanes = [dict(x) for x in raw.get("lanes") or [] if isinstance(x, dict)]
    if action == "add":
        name = str(body.get("name") or "")
        if any(x.get("name") == name for x in lanes):
            return None, name, f"レーン {name} はすでに計画にある（書き換えは set）"
        return {**raw, "lanes": [*lanes, dict(body)]}, name, None
    if not lane_name:
        return None, "", "set には --lane が要る"
    if bad := [k for k in body if k in FIXED_KEYS]:
        return None, lane_name, f"set では {', '.join(bad)} を変えない（名前と repo は変えられない。名前に役割を含めない）"
    if bad := [k for k in body if k not in SET_KEYS]:
        return None, lane_name, f"set で書き換えられないキー: {', '.join(bad)}（書き換えられるのは {', '.join(SET_KEYS)}）"
    hit = next((x for x in lanes if x.get("name") == lane_name), None)
    if hit is None:
        return None, lane_name, f"レーン {lane_name} が計画に無い"
    if "input" in body and "handoff" not in body:
        hit.pop("handoff", None)  # 入力（ループの名前）を変えたら引継ぎ文書の名も入力から決め直す
    hit.update(body)
    return {**raw, "lanes": lanes}, lane_name, None


def write_lane(main: Path, plan: pp.ParallelPlan, raw: dict, lane_name: str, action: str) -> list[Path]:
    """`plan.json`・`plan.md`・対象のレーンの引継ぎ文書を書く。add では在る引継ぎ文書を上書きしない（FileExistsError）。"""
    import handoff

    d = pp.plan_dir(main, plan.name)
    ln = plan.lane(lane_name)
    target = handoff.paths_of(ln.repo_dir(main), ln.handoff_name())[0]
    if action == "add" and target.exists():
        raise FileExistsError(str(target))
    data = dict(raw)
    data["lanes"] = [{**dict(r), "handoff": x.handoff_name()} for r, x in zip(raw["lanes"], plan.lanes)]
    (d / "plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "plan.md").write_text(pp.render_md(plan), encoding="utf-8")
    handoff.write_doc(target, pp.render_handoff(plan, ln, d / "plan.md"))
    return [d / "plan.json", d / "plan.md", target]


def notify(main: Path, name: str) -> bool:
    """耐久ワークフローへ `lanes_changed` を送る。記録が無い（まだ start していない）なら送らずに False。"""
    if not pl.record_exists(main, name):
        return False
    import durable

    wid = pl.workflow_id(name)
    durable.send_to(pl.KIND, pl.record_identity(main, name), wid, {"kind": "lanes_changed"}, "inbox", f"{wid}:inbox:lanes:{time.time_ns()}")
    return True


def reply(status: str, summary: str, items=None, code: int | None = None, next_step: str | None = None, **metrics):
    """結果を出して終える（`parallel.py` の `reply` と同じ形）。"""
    from step_result import emit, result

    emit(result("parallel", status, summary, items, metrics, next=next_step), code)


def cmd_lane(a) -> None:
    import decl
    import handoff
    import os

    import parallel_homes

    cwd = os.getcwd()
    main = decl.main_dir(cwd) or Path(cwd)
    try:
        raw = pp.load_plan(main, a.name)
        body = _read_json(a.src)
    except FileNotFoundError as exc:
        reply("stopped", f"並列の計画 {a.name} か --from のファイルが無い: {exc}", code=2)
    except (OSError, ValueError) as exc:
        reply("stopped", f"並列の計画 {a.name} か --from を読めない: {exc}", code=2)
    if not isinstance(raw, dict):
        reply("stopped", f"並列の計画 {a.name} が JSON のオブジェクトでない", code=2)
    new, lane_name, err = updated(raw, a.action, a.lane, body)
    if err:
        reply("stopped", err, code=1)
    plan, items, metrics = pp.check_data(new, home=main)
    if plan is None:
        items = limit_items(items, len(new.get("lanes") or []))
        reply("stopped", f"並列の計画 {a.name} の検査に誤りがある（{metrics['errors']} 件）。何も書いていない", items, 1, **metrics)
    try:
        written = write_lane(main, plan, new, lane_name, a.action)
        parallel_homes.record(main)
    except FileExistsError as exc:
        reply("stopped", f"在る引継ぎ文書を上書きしないため何も書いていない: {exc}", items, 1)
    except handoff.Failure as exc:
        reply("stopped", f"引継ぎ文書を書けない: {exc}", items, exc.code)
    except OSError as exc:
        reply("stopped", f"書けない: {exc}", items, 2)
    sent = notify(main, a.name)
    wait = f"python3 {Path(__file__).resolve().parent.parent / 'parallel.py'} wait {a.name}"
    verb = "足した" if a.action == "add" else "書き換えた"
    summary = f"並列の計画 {a.name} のレーン {lane_name} を{verb}" + (
        "。耐久ワークフローへ知らせた" if sent else "（計画はまだ動いていない）"
    )
    reply("ok", summary, [{"path": str(p)} for p in written] + items, lane=lane_name, notified=sent, next_step=wait, **metrics)
