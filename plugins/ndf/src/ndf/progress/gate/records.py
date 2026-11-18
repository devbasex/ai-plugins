"""承認ゲートの回答の記録を読む・写す（`ndf.progress.gate` の部品）。

回答の SSOT は事象 `answered` で、ここは読むか、サービスが読む `gate-rejections.jsonl`（表 ndf-files）へ写すだけである。
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.progress import invoke, model, run
from ndf.progress.gate.asking import REJECTING, Answer, Question, Target, accepted_path, sprint_issues

# --- 変更要求の記録 ---------------------------------------------------------------------------------


def rejections_path() -> Path:
    """`gate-rejections.jsonl` の保存先（表 locations の mvv-state。環境変数があればその位置、無ければ状態の保存先）。"""
    var = facts.value("locations", "mvv-state", "env")
    base = Path(os.environ[var]) if os.environ.get(var) else identity.load().state_dir / facts.value("locations", "mvv-state", "path")
    return base / facts.value("ndf-files", "gate-rejections", "path")


def rejection_row(v: run.View, q: Question, a: Answer) -> dict[str, Any]:
    """`gate-rejections.jsonl` の 1 行（回答した時刻・区分・回答・理由・範囲・回答した者）。"""
    ctx = v.context()
    issues = sprint_issues(v)
    # 設計承認の PR は工程の終わりまで文脈に無いので、質問が持つ PR（待っている工程の済んだステップの出力）も載せる
    prs = sorted({n for n in (ctx.get("design_pr"), ctx.get("pr"), q.pr) if isinstance(n, int)})
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate": q.gate if q.gate else "plan",
        "gate_id": q.id,
        "what": a.reason,
        "sprint": v.machine_id,
        "issues": issues,
        "prs": prs,
        "by": a.by,
        "outcome": "rejected" if a.value in REJECTING else a.value,
        "answer": a.value,
    }


def record_answer(v: run.View, q: Question, a: Answer, where: Callable[[], Path]) -> list[str]:
    """回答を `gate-rejections.jsonl` へ写す（承認も変更要求も）。写せなければ警告を返す（回答の SSOT は事象に書いてある）。"""
    try:
        path = where()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rejection_row(v, q, a), ensure_ascii=False) + "\n")
    except (OSError, identity.IdentityError) as exc:
        return [f"回答を gate-rejections へ写せなかった（回答はステートマシンに書いた）: {exc}"]
    return []


def rejections(events: Iterable[model.Event]) -> dict[str, int]:
    """承認しなかった回答（承認しない・変更要求）を承認ゲートの区分ごとに数える（gate-rejections の SSOT は事象）。"""
    answered = (run.payload_of(ev) for ev in events if ev.draft.kind == run.ANSWERED)
    unapproved = (p for p in answered if p.get("answer") in REJECTING and p.get("kind") == invoke.LISTEN)
    return dict(Counter(str(p["gate"]) if p.get("gate") else "plan" for p in unapproved))


def answers(v: run.View) -> list[dict[str, Any]]:
    """ステートマシンに書いた回答（古い順）。選択肢（`answer`）と変更要求の指摘（`notes`）を分け、理由・回答した者・時刻・質問を持つ。

    conductor が回答を読む口（#2934・#3174）。回答の SSOT は事象 `answered` で、ここは読むだけである。
    """
    out = []
    for ev in v.events:
        if ev.draft.kind != run.ANSWERED:
            continue
        p = run.payload_of(ev)
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
                "head": Target.from_dict(p.get("seen")).head,
                "reason": ev.draft.reason,
                "by": p["by"] if "by" in p else ev.draft.actor,
            }
        )
    return out


def approved_head(v: run.View, kind: str, root: Optional[Path] = None) -> Optional[str]:
    """区分 `kind` の承認ゲートで、最後に承認した回答が見た PR の head（無ければ None）。

    マージの側は、これを `--expect-head` へ渡し、承認の後に進んだ head をマージしない（#2147）。変更要求の後の承認だけを読む。
    承認の後に文書だけが加わって出し直さなかった（#3020）なら、そのときの head を返す（`root` は状態の保存先）。
    """
    got = [a for a in answers(v) if a["gate"] == kind]
    last = got[-1] if got else None
    if last is None or last["answer"] != run.APPROVED:
        return None
    accepted = accepted_path(root, v.machine_id, str(last["gate_id"])) if root is not None and last["gate_id"] else None
    if accepted is not None and accepted.exists():
        return str(json.loads(accepted.read_text(encoding="utf-8"))["head"])
    return str(last["head"]) if last["head"] else None
