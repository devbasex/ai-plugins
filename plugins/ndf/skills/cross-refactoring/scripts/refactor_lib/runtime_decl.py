"""cross-refactoring がランタイムの宣言（`.ndf/runtimes.json`。#1598）を読む口。

読み取りと照合の本体は `scripts/lib/runtime_policy.py` にあり、ここはカレントディレクトリの
リポジトリの宣言を読み、壊れていれば・外の名前があればこの工程の中断へ写すだけを持つ。
"""

from __future__ import annotations

import pathlib
from typing import Any, Iterable, Optional

import runtime_policy

from . import die, info


def load_policy() -> Optional[runtime_policy.RuntimePolicy]:
    """カレントディレクトリのリポジトリのランタイムの宣言。壊れていれば中断する（前提 7）。"""
    try:
        return runtime_policy.read_policy(pathlib.Path.cwd())
    except runtime_policy.RuntimePolicyError as e:
        die(str(e))
        raise


def require_in_policy(names: Iterable[Optional[str]], source: str) -> None:
    """新しく渡した名前が宣言の外なら、参加者を決める前に中断する（AC3・AC16）。"""
    policy = load_policy()
    if policy is None:
        return
    try:
        policy.require(names, source)
    except runtime_policy.RuntimePolicyError as e:
        die(str(e))
        raise


def keep_recorded(state: dict[str, Any], field_name: str, names: list[str]) -> list[str]:
    """再開で記録から引き継いだ名前のうち、宣言の外のものを落とす（設計の決定 5）。宣言が無ければそのまま。"""
    policy = load_policy()
    return policy.keep_allowed(state, field_name, names, info) if policy is not None else names


def policy_changed(state: dict[str, Any]) -> bool:
    """記録の宣言の写しと今の宣言が違うか（再開した時点の宣言に従う。前提 8）。"""
    return runtime_policy.differs_from_record(load_policy(), state.get("participants"))
