"""再起動の方針の適用値の解決と、工程境界の判定（#2262）。

- `resolve(root, env, cli)`: 既定 < `.ndf/context.json` < 環境変数 < CLI 引数 の順に重ねた `AppliedPolicy`
- `decide(applied, context, window, estimate)`: 帯と判定を返す純粋関数（LLM・ネットワーク・子プロセスを使わない）
- `cost(estimates, context, model, stage)`: 判断帯で続ける費用と再起動の費用を、較正した中央値で見込む

方針は 2 つ。現行方式（`current`）は運用上限 200,000 を超えたら次の工程を止めるだけで、容量と費用を見ない。
試行方式（`trial`）は 判断開始の値以下で継続・運用上限以下で費用の見込み・超えたら拒否で、窓が取れれば
窓 − 容量の余白 を運用上限より優先する。200,000 / 400,000 / 50,000 は比較実験の初期値で、品質が落ちる境目の
実測ではない。規約は skills/development-workflow/references/context-window.md にある。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Mapping

from token_rates import WEIGHTS, read_rate

CURRENT, TRIAL = "current", "trial"
POLICIES = (CURRENT, TRIAL)
SETTING = "context.json"
CURRENT_LIMIT = 200_000  # 現行方式の運用上限（NDF_CONTEXT_LIMIT で変える）
TRIAL_DECIDE_FROM = 200_000  # 試行方式の判断開始の値（初期値）
TRIAL_LIMIT = 400_000  # 試行方式の運用上限（初期値）
MARGIN = 50_000  # 容量の余白（初期値。窓の大きさに関わらず同じ量。決定 3）
MIN_SAMPLES = 3  # 見込みに使う中央値の標本の下限（決定 5）
HANDOFF_OUTPUT_WEIGHT = WEIGHTS["out"]
REWRITE_WEIGHT = WEIGHTS["w1h"]

# 帯・判定・理由・継続の根拠の語（判定の記録の列の値）
BAND_KEEP, BAND_JUDGE, BAND_OVER, BAND_CAPACITY = "継続帯", "判断帯", "上限超", "容量超"
CONTINUE, DENY, RESTART = "継続", "拒否", "再起動"
REASON_CAPACITY, REASON_LIMIT, REASON_COST = "容量", "運用上限", "費用"
REASON_INDEPENDENCE, REASON_MANUAL, REASON_CUTPOINT = "独立性", "手動", "カットポイント"
REASONS = (REASON_CAPACITY, REASON_LIMIT, REASON_COST, REASON_INDEPENDENCE, REASON_MANUAL, REASON_CUTPOINT)
BASIS_BELOW, BASIS_COST, BASIS_NO_ESTIMATE, BASIS_NO_VALUE = "判断開始の値以下", "費用", "見込みなし", "値なし"


@dataclass(frozen=True)
class AppliedPolicy:
    """そのセッションで効く方針と値。`sources` は値ごとの出所（default / config / env / cli）。"""

    policy: str = CURRENT
    decide_from: int = CURRENT_LIMIT
    limit: int = CURRENT_LIMIT
    margin: int = MARGIN
    sources: dict = field(default_factory=dict)
    config_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping) -> "AppliedPolicy | None":
        """記録や受け渡しのファイルから戻す。形が違えば None。"""
        try:
            policy = d["policy"]
            ints = [d["decide_from"], d["limit"], d.get("margin", MARGIN)]
        except (KeyError, TypeError):
            return None
        if policy not in POLICIES or not all(isinstance(v, int) and not isinstance(v, bool) for v in ints):
            return None
        src = d.get("sources")
        err = d.get("config_error")
        return cls(policy, ints[0], ints[1], ints[2], dict(src) if isinstance(src, dict) else {}, err if isinstance(err, str) else None)


@dataclass(frozen=True)
class Estimate:
    """判断帯の次の工程までの費用の見込み（入力の単価を 1 とした換算）。"""

    continue_cost: float
    restart_cost: float

    def to_dict(self) -> dict:
        return {"continue": round(self.continue_cost), "restart": round(self.restart_cost)}


@dataclass(frozen=True)
class Verdict:
    band: str | None
    verdict: str
    reason: str | None
    basis: str | None
    missing: tuple = ()
    effective_limit: int | None = None
    estimate: Estimate | None = None


@dataclass
class Estimates:
    """`calibrate` が較正した中央値（見込みのファイルの中身）。標本が足りない値は None。"""

    restore_context: float | None = None
    restore_calls: float | None = None
    handoff_calls: float | None = None
    handoff_output: float | None = None
    stage_calls: dict = field(default_factory=dict)
    stage_expiries: dict = field(default_factory=dict)
    samples: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d) -> "Estimates":
        if not isinstance(d, dict):
            return cls()

        def num(v):
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

        def table(v):
            return {str(k): float(x) for k, x in v.items() if num(x) is not None} if isinstance(v, dict) else {}

        samples = d.get("samples")
        return cls(
            num(d.get("restore_context")),
            num(d.get("restore_calls")),
            num(d.get("handoff_calls")),
            num(d.get("handoff_output")),
            table(d.get("stage_calls")),
            table(d.get("stage_expiries")),
            {str(k): int(v) for k, v in samples.items() if isinstance(v, int)} if isinstance(samples, dict) else {},
        )

    def to_dict(self) -> dict:
        return asdict(self)


# --- 適用値の解決 ------------------------------------------------------------------------------------


def _positive_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def parse_context_setting(data: dict) -> tuple[dict, str | None]:
    """`.ndf/context.json` の中身を検める。壊れていれば ({}, 理由)。ファイル全体を壊れたものとして扱う（I4）。"""
    if not data:
        return {}, None
    if data.get("version", 1) != 1:
        return {}, f".ndf/{SETTING}: version が 1 でない"
    policy = data.get("policy", CURRENT)
    if policy not in POLICIES:
        return {}, f".ndf/{SETTING}: policy が current / trial でない（{policy!r}）"
    trial = data.get("trial", {})
    if not isinstance(trial, dict):
        return {}, f".ndf/{SETTING}: trial がオブジェクトでない"
    decide_from, limit = trial.get("decide_from", TRIAL_DECIDE_FROM), trial.get("limit", TRIAL_LIMIT)
    if not (_positive_int(decide_from) and _positive_int(limit)):
        return {}, f".ndf/{SETTING}: trial.decide_from / trial.limit が正の整数でない"
    if decide_from > limit:
        return {}, f".ndf/{SETTING}: trial.decide_from（{decide_from}）が trial.limit（{limit}）より大きい"
    margin = data.get("margin", MARGIN)
    if not (isinstance(margin, int) and not isinstance(margin, bool) and margin >= 0):
        return {}, f".ndf/{SETTING}: margin が 0 以上の整数でない"
    return {"policy": policy, "decide_from": decide_from, "limit": limit, "margin": margin}, None


def read_context_setting(root) -> tuple[dict, str | None]:
    """`.ndf/context.json`（置き場は worktree）を読み、検めた値と壊れた理由を返す。root が無ければ空。"""
    if not root:
        return {}, None
    import decl  # git を呼ぶ解決は設定を読むときだけ載せる

    try:
        data, why = decl.read_json_setting(root, SETTING)
    except Exception as e:  # noqa: BLE001 — 設定が読めなくても判定を落とさない
        return {}, f".ndf/{SETTING}: 読めない（{e}）"
    return (({}, why) if why else parse_context_setting(data)) if data or why else ({}, None)


def resolve_policy(root=None, env: Mapping | None = None, cli: Mapping | None = None) -> AppliedPolicy:
    """適用値を解決する（I2・I3）。cli は {"policy": ..., "limit": ...}（値が None のキーは無視）。

    `NDF_CONTEXT_LIMIT` は適用中の方針の運用上限を上書きし、判断開始の値がそれを上回れば同じ値へ下げる（決定 2）。
    モデル名と窓の大きさは方針を選ばない（I15）。"""
    env = os.environ if env is None else env
    cli = cli or {}
    setting, error = read_context_setting(root)
    sources = {"policy": "default", "decide_from": "default", "limit": "default", "margin": "default"}
    policy = CURRENT
    if setting:
        policy = setting["policy"]
        sources["policy"] = "config"
    env_policy = (env.get("NDF_CONTEXT_POLICY") or "").strip()
    if env_policy in POLICIES:
        policy, sources["policy"] = env_policy, "env"
    elif env_policy:
        sources["ignored"] = f"NDF_CONTEXT_POLICY={env_policy}"
    if cli.get("policy") in POLICIES:
        policy, sources["policy"] = cli["policy"], "cli"
    margin = setting.get("margin", MARGIN)
    if "margin" in setting and policy == TRIAL:
        sources["margin"] = "config"
    if policy == TRIAL:
        decide_from, limit = setting.get("decide_from", TRIAL_DECIDE_FROM), setting.get("limit", TRIAL_LIMIT)
        if setting:
            sources["decide_from"] = sources["limit"] = "config"
    else:
        decide_from = limit = CURRENT_LIMIT
    env_limit = env.get("NDF_CONTEXT_LIMIT") or ""
    if env_limit.isdigit() and int(env_limit) > 0:
        limit, sources["limit"] = int(env_limit), "env"
    if _positive_int(cli.get("limit")):
        limit, sources["limit"] = cli["limit"], "cli"
    if policy == CURRENT or decide_from > limit:
        decide_from = limit
        sources["decide_from"] = sources["limit"]
    return AppliedPolicy(policy, decide_from, limit, margin, sources, error)


# --- 判定 --------------------------------------------------------------------------------------------


def decide_boundary(applied: AppliedPolicy, context: int | None, window: int | None = None, estimate: Estimate | None = None) -> Verdict:
    """帯と判定（I5〜I7）。現行方式は運用上限だけで決め、窓と見込みを使わない（決定 4）。"""
    if context is None:
        return Verdict(None, CONTINUE, None, BASIS_NO_VALUE, ("context",), applied.limit)
    if applied.policy == CURRENT:
        if context > applied.limit:
            return Verdict(BAND_OVER, DENY, REASON_LIMIT, None, (), applied.limit)
        return Verdict(BAND_KEEP, CONTINUE, None, BASIS_BELOW, (), applied.limit)
    missing: list[str] = []
    effective = applied.limit
    if window is None:
        missing.append("window")
    elif window - applied.margin < applied.limit:
        effective = window - applied.margin
        if context > effective:
            return Verdict(BAND_CAPACITY, DENY, REASON_CAPACITY, None, tuple(missing), effective)
    if context > applied.limit:
        return Verdict(BAND_OVER, DENY, REASON_LIMIT, None, tuple(missing), effective)
    if context <= applied.decide_from:
        return Verdict(BAND_KEEP, CONTINUE, None, BASIS_BELOW, tuple(missing), effective)
    if estimate is None:
        missing.append("estimate")
        return Verdict(BAND_JUDGE, CONTINUE, None, BASIS_NO_ESTIMATE, tuple(missing), effective)
    if estimate.restart_cost < estimate.continue_cost:
        return Verdict(BAND_JUDGE, DENY, REASON_COST, None, tuple(missing), effective, estimate)
    return Verdict(BAND_JUDGE, CONTINUE, None, BASIS_COST, tuple(missing), effective, estimate)


def _stage_value(table: dict, samples: dict, stage: str | None) -> float | None:
    """工程ごとの中央値。その工程の標本が足りなければ全工程（`*`）の値、それも足りなければ None。"""
    for key in (stage, "*"):
        if key and key in table and samples.get(f"stage:{key}", 0) >= MIN_SAMPLES:
            return table[key]
    return None


def estimate_cost(estimates: Estimates | None, context: int, model: str | None, stage: str | None) -> Estimate | None:
    """次の工程までの費用（決定 5）。標本が 3 未満の値があれば None。

    続ける: k·r·C + m·w·C
    再起動: h·r·C + 5·H + w·Q + q·r·Q + k·r·Q + m·w·Q"""
    if estimates is None:
        return None
    s = estimates.samples
    k = _stage_value(estimates.stage_calls, s, stage)
    m = _stage_value(estimates.stage_expiries, s, stage)
    q_ok = s.get("restore", 0) >= MIN_SAMPLES and s.get("handoff", 0) >= MIN_SAMPLES
    values = (estimates.restore_context, estimates.restore_calls, estimates.handoff_calls, estimates.handoff_output)
    if k is None or m is None or not q_ok or any(v is None for v in values):
        return None
    big_q, q, h, big_h = values
    r, w, c = read_rate(model), REWRITE_WEIGHT, float(context)
    keep = k * r * c + m * w * c
    restart = h * r * c + HANDOFF_OUTPUT_WEIGHT * big_h + w * big_q + q * r * big_q + k * r * big_q + m * w * big_q
    return Estimate(keep, restart)
