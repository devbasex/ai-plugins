"""見積・行・設定の型と採点（WSJF）。

見積の 1 件は課題ごとの 4 つの段階（UBV・TC・RR/OE・Size）で、遅延のコスト（CoD）は UBV + TC + RR/OE、
順位の値は CoD / Size^指数である。ラベルの下限と被依存の数は段階を上げる向きにだけ効く。

見積の入力（`--scores`）は列ごとに `{"step": <段階>, "why": "<根拠の 1 行>"}` で書き、`ubv` は実害の区分（`impact`）も持てる。
区分を持つ見積の UBV は実害の目安（設定の `harm_guide`）の帯の中になければならない（退避策が `防いでいる` なら 1 つ下の帯まで）。
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
# 尺度を超える Size のマーカー（見積の size の値）
OVER_SCALE = "over"
STEPS = ("ubv", "tc", "rr_oe")
COLUMNS = (*STEPS, "size")
COLUMN_NAMES = {"ubv": "UBV", "tc": "TC", "rr_oe": "RR/OE", "size": "Size"}
DIGEST_CHARS = 16
# 行の段階の出どころ（Row.sources の値）: 見積・ラベルの下限・被依存の数・見積もりの記録
SOURCE_LLM, SOURCE_LABEL, SOURCE_DEPENDENTS, SOURCE_RECORDS = "llm", "label", "dependents", "records"
# 実害の目安の既定（区分 → UBV の帯 [下限, 上限]。既定の尺度の値で書く）
HARM_GUIDE: dict[str, tuple[int, int]] = {"全体": (13, 20), "機能": (5, 8), "表示": (2, 3), "なし": (1, 1)}
# 退避策の状態。`防いでいる` だけが 1 つ下の区分の帯の UBV を受ける
MITIGATED, PARTIAL, NO_MITIGATION = "防いでいる", "部分的", "無い"
MITIGATIONS = (MITIGATED, PARTIAL, NO_MITIGATION)


def is_positive_int(v: Any) -> bool:
    """設定の値が正の整数か（真偽値は整数と見なさない）。"""
    return type(v) is int and v > 0


class EstimateError(ValueError):
    """見積の形が違う。"""


def stable_digest(obj: Any) -> str:
    """JSON にできる値の要約値（キーの順によらない）。"""
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
    """順位の設定。一般的な WSJF の値だけを既定に持ち、リポジトリに固有の値（容量・品質のラベル）は設定か引数で渡す。"""

    scale: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 20)
    margin_steps: int = 1
    auto_threshold: int = 8
    capacity: Optional[int] = None
    dependents_steps: tuple[tuple[int, int], ...] = ((0, 1), (1, 5), (2, 8), (3, 13), (5, 20))
    size_exponent: float = 0.3
    large_slot_ratio: float = 0.5
    large_min_size: int = 13
    label_floor: dict[str, int] = field(default_factory=dict)
    # 品質のクォータ（.ndf/backlog.json の quality）。{"label", "ratio", "min"}。無ければスロットを取らない
    quality: Optional[dict[str, Any]] = None
    # 実害の目安（区分 → UBV の帯）。空なら帯を確かめない（harm_guide を書かず、既定の帯の端が尺度に無いとき）
    harm_guide: dict[str, tuple[int, int]] = field(default_factory=lambda: dict(HARM_GUIDE))

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

    def lower_band(self, impact: str) -> Optional[tuple[int, int]]:
        """区分を 1 つ下げた帯（区分を帯の下限の降順に並べた次）。最下位なら None。"""
        order = sorted(self.harm_guide, key=lambda k: (-self.harm_guide[k][0], k))
        i = order.index(impact)
        return self.harm_guide[order[i + 1]] if i + 1 < len(order) else None


class ConfigError(ValueError):
    """設定（.ndf/backlog.json）の順位の設定の形が違う。"""


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


def _scale(raw: Any) -> tuple[int, ...]:
    """尺度を読む。2 個以上の正の整数の狭義の昇順でなければ `ConfigError`。"""
    scale = tuple(_int(v, "scale") for v in raw)
    if len(scale) < 2 or scale[0] <= 0 or any(b <= a for a, b in zip(scale, scale[1:])):
        raise ConfigError("scale は 2 個以上の正の整数の狭義の昇順で書く")
    return scale


def _in_scale(v: Any, key: str, scale: tuple[int, ...]) -> int:
    """`v` が尺度の値であることを確かめて返す。"""
    if _int(v, key) not in scale:
        raise ConfigError(f"{key} は尺度の値で書く: {v}")
    return v


def _dependents_steps(raw: Any, scale: tuple[int, ...]) -> tuple[tuple[int, int], ...]:
    """被依存の数の段階表（[下限, 段階] の列）を読む。下限は昇順、段階は尺度の値。"""
    if not isinstance(raw, (list, tuple)) or not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in raw):
        raise ConfigError("dependents_steps は [下限, 段階] の列で書く")
    deps = tuple((_int(a, "dependents_steps"), _in_scale(b, "dependents_steps の段階", scale)) for a, b in raw)
    if any(b <= a for (a, _), (b, _) in zip(deps, deps[1:])):
        raise ConfigError("dependents_steps は被依存の数の下限の昇順で書く")
    return deps


def _harm_guide(raw: Any, scale: tuple[int, ...]) -> dict[str, tuple[int, int]]:
    """実害の目安を読む。書いていなければ既定の帯で、その端のどれかが尺度に無ければ空（帯を確かめない）。

    書いた帯は端がどちらも尺度の値で、下限が上限以下でなければ `ConfigError`。
    """
    if raw is None:
        return dict(HARM_GUIDE) if all(v in scale for band in HARM_GUIDE.values() for v in band) else {}
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("harm_guide は区分 → [下限, 上限] のオブジェクトで書く")
    bands: dict[str, tuple[int, int]] = {}
    for k, band in raw.items():
        if not isinstance(band, (list, tuple)) or len(band) != 2:
            raise ConfigError(f"harm_guide[{k}] は [下限, 上限] で書く: {band!r}")
        lo, hi = (_in_scale(v, f"harm_guide[{k}]", scale) for v in band)
        if lo > hi:
            raise ConfigError(f"harm_guide[{k}] は下限を上限以下で書く: {band!r}")
        bands[str(k)] = (lo, hi)
    return bands


def config_from(decl: dict[str, Any], capacity: Optional[int]) -> Config:
    """設定の順位の設定（容量・ラベルの下限・尺度ほか）を読み、引数の容量で上書きする。無い値は既定値。"""
    base = Config()

    def pick(key: str) -> Any:
        return decl[key] if key in decl and decl[key] is not None else getattr(base, key)

    scale = _scale(pick("scale"))
    cap = capacity if capacity is not None else (decl["capacity"] if "capacity" in decl else None)
    if cap is not None and not is_positive_int(cap):
        raise ConfigError(f"capacity は正の整数で書く: {cap!r}")
    margin = _int(pick("margin_steps"), "margin_steps")
    if margin < 0:
        raise ConfigError("margin_steps は 0 以上")
    deps = _dependents_steps(pick("dependents_steps"), scale)
    floor = pick("label_floor")
    if not isinstance(floor, dict):
        raise ConfigError("label_floor はラベル → 段階のオブジェクトで書く")
    alpha = pick("size_exponent")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 <= alpha <= 1:
        raise ConfigError(f"size_exponent は 0 以上 1 以下の数で書く: {alpha!r}")
    return Config(
        scale=scale, margin_steps=margin, auto_threshold=_in_scale(pick("auto_threshold"), "auto_threshold", scale), capacity=cap,
        dependents_steps=deps, size_exponent=float(alpha), large_slot_ratio=_ratio(pick("large_slot_ratio"), "large_slot_ratio"),
        large_min_size=_in_scale(decl["large_min_size"] if "large_min_size" in decl else scale[-2], "large_min_size", scale),
        label_floor={str(k): _in_scale(v, f"label_floor[{k}]", scale) for k, v in floor.items()},
        quality=_quality(decl["quality"] if "quality" in decl else None),
        harm_guide=_harm_guide(decl["harm_guide"] if "harm_guide" in decl else None, scale),
    )  # fmt: skip


@dataclass(frozen=True)
class Estimate:
    """課題 1 件の見積。`size` は尺度の値か `OVER_SCALE`。

    `why` は列 → 根拠の 1 行、`impact` は UBV の実害の区分、`mitigation` は退避策の状態。`restored` は根拠を持たない
    見積（前回の順位の記録・説明の順位の表・旧い形の記録から戻した）である。
    """

    number: int
    ubv: int
    tc: int
    rr_oe: int
    size: int | str
    digest: Optional[str] = None
    waiting_decision: bool = False
    observed_harm: bool = False
    why: dict[str, str] = field(default_factory=dict)
    impact: Optional[str] = None
    mitigation: str = NO_MITIGATION
    restored: bool = False

    @property
    def over(self) -> bool:
        return self.size == OVER_SCALE

    def to_json(self) -> dict[str, Any]:
        """記録（estimates.json）の 1 行の形。列は `{"step", "why"}`（根拠が無ければ `step` だけ）で、`ubv` は区分も持つ。"""
        cols: dict[str, Any] = {}
        for c in COLUMNS:
            col: dict[str, Any] = {"step": getattr(self, c)}
            if c in self.why:
                col["why"] = self.why[c]
            cols[c] = col
        if self.impact is not None:
            cols["ubv"]["impact"] = self.impact
        return {"number": self.number, "digest": self.digest, **cols, "mitigation": self.mitigation,
                "waiting_decision": self.waiting_decision, "observed_harm": self.observed_harm}  # fmt: skip


def _flag(raw: dict, key: str) -> bool:
    return bool(raw[key]) if key in raw else False


def check_band(ubv: int, impact: str, mitigation: str, cfg: Config) -> Optional[str]:
    """UBV が区分 `impact` の実害の目安の帯にあるか。外れていれば理由の文、合えば None。

    退避策が `防いでいる` なら、1 つ下の区分の帯の値も受ける。
    """
    if impact not in cfg.harm_guide:
        return f"UBV の区分 {impact!r} が実害の目安に無い（{' / '.join(cfg.harm_guide)}）"
    lo, hi = cfg.harm_guide[impact]
    if lo <= ubv <= hi:
        return None
    lower = cfg.lower_band(impact) if mitigation == MITIGATED else None
    if lower is not None and lower[0] <= ubv < lo:
        return None
    extra = "（退避策が防いでいるときだけ 1 つ下の区分の帯も受ける）" if ubv < lo else ""
    return f"UBV {ubv} は区分「{impact}」の帯 {lo}〜{hi} の外{extra}"


def _step_ok(c: str, step: Any, cfg: Config) -> bool:
    if isinstance(step, bool):
        return False
    return step in cfg.scale or (c == "size" and step == OVER_SCALE)


def _column(n: int, c: str, col: Any, cfg: Config, given: bool) -> tuple[Any, Optional[str], bool]:
    """列 1 つの (段階, 根拠, 旧い形か)。`given` でなければ旧い形（段階だけの値）も根拠の無い見積として読む。"""
    name = COLUMN_NAMES[c]
    if isinstance(col, dict) and "step" in col:
        step, why, legacy = col["step"], col.get("why"), False
    elif not given and not isinstance(col, dict) and col is not None:
        step, why, legacy = col, None, True
    else:
        raise EstimateError(f'#{n} の {name} は {{"step", "why"}} で書く: {col!r}')
    if not _step_ok(c, step, cfg):
        allowed = f"尺度 {list(cfg.scale)}" + (f" か {OVER_SCALE!r}" if c == "size" else "")
        raise EstimateError(f"#{n} の {name} が{allowed}に無い: {step!r}")
    if why is not None and not isinstance(why, str):
        raise EstimateError(f"#{n} の {name} の根拠（why）は文字列で書く: {why!r}")
    why = why.strip() if why else None
    if given and not why:
        raise EstimateError(f"#{n} の {name} の根拠（why）が空")
    return step, why, legacy


def parse_estimate(raw: Any, cfg: Config, *, given: bool) -> Estimate:
    """見積の 1 件を確かめて読む。形・段階・根拠・区分の帯が合わなければ `EstimateError`（理由の文）。

    `given`（この回の入力）なら 4 列のすべてに根拠を要る。記録から戻すとき（`given` でない）は根拠の無い列と
    旧い形（段階だけの値）も受け、旧い形の見積は `restored` にする。区分を持つ見積は、どちらでも帯を確かめる。
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
        raise EstimateError("見積は number（整数）を持つオブジェクトで書く")
    n = raw["number"]
    steps: dict[str, Any] = {}
    why: dict[str, str] = {}
    legacy = False
    for c in COLUMNS:
        steps[c], w, old = _column(n, c, raw.get(c), cfg, given)
        legacy = legacy or old
        if w:
            why[c] = w
    ubv_col = raw.get("ubv")
    impact = ubv_col.get("impact") if isinstance(ubv_col, dict) else None
    if impact is not None and not isinstance(impact, str):
        raise EstimateError(f"#{n} の UBV の区分（impact）は文字列で書く: {impact!r}")
    mitigation = raw["mitigation"] if raw.get("mitigation") is not None else NO_MITIGATION
    if mitigation not in MITIGATIONS:
        raise EstimateError(f"#{n} の退避策の状態（mitigation）は {' / '.join(MITIGATIONS)} のどれか: {mitigation!r}")
    if impact is not None and cfg.harm_guide:
        err = check_band(steps["ubv"], impact, mitigation, cfg)
        if err:
            raise EstimateError(f"#{n} の {err}")
    digest = raw["digest"] if isinstance(raw.get("digest"), str) else None
    return Estimate(n, steps["ubv"], steps["tc"], steps["rr_oe"], steps["size"], digest, _flag(raw, "waiting_decision"),
                    _flag(raw, "observed_harm"), why, impact, mitigation, legacy)  # fmt: skip


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
