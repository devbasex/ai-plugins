"""効果指標の記録と出現率の更新（#2944 の詳細設計「効果指標と出現率」）。

- `write_outcome`: 割り付け ID から割り付けの行を引いて効果指標の行を足し（同じ割り付け ID の行があれば足さない。確認と追記は
  実験の記録のロックの中）、ロックを抜けてから、そのとき読んだ記録で出現率を計算し直すかを判定する（足さなかったときも判定する）
- `unit_values`: 実験の記録と使用量ログから、計算に使う単位の値を割り付け ID ごとに作る（純粋な関数）
- `refresh`: 計算し直す条件に当たれば、出現率の項目を置き換える。計算はロックの外で、書き換えは `model_choice.update_weights`

読み書きの部品（実験の記録・出現率のファイル・保存先）は adapters の `model_choice` が持ち、計算は `allocation_decide` が持つ。
使用量ログは実験の記録と同じ保存先にある（表 ndf-files の usage-log-file と launch-experiments の location が同じ）。
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    """割り付け ID → その単位の最初の起動（引き上げでない。基準へ切り替えた起動は割り付け ID を持たない）の実際のモデル。

    最初の起動をモデル名の有無で絞らずに決め、その行にモデル名が無ければ（期限切れで回答が無い）不明として載せない。
    再開後の起動のモデルを、最初の起動のモデルとして扱わないためである。
    """
    first: dict[str, tuple[datetime, Optional[str]]] = {}
    for row in usage_rows:
        if "assign_id" not in row or ("raised" in row and row["raised"]):
            continue
        when = datetime.fromisoformat(row["launched_at"] if "launched_at" in row else row["at"])
        if row["assign_id"] not in first or when < first[row["assign_id"]][0]:
            first[row["assign_id"]] = (when, row["model"] if "model" in row else None)
    return {aid: model for aid, (_, model) in first.items() if model is not None}


@dataclass(frozen=True)
class UsageIndex:
    """使用量ログから求めた、割り付け ID → 単位の費用（`_costs`）と、割り付け ID → 最初の起動の実際のモデル（`_models`）。

    どちらも全部の使用量ログを走査するので、実験のキーをいくつも集計する呼び手（`ndf models report`）は 1 回だけ求めて渡す。
    """

    costs: Mapping[str, Optional[float]]
    models: Mapping[str, str]


def usage_index(usage_rows: Sequence[Mapping[str, Any]]) -> UsageIndex:
    """使用量ログの全部の行から `UsageIndex` を求める。"""
    return UsageIndex(_costs(usage_rows), _models(usage_rows))


def refused_launches(rows: Sequence[Mapping[str, Any]]) -> Callable[[Mapping[str, Any]], bool]:
    """使用量ログの行が、モデルを理由に拒否された起動の行かを返す判定。拒否された起動は `launch_error` の行の `launch_id` で見分ける。

    割り付け ID は同じ単位のほかの起動（先に形が合わなかった判定など）も持つので、見分けるのに使わない。
    """
    ids = {r["launch_id"] for r in rows if r["kind"] == "launch_error"}
    return lambda row: "launch_id" in row and row["launch_id"] in ids


def rows_for_units(rows: Sequence[Mapping[str, Any]], end: datetime) -> list[Mapping[str, Any]]:
    """過去の期間の単位を作る記録: `launch_error` の行は `end` より前のものだけ、ほかの行はすべて。

    効果指標と停止は期間の後に届いても期間の単位に数え、期間の後に基準へ切り替えた単位は過去の集計から除かない（`ndf models report`）。
    """
    return [r for r in rows if r["kind"] != "launch_error" or _time(r) < end]


@dataclass(frozen=True)
class _Seen:
    """実際のモデルで絞る前の単位と、そのまとまり（`model_choice.model_group`）・評価した日時。"""

    value: UnitValue
    group: str
    evaluated: datetime


def _group_units(seen: Sequence[_Seen], observed: Optional[str] = None) -> list[UnitValue]:
    """1 つのまとまりの、今のモデルの単位と、数えるモデルの分からない単位。

    今のモデルは、まとまりの最も新しい `observed` の行のモデル（`observed`）。無ければ最も新しく評価した単位の実際のモデル。
    割り付けが項目を使えるかを見る観測（`model_choice.entry_fits`）と同じ観測で決め、モデルが切り替わってから新しいモデルの評価が
    そろうまでは、古いモデルの単位を数えずに件数を集める段階にする。
    モデルの分からない単位は、変化を観測していなければすべて、今と異なるモデルを観測した単位のうち割り付けが最後のものより後に
    今のモデルを観測した単位があれば、その最初の単位以降に割り付けたものだけを数える。後に今のモデルの単位が無ければ数えない。
    どの単位もモデルが分からず観測も無いまとまり（Codex など）は絞らない。
    """
    known = [s for s in seen if s.value.model is not None]
    unknown = [s.value for s in seen if s.value.model is None]
    if not known and observed is None:
        return unknown
    current = observed if observed is not None else max(known, key=lambda s: s.evaluated).value.model
    same = [s.value for s in known if s.value.model == current]
    changed = [s.value.at for s in known if s.value.model != current]
    if not changed:
        return same + unknown
    after = [u.at for u in same if u.at > max(changed)]
    return same + ([u for u in unknown if u.at >= min(after)] if after else [])


def unit_pool(rows: Sequence[Mapping[str, Any]], usage: UsageIndex, *, where: mc.Where) -> list[_Seen]:
    """`where`（リポジトリ・工程・ランタイム・互換性のキー）が同じで、今の指標の効果（null でない）がある単位のうち、停止と基準への
    切り替えの無いもの（割り付けの日時では絞らない）。実験の記録を 1 回だけ走査し、範囲ごとの絞り込みは `pick_units` が行う。"""
    left, effects = _left(rows), _effects(rows, mc.metric_of(where.stage))
    costs, models = usage.costs, usage.models
    seen = []
    for a in rows:
        aid = a["assign_id"] if a["kind"] == "assign" else None
        if aid is None or not where.of(a) or aid in left or aid not in effects or effects[aid]["quality"] is None:
            continue
        value = UnitValue(aid, a["chosen"], tuple(a["probs"]), _time(a), effects[aid]["quality"], costs.get(aid), models.get(aid))
        seen.append(_Seen(value, mc.model_group(a["arms"][a["chosen"]], a["arms"]), _time(effects[aid])))
    return seen


def pick_units(
    pool: Sequence[_Seen], start: datetime, end: Optional[datetime] = None, observed: Optional[Mapping[str, str]] = None
) -> list[UnitValue]:
    """`pool` のうち `start` 以降（`end` があればその前まで）に割り付けた単位を、実際のモデルで絞って割り付けの日時の順に返す。

    割り付けの日時で先に絞ってから、まとまりの今のモデルを決める。範囲の外の単位の評価で、範囲の中の単位を除かないためである。
    """
    observed = {} if observed is None else observed
    seen = [s for s in pool if start <= s.value.at and (end is None or s.value.at < end)]
    groups = sorted({s.group for s in seen})
    found = [u for g in groups for u in _group_units([s for s in seen if s.group == g], observed.get(g))]
    return sorted(found, key=lambda u: (u.at, u.assign_id))


def unit_values(
    rows: Sequence[Mapping[str, Any]],
    usage: UsageIndex,
    *,
    where: mc.Where,
    now: datetime,
    window_days: int,
    observed: Optional[Mapping[str, str]] = None,
) -> list[UnitValue]:
    """割り付け ID ごとの単位の値（割り付けの日時の順）。使うのは、直近 `window_days` 日に割り付けた、`where`（リポジトリ・工程・
    ランタイム・互換性のキー）が同じで、今の指標の効果（null でない）がある単位のうち、停止と基準への切り替えの無いもので、
    実際のモデルがまとまりの今のモデルと同じもの（設計の「出現率の再利用」「期限切れの単位とモデルの変化」）。

    `observed` は、まとまり → 最も新しい `observed` の行のモデル（`model_choice.observed_models`）。あるまとまりは、その
    モデルを今のモデルとする。`usage` は使用量ログから求めた単位の費用と実際のモデル（`usage_index`）で、呼び手が 1 回だけ求めて渡す。"""
    return pick_units(unit_pool(rows, usage, where=where), now - timedelta(days=window_days), observed=observed)


def seed_of(where: mc.Where, allocation: Mapping[str, Any], units: Sequence[UnitValue]) -> int:
    """乱数の種: 工程・ランタイム・互換性のキー・割り付けの値と、割り付け ID の順に並べた単位の ID・効果・費用の SHA-256。

    同じ単位の集まりからは、何度計算しても同じ出現率と「最良である確率」になる。割り付けの抽選には使わない。
    """
    shown = sorted((u.assign_id, u.quality, u.cost) for u in units)
    value = {"stage": where.stage, "runtime": where.runtime, "key": where.key, "allocation": dict(allocation), "units": shown}
    return int(launch_records.canonical_sha(value)[:16], 16)


# --- 効果指標の記録 ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Known:
    """効果指標を書いたときにロックの中で読んだ実験の記録と、その読み始めの日時。読み始めが計算に要る範囲を含めば、
    出現率の計算はこの記録を使い、月別のファイルを読み直さない。"""

    since: datetime
    rows: Sequence[Mapping[str, Any]]


def records_since(now: datetime, window_days: int) -> datetime:
    """出現率の計算に要る記録の読み始め（期間と、起動できない組を数える日数の長いほう）。"""
    return now - timedelta(days=max(window_days, mc.records_rule()["refusal_days"]))


def _read_range(rows: Sequence[Mapping[str, Any]], since: datetime) -> list[Mapping[str, Any]]:
    """`rows` のうち、`since` の月から後の月のファイルにある行（`model_choice.read_experiments(since)` が読む範囲と同じ）。

    行は日時（UTC）の年月のファイルにあるので、日時の文字列の年月で絞る。
    """
    first = f"{since.astimezone(UTC):%Y-%m}"
    return [r for r in rows if r["at"][:7] >= first]


def _rows_for(known: Optional[_Known], since: datetime, store: Path) -> Sequence[Mapping[str, Any]]:
    """計算に使う `since` の月からの記録。効果指標を書いたときに読んだ記録が同じ範囲ならそのまま、古い月まで読み足していれば
    割り付けが読むのと同じ月の範囲に絞り（期間の外の割り付けに付いた拒否や観測を数えない）、範囲が足りなければ読む。"""
    if known is None or known.since > since:
        return mc.read_experiments(since, store)
    return known.rows if known.since == since else _read_range(known.rows, since)


def _table_here(cwd: Path) -> Optional[mc.Table]:
    """作業ディレクトリのリポジトリの今の設定。読めないか値が制約に合わなければ None（効果指標の記録は止めない）。"""
    try:
        return mc.table(launch_settings.launch_root(cwd))
    except (OSError, ValueError, internal.InternalError, contracts.ContractError):  # noqa: NDF401 ファイル: 読めない設定は出現率の計算が読み直して警告する
        return None


def _logged(
    assign_id: str, quality: Optional[float], detail: Mapping[str, Any], now: datetime, since: datetime, longest: datetime
) -> Optional[_Known]:
    """`since` の月から読んだ記録で割り付けを引き、同じ割り付け ID の効果指標の行が無ければ足す。割り付けが無ければ None。

    ロックの外で読んだ `since` の範囲に割り付けが無ければ、`longest` の月から `since` の前の月までだけを、ロックを取る前に
    読み足して探す（読んだ月を読み直さず、ロックも取り直さない）。割り付けと効果指標の行の確認と追記は、1 つのロックの中で行う。
    効果指標の行は割り付けより後に書くので、割り付けが範囲にあれば先行する指標の行も範囲にある。
    """

    def older(rows: Sequence[Mapping[str, Any]]) -> Optional[datetime]:
        return None if any(r["kind"] == "assign" and r["assign_id"] == assign_id for r in rows) else longest

    with mc.ledger(since, mc.records_dir(), older) as led:
        assign = mc.assigns(led.rows).get(assign_id)
        if assign is None:
            return None
        if not any(r["kind"] == "outcome" and r["assign_id"] == assign_id for r in led.rows):
            stage = facts.row(mc.TABLE, assign["stage"])
            row = {"kind": "outcome", "at": _text(now), "assign_id": assign_id, "metric": stage["metric"]}
            led.append({**row, "metric_version": stage["metric_version"], "quality": quality, "detail": dict(detail)})
        return _Known(led.since, list(led.rows))


def write_outcome(assign_id: str, quality: Optional[float], detail: Mapping[str, Any]) -> bool:
    """割り付けを引いて効果指標の行を足し、出現率を計算し直すかを判定する。同じ割り付け ID の行が既にあれば足さずに判定だけを行い、
    真を返す（再開で重なった呼び出し。前の呼び出しが判定の前に止まっていても、ここで反映する）。

    割り付けはまず出現率の計算に要る期間（今の設定の `window_days`）から探し、無ければ同じロックの中で記録を残す最長の期間まで
    古い月を読み足して探す。そのとき読んだ記録を計算に渡して読み直さない。割り付けが無い（別のマシンの ID・消された記録）か記録を書けなければ、
    警告を出して偽。指標の名前とバージョンは割り付けの工程の表から引く。確認と追記は 1 つのロックの中で行い、計算はロックを
    抜けてから行う。
    """
    now, cwd = mc.utc_now(), Path.cwd()
    got = _table_here(cwd)
    longest = now - timedelta(days=mc.LONGEST_WINDOW + mc.records_rule()["kept_extra_days"])
    since = records_since(now, got.allocation["window_days"]) if got is not None else longest
    try:
        known = _logged(assign_id, quality, detail, now, since, longest)
    except (*mc.STORE_ERRORS, internal.InternalError, contracts.ContractError) as exc:
        log.warning("実験の記録へ効果指標を書けない（割り付け %s）: %s", assign_id, exc)
        return False
    if known is None:
        log.warning("効果指標を書く割り付け %s が実験の記録に無い（別のマシンの ID か、消された記録）", assign_id)
        return False
    _refresh_with(mc.assigns(known.rows)[assign_id], now, cwd, got, known)
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


def _scope(assign: Mapping[str, Any], now: datetime, got: mc.Table) -> Optional[_Scope]:
    """今の設定 `got` で、割り付けと同じキーの範囲。設定（互換性のキー）が違えば None。

    リポジトリのキーは割り付けの行の値を使う。作業ディレクトリのリポジトリと同じかは、計算すると決めた後に確かめる
    （git を起動するので、計算しない呼び出しでは確かめない）。
    """
    stage, runtime = assign["stage"], assign["runtime"]
    arms = got.arms[stage][runtime] if runtime in got.arms[stage] else ()
    where = mc.Where(assign["repo"], stage, runtime, mc.compatibility_key(arms, mc.metric_of(stage)))
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


def _computed(
    units: Sequence[UnitValue],
    rows: Sequence[Mapping[str, Any]],
    entry: Optional[Mapping[str, Any]],
    s: _Scope,
    observed: Mapping[str, str],
) -> dict:
    """出現率の項目（打ち切りの判定 → 候補の確定 → 段階の判定と確率の計算の順）。添字と確率が選択肢に合わなければ `InternalError`。

    項目の実際のモデルは、単位を絞ったのと同じ観測（`observed`）を優先する。新しいモデルの評価がまだ無いまとまりも観測のモデルを
    持つので、次の割り付けは項目を捨てずに件数を集める段階として使う。
    """
    seed = seed_of(s.where, s.allocation, units)
    cutoffs = _cutoffs(units, entry, s, seed)
    cands = mc.candidates(s.arms, rows, {"cutoffs": cutoffs}, s.now, s.where)
    found = ad.probabilities(units, cands, s.allocation, random.Random(seed))
    models = {mc.model_group(s.arms[u.arm], s.arms): u.model for u in units if u.model is not None} | dict(observed)
    item = {**s.where.fields(), "key": s.where.key, "at": _text(s.now), "phase": found.phase, "candidates": cands}
    item |= {"allocation": dict(s.allocation), "counts": [found.counts[i] if i < len(found.counts) else 0 for i in range(len(s.arms))]}
    item |= {"models": models, "cutoffs": cutoffs} | ({"probs": list(found.probs)} if found.probs is not None else {})
    if not mc.probs_fit(item, len(s.arms)):
        raise internal.InternalError("invalid", f"出現率の項目の添字か確率が {len(s.arms)} 組の選択肢に合わない")
    return item


def _recomputed(assign: Mapping[str, Any], now: datetime, cwd: Path, got: Optional[mc.Table], known: Optional[_Known]) -> bool:
    root = launch_settings.launch_root(cwd)
    s = _scope(assign, now, mc.table(root) if got is None else got)
    if s is None:
        return False
    window = s.allocation["window_days"]
    since = records_since(now, window)
    rows = _rows_for(known, since, s.store)
    observed = mc.observed_models(rows, s.where, s.arms, now - timedelta(days=window))
    entry = mc.weights_entry(s.store, s.where, now)
    if entry is not None and not mc.entry_fits(entry, observed, s.arms, s.allocation):
        entry = None  # 割り付けの値・選択肢の数・実際のモデルが合わない項目は、打ち切りも引き継がない
    if _fresh(entry, rows, s):
        return False
    if launch_approval.repo_key(root) != s.where.repo:
        return False  # 作業ディレクトリが割り付けと別のリポジトリなので、その設定では計算しない
    usage_rows, _ = jsonl.read_rows(s.store / facts.value("ndf-files", USAGE_LOG, "path"), kind="usage")
    units = unit_values(rows, usage_index(usage_rows), where=s.where, now=now, window_days=window, observed=observed)
    mc.update_weights(s.store, now, _computed(units, rows, entry, s, observed))
    return True


def _refresh_with(assign: Mapping[str, Any], now: datetime, cwd: Path, got: Optional[mc.Table], known: Optional[_Known]) -> bool:
    """`refresh` の本体。`got`（今の設定）と `known`（効果指標を書いたときに読んだ記録）があれば読み直さずに使う。"""
    try:
        return _recomputed(assign, now, cwd, got, known)
    except (*mc.STORE_ERRORS, internal.InternalError, contracts.ContractError, ValueError) as exc:
        log.warning("出現率を計算し直せないので、前の出現率のまま続ける（割り付け %s）: %s", assign["assign_id"], exc)
        return False


def refresh(assign: Mapping[str, Any]) -> bool:
    """割り付け `assign` のキーの出現率を、計算し直す条件に当たれば計算して置き換える。置き換えたら真。

    条件は、項目が無い・候補か割り付けの値が今と違う・実際のモデルが最新の観測と違う・前の計算から間隔（表 launch-experiments の
    records の行の `refresh_minutes`）を過ぎた、のどれか。今の設定は作業ディレクトリのリポジトリから読み、互換性のキーが割り付けと
    違えば計算しない。リポジトリが割り付けと違うかは、条件に当たったときだけ確かめ、違えば計算しない。計算か書き込みに失敗したら、
    警告を出して前の出現率のまま続ける（偽）。
    """
    return _refresh_with(assign, mc.utc_now(), Path.cwd(), None, None)
