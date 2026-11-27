"""流れの設定の読み取り（#4580）: `.ndf/project.json` の `workflow` と閉じる条件の要否。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import contracts
from ndf.progress import flow


def _write(root: Path, project: object) -> Path:
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    text = project if isinstance(project, str) else json.dumps(project, ensure_ascii=False)
    (root / ".ndf" / "project.json").write_text(text, encoding="utf-8")
    return root


def test_read_flow_reads_the_declared_items(tmp_path: Path) -> None:
    close = {"release_verification": {"required": False, "reason": "監視で確かめる"}}
    stages = {"確定仕様化": {"enabled": False, "reason": "別の場所で管理する"}}
    got = flow.read_flow(_write(tmp_path, {"workflow": {"stages": stages, "close": close}}))
    assert got.declared is True
    assert got.stages == stages
    assert got.close == {"release_verification": flow.CloseRule(False, "監視で確かめる")}
    assert got.kind is None and got.modes == {}


@pytest.mark.parametrize("project", [None, {}, {"version": 1}, {"workflow": {}}])
def test_read_flow_without_a_flow_is_not_declared(tmp_path: Path, project: object) -> None:
    if project is not None:
        _write(tmp_path, project)
    got = flow.read_flow(tmp_path)
    assert got == flow.Flow(None, {}, {}, {}, False)


def test_read_flow_counts_launch_alone_as_not_declared(tmp_path: Path) -> None:
    # launch と unit_required_checks は流れの設定に数えない（工程の要否と閉じる条件を変えない）
    got = flow.read_flow(_write(tmp_path, {"workflow": {"launch": {"review": ["ndf", "x"]}}}))
    assert got.declared is False


@pytest.mark.parametrize(
    "project",
    [
        {"workflow": {"close": {"release_verification": {"required": False}}}},
        {"workflow": {"close": {"retrospective_line": {"required": False, "reason": ""}}}},
        {"workflow": {"close": {"other": {"required": False, "reason": "x"}}}},
        {"workflow": {"stages": {"振り返り": {"enabled": False}}}},
        "{壊れた",
    ],
)
def test_read_flow_stops_on_a_broken_flow(tmp_path: Path, project: object) -> None:
    with pytest.raises(contracts.ContractError):
        flow.read_flow(_write(tmp_path, project))


def test_close_rules_fill_the_omitted_conditions_as_required() -> None:
    given = flow.Flow(None, {}, {}, {"retrospective_line": flow.CloseRule(False, "週次の会で行う")}, True)
    assert flow.close_rules(given) == {
        "release_verification": flow.CloseRule(True, None),
        "retrospective_line": flow.CloseRule(False, "週次の会で行う"),
    }
    explicit = flow.Flow(None, {}, {}, {"release_verification": flow.CloseRule(True, None)}, True)
    assert flow.close_rules(explicit) == flow.close_rules(flow.Flow(None, {}, {}, {}, False))
