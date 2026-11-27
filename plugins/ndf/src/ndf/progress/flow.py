"""流れの設定の読み取り（#4580）。

`.ndf/project.json` の `workflow` のうち、リポジトリの種類（`kind`）・工程の要否（`stages`・`modes`）・課題を閉じる
条件の要否（`close`）を読む。形は `project-workflow.schema.json` が持ち、閉じる条件の名前もそこから読む
（コードに名前を持たない）。NDF は流れの設定を書かない（書くのは利用者だけ）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.core.branch_strategy import PROJECT as PROJECT_FILE

# 流れの設定（`.ndf/project.json` の `workflow`）の形
WORKFLOW_SCHEMA = contracts.schema_path("project-workflow")


class CloseRule(NamedTuple):
    """閉じる条件 1 つの要否と、外したときの理由。"""

    required: bool
    reason: Optional[str]


@dataclass(frozen=True)
class Flow:
    """読んだ流れの設定。`declared` は `workflow` に流れの項目（`flow_keys`）のどれかを書いたか。"""

    kind: Optional[str]
    stages: Mapping[str, Mapping[str, Any]]
    modes: Mapping[str, Mapping[str, Any]]
    close: Mapping[str, CloseRule]
    declared: bool


def _workflow_properties() -> Mapping[str, Mapping[str, Any]]:
    return contracts.load_schema(WORKFLOW_SCHEMA)["properties"]


def _close_properties() -> Mapping[str, Mapping[str, Any]]:
    return _workflow_properties()["close"]["properties"]


def flow_keys() -> tuple[str, ...]:
    """流れの設定に数える `workflow` の項目（スキーマで `x-ndf-flow` が真の項目。launch などは数えない）。"""
    return tuple(name for name, prop in _workflow_properties().items() if prop.get("x-ndf-flow") is True)


def close_names() -> tuple[str, ...]:
    """外せる閉じる条件の名前（スキーマの `close` の `properties` の順）。"""
    return tuple(_close_properties())


def require_flag_condition() -> str:
    """`ndf records close-check --require` が求める閉じる条件の名前（スキーマで `x-ndf-require-flag` が真の条件）。"""
    found = [name for name, prop in _close_properties().items() if prop.get("x-ndf-require-flag") is True]
    if len(found) != 1:
        raise contracts.ContractError(f"{WORKFLOW_SCHEMA} の close に x-ndf-require-flag が真の条件がちょうど 1 つ無い")
    return found[0]


NO_FLOW = Flow(None, {}, {}, {}, False)


def read_flow(root: Path) -> Flow:
    """リポジトリの根 `root` の流れの設定。ファイルか `workflow` が無ければ `NO_FLOW`（流れの設定を書いていない）。

    読めないか形（`WORKFLOW_SCHEMA`）に合わなければ `contracts.ContractError`。
    """
    path = root / PROJECT_FILE
    if not path.exists():
        return NO_FLOW
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise contracts.ContractError(f"{PROJECT_FILE} を読めない: {exc}") from None
    workflow = project.get("workflow") if isinstance(project, dict) else None
    if workflow is None:
        return NO_FLOW
    contracts.require_schema(workflow, WORKFLOW_SCHEMA, contracts.ContractError, f"{PROJECT_FILE} の workflow")
    rules = workflow["close"] if "close" in workflow else {}
    return Flow(
        workflow.get("kind"),
        workflow["stages"] if "stages" in workflow else {},
        workflow["modes"] if "modes" in workflow else {},
        {name: CloseRule(rule["required"], rule.get("reason")) for name, rule in rules.items()},
        any(key in workflow for key in flow_keys()),
    )


def close_rules(flow: Flow) -> dict[str, CloseRule]:
    """外せる閉じる条件のすべての要否。省いた条件は求める（`required: true` と同じ）。"""
    return {name: flow.close[name] if name in flow.close else CloseRule(True, None) for name in close_names()}
