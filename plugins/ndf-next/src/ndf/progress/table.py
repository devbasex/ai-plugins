"""進行表の読み書き（1 つのトランザクションで読んだ進行表と、番号がぶつかれば読み直す追記）。

`command`（conductor の操作）と `run`（工程を流す）の両方が使う。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from ndf.progress import model, stages, store

# 番号のぶつかり（同時の追記）で読み直して書き直す回数
APPEND_ATTEMPTS = 3


@dataclass(frozen=True)
class View:
    """1 つのトランザクションで読んだ進行表（最新の改訂・定義・事象）。"""

    table_id: str
    rev: int
    snap: stages.Snapshot
    doc: dict[str, Any]
    events: list[model.Event]

    @property
    def rows(self) -> dict[str, model.RowState]:
        return model.fold(self.events, self.rev)

    @property
    def whole(self) -> Optional[str]:
        return model.table_state("sprint", self.events)

    def row_events(self, stage: str) -> list[model.Event]:
        return [e for e in self.events if e.draft.row_key == stage and e.draft.rev == self.rev]

    def context(self) -> dict[str, Any]:
        """スプリントの文脈。作成の時点の値に、工程が書いた出力（`outputs_recorded`）を順に重ねる。"""
        out = dict(self.doc["context"])
        for ev in self.events:
            if ev.draft.kind == stages.OUTPUTS_EVENT and ev.draft.rev == self.rev:
                out.update(payload(ev)["outputs"])
        return out


def payload(ev: model.Event) -> dict[str, Any]:
    """事象の payload。無ければ空。"""
    return ev.draft.payload if ev.draft.payload is not None else {}


def view(conn: Any, table_id: str) -> View:
    if store.read_table(conn, table_id) is None:
        raise model.Refused(f"進行表 {table_id} は無い")
    rev, raw = store.read_definition(conn, table_id)
    return View(table_id, rev, stages.snapshot_from(raw), raw, store.read_events(conn, table_id))


def read(st: store.Store, table_id: str) -> View:
    with store.transaction(st) as conn:
        return view(conn, table_id)


def append(st: store.Store, table_id: str, build: Callable[[View], list[model.EventDraft]]) -> list[int]:
    """今の進行表を読み、`build` が作った下書きを追記する。番号がぶつかれば読み直して作り直す。下書きが空なら書かない。"""
    for attempt in range(1, APPEND_ATTEMPTS + 1):
        try:
            with store.transaction(st) as conn:
                v = view(conn, table_id)
                drafts = build(v)
                return store.append_events(conn, table_id, drafts, v.events[-1].seq if v.events else 0) if drafts else []
        except store.Conflict:
            if attempt == APPEND_ATTEMPTS:
                raise
    raise AssertionError("到達しない")
