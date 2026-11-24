"""hook の予算の読み手と、上限の順序の検査（#3345 の決定 90・IH3）。

予算は上限の設定の方針 `hook.budget.<イベント>`（ランタイムで違うイベントは `hook.budget.<イベント>.<ランタイム>` が先）にある。
`limits` を渡さない呼び出し（hook の起動のたび）は、使う予算の 1 方針のファイルだけを読む。
検査はキーをまたぐ順序を見る（JSON Schema では書けない）:

- guard の部品の上限（guard の表の `limits` の秒）≤ 締め切り − 余白
- 締め切り ≤ 登録の timeout ≤ ランタイムの timeout の上限
- timeout の無い登録が 0 件
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.domains.hooks import tables

# Budget の値のキー（hook.budget.* の方針が持つもの）
BUDGET_KEYS = ("p95_ms", "deadline", "margin", "timeout", "inject_chars")


@dataclass(frozen=True)
class Budget:
    """事象ごとの予算。秒の値は秒、`p95_ms` はミリ秒。"""

    policy: str
    p95_ms: float
    deadline: float
    margin: float
    timeout: float
    inject_chars: int

    @property
    def usable(self) -> float:
        """部品の上限に使える秒（締め切り − 余白）。"""
        return self.deadline - self.margin


def _policy_names(spec: dict[str, Any], runtime: str) -> tuple[str, str]:
    """イベントのコントラクトの予算の方針の名前（ランタイム固有・共通）。"""
    return f"hook.budget.{spec['budget']}.{runtime}", f"hook.budget.{spec['budget']}"


def policy_name(runtime: str, event: str, limits: Optional[dict] = None) -> Optional[str]:
    """事象の予算の方針の名前。コントラクト表に無い事象は None。"""
    spec = tables.event_spec(runtime, event)
    if spec is None:
        return None
    specific, common = _policy_names(spec, runtime)
    found = tables.policy(specific) is not None if limits is None else specific in limits
    return specific if found else common


def budget_for(runtime: str, event: str, limits: Optional[dict] = None) -> Optional[Budget]:
    """事象の予算。コントラクト表に無い事象と、予算の行が欠けた事象は None。"""
    name = policy_name(runtime, event, limits)
    if name is None:
        return None
    row = tables.policy(name) if limits is None else limits.get(name)
    if row is None or any(k not in row for k in BUDGET_KEYS):
        return None
    return Budget(name, *(row[k] for k in BUDGET_KEYS))


def missing_budget(runtime: str, event: str, limits: Optional[dict] = None) -> str:
    """予算の無いイベントの文。直す先のファイルを示す。"""
    name = policy_name(runtime, event, limits)
    return f"{runtime} の {event} の予算が {tables.policy_path(name) if name else 'コントラクト表'} に無い"


def budget_registrations(runtime: Optional[str] = None) -> list[dict[str, Any]]:
    """予算から作る登録（事象ごとに 1 行・timeout は予算の値）。登録のファイルが無いときに検査へ渡す。

    `runtime` を渡せばそのランタイムの行だけを作る（省略時は全部）。
    """
    regs = []
    for name, rt in tables.runtimes().items():
        if runtime is not None and name != runtime:
            continue
        for event in rt["events"]:
            found = budget_for(name, event)
            regs.append({"runtime": name, "event": event, "timeout": found.timeout if found else None})
    return regs


def registrations_of(plugin_root: Path) -> list[dict[str, Any]]:
    """`hooks/<ランタイム>.json` の登録を 1 行ずつ。ファイルが無いランタイムは予算から作る。"""
    regs: list[dict[str, Any]] = []
    for runtime in tables.runtimes():
        path = plugin_root / "hooks" / f"{runtime}.json"
        regs += _file_registrations(runtime, path) if path.is_file() else budget_registrations(runtime)
    return regs


def _file_registrations(runtime: str, path: Path) -> list[dict[str, Any]]:
    """登録のファイル 1 つを、登録 1 つにつき 1 行へ開く。"""
    hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks")
    groups_of = hooks if isinstance(hooks, dict) else {}
    return [
        {"runtime": runtime, "event": event, "timeout": h.get("timeout")}
        for event, groups in groups_of.items()
        for g in groups
        for h in g["hooks"]
    ]


def checked_policies() -> dict[str, dict[str, Any]]:
    """`check_budget` が確かめる方針（名前 → 値）。ランタイムのイベントの予算と、guard の部品の上限のファイルだけを読む。"""
    budgets = {n for name, rt in tables.runtimes().items() for spec in rt["events"].values() for n in _policy_names(spec, name)}
    return tables.policies(budgets | {n for guard in tables.guards() for n in guard["limits"]})


def check_budget(limits: dict, registrations: list[dict[str, Any]]) -> list[str]:
    """予算の順序の破れの一覧（空なら守れている）。"""
    errors = _guard_limit_errors(limits)
    for reg in registrations:
        errors += _registration_errors(limits, reg)
    return errors


def _guard_limit_errors(limits: dict) -> list[str]:
    pairs = [(g, rt, ev) for g in tables.guards() for rt in tables.runtimes() for ev in g["events"]]
    errors = [e for guard, runtime, event in pairs for e in _pair_errors(guard, runtime, event, limits)]
    return sorted({e for e in errors if e})


def _pair_errors(guard: dict[str, Any], runtime: str, event: str, limits: dict) -> list[str]:
    """1 組（guard・ランタイム・事象）の破れの文。予算の行が無ければその文、あれば部品ごとの文（守れていれば空）。"""
    found = budget_for(runtime, event, limits)
    if found is None:
        return [missing_budget(runtime, event, limits)] if tables.event_spec(runtime, event) is not None else []
    return [_component_error(guard["name"], name, limits, found, runtime, event) for name in guard["limits"]]


def _component_error(guard: str, name: str, limits: dict, found: Budget, runtime: str, event: str) -> str:
    """部品の上限が締め切り − 余白を超えれば破れの文。守れていれば空。

    部品の行が `runtimes` を持てば、そのランタイムの予算とだけ比べる（ほかのランタイムでは部品を使わない。
    `hook.stop-wait.judge` は Claude Code だけでモデルを呼ぶ）。
    """
    if name not in limits:
        return f"{guard} の部品の上限 {tables.policy_path(name)} が無い"
    runtimes = limits[name].get("runtimes")
    if runtimes is not None and runtime not in runtimes:
        return ""
    where = f"{runtime} の {event}"
    seconds = limits[name].get("seconds")
    if seconds is not None and seconds > found.usable:
        return f"{guard} の部品の上限 {name}（{seconds} 秒）が {where} の締め切り − 余白（{found.usable} 秒）を超える"
    return ""


def _registration_errors(limits: dict, reg: dict[str, Any]) -> list[str]:
    runtime, event, timeout = reg["runtime"], reg["event"], reg.get("timeout")
    where = f"{runtime} の {event} の登録"
    if timeout is None:
        return [f"{where}に timeout が無い"]
    found = budget_for(runtime, event, limits)
    if found is None:
        return [f"{where}はコントラクト表か予算に無い事象を指す"]
    errors = []
    if found.deadline > timeout:
        errors.append(f"{where}の timeout {timeout} 秒が締め切り {found.deadline} 秒より短い")
    # found があれば事象のコントラクトもある（budget_for はコントラクトの無い事象に None を返す）
    cap = tables.event_spec(runtime, event).get("timeout_max", tables.runtimes()[runtime]["timeout_max"])  # noqa: NDF502 ファイル: 事象のコントラクトに timeout_max が無ければランタイムの上限
    if timeout > cap:
        errors.append(f"{where}の timeout {timeout} 秒がランタイムの上限 {cap} 秒を超える")
    return errors
