"""判定役の判断（#4363。純粋な関数）。

判定役は、相手役が書いた重い指摘（critical・major）が実際に起きるかを確かめる読むだけの AI である。ここは、渡す指摘を
選び（`select`）、指示を組み（`instruction`）、回答を読み（`read`）、下げてよいかを決める（`lowers`）。起動は持たず、
指摘の行へ書くのは `journal.FindingJournal.apply_checks` である。指示の文は `contracts/data/review-criteria.json` の `verify`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.domains.crossreview import criteria_decide
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.records import mvv_decide

# 指示に載せる指摘の欄（書いた相手役 `by` は載せない）
_SHOWN = ("id", "severity", "text", "impact", "path", "line", "section")


class Verdict(NamedTuple):
    """指摘 1 件への回答。`holds` は起きると示せたか（None は資料が足りず判断できない）。`tracks` は指摘の種類の表にある
    値だけで、`unknown` は表に無い値が書かれていたか。"""

    id: str
    holds: Optional[bool]
    condition: str = ""
    path: str = ""
    result: str = ""
    reason: str = ""
    tracks: tuple[str, ...] = ()
    unknown: bool = False


class Answers(NamedTuple):
    """判定役の回答。`verdicts` は番号から、有効な回答と判断できない回答だけを持つ。"""

    verdicts: Mapping[str, Verdict]
    warnings: tuple[str, ...] = ()


def _never_asked(row: Mapping[str, Any]) -> bool:
    """判定役へ渡さない指摘（必ず人の承認が要る操作の指摘と、NDF が機械で作った指摘）か。"""
    return bool(row.get(fin.REDLINE)) or row.get("by") == mvv_decide.BY_NDF


def _never_lowered(row: Mapping[str, Any]) -> bool:
    """回答がどうであっても下げない指摘（MVV に反する指摘と、保護の種類を持つ指摘）か。"""
    return bool(row.get(mvv_decide.REVIEW_ITEM)) or bool(row.get(criteria_decide.PROTECT))


def select(rows: Sequence[Mapping[str, Any]], use_tracks: bool = False) -> list[Mapping[str, Any]]:
    """呼び手が渡す指摘（このラウンドで新しく出たか戻った critical・major）のうち、判定役へ渡すもの。

    下がらない指摘（`_never_lowered`）は、ほかに渡す指摘があるか、呼び手が回答の指摘の種類を使う（`use_tracks`）ときだけ渡す。"""
    asked = [row for row in rows if not _never_asked(row)]
    if use_tracks or any(not _never_lowered(row) for row in asked):
        return asked
    return []


def instruction(rows: Sequence[Mapping[str, Any]], diff: Path, worktree: Path, criteria: Sequence[str]) -> str:
    """判定役への指示。`criteria` は指摘の基準の行。誰が書いた指摘かは載せない。"""
    text = criteria_decide.verify_text()
    kinds = {"protect": "・".join(criteria_decide.protect_kinds()), "all": "・".join(criteria_decide.track_kinds())}
    listed = [json.dumps({k: row[k] for k in _SHOWN if row.get(k) is not None}, ensure_ascii=False) for row in rows]
    return "\n".join(
        [
            text["system"],
            *text["steps"],
            f"作業場所: {worktree}",
            f"このラウンドの差分: {diff}",
            "## 指摘の基準",
            *criteria,
            "## 指摘の一覧",
            text["data"],
            *listed,
            "## 回答の形",
            text["tracks"].format(**kinds),
            text["answer"],
            text["shape"],
        ]
    )


def _filled(value: object) -> str:
    """文字列なら前後の空白を除いた値、ほかは空。"""
    return value.strip() if isinstance(value, str) else ""


def _tracks(value: object) -> tuple[tuple[str, ...], bool]:
    """回答の `tracks` を、表にある種類（表の順）と、表に無い値があったかに分ける。並びでない値は表に無い値とする。"""
    if value is None:
        return (), False
    if not isinstance(value, list):
        return (), True
    kinds = criteria_decide.track_kinds()
    return tuple(k for k in kinds if k in value), any(v not in kinds for v in value)


def _verdict(fid: str, item: Mapping[str, Any]) -> Optional[Verdict]:
    """回答の 1 件。起きると示せたのに条件・経路・結果が欠ける、成り立たないのに理由か `tracks` が無い（分類を確かめられ
    ない）、`holds` が無いか真偽値でも null でもない回答は None（不正）。"""
    if "holds" not in item:
        return None
    holds = item["holds"]
    condition, path, result, reason = (_filled(item.get(k)) for k in ("condition", "path", "result", "reason"))
    if holds is True:
        valid = bool(condition and path and result)
    elif holds is False:
        valid = bool(reason) and item.get("tracks") is not None
    else:
        valid = holds is None
    if not valid:
        return None
    tracks, unknown = _tracks(item.get("tracks"))
    return Verdict(fid, holds, condition, path, result, reason, tracks, unknown)


def _repeated(fid: str, items: Sequence[Mapping[str, Any]]) -> Verdict:
    """同じ番号への複数の回答。どれが正しいか決められないので判断できないとし、どれかが示した指摘の種類はすべて残す。"""
    shown = {kind for item in items for kind in _tracks(item.get("tracks"))[0]}
    kinds = tuple(k for k in criteria_decide.track_kinds() if k in shown)
    return Verdict(fid, None, reason="同じ番号の回答が複数ある", tracks=kinds)


def read(found: Optional[Sequence[Mapping[str, Any]]], ids: Sequence[str]) -> Answers:
    """回答の並び（出力から取り出せなければ None）を、渡した指摘 `ids` ごとの回答にする。読めない分は注意にする。"""
    if found is None:
        return Answers({}, (f"判定役の回答を読めないので下げない: {'・'.join(ids)}",))
    given: dict[str, list[Mapping[str, Any]]] = {}
    unknown: list[str] = []
    for item in found:
        fid = str(item.get("id"))
        if fid not in ids:
            unknown.append(" ".join(fid.split()))
        else:
            given.setdefault(fid, []).append(item)
    verdicts: dict[str, Verdict] = {}
    for fid, items in given.items():
        verdict = _repeated(fid, items) if len(items) > 1 else _verdict(fid, items[0])
        if verdict is not None:
            verdicts[fid] = verdict
    warnings = []
    unanswered = [fid for fid in ids if fid not in verdicts]
    if unanswered:
        warnings.append(f"判定役の有効な回答が無いので下げない: {'・'.join(unanswered)}")
    undecided = [f"{v.id}（{' '.join(v.reason.split()) or '理由なし'}）" for v in verdicts.values() if v.holds is None]
    if undecided:
        warnings.append(f"判定役が判断できないので下げない: {'・'.join(undecided)}")
    if unknown:
        warnings.append(f"判定役が渡していない番号に回答した: {'・'.join(unknown)}")
    return Answers(verdicts, tuple(warnings))


def lowers(row: Mapping[str, Any], verdict: Verdict) -> bool:
    """指摘 `row` を、回答 `verdict` を理由に軽い扱いへ下げてよいか。成り立たないとした指摘のうち、下げない指摘
    （`_never_asked`・`_never_lowered`）と、回答の種類が保護の種類か表に無い値を持つものを除く。"""
    if verdict.holds is not False or _never_asked(row) or _never_lowered(row):
        return False
    return not verdict.unknown and not set(verdict.tracks) & set(criteria_decide.protect_kinds())
