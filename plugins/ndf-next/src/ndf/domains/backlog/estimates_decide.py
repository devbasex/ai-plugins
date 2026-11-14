"""課題ごとの見積の採り方（純粋）。

見積は この回の入力（`--scores`）→ 記録（estimates.json）→ 前回の順位の表 の順に採る。見直しの回は今の課題の
要約値（`digests`）を渡し、要約値の違う記録と前回の表から戻した見積（要約値を持たない）を `excluded` に入れて
採点し直しを求める。候補の収集が「見積の欠け」と数える基準と同じにし、欠けを残したまま見直しを終えない（#2540）。

見積もりの記録から決めた Size（`--sizes`。実績に照らした段階か尺度を超える印）があれば、その課題の Size を置き換える
（`overlay_sizes`）。置き換えた値は記録（estimates.json）へ写さず、毎回の入力から重ねる。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Optional

from ndf.domains.backlog import model
from ndf.domains.backlog.model import Config, Estimate, EstimateError

NO_DIGEST = "digest が無い（candidates の値を写す）"
STALE = "見積が古い（採点し直す）"
MISSING = "見積が無い（採点する）"


def _restored(row: Mapping[str, Any]) -> dict[str, Any]:
    """前回の表の行から見積を戻す（要約値を持たない）。"""
    return {k: row[k] for k in ("number", *model.STEPS, "size", "waiting_decision") if k in row}


def _fresh(raw: Any, digest: str) -> bool:
    """記録の見積が今の課題に合うか（辞書で、要約値が今の要約値と同じ）。"""
    return isinstance(raw, dict) and raw.get("digest") == digest


def _pick(
    n: int, given: Mapping[int, Any], stored: Mapping[str, Any], previous: Mapping[int, Any], digests: Optional[Mapping[int, str]]
) -> tuple[Any, Optional[str]]:
    """採る見積の生の値と、採れないときの理由。"""
    if n in given:
        raw = given[n]
        if not isinstance(raw, dict) or not raw.get("digest"):
            return None, NO_DIGEST
        # 見直しは、この回の入力も今の課題と照らす（候補の収集の後に本文が書き換わった古い見積で終えない）
        return (None, STALE) if digests is not None and not _fresh(raw, digests[n]) else (raw, None)
    if str(n) in stored:
        raw = stored[str(n)]
        if digests is not None and not _fresh(raw, digests[n]):
            return None, STALE
        return raw, None
    if n in previous:
        return (None, STALE) if digests is not None else (_restored(previous[n]), None)
    return None, MISSING


def estimates(
    numbers: list[int],
    given: Mapping[int, Any],
    stored: Mapping[str, Any],
    previous: Mapping[int, Any],
    cfg: Config,
    digests: Optional[Mapping[int, str]] = None,
) -> tuple[dict[int, Estimate], list[dict[str, Any]]]:
    """open の課題ごとの見積と、外した課題（番号と理由）。"""
    ests: dict[int, Estimate] = {}
    excluded: list[dict[str, Any]] = []
    for n in sorted(numbers):
        raw, why = _pick(n, given, stored, previous, digests)
        if raw is None:
            excluded.append({"number": n, "reason": why})
            continue
        try:
            ests[n] = model.parse_estimate({**raw, "number": n}, cfg)
        except EstimateError as exc:
            excluded.append({"number": n, "reason": str(exc)})
    return ests, excluded


def unscored(numbers: list[int], stored: Mapping[str, Any], digests: Mapping[int, str]) -> list[int]:
    """記録の見積が無い課題と、見積を付けた後に要約値が変わった課題。記録が空（rank を打っていない）なら空。"""
    if not stored:
        return []
    return [n for n in numbers if not _fresh(stored.get(str(n)), digests[n])]


def overlay_sizes(ests: Mapping[int, Estimate], sizes: Mapping[int, Any], cfg: Config) -> tuple[dict[int, Estimate], list[int]]:
    """見積もりの記録から決めた Size を重ねた見積と、重ねた課題の番号。尺度にも印にも無い値は `EstimateError`。"""
    out = dict(ests)
    sized = []
    for n in sorted(set(sizes) & set(ests)):
        size = sizes[n]
        if size != model.OVER_SCALE and size not in cfg.scale:
            raise EstimateError(f"--sizes の #{n} の Size が尺度 {list(cfg.scale)} か {model.OVER_SCALE!r} でない: {size!r}")
        out[n] = replace(ests[n], size=size)
        sized.append(n)
    return out, sized
