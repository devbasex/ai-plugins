#!/usr/bin/env python3
"""工数の見積もりの部品の CLI（#1856）。結果は `step_result` の形の 1 行の JSON。

    estimate.py predict (--issue N | --task "<説明>") --done <フェーズ> [--items k=軽,...] [--like 1601,1631]
                        [--provisional A=1800-5400 ...] [--save] [--batch <file>] [--root DIR]
    estimate.py human --issue N [--issue M] --minutes M [--what 説明]
    estimate.py import [--sv-dir DIR]
    estimate.py errors [--since 日付]
    estimate.py actuals [--issue N] [--done <フェーズ>]

部品は手元の記録（実績の記録・見積もりの記録・`.ndf/estimate.json`・`.ndf/project.json`）だけを読み、
`subprocess` もネットワークも使わない。課題の題や本文を読んで 6 項目を判定するのは呼び出し側の AI である。
規約の正本は `skills/estimate/references/rules.md`。終了コード: 0 = 見込みを返した、2 = 引数の誤り。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

TOOL = "estimate"


class ArgError(ValueError):
    pass


def _parse_items(text: str | None) -> dict:
    import estimate_records as er

    out = {}
    for part in [p for p in (text or "").split(",") if p.strip()]:
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if k not in er.ITEM_KEYS:
            raise ArgError(f"--items の項目は {', '.join(er.ITEM_KEYS)} のどれか: {k}")
        if v not in er.ITEM_VALUES:
            raise ArgError(f"--items の値は {'・'.join(er.ITEM_VALUES)} のどれか: {k}={v}")
        out[k] = v
    return out


def _items_of_object(items) -> dict:
    if not isinstance(items, dict):
        raise ArgError("items はオブジェクトにする")
    return _parse_items(",".join(f"{k}={v}" for k, v in items.items()))


def _provisional(values) -> dict:
    out = {}
    for text in values or []:
        for part in [p for p in str(text).split(",") if p.strip()]:
            k, _, rng = part.partition("=")
            lo, _, hi = rng.partition("-")
            try:
                lo_v = float(lo)
                hi_v = float(hi) if hi else lo_v
            except ValueError as e:
                raise ArgError(f"--provisional は A=下-上 の形にする: {part}") from e
            if k.strip() not in ("A", "H", "L", "C") or lo_v < 0 or hi_v < lo_v:
                raise ArgError(f"--provisional は A・H・L・C のどれかに 0 以上の 下-上 を付ける: {part}")
            out[k.strip()] = (lo_v, hi_v)
    return out


def _like(text) -> list[int] | None:
    if text in (None, "", []):
        return None
    vals = text if isinstance(text, list) else str(text).split(",")
    try:
        return [int(str(v).strip().lstrip("#")) for v in vals if str(v).strip()]
    except ValueError as e:
        raise ArgError(f"--like は課題番号をカンマで並べる: {text}") from e


def _request(d: dict):
    import estimate_predict as ep
    import estimate_records as er

    issue, task = d.get("issue"), d.get("task")
    if issue is None and not task:
        raise ArgError("--issue か --task のどちらかが要る")
    if d.get("done") not in er.PHASES:
        raise ArgError(f"--done は {'・'.join(er.PHASES)} のどれか")
    items = d["items"] if isinstance(d.get("items"), dict) and d.get("_parsed") else _items_of_object(d.get("items") or {})
    prov = d.get("provisional") or {}
    if not isinstance(prov, dict) or any(not isinstance(v, tuple) for v in prov.values()):
        prov = _provisional([f"{k}={v}" for k, v in (prov.items() if isinstance(prov, dict) else [])])
    return ep.Request(
        issue=int(issue) if issue is not None else None,
        task=task or None,
        done=d["done"],
        items=items,
        like=_like(d.get("like")),
        provisional=prov,
    )


def _materials(root: Path) -> dict:
    import test_strategy as ts

    try:
        decl = json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        decl = {}
    w, source = ts.whole_seconds(decl if isinstance(decl, dict) else {})
    return {"test_seconds": w, "test_source": source}


def _estimate_row(item: dict) -> dict:
    import clock

    at = clock.now_iso()
    key = str(item["issue"]) if item["issue"] is not None else str(item["task"])
    return {
        "at": at,
        "id": f"{at}-{hashlib.sha256(key.encode()).hexdigest()[:8]}",
        "issue": item["issue"],
        "task": item["task"],
        "done": item["done"],
        "items": item["items"],
        "config": item["config"],
        "values": item["values"],
        "size": item["size"],
    }


def cmd_predict(a, root: Path) -> tuple[dict, int]:
    import estimate_predict as ep
    import estimate_records as er
    from step_result import result

    if a.batch:
        try:
            raw = json.loads(Path(a.batch).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ArgError(f"--batch を読めない: {e}") from e
        if not isinstance(raw, list) or not all(isinstance(d, dict) for d in raw):
            raise ArgError("--batch は見積もりのオブジェクトの配列にする")
        reqs = [_request(d) for d in raw]
    else:
        reqs = [
            _request(
                {
                    "issue": a.issue,
                    "task": a.task,
                    "done": a.done,
                    "items": _parse_items(a.items),
                    "_parsed": True,
                    "like": a.like,
                    "provisional": _provisional(a.provisional),
                }
            )
        ]
    decl = er.load_estimate_decl(root)
    rec = er.estimate_dir(root)
    rows, estimates = er.read_actuals(rec), er.read_estimates(rec)
    materials = _materials(er.repo_top(root))
    items = [ep.predict(r, rows, estimates, decl, materials) for r in reqs]
    warnings = []
    if a.save:
        for item in items:
            err = er.append_estimate(rec, _estimate_row(item))
            if err:
                warnings.append({"kind": "warning", "reason": "保存できなかった", "detail": err})
                break
    uncal = sum(1 for i in items if any(v["mark"] != "実績" for v in i["values"].values()))
    summary = f"見積もり {len(items)} 件（実績の無い値を含むもの {uncal} 件）"
    return result(
        TOOL + "-predict", "ok", summary, items + warnings, {"estimates": len(items), "uncalibrated": uncal, "records": str(rec)}
    ), 0


def cmd_human(a, root: Path) -> tuple[dict, int]:
    import estimate_records as er
    from step_result import result

    if a.minutes is None or a.minutes <= 0 or not a.issue:
        raise ArgError("human は --issue と正の --minutes が要る")
    rec = er.estimate_dir(root)
    err = er.append_actual(rec, er.human_row(a.issue, round(a.minutes * 60, 1), a.what))
    if err:
        return result(TOOL + "-human", "stopped", err, [], {}), 1
    return result(TOOL + "-human", "ok", f"人の作業時間 {a.minutes} 分を記録した", [], {"records": str(rec)}), 0


def cmd_import(a, root: Path) -> tuple[dict, int]:
    import estimate_import
    import estimate_records as er
    from step_result import result

    rec = er.estimate_dir(root)
    from supervise_lib.paths import sv_state_home

    got = estimate_import.import_supervise_progress(Path(a.sv_dir) if a.sv_dir else sv_state_home(), rec)
    return result(
        TOOL + "-import", "ok", f"実行 {got['added']} 件を取り込み、{got['skipped']} 件を重複で飛ばした", [], {**got, "records": str(rec)}
    ), 0


def cmd_errors(a, root: Path) -> tuple[dict, int]:
    import estimate_predict as ep
    import estimate_records as er
    from step_result import result

    rec = er.estimate_dir(root)
    items, metrics = ep.errors(er.read_estimates(rec), er.read_actuals(rec), since=a.since)
    return result(TOOL + "-errors", "ok", f"予測と実績の組 {metrics['pairs']} 件", items, metrics), 0


def cmd_actuals(a, root: Path) -> tuple[dict, int]:
    import estimate_records as er
    from step_result import result

    if a.done not in er.PHASES:
        raise ArgError(f"--done は {'・'.join(er.PHASES)} のどれか")
    rec = er.estimate_dir(root)
    got = er.issue_actuals(er.read_actuals(rec), a.done)
    items = [
        {
            "kind": "issue",
            "issue": i,
            "a": v.a,
            "h": v.h,
            "l": v.l,
            "c": v.c,
            "completed": v.completed,
            "shared": v.shared,
            "cost_unknown": v.cost_unknown,
            "runs": [{"run": r.run, "phase": r.phase, "outcome": r.outcome, "retry": r.retry} for r in v.runs],
        }
        for i, v in sorted(got.items())
        if a.issue is None or i == a.issue
    ]
    return result(TOOL + "-actuals", "ok", f"課題 {len(items)} 件の実績", items, {"records": str(rec)}), 0


def estimate_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="estimate.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict", help="A・H・L・C の見込みと根拠を返す")
    p.add_argument("--issue", type=lambda s: int(s.lstrip("#")))
    p.add_argument("--task")
    p.add_argument("--done")
    p.add_argument("--items")
    p.add_argument("--like")
    p.add_argument("--provisional", action="append")
    p.add_argument("--save", action="store_true")
    p.add_argument("--batch")
    p = sub.add_parser("human", help="人の作業時間を記録する")
    p.add_argument("--issue", type=lambda s: int(s.lstrip("#")), action="append")
    p.add_argument("--minutes", type=float)
    p.add_argument("--what")
    p = sub.add_parser("import", help="supervise の記録から実績の記録を作る")
    p.add_argument("--sv-dir")
    p = sub.add_parser("errors", help="予測と実績の誤差を集計する")
    p.add_argument("--since")
    p = sub.add_parser("actuals", help="課題ごとの実績の合計")
    p.add_argument("--issue", type=lambda s: int(s.lstrip("#")))
    p.add_argument("--done", default="実装")
    for p in sub.choices.values():
        p.add_argument("--root", help="リポジトリ（既定は今のディレクトリ）")
    return ap


def main(argv: list[str] | None = None) -> None:
    deps.require("schema")
    import estimate_records as er
    import schema
    from step_result import emit, result

    try:
        a = estimate_parser().parse_args(argv)
    except SystemExit as e:
        raise SystemExit(2 if e.code else 0) from None
    root = Path(a.root or Path.cwd()).resolve()
    cmds = {"predict": cmd_predict, "human": cmd_human, "import": cmd_import, "errors": cmd_errors, "actuals": cmd_actuals}
    try:
        out, code = cmds[a.cmd](a, root)
    except (ArgError, er.DeclError, schema.ShapeError) as e:
        emit(result(f"{TOOL}-{a.cmd}", "stopped", str(e), [], {}), 2)
    emit(out, code)


if __name__ == "__main__":
    main()
