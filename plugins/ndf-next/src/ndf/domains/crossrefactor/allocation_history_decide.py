"""cross-refactoring の配分の表を、履歴の実行の行から集計する。純粋な関数だけを置く（#3643。今の plugins/ndf の
refactor_lib/allocation.build_table と同等）。

- 配分の表は、履歴の実行の行の実測（その種類を含む直近 10 行の Σ秒 ÷ Σ件数）で `allocation_decide.decide` の値を
  置き換えたもので、保存しない。標本の無い手法は `structure/*` をまとめた値へ落とす
- 1 回の実行の状態から履歴の実行の行を作る（`run_row`）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ndf.domains.crossrefactor.allocation_decide import is_number

RUN_KIND = "run"  # 履歴の実行の行（`kind` を持たない旧い行も実行の行として読む）
STRUCTURE_PREFIX = "structure/"  # 改善項目の種類（`structure/<手法>`）
WINDOW = 10  # 種類ごとに遡る、その種類を含む行の数
DEFAULTS_SOURCE, HISTORY_SOURCE = "defaults", "history"  # 表の材料（`plan.table_source`）
SECTION_COUNT = {"verify": "items", "fix": "launches"}  # 実行の行の工程の区分と件数のキー（`drive` の `verify_stats`・`fix_stats` と同じ）
SECONDS = "seconds"  # 秒のキー


def _num(value: Any) -> float:
    """数として読めない値は 0 とみなす（手で直された行でも集計を止めない）。"""
    if not is_number(value):
        return 0.0
    return float(value)


def _mapping(holder: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """`holder[key]` が object ならそれ、無い・形が違えば空。"""
    got = holder[key] if key in holder else None
    return got if isinstance(got, Mapping) else {}


def _per_unit(samples: Sequence[tuple[float, float]]) -> Optional[float]:
    """`(件数, 秒)` のうち件数が正の直近 `WINDOW` 個から、1 件あたりの分（Σ秒 ÷ Σ件数 ÷ 60）。無ければ None。"""
    recent = [s for s in samples if s[0] > 0][-WINDOW:]
    count = sum(c for c, _ in recent)
    return sum(s for _, s in recent) / count / 60 if count > 0 else None


def _field(holder: Mapping[str, Any], key: str) -> float:
    """`holder[key]` を数として読む（無い・読めなければ 0）。"""
    return _num(holder[key] if key in holder else None)


def _sample(entry: Mapping[str, Any], count_key: str) -> tuple[float, float]:
    return (_field(entry, count_key), _field(entry, SECONDS))


def _kind_samples(rows: Sequence[Mapping[str, Any]], kind: str) -> list[tuple[float, float]]:
    return [_sample(e, "count") for r in rows if (e := _mapping(_mapping(r, "kinds"), kind))]


def _structure_samples(rows: Sequence[Mapping[str, Any]]) -> list[tuple[float, float]]:
    """行ごとに `structure/*` を合算した標本（手法の標本が無いときの代わり）。"""
    out = []
    for r in rows:
        kinds = _mapping(r, "kinds")
        parts = [_sample(_mapping(kinds, n), "count") for n in kinds if str(n).startswith(STRUCTURE_PREFIX)]
        out.append((sum(c for c, _ in parts), sum(s for _, s in parts)))
    return out


def _section_samples(rows: Sequence[Mapping[str, Any]], section: str, count_key: str) -> list[tuple[float, float]]:
    return [_sample(e, count_key) for r in rows if (e := _mapping(r, section))]


def _is_run(row: Mapping[str, Any]) -> bool:
    return (row["kind"] if "kind" in row else RUN_KIND) == RUN_KIND


def build_table(rows: Sequence[Mapping[str, Any]], values: Mapping[str, float]) -> dict[str, Any]:
    """履歴の実行の行から配分の表（分）を集計する。値が出ない種類は `values`（`allocation_decide.decide` の結果）で埋める。

    `source` は実行の行が 1 行でもあれば `history`、無ければ `defaults`（設定で上書きした値も `defaults` に含む）。
    """
    runs = [r for r in rows if _is_run(r)]
    names = sorted({str(n) for r in runs for n in _mapping(r, "kinds") if str(n).startswith(STRUCTURE_PREFIX)})
    kinds = {n: v for n in names if (v := _per_unit(_kind_samples(runs, n))) is not None}

    def pick(value: Optional[float], name: str) -> float:
        return float(values[name]) if value is None else value

    return {
        "source": HISTORY_SOURCE if runs else DEFAULTS_SOURCE,
        "test": pick(_per_unit(_kind_samples(runs, "test")), "test"),
        "verify": pick(_per_unit(_section_samples(runs, "verify", SECTION_COUNT["verify"])), "verify"),
        "fix": pick(_per_unit(_section_samples(runs, "fix", SECTION_COUNT["fix"])), "fix"),
        "structure": pick(_per_unit(_structure_samples(runs)), "structure"),
        "kinds": kinds,
    }


def item_estimate(table: Mapping[str, Any], technique: Any, tests: bool) -> dict[str, float]:
    """項目 1 件の見積り（分）。テストを足さない項目はテストの分を 0 にし、標本の無い手法は `structure` へ落とす。"""
    kinds = _mapping(table, "kinds")
    kind = f"{STRUCTURE_PREFIX}{technique}" if isinstance(technique, str) and technique else ""
    return {"test": float(table["test"]) if tests else 0.0, "implement": float(kinds[kind] if kind in kinds else table["structure"])}


def _technique(item: Mapping[str, Any]) -> str:
    got = item["technique"] if "technique" in item else None
    return got if isinstance(got, str) and got else "unknown"


def run_row(state: Mapping[str, Any], at: str) -> dict[str, Any]:
    """1 回の実行の状態から履歴の実行の行を作る。数えるのは採った項目の、コードを書いた工程の秒だけ。"""
    kinds: dict[str, dict[str, float]] = {}

    def add(kind: str, seconds: Any) -> None:
        bucket = kinds.setdefault(kind, {"count": 0, SECONDS: 0.0})
        bucket["count"] += 1
        bucket[SECONDS] = round(bucket[SECONDS] + _num(seconds), 1)

    raw = state["items"] if "items" in state else None
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or (item["status"] if "status" in item else None) != "adopted":
            continue
        spent = _mapping(item, "seconds")
        if "test" in spent:
            add("test", spent["test"])
        if "implement" in spent:
            add(f"{STRUCTURE_PREFIX}{_technique(item)}", spent["implement"])
    fix, verify = _mapping(state, "fix_stats"), _mapping(state, "verify_stats")
    return {
        "schema": 2,
        "kind": RUN_KIND,
        "at": at,
        "pr": state["pr"] if "pr" in state else None,
        "kinds": kinds,
        "verify": _section_row(verify, SECTION_COUNT["verify"]),
        "fix": _section_row(fix, SECTION_COUNT["fix"]),
    }


def add_stat(held: Mapping[str, Any], count_key: str, seconds: float) -> dict[str, Any]:
    """工程の集計（`verify_stats`・`fix_stats`）へ 1 件と秒を足した新しい集計。キーが無ければ 0 から数える。"""
    count = held[count_key] if count_key in held else 0
    spent = held[SECONDS] if SECONDS in held else 0.0
    return {count_key: count + 1, SECONDS: round(spent + seconds, 1)}


def _section_row(stats: Mapping[str, Any], count_key: str) -> dict[str, float]:
    """工程の集計（`verify_stats`・`fix_stats`）から履歴の行の区分（件数と秒）を作る。"""
    return {count_key: _field(stats, count_key), SECONDS: _field(stats, SECONDS)}
