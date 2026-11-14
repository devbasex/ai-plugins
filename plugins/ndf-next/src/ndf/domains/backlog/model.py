"""見積・行・設定の型と採点（WSJF）。

見積の 1 件は課題ごとの 4 つの段階（UBV・TC・RR/OE・Size）で、遅延のコスト（CoD）は UBV + TC + RR/OE、
順位の値は CoD / Size^指数である。ラベルの下限と被依存の数は段階を上げる向きにだけ効く。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional

FORWARD, BACKWARD = "前倒し", "後ろ倒し"
DIRECTIONS = (FORWARD, BACKWARD)
AUTO, APPROVAL, APPROVED = "自動", "承認", "承認済み"
SLOT_NONE, SLOT_SPLIT, SLOT_FIT = "なし", "分割が要る", "当てた"
# 棚卸しの深さ（差分・見直し・大整理）
DIFF, REVIEW, OVERHAUL = "diff", "review", "overhaul"
# 深さの実行の記録の事象（打ち切り・走らせた）
TIMED_OUT, RAN = "timed_out", "ran"
# 尺度を超える Size の印（見積の size の値）
OVER_SCALE = "over"
STEPS = ("ubv", "tc", "rr_oe")
COLUMN_NAMES = {"ubv": "UBV", "tc": "TC", "rr_oe": "RR/OE", "size": "Size"}
DIGEST_CHARS = 16
# 行の段階の出どころ（Row.sources の値）: 見積・ラベルの下限・被依存の数・見積もりの記録
SOURCE_LLM, SOURCE_LABEL, SOURCE_DEPENDENTS, SOURCE_RECORDS = "llm", "label", "dependents", "records"


def is_positive_int(v: Any) -> bool:
    """宣言の値が正の整数か（真偽値は整数と見なさない）。"""
    return type(v) is int and v > 0


class EstimateError(ValueError):
    """見積の形が違う。"""


def stable_digest(obj: Any) -> str:
    """JSON にできる値の要約値（鍵の順によらない）。"""
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def snapshot_digest(issue: dict) -> str:
    """課題の要約値。apply が書き換えうる欄（題・本文・状態・マイルストーン・ラベル）をまとめる。

    見積を付けた後と plan を作った後に課題が書き換わったかを比べる。題と本文だけでは、状態・ラベルだけの
    並行した更新を見落として、古い plan で閉じ直したりラベルを外したりする。
    """
    return stable_digest({"title": issue["title"], "body": issue["body"], "state": issue["state"],
                          "milestone": issue["milestone"], "labels": sorted(issue["labels"])})  # fmt: skip


@dataclass(frozen=True)
class Config:
    """順位の設定。一般的な WSJF の値だけを既定に持ち、リポジトリに固有の値（容量・品質のラベル）は宣言か引数で渡す。"""

    scale: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 20)
    margin_steps: int = 1
    auto_threshold: int = 8
    capacity: Optional[int] = None
    dependents_steps: tuple[tuple[int, int], ...] = ((0, 1), (1, 5), (2, 8), (3, 13), (5, 20))
    size_exponent: float = 0.3
    large_slot_ratio: float = 0.5
    large_min_size: int = 13
    label_floor: dict[str, int] = field(default_factory=dict)
    # 品質の枠（.ndf/backlog.json の quality）。{"label", "ratio", "min"}。無ければ枠を取らない
    quality: Optional[dict[str, Any]] = None

    def threshold(self, cod: int) -> int:
        """前倒しの閾値。尺度のうち cod より大きい値を小さい順に数えて余白番目の値。尺度の先は最後の差で延ばす。"""
        if self.margin_steps == 0:
            return cod + 1
        step = self.scale[-1] - self.scale[-2]
        above = [v for v in self.scale if v > cod]
        v = self.scale[-1]
        while len(above) < self.margin_steps:
            v += step
            if v > cod:
                above.append(v)
        return above[self.margin_steps - 1]

    def dependents_step(self, count: int) -> int:
        """被依存の数（閉じると解放する課題の数）を段階へ換算する。"""
        got = self.scale[0]
        for lo, step in self.dependents_steps:
            if count >= lo:
                got = step
        return got

    def label_step(self, labels: list[str]) -> Optional[int]:
        """ラベルの下限のうち最大。対応が無ければ None。"""
        hits = [self.label_floor[lb] for lb in labels if lb in self.label_floor]
        return max(hits) if hits else None


class ConfigError(ValueError):
    """宣言（.ndf/backlog.json）の順位の設定の形が違う。"""


QUALITY_DEFAULTS = {"label": "quality", "ratio": 0.2, "min": 1}


def _int(v: Any, key: str) -> int:
    if type(v) is not int:
        raise ConfigError(f"{key} は整数で書く: {v!r}")
    return v


def _ratio(v: Any, key: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 1:
        raise ConfigError(f"{key} は 0 以上 1 未満の数で書く: {v!r}")
    return float(v)


def _quality(sec: Any) -> Optional[dict[str, Any]]:
    if sec is None:
        return None
    if not isinstance(sec, dict):
        raise ConfigError("quality は label・ratio・min のオブジェクトで書く")
    q = {**QUALITY_DEFAULTS, **sec}
    if not isinstance(q["label"], str) or not q["label"]:
        raise ConfigError("quality.label は空でない文字列で書く")
    if _int(q["min"], "quality.min") < 0:
        raise ConfigError("quality.min は 0 以上")
    return {"label": q["label"], "ratio": _ratio(q["ratio"], "quality.ratio"), "min": q["min"]}


def config_from(decl: dict[str, Any], capacity: Optional[int]) -> Config:
    """宣言の順位の設定（容量・ラベルの下限・尺度ほか）を読み、引数の容量で上書きする。無い値は既定値。"""
    base = Config()

    def pick(key: str) -> Any:
        return decl[key] if key in decl and decl[key] is not None else getattr(base, key)

    scale = tuple(_int(v, "scale") for v in pick("scale"))
    if len(scale) < 2 or scale[0] <= 0 or any(b <= a for a, b in zip(scale, scale[1:])):
        raise ConfigError("scale は 2 個以上の正の整数の狭義の昇順で書く")

    def in_scale(v: Any, key: str) -> int:
        if _int(v, key) not in scale:
            raise ConfigError(f"{key} は尺度の値で書く: {v}")
        return v

    cap = capacity if capacity is not None else (decl["capacity"] if "capacity" in decl else None)
    if cap is not None and not is_positive_int(cap):
        raise ConfigError(f"capacity は正の整数で書く: {cap!r}")
    margin = _int(pick("margin_steps"), "margin_steps")
    if margin < 0:
        raise ConfigError("margin_steps は 0 以上")
    deps = pick("dependents_steps")
    if not isinstance(deps, (list, tuple)) or not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in deps):
        raise ConfigError("dependents_steps は [下限, 段階] の列で書く")
    deps = tuple((_int(a, "dependents_steps"), in_scale(b, "dependents_steps の段階")) for a, b in deps)
    if any(b <= a for (a, _), (b, _) in zip(deps, deps[1:])):
        raise ConfigError("dependents_steps は被依存の数の下限の昇順で書く")
    floor = pick("label_floor")
    if not isinstance(floor, dict):
        raise ConfigError("label_floor はラベル → 段階のオブジェクトで書く")
    alpha = pick("size_exponent")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 <= alpha <= 1:
        raise ConfigError(f"size_exponent は 0 以上 1 以下の数で書く: {alpha!r}")
    return Config(
        scale=scale, margin_steps=margin, auto_threshold=in_scale(pick("auto_threshold"), "auto_threshold"), capacity=cap,
        dependents_steps=deps, size_exponent=float(alpha), large_slot_ratio=_ratio(pick("large_slot_ratio"), "large_slot_ratio"),
        large_min_size=in_scale(decl["large_min_size"] if "large_min_size" in decl else scale[-2], "large_min_size"),
        label_floor={str(k): in_scale(v, f"label_floor[{k}]") for k, v in floor.items()},
        quality=_quality(decl["quality"] if "quality" in decl else None),
    )  # fmt: skip


@dataclass(frozen=True)
class Estimate:
    """課題 1 件の見積。`size` は尺度の値か `OVER_SCALE`。"""

    number: int
    ubv: int
    tc: int
    rr_oe: int
    size: int | str
    digest: Optional[str] = None
    waiting_decision: bool = False
    observed_harm: bool = False

    @property
    def over(self) -> bool:
        return self.size == OVER_SCALE

    def to_json(self) -> dict[str, Any]:
        return {
            "number": self.number, "ubv": self.ubv, "tc": self.tc, "rr_oe": self.rr_oe, "size": self.size,
            "digest": self.digest, "waiting_decision": self.waiting_decision, "observed_harm": self.observed_harm,
        }  # fmt: skip


def _flag(raw: dict, key: str) -> bool:
    return bool(raw[key]) if key in raw else False


def parse_estimate(raw: Any, cfg: Config) -> Estimate:
    """見積の 1 件を確かめて読む。段階が尺度に無ければ `EstimateError`。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
        raise EstimateError("見積は number（整数）を持つオブジェクトで書く")
    for key in STEPS:
        if raw.get(key) not in cfg.scale:
            raise EstimateError(f"#{raw['number']} の {COLUMN_NAMES[key]} が尺度 {list(cfg.scale)} に無い: {raw.get(key)!r}")
    size = raw.get("size")
    if size != OVER_SCALE and size not in cfg.scale:
        raise EstimateError(f"#{raw['number']} の Size が尺度か {OVER_SCALE!r} でない: {size!r}")
    digest = raw["digest"] if isinstance(raw.get("digest"), str) else None
    return Estimate(
        raw["number"], raw["ubv"], raw["tc"], raw["rr_oe"], size, digest, _flag(raw, "waiting_decision"), _flag(raw, "observed_harm")
    )


@dataclass(frozen=True)
class Row:
    """順位の 1 行（実効の段階と CoD）。尺度を超える課題の size は尺度の最大で数える。"""

    number: int
    ubv: int
    tc: int
    rr_oe: int
    size: int
    cod: int
    value: float
    waiting_decision: bool
    observed_harm: bool
    over: bool
    sources: dict[str, str]


def score(est: Estimate, labels: list[str], released: int, cfg: Config) -> Row:
    """実効の段階・CoD・順位の値。ラベルの下限と被依存の対応表は上げる向きにだけ効く。"""
    ubv, ubv_src = est.ubv, SOURCE_LLM
    floor = cfg.label_step(labels)
    if floor is not None and floor > ubv:
        ubv, ubv_src = floor, SOURCE_LABEL
    rr, rr_src = est.rr_oe, SOURCE_LLM
    by_deps = cfg.dependents_step(released)
    if by_deps > rr:
        rr, rr_src = by_deps, SOURCE_DEPENDENTS
    size = cfg.scale[-1] if est.over else int(est.size)
    cod = ubv + est.tc + rr
    value = round(cod / math.pow(size, cfg.size_exponent), 9)
    return Row(
        est.number, ubv, est.tc, rr, size, cod, value, est.waiting_decision, est.observed_harm, est.over, {"ubv": ubv_src, "rr_oe": rr_src}
    )


def row_key(r: Row) -> tuple[float, int, int]:
    """同じ順に並べる課題の中の順序: 順位の値の降順 → CoD の降順 → 番号の昇順。"""
    return (-r.value, -r.cod, r.number)
