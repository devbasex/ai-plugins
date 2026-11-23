"""マージの方式を決められずに止まった試行の目印（#3908。差し込みの組み立て `ndf.progress.invoke` の補い）。

工程の試行がマージの方式を決められずに止まると、プランの実行の保存先へ目印のファイル（`PENDING_FILE`）を書く。
次の打ち直しは目印を見て、方式を課題より先に GitHub に問う（`ask`）。方式が決まるまで打ち直しのたびに課題と
書き手の権限を読み直さないためである（PR 4289 の指摘）。決まった方式は目印へ記録しておき（`remember`）、その後で
課題を読めずに止まった打ち直しは課題より先には問い合わせ直さない（`held_method`）。記録した方式はプランへ入れず、
課題を読んだ後に問い直す（止まっている間に GitHub の許す方式が変わりうるため。PR 4289 の指摘）。目印は差し込みがそろった試行が消す
（`invoke.clear_values` は消さない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import github
from ndf.core import route
from ndf.core.plan import files as plan_files

# 目印のファイルの名前（プランの実行の保存先の下）
PENDING_FILE = "merge-method-pending"


def mark(pending: Path) -> None:
    """マージの方式を決められずに止まったことを目印へ書く（決まった方式は持たせない）。"""
    plan_files.write_json(pending, {})


def held_method(pending: Path, values: Mapping[str, route.Value]) -> Optional[str]:
    """目印に記録しておいた方式のうち、この試行と同じ値（`repo`・`base`・`remote`）で決めたもの。無ければ None。"""
    held = plan_files.read_json(pending)
    if not held or held.get("key") != _key(values):
        return None
    method = held.get("merge_method")
    return method if isinstance(method, str) else None


def remember(pending: Path, values: Mapping[str, route.Value], method: route.Value) -> None:
    """決まった方式を目印へ記録しておく（次の打ち直しが問い合わせ直さないため）。方式が文字列でなければ書かない。"""
    if isinstance(method, str):
        plan_files.write_json(pending, {"key": _key(values), "merge_method": method})


def ask(t: route.Template, values: Mapping[str, route.Value], pending: Path) -> dict[str, route.Value]:
    """省いたマージの方式を GitHub に問う（`route.derived_merge_method`）。

    打ち直せば通りうる失敗（`github.GitHubError.passing`。上限・不達・5xx）は、方式が無いとせずに目印を書いて上げる
    （呼び手の `invoke.plan_failure` が待ち時間付きの transient にし、次の打ち直しが方式を課題より先に問う）。
    """
    try:
        return route.derived_merge_method(t, values, raise_passing=True)
    except github.GitHubError:
        mark(pending)
        raise


def _key(values: Mapping[str, route.Value]) -> list[Any]:
    """方式を決める値の並び。方式は `repo`・`base`・`remote` だけで決まり、課題の値に依らない。

    目印に記録しておいた方式が、この試行の値で決めたものかを比べるために使う。
    """
    return [values.get(k) for k in ("repo", "base", "remote")]
