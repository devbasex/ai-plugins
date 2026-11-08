"""出し直しの指摘を持つ課題の設計のプラン（#2510 の I4）。`sprint_waves` が組んだプランへ指摘を入れる。"""

from __future__ import annotations

import shlex

import revise_note

from supervise_lib.paths import HERE

REVISE_NOTE_PY = f"python3 {HERE / 'revise-note.py'}"
REVISE_CHECK = "revise-check"  # 表の突き合わせのステップ


def revise_path(a, n: int) -> str | None:
    """課題の出し直しの指摘の入力のパス（`--revise-note`）。無ければ None。"""
    return (getattr(a, "revise_notes", None) or {}).get(n)


def with_revise_note(plan: dict, a, n: int, doc: str) -> dict:
    """指摘の入力があれば、仕様のコピーのチェックを外して要求のステップへ直行させ、要求と設計の起動指示へすべての回の指摘を入れ、
    最後の送りの後に表の突き合わせのステップを足す。欠け（10）は MVV 判定へ進まず承認ゲートで終え、読めなければ（2）Draft を外して
    設計承認ゲートへ回す。どちらも周回の締めが同じ突き合わせで扱いを決める（決定 4）。入力が無ければプランを変えない。"""
    path = revise_path(a, n)
    if not path:
        return plan
    data = revise_note.read_input(path)
    steps = plan["steps"]
    by_id = {s["id"]: s for s in steps}
    steps.remove(by_id["requirements-check"])
    by_id["glossary"]["next"] = "requirements"
    by_id["requirements"]["prompt"] += "\n\n" + revise_note.prompt_text(data)
    by_id["design"]["prompt"] += "\n\n" + revise_note.prompt_text(data, doc)
    push = by_id["push-glossary"]
    check = {
        "id": REVISE_CHECK,
        "type": "run",
        "stage": "ドキュメントレビュー",
        "timeout": 120,
        "cmd": f"{REVISE_NOTE_PY} check --pr {{pr}} --doc {doc} --notes {shlex.quote(path)}",
        "gate_next": "end",
        "on_fail": "ready",
        "next": push["next"],
    }
    push["next"] = REVISE_CHECK
    steps.insert(steps.index(push) + 1, check)
    return plan


def with_revise_mvv(plan: dict, a, n: int) -> dict:
    """MVV 判定を置く形: 突き合わせの後に MVV 判定へ進め、判定の材料へ入力を渡す。入力が無ければプランを変えない。"""
    path = revise_path(a, n)
    if not path:
        return plan
    by_id = {s["id"]: s for s in plan["steps"]}
    by_id["push-glossary"]["next"] = REVISE_CHECK
    by_id[REVISE_CHECK]["next"] = "mvv"
    by_id["mvv"]["cmd"] += f" --revise-note {shlex.quote(path)}"
    return plan
