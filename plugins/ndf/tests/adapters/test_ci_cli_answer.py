"""サブコマンド `ndf ci answer` の確かめの順・終え方・書く証跡の行の今の振る舞い（#4409）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ndf import ci_cli
from ndf.contracts import causes
from ndf.core import merge_approval

import merge_hub

RECORD_ID = "run-1:merge:pid-1"


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> merge_hub.MergeHub:
    fake = merge_hub.MergeHub()
    monkeypatch.setattr(ci_cli, "_hub", lambda args: fake)
    return fake


def _record(**changed: Any) -> dict:
    return {"step": "merge", "answer": "approved", "by": "human", "run": "run-1", "presented_id": "pid-1", **changed}


def _shown(**changed: Any) -> dict:
    return {
        "presented_id": "pid-1",
        "repo": merge_hub.REPO,
        "number": 7,
        "base": "main",
        "head": merge_hub.HEAD,
        "production": True,
        **changed,
    }


def _answer(tmp_path: Path, capsys, last_result, record: Any, shown: Any) -> tuple[int, dict]:
    """回答の記録と提示のファイルを書いて打つ。値が文字列ならそのまま書き、None ならファイルを置かない。"""
    paths = {}
    for name, body in (("record", record), ("shown", shown)):
        paths[name] = tmp_path / f"{name}.json"
        if body is not None:
            paths[name].write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    capsys.readouterr()
    code = ci_cli.main(["answer", "--branch", "sprint/x", "--answer-record", str(paths["record"]), "--presented", str(paths["shown"])])
    return code, last_result(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("record", "shown"),
    [
        pytest.param("not json", _shown(), id="record-not-json"),
        pytest.param("[]", _shown(), id="record-not-object"),
        pytest.param(_record(answer="yes"), _shown(), id="unknown-answer"),
        pytest.param(_record(), None, id="no-presented-file"),
        pytest.param(_record(), "not json", id="presented-not-json"),
        pytest.param(_record(), "[]", id="presented-not-object"),
        pytest.param(_record(presented_id=""), _shown(presented_id=""), id="empty-presented-id"),
    ],
)
def test_answer_refuses_the_record_or_presentation_before_reading_the_pr(hub, tmp_path, capsys, last_result, record, shown):
    code, found = _answer(tmp_path, capsys, last_result, record, shown)
    assert code == causes.exit_code("refused")
    assert found["items"] == [] and hub.calls == []


@pytest.mark.parametrize(
    "shown",
    [
        pytest.param({"presented_id": "pid-1"}, id="no-target"),
        pytest.param(_shown(number="seven"), id="number-not-int"),
        pytest.param(_shown(number=None), id="number-null"),
    ],
)
def test_answer_refuses_a_presented_target_of_another_shape_after_reading_the_pr(hub, tmp_path, capsys, last_result, shown):
    code, found = _answer(tmp_path, capsys, last_result, _record(), shown)
    assert code == causes.exit_code("refused")
    assert found["items"] == [{"kind": "pr", "number": 7}]
    assert hub.calls == ["pulls_by_head"]


def test_answer_without_an_open_pr_stops_before_checking_the_presented_target(hub, tmp_path, capsys, last_result):
    hub.pulls_of = {}
    code, found = _answer(tmp_path, capsys, last_result, _record(), {"presented_id": "pid-1"})
    assert code == causes.exit_code("precondition")
    assert hub.calls == ["pulls_by_head"]


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"repo": "o/other"}, id="repo"),
        pytest.param({"number": 8}, id="number"),
        pytest.param({"head": merge_hub.OTHER}, id="head"),
    ],
)
def test_answer_refuses_a_moved_target_without_reading_the_config(hub, tmp_path, capsys, last_result, changed):
    code, found = _answer(tmp_path, capsys, last_result, _record(), _shown(**changed))
    assert code == causes.exit_code("refused")
    assert found["items"] == [{"kind": "pr", "number": 7}]
    assert hub.calls == ["pulls_by_head"]


@pytest.mark.parametrize("production", [pytest.param(None, id="null"), pytest.param("true", id="string"), pytest.param(1, id="int")])
def test_answer_refuses_a_presented_production_that_is_not_the_same_bool(hub, tmp_path, capsys, last_result, production):
    code, _ = _answer(tmp_path, capsys, last_result, _record(), _shown(production=production))
    assert code == causes.exit_code("refused")
    assert "post_comment" not in hub.calls


@pytest.mark.parametrize(
    ("answer", "deliveries", "production"),
    [
        pytest.param("approved", "auto", True, id="approved-production"),
        pytest.param("rejected", "manual", False, id="rejected-not-production"),
    ],
)
def test_answer_writes_the_trail_row_of_the_record_and_the_current_pr(hub, tmp_path, capsys, last_result, answer, deliveries, production):
    hub.files["main"] = (merge_hub.project_text(merge_hub.strategy(deliveries=deliveries)), "c1")
    # 提示のリポジトリは大文字小文字が違っても同じ対象で、番号は数字の文字列でも読む。証跡には今の PR の値を書く
    shown = _shown(repo=merge_hub.REPO.upper(), number="7", production=production)
    results = [_answer(tmp_path, capsys, last_result, _record(answer=answer), shown) for _ in range(2)]
    assert [code for code, _ in results] == [0, 0]
    # 本番へ反映されない回答は、1 回目も書かない
    assert [found["metrics"] for _, found in results] == [{"answer": answer, "written": production}, {"answer": answer, "written": False}]
    assert results[0][1]["items"] == [{"kind": "pr", "number": 7}]
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, "main", merge_hub.HEAD)
    expected = [merge_approval.MergeRow(answer, "user", target, True, RECORD_ID)] if production else []
    assert merge_approval.merge_rows(hub, 7, hub.viewer()) == expected
