"""upkeep_overhaul.py: 大整理の決まった手順（#2315 の手順 1 の固定・2 数える・3 裏付ける）。

    upkeep.py overhaul snapshot --out <dir>
    upkeep.py overhaul count --snapshot <snapshot.json> --classes <classes.json> [--out <dir>]
    upkeep.py overhaul measure [--out <dir>]

snapshot: 未完了の課題すべて（後回しを含む）を固定し、`<dir>/snapshot.json` に書く（番号・題名・ラベル・マイルストーン・
  本文の先頭 2500 字・起票の時刻）。月ごとの起票と完了を数えるため、閉じた課題の起票と完了の時刻も `closed` に残す。
count: 固定した一覧と型の分類（`[{number, type, sub}]`）から、型ごとの件数・課題の集まる部品・マイルストーンごとの割合・
  月ごとの起票と完了を数える。分類に無い課題は I（その他）に数える。
measure: 材料の宣言の `code_measures` のコマンドを打ち、宣言ごとの値を返す。宣言が無ければ飛ばして報告に書く。
分類（手順 1）・根本原因の判断（手順 4）・大方針の本文（手順 6）は LLM が行う（`references/overhaul.md`）。
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import clock
import jsonio
from step_result import EXIT_UNREADABLE, StepError, emit, git_root, result
import upkeep_depth as D

TOOL = "backlog-refinement"
BODY_CHARS = 2500
# 課題の集まる部品: 本文と題名に現れるファイル名（拡張子つき）と Skill の名前
_COMPONENT = re.compile(r"(?<![\w/.-])([\w-]+\.(?:py|sh|md|json|ts|js|go|rb|rs|java|kt|yml|yaml))\b")


def _shape(i: dict) -> dict:
    return {
        "number": i["number"],
        "title": i.get("title") or "",
        "labels": sorted(lb.get("name", "") for lb in i.get("labels") or []),
        "milestone": (i.get("milestone") or {}).get("title"),
        "body": (i.get("body") or "")[:BODY_CHARS],
        "created_at": i.get("created_at") or i.get("createdAt"),
    }


def snapshot(root, out: Path, repo: str | None = None) -> dict:
    from upkeep_gh import Gh, list_issues, target_repo

    gh = Gh(target_repo(root, repo))
    open_ = [_shape(i) for i in list_issues(gh, "state=open")]
    closed = [
        {"number": c["number"], "created_at": c.get("created_at"), "closed_at": c.get("closed_at")} for c in list_issues(gh, "state=closed")
    ]
    data = {"at": clock.now_iso("utc"), "open": open_, "closed": closed}
    out.mkdir(parents=True, exist_ok=True)
    jsonio.write_atomic(out / "snapshot.json", data, indent=1)
    return data


def _read(path, what: str):
    got = jsonio.read(path, missing=None, broken=None)
    if got is None:
        raise StepError(f"{what} を読めない: {path}", EXIT_UNREADABLE)
    return got


def count(snap: dict, classes: list[dict], types: list[dict]) -> dict:
    """型ごとの件数・部品・マイルストーンごとの割合・月ごとの起票と完了。"""
    ids = [t["id"] for t in types]
    other = "I" if "I" in ids else ids[-1]
    by_num = {c["number"]: c for c in classes if isinstance(c, dict) and isinstance(c.get("number"), int)}
    issues = snap.get("open") or []
    kind = {i["number"]: (by_num.get(i["number"]) or {}).get("type") if (by_num.get(i["number"]) or {}).get("type") in ids else other for i in issues}
    by_type = {t: sum(1 for v in kind.values() if v == t) for t in ids}
    defect = {t["id"] for t in types if t.get("defect", True)}
    n_defect = sum(by_type[t] for t in defect)
    comps = Counter()
    for i in issues:
        comps.update(set(_COMPONENT.findall(f"{i.get('title')}\n{i.get('body')}")))
    per_ms: dict = {}
    for i in issues:
        row = per_ms.setdefault(i.get("milestone") or "（なし）", Counter())
        row[kind[i["number"]]] += 1
    months: dict = {}
    for i in list(issues) + list(snap.get("closed") or []):
        if i.get("created_at"):
            months.setdefault(i["created_at"][:7], {"created": 0, "closed": 0})["created"] += 1
    for c in snap.get("closed") or []:
        if c.get("closed_at"):
            months.setdefault(c["closed_at"][:7], {"created": 0, "closed": 0})["closed"] += 1
    return {
        "issues": len(issues),
        "unclassified": sum(1 for i in issues if i["number"] not in by_num),
        "by_type": by_type,
        "defects": n_defect,
        "defect_share": {t: round(by_type[t] / n_defect, 4) for t in ids if t in defect and n_defect},
        "components": comps.most_common(10),
        "milestones": {m: dict(c) for m, c in sorted(per_ms.items())},
        "months": dict(sorted(months.items())),
    }


def measure(root) -> tuple[list[dict], bool]:
    """(宣言ごとの値, 宣言があったか)。コマンドの失敗はその行に残して次へ進む。"""
    rows = (D.materials(root) or {}).get("code_measures") or []
    out = []
    for m in rows:
        try:
            p = subprocess.run(m["command"], shell=True, cwd=str(root), capture_output=True, text=True, timeout=600)
            out.append({"name": m.get("name"), "types": m.get("types") or [], "code": p.returncode, "value": p.stdout.strip()[-2000:]})
        except (OSError, subprocess.SubprocessError, KeyError) as e:
            out.append({"name": m.get("name"), "types": m.get("types") or [], "code": None, "error": str(e)})
    return out, bool(rows)


def cmd_snapshot(a):
    root = git_root(a.root)
    data = snapshot(root, Path(a.out), getattr(a, "repo", None))
    emit(result(TOOL, "ok", f"未完了 {len(data['open'])} 件を固定した", [], {"path": str(Path(a.out) / "snapshot.json"), "open": len(data["open"])}))


def cmd_count(a):
    root = git_root(a.root)
    snap = _read(a.snapshot, "固定した一覧")
    classes = _read(a.classes, "型の分類")
    if not isinstance(snap, dict) or not isinstance(classes, list):
        raise StepError("snapshot は {open, closed}、classes は [{number, type, sub}] の形で書く", EXIT_UNREADABLE)
    got = count(snap, classes, D.cause_types(root))
    if a.out:
        jsonio.write_atomic(Path(a.out) / "counts.json", got, indent=1)
    top = max(got["by_type"], key=got["by_type"].get) if got["by_type"] else None
    emit(result(TOOL, "ok", f"{got['issues']} 件を型で数えた（最多 {top} {got['by_type'].get(top)} 件）", [], got))


def cmd_measure(a):
    root = git_root(a.root)
    rows, declared = measure(root)
    if a.out:
        jsonio.write_atomic(Path(a.out) / "measures.json", rows, indent=1)
    summary = f"宣言した計測 {len(rows)} 件を打った" if declared else "code_measures の宣言が無いため裏付けを飛ばした（報告に書く）"
    emit(result(TOOL, "ok", summary, rows, {"declared": declared, "measures": len(rows)}))


def add_parsers(sub, common) -> None:
    op = sub.add_parser("overhaul").add_subparsers(dest="overhaul_cmd", required=True)
    s = op.add_parser("snapshot", parents=[common])
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_snapshot)
    c = op.add_parser("count", parents=[common])
    c.add_argument("--snapshot", required=True)
    c.add_argument("--classes", required=True)
    c.add_argument("--out", default=None)
    c.set_defaults(func=cmd_count)
    m = op.add_parser("measure", parents=[common])
    m.add_argument("--out", default=None)
    m.set_defaults(func=cmd_measure)
