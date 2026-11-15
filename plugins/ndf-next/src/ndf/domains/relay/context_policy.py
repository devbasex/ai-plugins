"""再起動の方針の適用値の解決（#3569。旧い `lib/context_policy.py` の `resolve_policy` の移し先）。

適用値は 既定 < `.ndf/context.json` < 環境変数 < `--policy` の順に重ねる。方針は 2 つで、現行方式（`current`）は
運用上限 200,000 だけを持ち、試行方式（`trial`）は判断開始の値・運用上限・容量の余白を持つ。設定が壊れていれば
ファイル全体を読まずに現行方式の既定で返し、`config_error` に理由を持つ。LLM・ネットワーク・子プロセスを使わない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, NamedTuple, Optional

from ndf.domains.records import files, jsonfile

CURRENT, TRIAL = "current", "trial"
POLICIES = (CURRENT, TRIAL)
CURRENT_LIMIT = 200_000  # 現行方式の運用上限（NDF_CONTEXT_LIMIT で変える）
TRIAL_DECIDE_FROM = 200_000  # 試行方式の判断開始の値（初期値）
TRIAL_LIMIT = 400_000  # 試行方式の運用上限（初期値）
MARGIN = 50_000  # 容量の余白（初期値）
MIN_SAMPLES = 3  # 見込みに使う中央値の標本の下限
CONTINUE, DENY, RESTART = "継続", "拒否", "再起動"
# 再起動の理由（判定の記録の列の値。旧い context_policy.REASONS と同じ並び）
REASONS = ("容量", "運用上限", "費用", "独立性", "手動", "カットポイント", "待ち")
DEFAULT, CONFIG, ENV, CLI = "default", "config", "env", "cli"


class Resolved(NamedTuple):
    """そのセッションで効く方針と値。`sources` は値ごとの出所（default・config・env・cli）。"""

    policy: str
    decide_from: int
    limit: int
    margin: int
    sources: dict[str, str]
    config_error: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


def setting_id() -> str:
    """設定のファイルの、宣言のファイルの表（ndf-files）の id。"""
    return "context"


def is_positive_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _value(data: Mapping[str, Any], key: str, default: Any) -> Any:
    return data[key] if key in data else default


def _trial_part(data: Mapping[str, Any], where: str) -> tuple[dict[str, Any], Optional[str]]:
    trial = _value(data, "trial", {})
    if not isinstance(trial, dict):
        return {}, f"{where}: trial がオブジェクトでない"
    decide_from, limit = _value(trial, "decide_from", TRIAL_DECIDE_FROM), _value(trial, "limit", TRIAL_LIMIT)
    if not (is_positive_int(decide_from) and is_positive_int(limit)):
        return {}, f"{where}: trial.decide_from / trial.limit が正の整数でない"
    if decide_from > limit:
        return {}, f"{where}: trial.decide_from（{decide_from}）が trial.limit（{limit}）より大きい"
    return {"decide_from": decide_from, "limit": limit}, None


def parse_setting(data: Mapping[str, Any], where: str) -> tuple[dict[str, Any], Optional[str]]:
    """設定の中身を検める。壊れていれば ({}, 理由)。ファイル全体を壊れたものとして扱う。"""
    if not data:
        return {}, None
    if _value(data, "version", 1) != 1:
        return {}, f"{where}: version が 1 でない"
    policy = _value(data, "policy", CURRENT)
    if policy not in POLICIES:
        return {}, f"{where}: policy が current / trial でない（{policy!r}）"
    trial, why = _trial_part(data, where)
    if why:
        return {}, why
    margin = _value(data, "margin", MARGIN)
    if not (isinstance(margin, int) and not isinstance(margin, bool) and margin >= 0):
        return {}, f"{where}: margin が 0 以上の整数でない"
    return {"policy": policy, "margin": margin, **trial}, None


def read_setting(root: Optional[Path]) -> tuple[dict[str, Any], Optional[str]]:
    """`.ndf/context.json` を読み、検めた値と壊れた理由を返す。根が無い・ファイルが無ければ空。"""
    if root is None:
        return {}, None
    path = files.path(root, setting_id())
    if not path.is_file():
        return {}, None
    try:
        data = jsonfile.read_object(path)
    except (jsonfile.JsonFileError, UnicodeDecodeError) as exc:
        return {}, f"{files.relpath(setting_id())}: 読めない（{exc}）"
    return parse_setting(data, files.relpath(setting_id()))


def _policy_name(setting: Mapping[str, Any], environ: Mapping[str, str], cli: Optional[str]) -> tuple[str, str, Optional[str]]:
    """方針名を 既定 < config < env < CLI で重ね、(方針名, 出所, 無視した環境変数の記述) を返す。"""
    policy, source = (setting["policy"], CONFIG) if setting else (CURRENT, DEFAULT)
    ignored: Optional[str] = None
    raw = environ.get("NDF_CONTEXT_POLICY")
    env_policy = raw.strip() if raw else ""
    if env_policy in POLICIES:
        policy, source = env_policy, ENV
    elif env_policy:
        ignored = f"NDF_CONTEXT_POLICY={env_policy}"
    if cli in POLICIES:
        policy, source = cli, CLI
    return policy, source, ignored


def _thresholds(policy: str, setting: Mapping[str, Any], environ: Mapping[str, str]) -> tuple[int, int, int, dict[str, str]]:
    """方針に応じた判断開始・運用上限・余白を重ね、(decide_from, limit, margin, 出所) を返す。"""
    sources = {"decide_from": DEFAULT, "limit": DEFAULT, "margin": DEFAULT}
    margin = _value(setting, "margin", MARGIN)
    if "margin" in setting and policy == TRIAL:
        sources["margin"] = CONFIG
    decide_from = limit = CURRENT_LIMIT
    if policy == TRIAL:
        decide_from, limit = _value(setting, "decide_from", TRIAL_DECIDE_FROM), _value(setting, "limit", TRIAL_LIMIT)
        if setting:
            sources["decide_from"] = sources["limit"] = CONFIG
    raw = environ.get("NDF_CONTEXT_LIMIT")
    if raw and raw.isdigit() and int(raw) > 0:
        limit, sources["limit"] = int(raw), ENV
    if policy == CURRENT or decide_from > limit:
        decide_from = limit
        sources["decide_from"] = sources["limit"]
    return decide_from, limit, margin, sources


def resolve(root: Optional[Path], policy: Optional[str], environ: Mapping[str, str]) -> Resolved:
    """適用値を解決する。`NDF_CONTEXT_LIMIT` は適用中の方針の運用上限を上書きし、判断開始の値がそれを上回れば
    同じ値へ下げる。`policy`（`--policy`）は None なら渡さなかったものとして扱う。"""
    setting, error = read_setting(root)
    name, name_source, ignored = _policy_name(setting, environ, policy)
    decide_from, limit, margin, sources = _thresholds(name, setting, environ)
    all_sources = {"policy": name_source, **sources}
    if ignored:
        all_sources["ignored"] = ignored
    return Resolved(name, decide_from, limit, margin, all_sources, error)
