"""課題ごとの見積の採り方（純粋）。

見積は この回の入力（`--scores`）→ 記録（estimates.json）→ 前回の順位の記録（rank.json）→ マイルストーンの説明の
`### 順位` の表 の順に採る。見直しの回は今の課題の要約値（`digests`）を渡し、要約値の違う入力と記録を `excluded` に入れて
採点し直しを求める。候補の収集が「見積の欠け」と数える基準と同じにし、欠けを残したまま見直しを終えない（#2540）。
前回の順位の記録と説明の表から戻した見積は要約値を持たないため、深さによらず採って `restored` に番号を返す（#3854）。

差分の棚卸しで起点の時刻（`since`）を渡すと、見積がどこにも無く起点以前に作られた課題は `excluded` にも順位にも入れず、
`left_for_review`（見直しで段階を付ける課題）に番号を返す。

見積もりの記録から決めた Size（`--sizes`。実績に照らした段階か尺度を超えるマーカー）があれば、その課題の Size を置き換える
（`overlay_sizes`）。置き換えた値は記録（estimates.json）へ写さず、毎回の入力から重ねる。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, NamedTuple, Optional

from ndf.domains.backlog import model
from ndf.domains.backlog.model import Config, Estimate, EstimateError

NO_DIGEST = "digest が無い（candidates の値を写す）"
STALE = "見積が古い（採点し直す）"
MISSING = "見積が無い（採点する）"
# metrics のキー名（見直しへ回した課題・根拠を持たない戻した見積の課題）
LEFT_FOR_REVIEW = "left_for_review"
RESTORED = "restored"


class Picked(NamedTuple):
    """見積の採り方の結果: 採った見積・外した課題（番号と理由）・見直しへ回した課題・戻した見積の課題。"""

    estimates: dict[int, Estimate]
    excluded: list[dict[str, Any]]
    left_for_review: list[int]
    restored: list[int]


def _restored(row: Mapping[str, Any], cfg: Config) -> dict[str, Any]:
    """前回の順位の行か説明の表の行から見積を戻す（要約値を持たない）。マーカーで外した段階（None）は尺度の最小へ戻す。"""
    out = {k: row[k] for k in ("number", *model.COLUMNS, "waiting_decision") if k in row}
    return {k: (cfg.scale[0] if v is None and k in model.COLUMNS else v) for k, v in out.items()}


def _fresh(raw: Any, digest: str) -> bool:
    """記録の見積が今の課題に合うか（辞書で、要約値が今の要約値と同じ）。"""
    return isinstance(raw, dict) and raw.get("digest") == digest


class Sources(NamedTuple):
    """見積の出どころ（採る順）: この回の入力・記録（estimates.json）・前回の順位の記録（rank.json）・説明の順位の表。"""

    given: Mapping[int, Any]
    stored: Mapping[str, Any]
    previous: Mapping[int, Any]
    tables: Mapping[int, Any]


def _pick(n: int, src: Sources, cfg: Config, digests: Optional[Mapping[int, str]]) -> tuple[Any, Optional[str], bool]:
    """採る見積の生の値と、採れないときの理由と、この回の入力か。"""
    if n in src.given:
        raw = src.given[n]
        if not isinstance(raw, dict) or not raw.get("digest"):
            return None, NO_DIGEST, True
        # 見直しは、この回の入力も今の課題と照らす（候補の収集の後に本文が書き換わった古い見積で終えない）
        return (None, STALE, True) if digests is not None and not _fresh(raw, digests[n]) else (raw, None, True)
    if str(n) in src.stored:
        raw = src.stored[str(n)]
        if digests is not None and not _fresh(raw, digests[n]):
            return None, STALE, False
        return raw, None, False
    for rows in (src.previous, src.tables):
        if n in rows:
            return {**_restored(rows[n], cfg), RESTORED: True}, None, False
    return None, MISSING, False


def _left(n: int, since: Optional[str], created: Optional[Mapping[int, str]]) -> bool:
    """見積の無い課題を見直しへ回すか（起点があり、作成の時刻が起点以前）。"""
    return since is not None and created is not None and n in created and created[n] <= since


def estimates(
    numbers: list[int],
    src: Sources,
    cfg: Config,
    digests: Optional[Mapping[int, str]] = None,
    since: Optional[str] = None,
    created: Optional[Mapping[int, str]] = None,
) -> Picked:
    """open の課題ごとの見積と、外した課題・見直しへ回した課題・戻した見積の課題。"""
    ests: dict[int, Estimate] = {}
    excluded: list[dict[str, Any]] = []
    left: list[int] = []
    for n in sorted(numbers):
        raw, why, is_given = _pick(n, src, cfg, digests)
        if raw is None:
            if why == MISSING and _left(n, since, created):
                left.append(n)
            else:
                excluded.append({"number": n, "reason": why})
            continue
        try:
            est = model.parse_estimate({**raw, "number": n}, cfg, given=is_given)
        except EstimateError as exc:
            excluded.append({"number": n, "reason": str(exc)})
            continue
        ests[n] = replace(est, restored=True) if raw.get(RESTORED) else est
    return Picked(ests, excluded, left, [n for n, e in ests.items() if e.restored])


def unscored(numbers: list[int], stored: Mapping[str, Any], digests: Mapping[int, str]) -> list[int]:
    """記録の見積が無い課題と、見積を付けた後に要約値が変わった課題。記録が空（rank を打っていない）なら空。"""
    if not stored:
        return []
    return [n for n in numbers if not _fresh(stored.get(str(n)), digests[n])]


def overlay_sizes(ests: Mapping[int, Estimate], sizes: Mapping[int, Any], cfg: Config) -> tuple[dict[int, Estimate], list[int]]:
    """見積もりの記録から決めた Size を重ねた見積と、重ねた課題の番号。尺度にもマーカーにも無い値は `EstimateError`。"""
    out = dict(ests)
    sized = []
    for n in sorted(set(sizes) & set(ests)):
        size = sizes[n]
        if size != model.OVER_SCALE and size not in cfg.scale:
            raise EstimateError(f"--sizes の #{n} の Size が尺度 {list(cfg.scale)} か {model.OVER_SCALE!r} でない: {size!r}")
        out[n] = replace(ests[n], size=size)
        sized.append(n)
    return out, sized
