"""記録の JSONL（`ndf.domains.records.jsonl`）が核（`ndf.contracts.internal`）を通して読み書きすること。#2339 の受け入れ条件 1・5。"""

from __future__ import annotations

import json
from pathlib import Path

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


def test_read_checked_validates_each_row_once_and_keeps_reporting_the_broken_lines(tmp_path, monkeypatch):
    checked: list[dict] = []
    real = internal.read_record
    monkeypatch.setattr(internal, "read_record", lambda kind, raw: checked.append(raw) or real(kind, raw))

    def read() -> tuple[list[str], list[int], int]:
        checked.clear()
        rows, broken = jsonl.read_checked(path, kind="usage", wanted=lambda r: r.get("agent_id") == "a")
        return [r["at"][17:19] for r in rows], [b.line for b in broken], len(checked)

    def line(agent: str, second: int) -> str:
        return json.dumps({**ROW, "agent_id": agent, "at": f"2026-11-23T01:00:0{second}+00:00"}) + "\n"

    path = write(tmp_path / "u.jsonl", line("a", 1).strip(), "{}", line("b", 2).strip())
    assert read() == (["01"], [2], 3)
    assert read() == (["01"], [2], 0)
    with path.open("a", encoding="utf-8") as f:
        f.write(line("a", 3) + '{"書きかけ')
    assert read() == (["01", "03"], [2, 5], 1)
    # 書きかけの行は確かめ終えた位置に入れない
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + line("b", 4))
    assert read() == (["01", "03"], [2, 5], 1)
    # 確かめ終えた中身が変わる・位置のファイルが壊れると、初めから確かめ直す
    path.write_text(line("a", 5) + path.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")
    assert read() == (["05", "03"], [2, 5], 5)
    Path(f"{path}.checked").write_text("[", encoding="utf-8")
    assert read() == (["05", "03"], [2, 5], 5)


def test_read_checked_stops_on_a_newer_version_every_time(tmp_path):
    path = write(tmp_path / "u.jsonl", json.dumps(ROW), json.dumps({**ROW, "version": 2}))
    for _ in range(2):
        with pytest.raises(InternalError):
            jsonl.read_checked(path, kind="usage", wanted=lambda r: True)


@pytest.mark.parametrize("fn", [jsonl.append, jsonl.append_locked])
def test_append_writes_the_stamped_row_and_nothing_when_it_does_not_fit(tmp_path, fn):
    path = tmp_path / "u.jsonl"
    assert fn(path, ROW, kind="usage") == {**ROW, "version": 1}
    before = path.read_text(encoding="utf-8")
    assert json.loads(before) == {**ROW, "version": 1}
    with pytest.raises(InternalError):
        fn(path, {**ROW, "created_at": ROW["at"]}, kind="usage")
    assert path.read_text(encoding="utf-8") == before
