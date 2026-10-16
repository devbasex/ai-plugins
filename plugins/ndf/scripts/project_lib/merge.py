"""書き出しの規則（I1〜I3・I7）と差分。宣言の値は辞書のまま扱い、キーの順序と知らないキーを保つ。

- I7 を最初に当てる: 書く値・既存の値のどちらかが秘密の形なら、その項目を `{"unknown": "秘密の形"}` にし、値を出さない
- I1: 置き換えてよいのは、キーが無い項目と、前の `write` の値のまま（`analysis.written` の指紋が一致する）項目だけ
- I2: `worktree.json` は `base_branch`・`production_branch` の無いキー（と前の `write` の値のままのキー）だけを変える
- I3: 新しい解析で不明でも、前の値があれば残す。`ci` のワークフローの行の壁時計も、新しい行に無ければ同じパスの前の値を残す（#1664 の I6）
"""

from __future__ import annotations

import difflib
import hashlib
import json
from typing import Any, Callable

from . import BRANCHES, ITEM_KEYS
from .secret import has_secret, redact

SECRET_REASON = "秘密の形"
WORKTREE_KEYS = (("base_branch", "base"), ("production_branch", "production"))


def value_digest(value: Any) -> str:
    """値を正規化した JSON の SHA-256（書いた値の指紋）。"""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def is_unknown(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"unknown"}


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def resolve_item(key: str, m: dict | None, answers: dict, check: Callable[[str, Any], str | None]) -> tuple[Any, str]:
    """1 項目の新しい値と出所（`測った` / `判断した` / `不明`）。`check` は形の誤りを返す（合えば None）。"""
    if not m:
        return {"unknown": "測定の結果に無い"}, "不明"
    status = m.get("status")
    if status == "measured":
        return m.get("value"), "測った"
    if status == "question":
        ans = answers.get(key)
        if not isinstance(ans, dict):
            return {"unknown": "答えが無い"}, "不明"
        if "unknown" in ans:
            return {"unknown": str(ans["unknown"] or "決められない")}, "不明"
        problem = check(key, ans.get("value"))
        if problem:
            return {"unknown": f"答えが形に合わない: {problem}"}, "不明"
        return ans.get("value"), "判断した"
    return {"unknown": str(m.get("reason") or "測れない")}, "不明"


def _carry_walls(cur: Any, new: Any) -> tuple[Any, list[dict]]:
    """`ci` の新しい値で `wall_seconds` の無い行に、前の値の同じ `path` の行の壁時計を引き継ぐ（#1664 の I6）。"""
    if not isinstance(cur, dict) or not isinstance(new, dict) or is_unknown(cur) or is_unknown(new):
        return new, []
    before = {w.get("path"): w["wall_seconds"] for w in cur.get("workflows") or [] if isinstance(w, dict) and "wall_seconds" in w}
    rows, workflows = [], []
    for w in new.get("workflows") or []:
        if isinstance(w, dict) and "wall_seconds" not in w and w.get("path") in before:
            w = {**w, "wall_seconds": before[w["path"]]}
            rows.append({"kind": "unknown", "key": f"ci#{w['path']}", "reason": "新しい解析に壁時計が無い", "kept_previous": True})
        workflows.append(w)
    return ({**new, "workflows": workflows} if rows else new), rows


def merge_item(key: str, cur: Any, has_cur: bool, new: Any, origin: str, written: dict) -> tuple[Any, list[dict], bool]:
    """1 項目を決める。返すのは (書く値, 出力の行, 解析の値か)。解析の値なら呼ぶ側が `written` に指紋を持つ。"""
    if has_cur and has_secret(cur):
        return {"unknown": SECRET_REASON}, [{"kind": "secret", "key": key}], True
    rows = []
    if has_secret(new):
        new, origin = {"unknown": SECRET_REASON}, "不明"
        rows.append({"kind": "secret", "key": key})

    def put(value):
        if is_unknown(value):
            return {"kind": "unknown", "key": key, "reason": value["unknown"]}
        return {"kind": "written", "key": key, "origin": origin}

    if not has_cur:
        return new, rows + [put(new)], True
    if written.get(key) != value_digest(cur):
        if not is_unknown(new) and value_digest(new) != value_digest(cur):
            return cur, rows + [{"kind": "mismatch", "key": key, "declared": cur, "analyzed": new}], False
        return cur, rows + [{"kind": "kept", "key": key}], False
    if is_unknown(new) and not is_unknown(cur):
        return cur, rows + [{"kind": "unknown", "key": key, "reason": new["unknown"], "kept_previous": True}], True
    if key == "ci":
        new, carried = _carry_walls(cur, new)
        rows += carried
    return new, rows + [put(new)], True


def merge_project(existing: dict | None, measure: dict, answers: dict, check, analysis: dict, schema_url: str) -> tuple[dict, list[dict]]:
    """新しい `project.json` と出力の行。`analysis` は `written` を除いた節（呼ぶ側が指紋を作る）。"""
    doc = dict(existing) if existing else {"$schema": schema_url, "version": 1}
    written_before = ((existing or {}).get("analysis") or {}).get("written") or {}
    written, rows = {}, []
    items = measure.get("items") or {}
    for key in ITEM_KEYS:
        new, origin = resolve_item(key, items.get(key), answers, check)
        value, got, ours = merge_item(key, doc.get(key), key in doc, new, origin, written_before)
        doc[key] = value
        rows += got
        if ours:
            written[key] = value_digest(value)
    doc.pop("analysis", None)
    doc["analysis"] = {**analysis, "written": written}
    return doc, rows


def branch_answer(measure: dict, answers: dict, check) -> tuple[dict, str]:
    """P6 の値（`{"base", "production"}`）と出所。"""
    value, origin = resolve_item(BRANCHES, (measure.get("items") or {}).get(BRANCHES), answers, check)
    return ({} if is_unknown(value) else dict(value or {})), origin


def merge_worktree(existing: dict, branches: dict, origin: str, written_before: dict) -> tuple[dict, list[dict], dict]:
    """`worktree.json` に欠けた 2 キーを足す（I2）。返すのは (新しい中身, 出力の行, 書いたキーの指紋)。"""
    doc = dict(existing)
    rows, written = [], {}
    for wkey, bkey in WORKTREE_KEYS:
        new = branches.get(bkey)
        tag = f"worktree.json#{wkey}"
        if not isinstance(new, str) or not new or has_secret(new):
            if wkey not in doc:
                rows.append({"kind": "unknown", "key": tag, "reason": "起点・本番を決められない"})
            elif written_before.get(tag) == value_digest(doc[wkey]):
                written[tag] = written_before[tag]
            continue
        if wkey not in doc:
            doc[wkey] = new
            rows.append({"kind": "written", "key": tag, "origin": origin})
            written[tag] = value_digest(new)
        elif written_before.get(tag) == value_digest(doc[wkey]):
            doc[wkey] = new
            rows.append({"kind": "written" if doc[wkey] != existing[wkey] else "kept", "key": tag, "origin": origin})
            written[tag] = value_digest(new)
        elif doc[wkey] != new:
            rows.append({"kind": "mismatch", "key": tag, "declared": doc[wkey], "analyzed": new})
        else:
            rows.append({"kind": "kept", "key": tag})
    return doc, rows, written


def unified(path: str, old: str | None, new: str) -> str:
    """差分（秘密の形の行は伏せる）。"""
    lines = difflib.unified_diff((old or "").splitlines(), new.splitlines(), f"a/{path}", f"b/{path}", lineterm="")
    return "\n".join(line[:1] + redact(line[1:]) if not line.startswith(("+++", "---", "@@")) else line for line in lines)
