"""効果指標の記録と出現率の更新（#2944 の詳細設計「効果指標と出現率」）。

- `write_outcome`: 割り付け ID から割り付けの行を引いて効果指標の行を足し（同じ割り付け ID の行があれば足さない。確認と追記は
  実験の記録のロックの中）、ロックを抜けてから `refresh` を呼ぶ
- `unit_values`: 実験の記録と使用量ログから、計算に使う単位の値を割り付け ID ごとに作る（純粋な関数）
- `refresh`: 計算し直す条件に当たれば、出現率の項目を置き換える。計算はロックの外で、書き換えは `model_choice.update_weights`

読み書きの部品（実験の記録・出現率のファイル・保存先）は adapters の `model_choice` が持ち、計算は `allocation_decide` が持つ。
使用量ログは実験の記録と同じ保存先にある（表 ndf-files の usage-log-file と launch-experiments の location が同じ）。
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.adapters import launch_approval, launch_records, launch_settings
from ndf.adapters import model_choice as mc
from ndf.contracts import facts, internal
from ndf.domains.records import allocation_decide as ad
from ndf.domains.records import jsonl
from ndf.domains.records import usage_decide as ud

# 表 ndf-files の使用量ログの行
USAGE_LOG = "usage-log-file"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnitValue:
    """計算に使う単位 1 つ: 割り付け ID・選んだ組の添字・割り付けた時の確率・割り付けた日時・効果（0〜1）・単位の費用（USD。
    不明か 0 なら None）・実際のモデル（分からなければ None）。"""

    assign_id: str
    arm: int
    probs: tuple[float, ...]
    at: datetime
    quality: float
    cost: Optional[float]
    model: Optional[str]


def _time(row: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(row["at"])


def _text(at: datetime) -> str:
    return internal.stamp(at.timestamp())


# --- 単位の値 ----------------------------------------------------------------------------------------


def _left(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    """実験に数えない割り付け ID: 停止の行か、基準の組へ切り替えた拒否の行があるもの。"""
    return {
        r["assign_id"]
        for r in rows
        if (r["kind"] == "halted" and r["assign_id"] is not None) or (r["kind"] == "launch_error" and r["switched"])
    }


def _effects(rows: Sequence[Mapping[str, Any]], metric: Optional[str]) -> dict[str, Mapping[str, Any]]:
    """割り付け ID → 今の指標の名前とバージョンの、先に書いた効果指標の行（壊れて 2 行あっても先の行だけを使う）。"""
    out: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        if r["kind"] == "outcome" and f"{r['metric']}/{r['metric_version']}" == metric and r["assign_id"] not in out:
            out[r["assign_id"]] = r
    return out


def _costs(usage_rows: Sequence[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    """割り付け ID → 単位の費用。`assign_id` か `charge_to` がその ID の起動の費用を、`launch_id` ごとに 1 回足す。

    費用の分からない起動があるか、合計が 0 なら None（0 は対数にも割る数にも使えない）。
    """
    per_launch = ud.launch_costs(usage_rows)
    launches: dict[str, dict[str, Optional[float]]] = defaultdict(dict)
    for row in usage_rows:
        lid = row["launch_id"] if "launch_id" in row else ""  # launch_id の無い行は費用を求められない
        for aid in {row[k] for k in ("assign_id", "charge_to") if k in row}:
            launches[aid][lid] = per_launch[lid] if lid in per_launch else None
    out: dict[str, Optional[float]] = {}
    for aid, costs in launches.items():
        known = [c for c in costs.values() if c is not None]
        out[aid] = sum(known) if len(known) == len(costs) and sum(known) > 0 else None
    return out


def _models(usage_rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """割り付け ID → その単位の最初の起動（引き上げでない。基準へ切り替えた起動は割り付け ID を持たない）の実際のモデル。"""
    first: dict[str, tuple[datetime, str]] = {}
    for row in usage_rows:
        if "assign_id" not in row or "model" not in row or ("raised" in row and row["raised"]):
            continue
        when = datetime.fromisoformat(row["launched_at"] if "launched_at" in row else row["at"])
        if row["assign_id"] not in first or when < first[row["assign_id"]][0]:
            first[row["assign_id"]] = (when, row["model"])
    return {aid: model for aid, (_, model) in first.items()}


@dataclass(frozen=True)
class _Seen:
    """実際のモデルで絞る前の単位と、そのまとまり（`model_choice.model_group`）・評価した日時。"""

    value: UnitValue
    group: str
    evaluated: datetime


def _group_units(seen: Sequence[_Seen]) -> list[UnitValue]:
    """1 つのまとまりの、今のモデル（最も新しく評価した単位の実際のモデル）の単位と、数えるモデルの分からない単位。

    モデルの分からない単位は、変化を観測していなければすべて、今と異なるモデルを観測した単位のうち割り付けが最後のものより後に
    今のモデルを観測した単位があれば、その最初の単位以降に割り付けたものだけを数える。後に今のモデルの単位が無ければ数えない。
    どの単位もモデルが分からないまとまり（Codex など）は絞らない。
    """
    known = [s for s in seen if s.value.model is not None]
    unknown = [s.value for s in seen if s.value.model is None]
    if not known:
        return unknown
    current = max(known, key=lambda s: s.evaluated).value.model
    same = [s.value for s in known if s.value.model == current]
    changed = [s.value.at for s in known if s.value.model != current]
    if not changed:
        return same + unknown
    after = [u.at for u in same if u.at > max(changed)]
    return same + ([u for u in unknown if u.at >= min(after)] if after else [])


def unit_values(
    rows: Sequence[Mapping[str, Any]], usage_rows: Sequence[Mapping[str, Any]], *, where: mc.Where, now: datetime, window_days: int
) -> list[UnitValue]:
    """割り付け ID ごとの単位の値（割り付けの日時の順）。使うのは、直近 `window_days` 日に割り付けた、`where`（リポジトリ・工程・
    ランタイム・互換性のキー）が同じで、今の指標の効果（null でない）がある単位のうち、停止と基準への切り替えの無いもので、
    実際のモデルがまとまりの今のモデルと同じもの（設計の「出現率の再利用」「期限切れの単位とモデルの変化」）。"""
    start = now - timedelta(days=window_days)
    left, effects = _left(rows), _effects(rows, mc.metric_of(where.stage))
    costs, models = _costs(usage_rows), _models(usage_rows)
    seen = []
    for a in rows:
        aid = a["assign_id"] if a["kind"] == "assign" else None
        if aid is None or not where.of(a) or _time(a) < start or aid in left or aid not in effects or effects[aid]["quality"] is None:
            continue
        value = UnitValue(aid, a["chosen"], tuple(a["probs"]), _time(a), effects[aid]["quality"], costs.get(aid), models.get(aid))
        seen.append(_Seen(value, mc.model_group(a["arms"][a["chosen"]], a["arms"]), _time(effects[aid])))
    groups = sorted({s.group for s in seen})
    found = [u for g in groups for u in _group_units([s for s in seen if s.group == g])]
    return sorted(found, key=lambda u: (u.at, u.assign_id))


def seed_of(where: mc.Where, allocation: Mapping[str, Any], units: Sequence[UnitValue]) -> int:
    """乱数の種: 工程・ランタイム・互換性のキー・割り付けの値と、割り付け ID の順に並べた単位の ID・効果・費用の SHA-256。

    同じ単位の集まりからは、何度計算しても同じ出現率と「最良である確率」になる。割り付けの抽選には使わない。
    """
    shown = sorted((u.assign_id, u.quality, u.cost) for u in units)
    value = {"stage": where.stage, "runtime": where.runtime, "key": where.key, "allocation": dict(allocation), "units": shown}
    return int(launch_records.canonical_sha(value)[:16], 16)


# --- 効果指標の記録 ----------------------------------------------------------------------------------


def write_outcome(assign_id: str, quality: Optional[float], detail: Mapping[str, Any]) -> bool:
    """割り付けを引いて効果指標の行を足し、`refresh` を呼ぶ。同じ割り付け ID の行が既にあれば足さず真を返す（再開で重なった呼び出し）。

    割り付けが無い（別のマシンの ID・消された記録）か記録を書けなければ、警告を出して偽。指標の名前とバージョンは割り付けの工程の
    表から引く。確認と追記は 1 つのロックの中で行い、`refresh` はロックを抜けてから呼ぶ。
    """
    now = mc.utc_now()
    since = now - timedelta(days=mc.LONGEST_WINDOW + mc.records_rule()["kept_extra_days"])
    try:
        with mc.ledger(since, mc.records_dir()) as led:
            assign = mc.assigns(led.rows).get(assign_id)
            if assign is None:
                log.warning("効果指標を書く割り付け %s が実験の記録に無い（別のマシンの ID か、消された記録）", assign_id)
                return False
            if any(r["kind"] == "outcome" and r["assign_id"] == assign_id for r in led.rows):
                return True
            stage = facts.row(mc.TABLE, assign["stage"])
            row = {"kind": "outcome", "at": _text(now), "assign_id": assign_id, "metric": stage["metric"]}
            led.append({**row, "metric_version": stage["metric_version"], "quality": quality, "detail": dict(detail)})
    except (*mc.STORE_ERRORS, internal.InternalError, contracts.ContractError) as exc:
        log.warning("実験の記録へ効果指標を書けない（割り付け %s）: %s", assign_id, exc)
        return False
    refresh(assign)
    return True


# --- 出現率の計算し直し ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scope:
    """計算する項目のキーと、今の設定の選択肢・割り付けの値と、保存先・日時。"""

    where: mc.Where
    arms: tuple[Mapping[str, str], ...]
    allocation: Mapping[str, Any]
    store: Path
    now: datetime


def _scope(assign: Mapping[str, Any], now: datetime, cwd: Path) -> Optional[_Scope]:
    """作業ディレクトリのリポジトリの今の設定で、割り付けと同じキーの範囲。リポジトリか設定（互換性のキー）が違えば None。"""
    root = launch_settings.launch_root(cwd)
    got = mc.table(root)
    stage, runtime = assign["stage"], assign["runtime"]
    arms = got.arms[stage][runtime] if runtime in got.arms[stage] else ()
    where = mc.Where(launch_approval.repo_key(root), stage, runtime, mc.compatibility_key(arms, mc.metric_of(stage)))
    return _Scope(where, tuple(arms), got.allocation, mc.records_dir(), now) if where.of(assign) else None


def _fresh(entry: Optional[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], s: _Scope) -> bool:
    """項目（今の設定・選択肢・実際のモデルに合うもの）を計算し直さなくてよいか: 候補が同じで、前の計算から間隔の分を過ぎていない。"""
    if entry is None:
        return False
    recent = s.now - _time(entry) < timedelta(minutes=mc.records_rule()["refresh_minutes"])
    return recent and entry["candidates"] == mc.candidates(s.arms, rows, entry, s.now, s.where)


def _cutoffs(units: Sequence[UnitValue], entry: Optional[Mapping[str, Any]], s: _Scope, seed: int) -> list[dict[str, Any]]:
    """打ち切った組と期限: 前の項目（今の設定に合うもの）の期限の前の打ち切りに、今回の判定を重ねる（同じ組は今回の期限）。"""
    kept = entry["cutoffs"] if entry is not None else []
    cut = {c["arm"]: datetime.fromisoformat(c["until"]) for c in kept if s.now < datetime.fromisoformat(c["until"])}
    cut.update(ad.eliminated(units, range(len(s.arms)), s.allocation["margin"], s.allocation["window_days"], random.Random(seed)))
    return [{"arm": arm, "until": _text(until)} for arm, until in sorted(cut.items())]


def _computed(units: Sequence[UnitValue], rows: Sequence[Mapping[str, Any]], entry: Optional[Mapping[str, Any]], s: _Scope) -> dict:
    """出現率の項目（打ち切りの判定 → 候補の確定 → 段階の判定と確率の計算の順）。添字と確率が選択肢に合わなければ `InternalError`。"""
    seed = seed_of(s.where, s.allocation, units)
    cutoffs = _cutoffs(units, entry, s, seed)
    cands = mc.candidates(s.arms, rows, {"cutoffs": cutoffs}, s.now, s.where)
    found = ad.probabilities(units, cands, s.allocation, random.Random(seed))
    models = {mc.model_group(s.arms[u.arm], s.arms): u.model for u in units if u.model is not None}
    item = {**s.where.fields(), "key": s.where.key, "at": _text(s.now), "phase": found.phase, "candidates": cands}
    item |= {"allocation": dict(s.allocation), "counts": [found.counts[i] if i < len(found.counts) else 0 for i in range(len(s.arms))]}
    item |= {"models": models, "cutoffs": cutoffs} | ({"probs": list(found.probs)} if found.probs is not None else {})
    if not mc.probs_fit(item, len(s.arms)):
        raise internal.InternalError("invalid", f"出現率の項目の添字か確率が {len(s.arms)} 組の選択肢に合わない")
    return item


def _recomputed(assign: Mapping[str, Any], now: datetime, cwd: Path) -> bool:
    s = _scope(assign, now, cwd)
    if s is None:
        return False
    window = s.allocation["window_days"]
    rows = mc.read_experiments(now - timedelta(days=max(window, mc.records_rule()["refusal_days"])), s.store)
    entry = mc.weights_entry(s.store, s.where, now)
    if entry is not None and not mc.entry_fits(entry, rows, s.where, s.arms, s.allocation, now):
        entry = None  # 割り付けの値・選択肢の数・実際のモデルが合わない項目は、打ち切りも引き継がない
    if _fresh(entry, rows, s):
        return False
    usage_rows, _ = jsonl.read_rows(s.store / facts.value("ndf-files", USAGE_LOG, "path"), kind="usage")
    units = unit_values(rows, usage_rows, where=s.where, now=now, window_days=window)
    mc.update_weights(s.store, now, _computed(units, rows, entry, s))
    return True


def refresh(assign: Mapping[str, Any]) -> bool:
    """割り付け `assign` のキーの出現率を、計算し直す条件に当たれば計算して置き換える。置き換えたら真。

    条件は、項目が無い・候補か割り付けの値が今と違う・実際のモデルが最新の観測と違う・前の計算から間隔（表 launch-experiments の
    records の行の `refresh_minutes`）を過ぎた、のどれか。今の設定は作業ディレクトリのリポジトリから読み、リポジトリか互換性の
    キーが割り付けと違えば計算しない。計算か書き込みに失敗したら、警告を出して前の出現率のまま続ける（偽）。
    """
    try:
        return _recomputed(assign, mc.utc_now(), Path.cwd())
    except (*mc.STORE_ERRORS, internal.InternalError, contracts.ContractError, ValueError) as exc:
        log.warning("出現率を計算し直せないので、前の出現率のまま続ける（割り付け %s）: %s", assign["assign_id"], exc)
        return False
