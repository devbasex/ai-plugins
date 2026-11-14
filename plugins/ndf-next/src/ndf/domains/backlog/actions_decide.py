"""判定の反映と起票の決まり（純粋）。plan の `actions`・`create` の形の確かめ、反映の区分、書く差分、記録の鍵。

判定（verdict）ごとの扱い:

| 判定 | 反映 |
| --- | --- |
| `要判断` | 書かずに人へ返す（returned） |
| `やらない` | `approved: true` のときだけ書く。無ければ承認待ち（needs_approval） |
| ほか | `changes`（題・本文・状態・ラベル）のうち今と違うものだけを書く |

書く前に今の課題を読み、plan を作った後に課題が書き換わっていれば（更新の時刻と要約値の両方が違い、前の打ち直しで
自分が書いた状態でもなければ）書かずに飛ばす（skipped_changed）。済んだ反映と起票は記録（ledger.json）に残り、
同じ plan の打ち直しで 2 度書かない。

起票は種類（needs・seeds・policy）・題・出典・MVV の当たる所を要る。MVV の判定が `follow` でない起票には
要判断の印（ラベル `needs-decision`）を付ける。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional

from ndf.domains.backlog import model

VERDICTS = ("そのまま", "追記が要る", "書き直しが要る", "閉じてよい", "やらない", "重複", "ルートコーズ", "要判断")
NEEDS_APPROVAL = ("やらない",)
RETURNED = ("要判断",)
CHANGE_KEYS = ("title", "body", "state", "state_reason", "add_labels", "remove_labels")
STATES = ("open", "closed")
KINDS = ("needs", "seeds", "policy")
DECISION_LABEL = "needs-decision"
FOLLOW = "follow"
# 反映の区分（結果の items の result と metrics の鍵）
APPLIED, UNCHANGED, ALREADY, SKIPPED, WAITING, RETURNED_BUCKET, FAILED = (
    "applied", "unchanged", "already", "skipped_changed", "needs_approval", "returned", "failed",
)  # fmt: skip
BUCKETS = (APPLIED, UNCHANGED, ALREADY, SKIPPED, WAITING, RETURNED_BUCKET, FAILED)
PARTIAL = "partial"


@dataclass
class Diff:
    """課題へ書く差分（PATCH の中身・足すラベル・外すラベル）。書けた分の実績も同じ形で持つ。"""

    patch: dict[str, Any] = field(default_factory=dict)
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.patch or self.add or self.remove)


def _list_of_str(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


# 鍵 → 値の形。TEXT は文字列、TEXTS は文字列の列、タプルは取れる値の列。並びは誤りを返す順
TEXT, TEXTS = "text", "texts"
_SHAPE_CHECKS: dict[str, tuple[Callable[[Any], bool], str]] = {
    TEXT: (lambda v: isinstance(v, str), "は文字列で書く"),
    TEXTS: (_list_of_str, "は文字列の列で書く"),
}
CHANGE_SHAPES: dict[str, Any] = {
    "title": TEXT, "body": TEXT, "state_reason": TEXT, "state": STATES, "add_labels": TEXTS, "remove_labels": TEXTS,
}  # fmt: skip
CREATE_SHAPES: dict[str, Any] = {"title": TEXT, "body": TEXT, "mvv_basis": TEXT, "mvv_verdict": TEXT, "labels": TEXTS}


def _shape_errors(prefix: str, obj: dict[str, Any], shapes: dict[str, Any]) -> list[str]:
    """`obj` にある鍵のうち形の違うものの誤り（`<prefix><鍵> は…`）。"""
    errs = []
    for key, shape in shapes.items():
        if key not in obj:
            continue
        if isinstance(shape, tuple):
            if obj[key] not in shape:
                errs.append(f"{prefix}{key} は {' / '.join(shape)} のどれか")
            continue
        ok, why = _SHAPE_CHECKS[shape]
        if not ok(obj[key]):
            errs.append(f"{prefix}{key} {why}")
    return errs


def _change_errors(n: int, ch: Any) -> list[str]:
    if not isinstance(ch, dict):
        return [f"#{n} の changes はオブジェクトで書く"]
    errs = [f"#{n} の changes の知らない鍵: {k}" for k in sorted(set(ch) - set(CHANGE_KEYS))]
    return errs + _shape_errors(f"#{n} の changes.", ch, CHANGE_SHAPES)


def _action_errors(k: int, act: Any) -> list[str]:
    if not isinstance(act, dict) or not isinstance(act.get("number"), int):
        return [f"actions[{k}] に number（整数）が無い"]
    n = act["number"]
    errs = []
    if act.get("verdict") not in VERDICTS:
        errs.append(f"#{n} の verdict は {' / '.join(VERDICTS)} のどれか")
    if not isinstance(act.get("updated_at"), str) or not isinstance(act.get("digest"), str):
        errs.append(f"#{n} に updated_at と digest が無い（照合できない）")
    if "approved" in act and not isinstance(act["approved"], bool):
        errs.append(f"#{n} の approved は true / false で書く")
    return errs + (_change_errors(n, act["changes"]) if "changes" in act else [])


def _create_errors(k: int, c: Any) -> list[str]:
    if not isinstance(c, dict):
        return [f"create[{k}] はオブジェクトで書く"]
    return _shape_errors(f"create[{k}].", c, CREATE_SHAPES)


def _duplicate_errors(acts: list[Any]) -> list[str]:
    """同じ番号の action が 2 つ以上ある誤り。"""
    seen = [act["number"] for act in acts if isinstance(act, dict) and isinstance(act.get("number"), int)]
    return [f"#{n} の action が 2 つある" for n in sorted({n for n in seen if seen.count(n) > 1})]


def plan_errors(plan: dict[str, Any]) -> list[str]:
    """`actions` と `create` の形の誤り。1 つでもあれば 1 件も書かない。"""
    errs = []
    for key in ("actions", "create"):
        if key in plan and not isinstance(plan[key], list):
            errs.append(f"{key} は列で書く")
    if errs:
        return errs
    acts = plan.get("actions", [])
    errs += [e for k, act in enumerate(acts) for e in _action_errors(k, act)]
    errs += [e for k, c in enumerate(plan.get("create", [])) for e in _create_errors(k, c)]
    return errs + _duplicate_errors(acts)


def changes_of(act: dict[str, Any]) -> dict[str, Any]:
    return act["changes"] if "changes" in act else {}


def ledger_key(repo: str, act: dict[str, Any]) -> str:
    """記録の鍵。plan の更新の時刻と要約値も含め、後の回の同じ変更を「済み」にしない。"""
    return (
        f"{repo}#{act['number']}:"
        + model.stable_digest({"changes": changes_of(act), "updated_at": act["updated_at"], "digest": act["digest"]})[:12]
    )


def gate(act: dict[str, Any], rec: Optional[dict[str, Any]]) -> Optional[tuple[str, str]]:
    """課題を読む前に決まる区分と理由。読んで書く課題は None。"""
    if act["verdict"] in RETURNED:
        return RETURNED_BUCKET, "要判断は反映せず人へ返す"
    if rec is not None and rec.get("result") != PARTIAL:
        return ALREADY, "記録にある"
    if act["verdict"] in NEEDS_APPROVAL and act.get("approved") is not True:
        return WAITING, "やらないは承認を得てから反映する"
    return None


def changed_since_plan(act: dict[str, Any], cur: dict[str, Any], rec: Optional[dict[str, Any]]) -> bool:
    """plan を作った後に課題が書き換わったか（前の打ち直しで自分が書いた状態は除く）。"""
    now = model.snapshot_digest(cur)
    own = rec is not None and now == rec.get("digest")
    return cur["updated_at"] != act["updated_at"] and now != act["digest"] and not own


def diff(cur: dict[str, Any], ch: dict[str, Any]) -> Diff:
    """今の課題と比べて、書き込みが要る (PATCH の中身, 足すラベル, 外すラベル)。"""
    patch = {k: ch[k] for k in ("title", "body") if k in ch and ch[k] != cur[k]}
    if "state" in ch and ch["state"] != cur["state"]:
        patch["state"] = ch["state"]
        if "state_reason" in ch:
            patch["state_reason"] = ch["state_reason"]
    have = set(cur["labels"])
    add = [lb for lb in (ch["add_labels"] if "add_labels" in ch else []) if lb not in have]
    remove = [lb for lb in (ch["remove_labels"] if "remove_labels" in ch else []) if lb in have]
    return Diff(patch, add, remove)


def after_write(cur: dict[str, Any], d: Diff) -> dict[str, Any]:
    """書いた後の課題（要約値を記録して、打ち直しで自分の書き込みを並行の更新と取り違えない）。"""
    labels = [lb for lb in cur["labels"] if lb not in d.remove] + [lb for lb in d.add if lb not in cur["labels"]]
    return {**cur, **{k: v for k, v in d.patch.items() if k in ("title", "body", "state")}, "labels": labels}


def create_reason(c: dict[str, Any]) -> Optional[str]:
    """起票しない理由（要るものが欠ける）。そろっていれば None。"""
    if c.get("kind") not in KINDS or not c.get("title") or not c.get("sources") or not c.get("mvv_basis"):
        return f"種類（{' / '.join(KINDS)}）・題・出典・MVV の当たる所のどれかが無い"
    return None


def compose_body(c: dict[str, Any]) -> str:
    """本文に出典と MVV の当たる所を足す。"""
    sources = c["sources"] if isinstance(c["sources"], list) else [c["sources"]]
    src = "\n".join(f"- {s['kind']}: {s['value']}" if isinstance(s, dict) and {"kind", "value"} <= set(s) else f"- {s}" for s in sources)
    body = c["body"] if "body" in c else ""
    return f"{body.rstrip()}\n\n## 出典\n\n{src}\n\n## MVV の当たる所\n\n{c['mvv_basis']}\n"


def create_labels(c: dict[str, Any]) -> list[str]:
    """起票のラベル。種類のラベルを足し、MVV の判定が follow でなければ要判断の印を付ける。"""
    labels = list(dict.fromkeys([*(c["labels"] if "labels" in c else []), c["kind"]]))
    if c.get("mvv_verdict") != FOLLOW and DECISION_LABEL not in labels:
        labels.append(DECISION_LABEL)
    return labels


def create_key(c: dict[str, Any]) -> str:
    return "create:" + model.stable_digest([c["kind"], c["title"]])[:12]
