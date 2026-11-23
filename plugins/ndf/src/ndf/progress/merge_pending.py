"""マージの方式を決められずに止まった試行の目印（#3908。差し込みの組み立て `ndf.progress.invoke` の補い）。

工程の試行がマージの方式を決められずに止まると、プランの実行の保存先へ目印のファイル（`PENDING_FILE`）を書く。
次の打ち直しは目印を見て、方式を課題より先に GitHub に問う（`ask`）。方式が決まるまで打ち直しのたびに課題と
書き手の権限を読み直さないためである（PR 4289 の指摘）。決まった方式は目印へ記録しておき（`remember`）、その後で
課題を読めずに止まった打ち直しは問い合わせ直さない（`held_method`）。止まっている間に GitHub の許す方式が変わって
マージが断られたら、組み立て済みの値を捨てて方式をもう一度問う（`refused`。PR 4289 の指摘）。目印は差し込みが
そろった試行が消す（`invoke.clear_values` は消さない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import github
from ndf.contracts import result
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


def refused(report: Mapping[str, Any], inputs: Mapping[str, Any]) -> bool:
    """プランの結果 `report` がマージのステップで断られて止まり、その方式が工程の入力 `inputs` で渡されずに問うて決めたものか。

    真なら呼び手（`invoke._call_plan`）が組み立て済みの値を捨て、次の打ち直しが方式をもう一度 GitHub に問う。方式は
    課題を読めずに止まった打ち直しで記録した値を使い回す（`held_method`）ので、その間に GitHub の許す方式が変わると、
    捨てなければ同じ試行の打ち直しが断られる方式を使い続ける（PR 4289 の指摘）。方式を問うのは断られたときだけである。
    マージのステップはテンプレートの部品 `merge`、止まったステップはプランの結果の items の回の行（`kind` が `plan`）にある。
    """
    cause = report.get("cause")
    if report.get("status") != result.STOPPED or not isinstance(cause, dict) or cause.get("kind") != "refused":
        return False
    return _stopped_at_merge(report) and "merge_method" not in inputs


def _stopped_at_merge(report: Mapping[str, Any]) -> bool:
    """プランの結果 `report` の items の回の行（`kind` が `plan`）に、マージのステップで止まったものがあるか。"""
    items = report["items"] if isinstance(report.get("items"), list) else []
    return any(isinstance(i, dict) and i.get("kind") == "plan" and i.get("step") == "merge" for i in items)


def _key(values: Mapping[str, route.Value]) -> list[Any]:
    """方式を決める値の並び。方式は `repo`・`base`・`remote` だけで決まり、課題の値に依らない。

    目印に記録しておいた方式が、この試行の値で決めたものかを比べるために使う。
    """
    return [values.get(k) for k in ("repo", "base", "remote")]
