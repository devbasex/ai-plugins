"""upkeep_rank_config.py: 順位付けの設定（バックログの宣言と引数の合成）と見積の検証（#1429 の I1・I8・I10）。

算出は `upkeep_rank.py` が持つ。ここは値の検証と、既定値（一般的な WSJF の値だけ）を持つ。
"""

from __future__ import annotations

from dataclasses import dataclass, field

COLUMNS = ("ubv", "tc", "rr_oe", "size")
COLUMN_NAMES = {"ubv": "UBV", "tc": "TC", "rr_oe": "RR/OE", "size": "Size"}
MITIGATIONS = ("防いでいる", "部分的", "無い")
FORWARD, BACKWARD = "前倒し", "後ろ倒し"
DIRECTIONS = (FORWARD, BACKWARD)
AUTO, APPROVAL, APPROVED = "自動", "承認", "承認済み"
SLOT_NONE, SLOT_SPLIT, SLOT_FIT = "なし", "分割が要る", "当てた"
HEADING = "### 順位"
RESTORED_WHY = "前回の表から戻した"

# 一般的な WSJF の値だけを持つ。リポジトリに固有の値（ラベルの名前・容量・課題番号）は持たない（I8）。
DEFAULT_SCALE = (1, 2, 3, 5, 8, 13, 20)
DEFAULTS = {
    "margin_steps": 1,
    "auto_threshold": 8,
    "dependents_steps": ((0, 1), (1, 5), (2, 8), (3, 13), (5, 20)),
    "size_exponent": 0.3,
    "large_slot_ratio": 0.5,
    "harm_guide": {"全体": (13, 20), "機能": (5, 8), "表示": (2, 3), "なし": (1, 1)},
}


class RankError(Exception):
    """入力・宣言の誤り。"""


class CycleError(RankError):
    """依存の循環。循環する課題の番号を持つ。"""

    def __init__(self, numbers):
        self.numbers = sorted(numbers)
        super().__init__("依存が循環している: " + " ".join(f"#{n}" for n in self.numbers))


# ---------------- 設定 ----------------


def _decl_int(v, key) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise RankError(f"{key} は整数で書く: {v!r}")
    return v


def _decl_number(v, key) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RankError(f"{key} は数で書く: {v!r}")
    return float(v)


@dataclass(frozen=True)
class Config:
    scale: tuple
    margin_steps: int
    auto_threshold: int
    capacity: int | None
    dependents_steps: tuple
    label_floor: dict
    size_exponent: float
    large_slot_ratio: float
    large_min_size: int
    harm_guide: dict
    anchors: dict = field(default_factory=dict)

    def threshold(self, cod: int) -> int:
        """前倒しの閾値。尺度のうち cod より大きい値を小さい順に数えて余白番目の値。尺度の最大の先は最後の差で延ばす（決定 10）。"""
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
        """被依存の数（解放する数）を対応表で段階へ換算する。"""
        got = self.scale[0]
        for lo, step in self.dependents_steps:
            if count >= lo:
                got = step
        return got

    def label_step(self, labels) -> int | None:
        """ラベルの下限のうち最大。対応が無ければ None（下限は効かない）。"""
        hits = [self.label_floor[lb] for lb in labels if lb in self.label_floor]
        return max(hits) if hits else None

    def lower_band(self, impact: str):
        """区分を 1 つ下げた帯（目安の区分を帯の下端の降順に並べた次）。最下位なら None。"""
        order = sorted(self.harm_guide, key=lambda k: (-self.harm_guide[k][0], k))
        i = order.index(impact)
        return self.harm_guide[order[i + 1]] if i + 1 < len(order) else None


def _parse_pairs(v, key):
    if isinstance(v, str):
        try:
            v = [tuple(int(x) for x in p.split(":")) for p in v.split(",") if p.strip()]
        except ValueError:
            raise RankError(f"{key} は 下限:段階 の並びで書く: {v!r}")
    if not isinstance(v, (list, tuple)) or not all(isinstance(p, (list, tuple)) and len(p) == 2 for p in v):
        raise RankError(f"{key} は [下限, 段階] の列で書く")
    return tuple((_decl_int(a, key), _decl_int(b, key)) for a, b in v)


def _parse_scale(v) -> tuple:
    if isinstance(v, str):
        try:
            v = [int(x) for x in v.split(",") if x.strip()]
        except ValueError:
            raise RankError(f"scale は整数の並びで書く: {v!r}")
    scale = tuple(_decl_int(x, "scale") for x in v)
    if len(scale) < 2 or scale[0] <= 0 or any(b <= a for a, b in zip(scale, scale[1:])):
        raise RankError("scale は 2 個以上の正の整数の狭義の昇順で書く")
    return scale


def _parse_label_floor(floor, in_scale) -> dict:
    if not isinstance(floor, dict):
        raise RankError("label_floor はラベル → 段階のオブジェクトで書く")
    return {str(k): in_scale(v, f"label_floor[{k}]") for k, v in floor.items()}


def _parse_harm_guide(guide, scale) -> dict:
    if not isinstance(guide, dict) or not guide:
        raise RankError("harm_guide は区分 → [下端, 上端] のオブジェクトで書く")
    bands = {}
    for k, band in guide.items():
        if not isinstance(band, (list, tuple)) or len(band) != 2:
            raise RankError(f"harm_guide[{k}] は [下端, 上端] で書く")
        lo, hi = _decl_int(band[0], "harm_guide"), _decl_int(band[1], "harm_guide")
        if lo > hi or not any(lo <= v <= hi for v in scale):
            raise RankError(f"harm_guide[{k}] の帯 {lo}〜{hi} に尺度の値が無い")
        bands[str(k)] = (lo, hi)
    return bands


def build_config(args: dict, decl: dict | None) -> Config:
    """引数 → 宣言 → 既定値の順に採り、検証する。args の値が None のキーは渡されていないとみなす。"""
    decl = decl or {}
    if not isinstance(decl, dict):
        raise RankError("宣言はオブジェクトで書く")

    def pick(key, default=None):
        if args.get(key) is not None:
            return args[key]
        if key in decl:
            return decl[key]
        return default

    scale = _parse_scale(pick("scale", DEFAULT_SCALE))

    def in_scale(v, key):
        v = _decl_int(v, key)
        if v not in scale:
            raise RankError(f"{key} は尺度の値で書く: {v}")
        return v

    margin = _decl_int(pick("margin_steps", DEFAULTS["margin_steps"]), "margin_steps")
    if margin < 0:
        raise RankError("margin_steps は 0 以上")
    auto = in_scale(pick("auto_threshold", DEFAULTS["auto_threshold"]), "auto_threshold")
    capacity = pick("capacity")
    if capacity is not None and _decl_int(capacity, "capacity") <= 0:
        raise RankError("capacity は正の整数")
    deps = _parse_pairs(pick("dependents_steps", DEFAULTS["dependents_steps"]), "dependents_steps")
    if any(b <= a for (a, _), (b, _) in zip(deps, deps[1:])):
        raise RankError("dependents_steps は被依存の数の下限の昇順で書く")
    for _, s in deps:
        in_scale(s, "dependents_steps の段階")
    floor = _parse_label_floor(pick("label_floor", {}) or {}, in_scale)
    alpha = _decl_number(pick("size_exponent", DEFAULTS["size_exponent"]), "size_exponent")
    if not 0 <= alpha <= 1:
        raise RankError("size_exponent は 0 以上 1 以下")
    ratio = _decl_number(pick("large_slot_ratio", DEFAULTS["large_slot_ratio"]), "large_slot_ratio")
    if not 0 <= ratio < 1:
        raise RankError("large_slot_ratio は 0 以上 1 未満")
    large = in_scale(pick("large_min_size", scale[-2]), "large_min_size")
    bands = _parse_harm_guide(pick("harm_guide", DEFAULTS["harm_guide"]), scale)
    anchors = pick("anchors", {}) or {}
    if not isinstance(anchors, dict):
        raise RankError("anchors は列 → 段階 → 課題の列のオブジェクトで書く")
    return Config(scale, margin, auto, capacity, deps, floor, alpha, ratio, large, bands, anchors)


# ---------------- 見積 ----------------


@dataclass(frozen=True)
class Estimate:
    number: int
    steps: dict
    why: dict
    impact: str | None = None
    mitigation: str = "無い"
    observed_harm: bool = False
    needs_detail: tuple = ()
    depends_on: tuple = ()
    waiting_decision: bool = False
    digest: str | None = None
    restored: bool = False

    def to_json(self) -> dict:
        d = {c: {"step": self.steps[c], "why": self.why[c]} for c in COLUMNS}
        if self.impact is not None:
            d["ubv"]["impact"] = self.impact
        d.update(
            number=self.number,
            mitigation=self.mitigation,
            observed_harm=self.observed_harm,
            needs_detail=list(self.needs_detail),
            depends_on=list(self.depends_on),
            waiting_decision=self.waiting_decision,
            digest=self.digest,
        )
        if self.restored:
            d["restored"] = True
        return d


def _read_columns(raw: dict, cfg: Config):
    """列ごとの段階と根拠を読む。(steps, why, None) か (None, None, 理由) を返す。"""
    steps, why = {}, {}
    for c in COLUMNS:
        col = raw.get(c)
        if not isinstance(col, dict) or "step" not in col:
            return None, None, f"{COLUMN_NAMES[c]} の段階が無い"
        if col["step"] not in cfg.scale or isinstance(col["step"], bool):
            return None, None, f"{COLUMN_NAMES[c]} の段階 {col['step']!r} は尺度に無い"
        if not isinstance(col.get("why"), str) or not col["why"].strip():
            return None, None, f"{COLUMN_NAMES[c]} の根拠の 1 行が空"
        steps[c], why[c] = col["step"], col["why"].strip()
    return steps, why, None


def _check_ubv_band(ubv, impact, mitigation, cfg: Config) -> str | None:
    """UBV が区分の帯に入るかを見る。外れていれば理由を返す。"""
    if impact not in cfg.harm_guide:
        return f"UBV の区分 {impact!r} は実害の目安に無い（{' / '.join(cfg.harm_guide)}）"
    lo, hi = cfg.harm_guide[impact]
    lower = cfg.lower_band(impact) if mitigation == "防いでいる" else None
    if not (lo <= ubv <= hi or (lower and lower[0] <= ubv <= lower[1] and ubv < lo)):
        extra = "（退避策が防いでいるときだけ 1 つ下の区分の帯も受け付ける）" if ubv < lo else ""
        return f"UBV {ubv} は区分「{impact}」の帯 {lo}〜{hi} の外{extra}"
    return None


def validate_estimate(raw: dict, cfg: Config, check_band: bool = True):
    """見積の入力を検証する。(Estimate, None) か (None, 理由) を返す（I1・I10）。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
        return None, "number が無い"
    steps, why, err = _read_columns(raw, cfg)
    if err:
        return None, err
    mitigation = raw.get("mitigation", "無い")
    if mitigation not in MITIGATIONS:
        return None, f"退避策の状態 {mitigation!r} は {' / '.join(MITIGATIONS)} のどれでもない"
    impact = raw["ubv"].get("impact")
    if check_band:
        err = _check_ubv_band(steps["ubv"], impact, mitigation, cfg)
        if err:
            return None, err
    for key in ("observed_harm", "waiting_decision"):
        if key in raw and not isinstance(raw[key], bool):
            return None, f"{key} は true / false で書く"
    deps = raw.get("depends_on") or []
    if not isinstance(deps, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in deps):
        return None, "depends_on は番号の列で書く"
    detail = raw.get("needs_detail") or []
    if not isinstance(detail, list) or any(x not in COLUMNS for x in detail):
        return None, f"needs_detail は {' / '.join(COLUMNS)} の列で書く"
    return (
        Estimate(
            raw["number"],
            steps,
            why,
            impact,
            mitigation,
            bool(raw.get("observed_harm", False)),
            tuple(detail),
            tuple(sorted(set(deps))),
            bool(raw.get("waiting_decision", False)),
            raw.get("digest"),
            bool(raw.get("restored", False)),
        ),
        None,
    )


def restore_from_row(row: dict, cfg: Config) -> dict:
    """前回の表の 1 行から見積の入力を戻す（決定 12）。印の付いた段階は LLM の値が分からないため尺度の最小にする。"""
    low = cfg.scale[0]
    return {
        "number": row["number"],
        "ubv": {"step": low if row.get("ubv_label") else row["ubv"], "why": RESTORED_WHY},
        "tc": {"step": row["tc"], "why": RESTORED_WHY},
        "rr_oe": {"step": low if row.get("rr_oe_dependents") else row["rr_oe"], "why": RESTORED_WHY},
        "size": {"step": row["size"], "why": RESTORED_WHY},
        "observed_harm": False,
        "waiting_decision": bool(row.get("waiting_decision")),
        "restored": True,
    }
