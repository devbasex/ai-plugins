"""`mvv_gate._bound` の振る舞いの固定（省かない理由の文・その並び・要求ごとの診断の記録）。

承認の対象と表の番号を返す `_target` と、診断の記録を書く `mvv.record_failure` を差し替え、`_bound` だけを呼ぶ。
診断の記録の識別子は呼ばれた順の連番にし、どの要求の失敗をどの順で記録したかを理由の文から読めるようにする。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from ndf.adapters import github
from ndf.domains.records import mvv_gate
from ndf.domains.records import mvv_material_decide as mm

A, B, C, D = (f"issues/issue-{n}-design.md" for n in (1, 2, 3, 4))
TARGET = {"repo": "o/r", "pr": 5, "base": "main", "head": "h", "production": False}
STOP_TABLE = mm.STOP_REASONS["table_unreadable"]
STOP_BASE = mm.STOP_REASONS["base_unreadable"]


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """診断の記録を書く代わりに行を集め、識別子 `id1`・`id2`… を返す。"""
    rows: list[dict[str, Any]] = []

    def record(row: dict[str, Any]) -> str:
        rows.append(dict(row))
        return f"id{len(rows)}"

    monkeypatch.setattr(mvv_gate.mvv, "record_failure", record)
    monkeypatch.setattr(mvv_gate.mvv, "repo_key", lambda root: "o/r")
    return rows


def _judging() -> Any:
    pull = SimpleNamespace(head_sha="h", base_ref="main", number=5)
    where = {"root": Path("/nowhere"), "gate": "design", "pr": 5, "head": "h"}
    return SimpleNamespace(hub=object(), pull=pull, where=where)


def _rows(*paths: str, removed: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    return [{"filename": p, "status": "removed" if p in removed else "modified"} for p in paths]


def _stub_target(
    monkeypatch: pytest.MonkeyPatch,
    found: Optional[dict[str, Optional[tuple[str, ...]]]] = None,
    unread: Optional[list[github.Unread]] = None,
    error: Optional[Exception] = None,
) -> list[list[str]]:
    calls: list[list[str]] = []

    def target(hub: Any, pull: Any, paths: Any) -> Any:
        calls.append(list(paths))
        if error is not None:
            raise error
        return dict(TARGET), dict(found or {}), list(unread or [])

    monkeypatch.setattr(mvv_gate, "_target", target)
    return calls


def _outcome(skip: bool = True, why: Optional[list[str]] = None) -> dict[str, Any]:
    return {"gate": "design", "skip": skip, "why": list(why or []), "verdict": {"verdict": "follow"}}


def test_a_kept_outcome_is_returned_as_is_without_reading(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    calls = _stub_target(monkeypatch)
    outcome = _outcome(skip=False, why=["前の理由"])
    assert mvv_gate._bound(_judging(), _rows(A), outcome) is outcome
    assert (calls, records) == ([], [])


def test_only_design_documents_on_head_are_asked(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    calls = _stub_target(monkeypatch, found={A: (), C: ()})
    rows = [*_rows(A, B, removed=(B,)), {"filename": "src/x.py", "status": "modified"}, *_rows(C)]
    out = mvv_gate._bound(_judging(), rows, _outcome(why=["前の理由"]))
    assert calls == [[A, C]]
    assert out == {**_outcome(why=["前の理由"]), "target": TARGET}
    assert records == []


def test_a_first_request_failure_writes_one_record_and_one_line_per_document(
    monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]
) -> None:
    _stub_target(monkeypatch, error=github.GitHubError("届かない zz-secret"))
    out = mvv_gate._bound(_judging(), _rows(A, B), _outcome(why=["前の理由"]))
    assert out == {
        **_outcome(skip=False),
        "why": [
            "前の理由",
            f"マージ先の設定を読めない: {STOP_BASE}（診断の記録 id1）",
            f"{mvv_gate.TABLE_UNREADABLE}{A}（{STOP_TABLE}。診断の記録 id1）",
            f"{mvv_gate.TABLE_UNREADABLE}{B}（{STOP_TABLE}。診断の記録 id1）",
        ],
    }
    (row,) = records
    assert (row["kind"], row["gate"], row["pr"], row["head"], row["repo"]) == ("base_unreadable", "design", 5, "h", "o/r")
    assert "zz-secret" in row["cause"]


def test_a_first_request_git_failure_without_design_documents_keeps_with_one_line(
    monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]
) -> None:
    _stub_target(monkeypatch, error=mvv_gate.GitError("壊れた"))
    out = mvv_gate._bound(_judging(), [], _outcome())
    assert out["skip"] is False and "target" not in out
    assert out["why"] == [f"マージ先の設定を読めない: {STOP_BASE}（診断の記録 id1）"]
    assert len(records) == 1


def test_later_failures_record_once_per_request_in_order(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    """後続の要求の失敗は要求ごとに 1 件、要求の順に記録し、理由は文書の順（`found` の順）に並ぶ。"""
    unread = [
        github.Unread((C, A), github.GitHubError("一つ目")),
        github.Unread((D,), github.GitHubError("二つ目")),
    ]
    _stub_target(monkeypatch, found={A: None, B: (), C: None, D: None}, unread=unread)
    out = mvv_gate._bound(_judging(), _rows(A, B, C, D), _outcome(why=["前の理由"]))
    assert [r["cause"] for r in records] == ["一つ目", "二つ目"]
    assert all(r["kind"] == "table_unreadable" for r in records)
    assert out == {
        **_outcome(skip=False),
        "why": [
            "前の理由",
            f"{mvv_gate.TABLE_UNREADABLE}{A}（{STOP_TABLE}。診断の記録 id1）",
            f"{mvv_gate.TABLE_UNREADABLE}{C}（{STOP_TABLE}。診断の記録 id1）",
            f"{mvv_gate.TABLE_UNREADABLE}{D}（{STOP_TABLE}。診断の記録 id2）",
        ],
    }


def test_each_kind_of_reason_keeps_the_found_order(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    """読めなかった・本文が無い・番号のある行がある、の 3 種を混ぜても、理由は文書の順で、番号の無い文書は載らない。"""
    unread = [github.Unread((C,), github.GitHubError("失敗"))]
    _stub_target(monkeypatch, found={D: ("C7", "C2"), A: None, B: (), C: None}, unread=unread)
    out = mvv_gate._bound(_judging(), _rows(D, A, B, C), _outcome())
    assert out["skip"] is False and "target" not in out
    assert out["why"] == [
        f"{mvv_gate.TABLE_KEPT}{D}（C7・C2）",
        f"{mvv_gate.TABLE_UNREADABLE}{A}（本文が無い）",
        f"{mvv_gate.TABLE_UNREADABLE}{C}（{STOP_TABLE}。診断の記録 id1）",
    ]
    assert len(records) == 1


def test_a_failed_document_with_numbers_is_reported_as_unreadable(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    """失敗した要求に載った文書は、`found` に番号があっても読めなかったとして書く。"""
    unread = [github.Unread((A,), github.GitHubError("失敗"))]
    _stub_target(monkeypatch, found={A: ("C7",)}, unread=unread)
    out = mvv_gate._bound(_judging(), _rows(A), _outcome())
    assert out["why"] == [f"{mvv_gate.TABLE_UNREADABLE}{A}（{STOP_TABLE}。診断の記録 id1）"]


def test_an_unwritable_record_is_named_in_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(row: Any) -> str:
        raise OSError("書けない")

    monkeypatch.setattr(mvv_gate.mvv, "record_failure", broken)
    monkeypatch.setattr(mvv_gate.mvv, "repo_key", lambda root: "o/r")
    _stub_target(monkeypatch, found={A: None}, unread=[github.Unread((A,), github.GitHubError("失敗"))])
    out = mvv_gate._bound(_judging(), _rows(A), _outcome())
    assert out["why"] == [f"{mvv_gate.TABLE_UNREADABLE}{A}（{STOP_TABLE}。診断の記録に書けない）"]
    _stub_target(monkeypatch, error=github.GitHubError("届かない"))
    out = mvv_gate._bound(_judging(), _rows(A), _outcome())
    assert out["why"] == [
        f"マージ先の設定を読めない: {STOP_BASE}（診断の記録に書けない）",
        f"{mvv_gate.TABLE_UNREADABLE}{A}（{STOP_TABLE}。診断の記録に書けない）",
    ]
