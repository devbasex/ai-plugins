"""glossary_merge.py: 用語集の 3 方向の取り込み（#1913）。標準ライブラリだけを読む。

祖先（base）・自分（ours）・相手（theirs）の 3 つの用語集を、語は `(context, term)`、
コンテキストは `id` を鍵に突き合わせる。片側だけが変えた鍵はその側を採り、両側が同じに
したものは 1 つにする。両側が違う中身にした鍵は「語の衝突」として返す。

並びは自分の側の順を土台にし、相手だけの鍵を、相手の並びで直前にある結果の鍵の直後へ
差し込む（直前が無ければ先頭）。同じ 3 つの版からは同じ結果が出る。

ファイルも git も触らない。例外は投げず、衝突と同じ鍵の重複は戻り値で返す。
"""

from __future__ import annotations

from typing import NamedTuple

MISSING = object()
SIDES = ("base", "ours", "theirs")
CHANGES = ("added", "changed", "removed")


class Merged(NamedTuple):
    glossary: dict | None  # 衝突か重複があれば None
    conflicts: list[dict]
    duplicates: list[dict]
    counts: dict[str, int]  # 相手の側から採った語の added / changed / removed


def term_key(t: dict) -> tuple:
    return (t.get("context"), t.get("term"))


def context_key(c: dict) -> str:
    return c.get("id")


def bad_keys(g: dict) -> str | None:
    """語とコンテキストの鍵が文字列でなければ理由を返す（呼び手は読めないとして止める）。"""
    for c in g.get("contexts", []):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str):
            return f"コンテキストの id が文字列でない: {c!r}"
    for t in g.get("terms", []):
        if not isinstance(t, dict) or not isinstance(t.get("context"), str) or not isinstance(t.get("term"), str):
            return f"語の context / term が文字列でない: {t!r}"
    return None


def unmerged_stages(ls_files_u: str) -> dict[str, set[int]]:
    """`git ls-files -u -z` の出力から、パスごとの段（1 = 祖先・2 = 自分・3 = 相手）を返す。"""
    out: dict[str, set[int]] = {}
    for line in ls_files_u.split("\0"):
        meta, _, path = line.partition("\t")
        if path:
            out.setdefault(path, set()).add(int(meta.split()[2]))
    return out


def error_line(it: dict) -> str:
    """止まったときに標準エラーへ出す 1 行（`ERROR: <kind>: <side>: <context>: <term>` の空でない欄）。"""
    fields = [it.get("kind") or it.get("result")] + [it[k] for k in ("side", "context", "term", "key", "path") if it.get(k) is not None]
    return "ERROR: " + ": ".join(map(str, fields))


def _pick(b, o, t):
    """1 つの鍵の (採る中身, 相手から採った変化, 衝突の種類)。中身が無いことは MISSING。"""
    if o == t:
        return o, None, None
    if o == b:
        change = "added" if b is MISSING else "removed" if t is MISSING else "changed"
        return t, change, None
    if t == b:
        return o, None, None
    kind = "both_added" if b is MISSING else "deleted_and_changed" if MISSING in (o, t) else "both_changed"
    return MISSING, None, kind


def _ordered(ours: list, theirs: list, present) -> list:
    """自分の並びに、相手だけの鍵を相手の並びの直前の鍵の後へ差し込む。"""
    order = [k for k in ours if present(k)]
    seen = set(ours)
    for i, k in enumerate(theirs):
        if k in seen or not present(k):
            continue
        at = 0
        for prev in reversed(theirs[:i]):
            if prev in order:
                at = order.index(prev) + 1
                break
        order.insert(at, k)
    return order


def _index(entries: list, key, side: str, duplicates: list, describe) -> dict:
    out: dict = {}
    for e in entries:
        k = key(e)
        if k in out:
            duplicates.append({"kind": "duplicate", "side": side, **describe(k)})
        else:
            out[k] = e
    return out


def _merge_entries(versions: tuple, key, describe, conflicts: list, duplicates: list, counts: dict | None) -> list:
    idx = [_index(v, key, side, duplicates, describe) for v, side in zip(versions, SIDES)]
    picked: dict = {}
    for k in dict.fromkeys([*idx[0], *idx[1], *idx[2]]):
        value, change, kind = _pick(*(i.get(k, MISSING) for i in idx))
        if kind:
            conflicts.append({"kind": kind, **describe(k)})
        elif value is not MISSING:
            picked[k] = value
        if change and counts is not None:
            counts[change] += 1
    return [picked[k] for k in _ordered(list(idx[1]), list(idx[2]), picked.__contains__)]


def merge_glossaries(base: dict, ours: dict, theirs: dict) -> Merged:
    """3 つの用語集から、取り込んだ用語集・語の衝突・同じ鍵の重複・件数を返す。"""
    versions = (base, ours, theirs)
    conflicts: list[dict] = []
    duplicates: list[dict] = []
    counts = dict.fromkeys(CHANGES, 0)
    lists = {
        "contexts": _merge_entries(
            tuple(v.get("contexts", []) for v in versions),
            context_key,
            lambda k: {"context": k, "term": None},
            conflicts,
            duplicates,
            None,
        ),
        "terms": _merge_entries(
            tuple(v.get("terms", []) for v in versions),
            term_key,
            lambda k: {"context": k[0], "term": k[1]},
            conflicts,
            duplicates,
            counts,
        ),
    }
    top: dict = {}
    for k in _ordered(list(ours), list(theirs), lambda k: True):
        if k in lists:
            top[k] = lists.pop(k)
            continue
        value, _, kind = _pick(*(v.get(k, MISSING) for v in versions))
        if kind:
            conflicts.append({"kind": "key_changed", "context": None, "term": None, "key": k})
        elif value is not MISSING:
            top[k] = value
    top.update(lists)  # どの版にも contexts / terms が無いときも配列を置く
    if duplicates:
        return Merged(None, [], duplicates, counts)
    return Merged(None if conflicts else top, conflicts, [], counts)
