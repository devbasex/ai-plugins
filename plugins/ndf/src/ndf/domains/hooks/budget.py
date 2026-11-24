"""hook の予算の読み手と、上限の順序の検査（#3345 の決定 90・IH3）。

予算は上限の設定の方針 `hook.budget.<予算のキー>` にある。キーはランタイムのコントラクト表のイベントが持ち、ランタイムで予算が違う
イベントは別のキー（`stop` と `stop.codex`）を指す（方針のファイルの有無を試して選ばない）。
使う方針のファイルだけを読む（hook の起動のたびに全件を読まない）。
検査はキーをまたぐ順序を見る（JSON Schema では書けない）:

- guard の部品の上限（guard の表の `limits` の秒）≤ 締め切り − 余白
- 締め切り ≤ 登録の timeout ≤ ランタイムの timeout の上限
- timeout の無い登録が 0 件
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf.domains.hooks import tables
from ndf.domains.relay.keep_cache import MAX_CHECKS, TTL_SECONDS

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


def _budget_policy(runtime: str, event: str) -> Optional[tuple[str, Optional[dict[str, Any]]]]:
    """イベントの予算の方針の名前と中身。コントラクト表に無いイベントは None。"""
    spec = tables.event_spec(runtime, event)
    if spec is None:
        return None
    name = f"hook.budget.{spec['budget']}"
    return name, tables.policy(name)


def budget_for(runtime: str, event: str) -> Optional[Budget]:
    """事象の予算。コントラクト表に無い事象と、予算の行が欠けた事象は None。"""
    found = _budget_policy(runtime, event)
    if found is None:
        return None
    name, row = found
    if row is None or any(k not in row for k in BUDGET_KEYS):
        return None
    return Budget(name, *(row[k] for k in BUDGET_KEYS))


def rewake_budget(runtime: str, event: str) -> Optional[Budget]:
    """事象のバックグラウンドの登録（起床の hook。`async_rewake`）の予算。`async_rewake` の無い事象・予算の欠けは None。"""
    spec = tables.event_spec(runtime, event)
    if spec is None or "async_rewake" not in spec:
        return None
    row = tables.policy(f"hook.budget.{spec['async_rewake']['budget']}")
    if row is None or any(k not in row for k in BUDGET_KEYS):
        return None
    return Budget(f"hook.budget.{spec['async_rewake']['budget']}", *(row[k] for k in BUDGET_KEYS))


def rewake_reach(found: Budget) -> Optional[str]:
    """`cache.wake` の起床の時点と確かめる間隔が、バックグラウンドの登録の timeout の内に届かなければ理由の文。届けば None。"""
    margin = tables.policy_value("cache.wake", "seconds")
    interval = tables.policy_value("cache.wake", "interval")
    if interval <= 0 or math.ceil((TTL_SECONDS["1h"] - margin) / interval) > MAX_CHECKS:
        return f"cache.wake の間隔 {interval} 秒（余白 {margin} 秒）では、1 回の眠りで世代を確かめる回数が {MAX_CHECKS} を越える"
    if not 0 <= margin < TTL_SECONDS["1h"]:
        return f"cache.wake の余白 {margin} 秒が 0 以上 {TTL_SECONDS['1h']} 秒未満でない"
    need = TTL_SECONDS["1h"] - margin + interval + found.margin
    if need > found.timeout:
        return f"起床の時点と確かめる間隔と余りの {need} 秒が、バックグラウンドの登録の timeout {found.timeout} 秒を {need - found.timeout} 秒越える"
    return None


def rewake_registrations(runtime: Optional[str] = None) -> list[dict[str, Any]]:
    """`async_rewake` を持つ事象ごとのバックグラウンドの登録の行（timeout は `rewake_budget` の値）。"""
    regs = []
    for name, rt in tables.runtimes().items():
        if runtime is not None and name != runtime:
            continue
        for event in rt["events"]:
            if "async_rewake" in rt["events"][event]:
                found = rewake_budget(name, event)
                regs.append({"runtime": name, "event": event, "timeout": found.timeout if found else None, "async_rewake": True})
    return regs


def missing_budget(runtime: str, event: str) -> str:
    """予算の無いイベントの文。直す先のファイルを示す。"""
    found = _budget_policy(runtime, event)
    return f"{runtime} の {event} の予算が {tables.policy_path(found[0]) if found else 'コントラクト表'} に無い"


def budget_registrations(runtime: Optional[str] = None) -> list[dict[str, Any]]:
    """予算から作る登録（事象ごとに 1 行・timeout は予算の値と、バックグラウンドの登録の行）。登録のファイルが無いときに検査へ渡す。

    `runtime` を渡せばそのランタイムの行だけを作る（省略時は全部）。
    """
    regs = []
    for name, rt in tables.runtimes().items():
        if runtime is not None and name != runtime:
            continue
        for event in rt["events"]:
            found = budget_for(name, event)
            regs.append({"runtime": name, "event": event, "timeout": found.timeout if found else None})
        regs += rewake_registrations(name)
    return regs


def registrations_of(plugin_root: Path) -> list[dict[str, Any]]:
    """`hooks/<ランタイム>.json` の登録を 1 行ずつ。ファイルが無いランタイムは予算から作る。"""
    regs: list[dict[str, Any]] = []
    for runtime in tables.runtimes():
        path = plugin_root / "hooks" / f"{runtime}.json"
        regs += _file_registrations(runtime, path) + rewake_registrations(runtime) if path.is_file() else budget_registrations(runtime)
    return regs


def _file_registrations(runtime: str, path: Path) -> list[dict[str, Any]]:
    """登録のファイル 1 つを、登録 1 つにつき 1 行へ開く。"""
    hooks = json.loads(path.read_text(encoding="utf-8")).get("hooks")
    groups_of = hooks if isinstance(hooks, dict) else {}
    return [
        {"runtime": runtime, "event": event, "timeout": h.get("timeout"), "async_rewake": h.get("asyncRewake") is True, "file": True}
        for event, groups in groups_of.items()
        for g in groups
        for h in g["hooks"]
    ]


def check_budget(registrations: list[dict[str, Any]]) -> list[str]:
    """上限の設定の予算の順序の破れの一覧（空なら守れている）。"""
    errors = _guard_limit_errors()
    for reg in registrations:
        errors += _registration_errors(reg)
    return errors


def _guard_limit_errors() -> list[str]:
    pairs = [(g, rt, ev) for g in tables.guards() for rt in tables.runtimes() for ev in g["events"]]
    errors = [e for guard, runtime, event in pairs for e in _pair_errors(guard, runtime, event)]
    return sorted({e for e in errors if e})


def _pair_errors(guard: dict[str, Any], runtime: str, event: str) -> list[str]:
    """1 組（guard・ランタイム・事象）の破れの文。予算の行が無ければその文、あれば部品ごとの文（守れていれば空）。"""
    found = budget_for(runtime, event)
    if found is None:
        return [missing_budget(runtime, event)] if tables.event_spec(runtime, event) is not None else []
    return [_component_error(guard["name"], name, tables.policy(name), found, runtime, event) for name in guard["limits"]]


def _component_error(guard: str, name: str, row: Optional[dict[str, Any]], found: Budget, runtime: str, event: str) -> str:
    """部品の上限が締め切り − 余白を超えれば破れの文。守れていれば空。

    部品の行が `runtimes` を持てば、そのランタイムの予算とだけ比べる（ほかのランタイムでは部品を使わない。
    `hook.stop-wait.judge` は Claude Code だけでモデルを呼ぶ）。
    """
    if row is None:
        return f"{guard} の部品の上限 {tables.policy_path(name)} が無い"
    runtimes = row.get("runtimes")
    if runtimes is not None and runtime not in runtimes:
        return ""
    where = f"{runtime} の {event}"
    seconds = row.get("seconds")
    if seconds is not None and seconds > found.usable:
        return f"{guard} の部品の上限 {name}（{seconds} 秒）が {where} の締め切り − 余白（{found.usable} 秒）を超える"
    return ""


def _registration_errors(reg: dict[str, Any]) -> list[str]:
    runtime, event, timeout = reg["runtime"], reg["event"], reg.get("timeout")
    where = f"{runtime} の {event} の登録"
    if reg.get("async_rewake") is True:
        return _rewake_errors(reg, where)
    if timeout is None:
        return [f"{where}に timeout が無い"]
    found = budget_for(runtime, event)
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


def _rewake_errors(reg: dict[str, Any], where: str) -> list[str]:
    """バックグラウンドの登録の行の破れ。プラグインの登録のファイルに在れば事象によらず破れ（全ての会話で起動させない）。"""
    if reg.get("file") is True:
        return [f"{where}に asyncRewake の登録がある（起床の hook はラッパーが中継の子にだけ渡す）"]
    runtime, event, timeout = reg["runtime"], reg["event"], reg.get("timeout")
    found = rewake_budget(runtime, event)
    if found is None or timeout is None:
        return [f"{where}（バックグラウンド）の予算が無い"]
    errors = []
    if found.deadline > timeout:
        errors.append(f"{where}（バックグラウンド）の timeout {timeout} 秒が締め切り {found.deadline} 秒より短い")
    cap = tables.event_spec(runtime, event)["async_rewake"]["timeout_max"]
    if timeout > cap:
        errors.append(f"{where}（バックグラウンド）の timeout {timeout} 秒が上限 {cap} 秒を超える")
    reach = rewake_reach(found)
    return errors + ([f"{where}（バックグラウンド）: {reach}"] if reach else [])
