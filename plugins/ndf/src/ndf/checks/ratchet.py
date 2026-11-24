"""ラチェット: 0 件にできない規則の件数を、規則ごとの基準の数（`ratchet.json`）と比べる。

通るのは「件数 = 基準 ≦ 変更の前の基準」のときだけである。重複を減らす変更は、基準の更新（`ndf check --write-spec`）を
同じ変更に含める。基準を上げる経路は持たない。規則を読まず、規則の名前と件数だけを受け取る。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from ndf import contracts
from ndf.checks.model import CheckError, module_rel

# 基準のファイルの保存先（プラグインの根からの相対パス）
RATCHET_REL = module_rel(__name__, ".json")
RATCHET_VERSION = 1


class RatchetError(Exception):
    """基準のファイルを書けない。"""


@dataclass(frozen=True)
class Baseline:
    """規則 1 つの基準の数と、測った日・測った時の HEAD。"""

    max: int
    measured: str
    commit: str

    @property
    def ok(self) -> bool:
        return type(self.max) is int and self.max >= 0 and all(isinstance(v, str) and v for v in (self.measured, self.commit))


COLUMNS = frozenset(f.name for f in fields(Baseline))


def limits_of(data: bytes, where: str) -> dict[str, Baseline]:
    """基準のファイルの中身 `data` の、規則ごとの基準。壊れている・知らないバージョン・形が違うときは `CheckError`（`where` は出所）。"""
    try:
        doc = contracts.parse_json(Path(where), data.decode("utf-8"))
    except (contracts.ContractError, UnicodeDecodeError) as exc:
        raise CheckError(f"ラチェットの基準を読めない: {where}: {exc}") from exc
    if not isinstance(doc, dict) or type(doc.get("version")) is not int or doc["version"] != RATCHET_VERSION:
        raise CheckError(f"ラチェットの基準の version が {RATCHET_VERSION} でない: {where}")
    rules = doc.get("rules")
    if not isinstance(rules, dict) or not all(isinstance(row, dict) and set(row) == COLUMNS for row in rules.values()):
        raise CheckError(f"ラチェットの基準の rules の形が違う（規則ごとに {sorted(COLUMNS)}）: {where}")
    rows = {rule: Baseline(**row) for rule, row in rules.items()}
    if not all(row.ok for row in rows.values()):
        raise CheckError(f"ラチェットの基準の値が違う（max は 0 以上の整数、ほかは空でない文字列）: {where}")
    return rows


def baseline(path: Path) -> dict[str, Baseline]:
    """規則ごとの基準。ファイルが無い・壊れている・形が違うときは `CheckError`。"""
    try:
        return limits_of(path.read_bytes(), str(path))
    except OSError as exc:
        raise CheckError(f"ラチェットの基準を読めない: {path}: {exc}") from exc


def _verdict(count: int, limit: int, before: Optional[Baseline]) -> list[str]:
    reasons = []
    if count > limit:
        reasons.append(f"{count} 件で、基準の {limit} 件より増えた。重複を減らす（基準は上げない）")
    if count < limit:
        reasons.append(f"{count} 件で、基準の {limit} 件より減った。ndf check --write-spec で基準を下げてコミットする")
    if before is not None and limit > before.max:
        reasons.append(f"基準を変更の前の {before.max} 件から {limit} 件へ引き上げている。基準は上げない")
    return reasons


def verdicts(counts: Mapping[str, int], current: Mapping[str, Baseline], base: Mapping[str, Baseline]) -> dict[str, str]:
    """通らない規則の名前と理由（空なら通る）。`current` は今の基準、`base` は変更の前の基準（行の無い規則の引き上げは見ない）。"""
    missing = sorted(set(counts) - set(current))
    if missing:
        raise CheckError(f"ラチェットの基準が無い規則: {missing}")
    reasons = {rule: _verdict(count, current[rule].max, base.get(rule)) for rule, count in counts.items()}
    return {rule: "。".join(why) for rule, why in reasons.items() if why}


def lower(path: Path, counts: Mapping[str, int], commit: str, day: str) -> list[str]:
    """件数が基準を下回った規則の基準を今の件数へ書き直し、書いた規則の名前を返す。無ければ書かない。"""
    rows = baseline(path)
    below = [rule for rule in rows if rule in counts and counts[rule] < rows[rule].max]
    if not below:
        return []
    rows.update({rule: Baseline(counts[rule], day, commit) for rule in below})
    doc = {"version": RATCHET_VERSION, "rules": {rule: asdict(row) for rule, row in rows.items()}}
    try:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RatchetError(f"ラチェットの基準を書けない: {path}: {exc}") from exc
    return below
