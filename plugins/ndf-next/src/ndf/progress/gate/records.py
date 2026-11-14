"""承認ゲートの答えの記録を読む・写す（`ndf.progress.gate` の部品）。

答えの正本は事象 `answered` で、ここは読むか、サービスが読む `gate-rejections.jsonl`（表 ndf-files）へ写すだけである。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.progress import invoke, model, run
from ndf.progress.gate.asking import REJECTING, Answer, Question, accepted_path

# --- 差し戻しの記録 ---------------------------------------------------------------------------------


def rejections_path() -> Path:
    """`gate-rejections.jsonl` の置き場（表 places の mvv-state。環境変数があればその位置、無ければ状態の置き場）。"""
    var = facts.value("places", "mvv-state", "env")
    base = Path(os.environ[var]) if os.environ.get(var) else identity.load().state_dir / facts.value("places", "mvv-state", "path")
    return base / facts.value("ndf-files", "gate-rejections", "path")


def rejection_row(v: run.View, q: Question, a: Answer) -> dict[str, Any]:
    """`gate-rejections.jsonl` の 1 行（答えた時刻・区分・答え・理由・範囲・答えた者）。"""
    ctx = v.context()
    issues = sorted(int(n) for n in ctx["issues"]) if isinstance(ctx.get("issues"), list) else []
    prs = sorted({n for n in (ctx.get("design_pr"), ctx.get("pr")) if isinstance(n, int)})
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate": q.gate if q.gate else "plan",
        "gate_id": q.id,
        "what": a.reason,
        "sprint": v.table_id,
        "issues": issues,
        "prs": prs,
        "by": a.by,
        "outcome": "rejected" if a.value in REJECTING else a.value,
        "answer": a.value,
    }


def record_answer(v: run.View, q: Question, a: Answer, where: Callable[[], Path]) -> list[str]:
    """答えを `gate-rejections.jsonl` へ写す（承認も差し戻しも）。写せなければ警告を返す（答えの正本は事象に書いてある）。"""
    try:
        path = where()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rejection_row(v, q, a), ensure_ascii=False) + "\n")
    except (OSError, identity.IdentityError) as exc:
        return [f"答えを gate-rejections へ写せなかった（答えは進行表に書いた）: {exc}"]
    return []


def rejections(events: Iterable[model.Event]) -> dict[str, int]:
    """承認しなかった答え（承認しない・差し戻し）を承認ゲートの区分ごとに数える（gate-rejections の正本は事象）。"""
    out: dict[str, int] = {}
    for ev in events:
        p = ev.draft.payload if ev.draft.payload is not None else {}
        if ev.draft.kind == run.ANSWERED and p.get("answer") in REJECTING and p.get("kind") == invoke.LISTEN:
            gate = str(p["gate"]) if p.get("gate") else "plan"
            out[gate] = out[gate] + 1 if gate in out else 1
    return out


def answers(v: run.View) -> list[dict[str, Any]]:
    """進行表に書いた答え（古い順）。選択肢（`answer`）と差し戻しの指摘（`notes`）を分け、理由・答えた者・時刻・問いを持つ。

    conductor が答えを読む口（#2934・#3174）。答えの正本は事象 `answered` で、ここは読むだけである。
    """
    out = []
    for ev in v.events:
        if ev.draft.kind != run.ANSWERED:
            continue
        p = ev.draft.payload if ev.draft.payload is not None else {}
        out.append(
            {
                "seq": ev.seq,
                "at": ev.at.isoformat(),
                "stage": ev.draft.row_key,
                "step": p.get("step"),
                "gate": p.get("gate"),
                "gate_id": p.get("gate_id"),
                "answer": p["answer"],
                "notes": p.get("notes"),
                "head": p["seen"].get("head") if isinstance(p.get("seen"), dict) else None,
                "reason": ev.draft.reason,
                "by": ev.draft.actor,
            }
        )
    return out


def approved_head(v: run.View, kind: str, root: Optional[Path] = None) -> Optional[str]:
    """区分 `kind` の承認ゲートで、最後に承認した答えが見た PR の head（無ければ None）。

    マージの側は、これを `--expect-head` へ渡し、承認の後に進んだ head をマージしない（#2147）。差し戻しの後の承認だけを読む。
    承認の後に文書だけが加わって出し直さなかった（#3020）なら、そのときの head を返す（`root` は状態の置き場）。
    """
    got = [a for a in answers(v) if a["gate"] == kind]
    last = got[-1] if got else None
    if last is None or last["answer"] != run.APPROVED:
        return None
    accepted = accepted_path(root, v.table_id, str(last["gate_id"])) if root is not None and last["gate_id"] else None
    if accepted is not None and accepted.exists():
        return str(json.loads(accepted.read_text(encoding="utf-8"))["head"])
    return str(last["head"]) if last["head"] else None
