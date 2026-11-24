"""記録の JSONL（`ndf.domains.records.jsonl`）が核（`ndf.contracts.internal`）を通して読み書きすること。#2339 の受け入れ条件 1・5。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from ndf.contracts import internal
from ndf.contracts.internal import InternalError
from ndf.domains.records import jsonl, usage

ROW = {"at": "2026-11-23T01:00:00+00:00", "caller": usage.CLAUDE_CLI, "usage": "unknown"}


def write(path: Path, *lines: str) -> Path:
    path.write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    return path


def test_read_returns_the_rows_that_fit_and_the_lines_that_do_not(tmp_path):
    old, new = json.dumps(ROW), json.dumps({**ROW, "version": 1, "step": "区切り を含む"}, ensure_ascii=False)
    missing = json.dumps({k: v for k, v in ROW.items() if k != "caller"})
    path = write(tmp_path / "u.jsonl", old, "", missing, "[1]", new, '{"書きかけ')
    rows, broken = jsonl.read_rows(path, kind="usage")
    assert [r["version"] for r in rows] == [1, 1]
    assert [b.line for b in broken] == [3, 4, 6] and "caller" in broken[0].reason
    assert jsonl.broken_items(path, broken) == [{"kind": "broken_rows", "path": str(path), "lines": [3, 4, 6]}]


def test_read_of_fitting_rows_has_no_broken_line(tmp_path):
    path = write(tmp_path / "u.jsonl", json.dumps(ROW))
    assert jsonl.read_rows(path, kind="usage") == ([{**ROW, "version": 1}], [])
    assert jsonl.broken_items(path, []) == []
    assert jsonl.read_rows(tmp_path / "none.jsonl", kind="usage") == ([], [])


def test_read_stops_on_a_newer_version(tmp_path):
    path = write(tmp_path / "u.jsonl", json.dumps(ROW), json.dumps({**ROW, "version": 2}))
    with pytest.raises(InternalError) as exc:
        jsonl.read_rows(path, kind="usage")
    assert exc.value.kind == "precondition"


def test_read_validates_only_the_wanted_rows_and_stops_on_a_newer_version_of_any_row(tmp_path, monkeypatch):
    checked: list[Mapping[str, Any]] = []
    real = internal.read_record
    monkeypatch.setattr(internal, "read_record", lambda kind, raw: checked.append(raw) or real(kind, raw))
    mine, other = {**ROW, "agent_id": "a"}, {"agent_id": "b"}
    path = write(tmp_path / "u.jsonl", json.dumps(mine), json.dumps(other), "[1]", json.dumps({"agent_id": "a"}))
    rows, broken = jsonl.read_rows(path, kind="usage", wanted=_wanted)
    assert rows == [{**mine, "version": 1}] and [b.line for b in broken] == [3, 4] and len(checked) == 2
    write(path, json.dumps(mine), json.dumps({**other, "version": 2}))
    with pytest.raises(InternalError) as exc:
        jsonl.read_rows(path, kind="usage", wanted=_wanted)
    assert exc.value.kind == "precondition"


def _wanted(row: Mapping[str, Any]) -> bool:
    return row.get("agent_id") == "a"


@pytest.mark.parametrize("fn", [jsonl.append, jsonl.append_locked])
def test_append_writes_the_stamped_row_and_nothing_when_it_does_not_fit(tmp_path, fn):
    path = tmp_path / "u.jsonl"
    assert fn(path, ROW, kind="usage") == {**ROW, "version": 1}
    before = path.read_text(encoding="utf-8")
    assert json.loads(before) == {**ROW, "version": 1}
    with pytest.raises(InternalError):
        fn(path, {**ROW, "created_at": ROW["at"]}, kind="usage")
    assert path.read_text(encoding="utf-8") == before
