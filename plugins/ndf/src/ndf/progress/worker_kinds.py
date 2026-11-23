"""worker の作業の定義（`contracts/worker/kinds.json`）を読み、作業ごとの入力と作業場所の欠けを返す（#3908 の決定 3）。

作業の名前の唯一の基準は `kinds.json` のキーである。`progress.invoke`（工程がプランを組む層）と `progress.gate.entry`
（`ndf answer work`）の両方から読むので、どちらも読まずに `ndf.contracts` だけを読む。差し込みの出所の文
（`invoke.FILL_SOURCES`）は呼び手が `sources` で渡す。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Optional

from ndf import contracts

KINDS_FILE = "worker/kinds.json"
NO_SOURCES: Mapping[str, str] = MappingProxyType({})


@contracts.cached_reader
def work_kinds() -> dict[str, dict[str, Any]]:
    """作業の名前 → 定義（`work`・`summary`・`skills`・`excerpt`・`inputs`・`cwd`・`outputs`）。"""
    return contracts.load_json(KINDS_FILE)["kinds"]


def _filled(value: Any) -> bool:
    return value is not None and value not in ("", [])


def kind_values(kind: str, values: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """作業の定義の `inputs` の名前ごとの値。差し込みの値（`values`）にあればそれを、無ければ工程の入力（`inputs`）を取る。"""
    out: dict[str, Any] = {}
    for name in work_kinds()[kind]["inputs"]:
        if name in values:
            out[name] = values[name]
        elif name in inputs:
            out[name] = inputs[name]
    return out


def work_dir_of(kind: str, values: Mapping[str, Any]) -> Optional[str]:
    """作業の定義の `cwd`（`worktree` か `repo`）が指す差し込みの値。空か無ければ None。"""
    found = values.get(work_kinds()[kind]["cwd"])
    return found if isinstance(found, str) and found else None


def kind_gaps(
    kind: str, values: Mapping[str, Any], inputs: Mapping[str, Any], *, sources: Mapping[str, str] = NO_SOURCES
) -> dict[str, str]:
    """作業の定義の `inputs` と `cwd` が指す値のうち、どちらにも無い（または空の）名前 → 出所の人が読む文。そろえば空。

    出所は `sources`（差し込みの名前 → 出所の文。呼び手が `invoke.FILL_SOURCES` を渡す）にあればその文、無ければ工程の入力。
    """
    got = kind_values(kind, values, inputs)
    gaps = {
        n: sources[n] if n in sources else f"工程の入力 {n}（工程の定義の metadata.ndf.inputs）"
        for n in work_kinds()[kind]["inputs"]
        if not _filled(got.get(n))
    }
    if work_dir_of(kind, values) is None:
        cwd = work_kinds()[kind]["cwd"]
        gaps[cwd] = f"作業 {kind} の作業場所（差し込み {cwd}）"
    return gaps
