"""ステートマシンの事象の種類・読んだ形（`View`）と、工程の定義の写しの移行（`ndf.progress.run` から分けた）。

`View` と事象の種類は `ndf.progress.run` が読み書きと次の 1 手の判断に使う。移行（#4409 の決定 6）は、このリリースの前に
始めた状態機械の写しでクリーンアップをデプロイの直後へ移す事象を作るだけで、書くのは `run.migrate_definition` である。

承認を繰り上げた head のファイルもここで読み書きする。`gate` は `run` を読むので、`run` も読むものはその下に置く。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import result
from ndf.contracts.worker_kinds import DELIVERY, PRODUCT_VERSION, Recorded, recorded_of
from ndf.progress import invoke, model, stages

ACTOR = invoke.ACTOR
# 回答の事象を書く書き手（progress.json の actors のキー。回答した者は payload の by に分けて残す）
CONDUCTOR, ORGANIZER = "conductor", "organizer"


def _event_kind(kind: str) -> str:
    """進行の事象の種類（コントラクト `workflow/progress.json` のスプリントの行）。表に無ければ import の時点で `KeyError`。"""
    return model.rules()[("sprint", kind)].kind


# 事象の種類。行を終える事象の種類は、終えた行の状態とステップの記録の status にも使う
STARTED = _event_kind("started")
STEP_EVENT = _event_kind("step_recorded")
ANSWERED = _event_kind("answered")
FAILED = _event_kind("failed")
FINISHED = _event_kind("finished")
# 失敗した行を待ちへ戻す事象（`ndf progress retry` と同じ種類）
RETRY_EVENT = _event_kind("retry")
# 工程の定義の写しの工程の順を置き換えた事象（このリリースの前に始めた状態機械の移行。#4409 の決定 6）
DEFINITION_MIGRATED = _event_kind("definition_migrated")
# 移行で並べ替える工程（定義の id）。クリーンアップをデプロイの直後へ移す
CLEANUP_STAGE_ID, DEPLOY_STAGE_ID = "cleanup", "deploy"
# 作業の保存先（チェックアウトの保存先の下）
WORK_DIR = "progress-work"
# 承認資料の保存先（状態機械の作業の保存先 `work_root` の下）
MATERIAL_DIR = "gate-materials"
# 承認の後に文書だけが加わって出し直さなかった head の保存先（承認資料の隣）
ACCEPTED_SUFFIX = ".accepted.json"


def work_root(root: Path, machine_id: str) -> Path:
    """ステートマシンごとの作業の保存先（プランの実行の保存先と作業の記録）。名前はステートマシンの識別子の sha256 の頭。"""
    return root / WORK_DIR / hashlib.sha256(machine_id.encode()).hexdigest()[: model.REPO_KEY_LENGTH]


def _accepted_path(work: Path, gate_id: str) -> Path:
    return work / MATERIAL_DIR / f"{gate_id}{ACCEPTED_SUFFIX}"


def write_accepted_head(work: Path, gate_id: str, head: Optional[str]) -> None:
    """質問 `gate_id` の承認を、出し直さずに `head` へ繰り上げたことを残す。"""
    path = _accepted_path(work, gate_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"head": head}) + "\n", encoding="utf-8")


def read_accepted_head(work: Path, gate_id: str) -> Optional[str]:
    """質問 `gate_id` の承認を繰り上げた head（繰り上げていなければ None）。"""
    path = _accepted_path(work, gate_id)
    if not path.exists():
        return None
    return str(json.loads(path.read_text(encoding="utf-8"))["head"])


@dataclass(frozen=True)
class View:
    """1 つのトランザクションで読んだステートマシン（最新の改訂・定義・事象）。"""

    machine_id: str
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
        return stages.compose_context(self.doc["context"], stages.recorded_outputs(self.events, self.rev))


def event_payload(ev: model.Event) -> dict[str, Any]:
    """事象の payload。無ければ空。"""
    return ev.draft.payload if ev.draft.payload is not None else {}


def migrated_order(snap: stages.Snapshot, events: Sequence[model.Event]) -> stages.Snapshot:
    """事象 `definition_migrated` があれば、その工程の順に並べ替えた写し。無い・工程の名前の集合が合わなければ写しのまま。

    写しそのもの（`sha256`・`definition_version`）は書き換えない（写しの由来を残す）。
    """
    held = next((event_payload(e).get("order") for e in events if e.draft.kind == DEFINITION_MIGRATED), None)
    by_name = {s.name: s for s in snap.stages}
    if not isinstance(held, list) or sorted(map(str, held)) != sorted(by_name) or len(held) != len(by_name):
        return snap
    return replace(snap, stages=tuple(by_name[str(n)] for n in held))


def _new_order(v: View) -> Optional[list[str]]:
    """古い順（クリーンアップ → デプロイ）の写しで、デプロイが始まっておらず、クリーンアップが終わっていなければ、
    クリーンアップをデプロイの直後へ移した工程の名前の並び。移さないなら None。"""
    if any(e.draft.kind == DEFINITION_MIGRATED for e in v.events):
        return None
    ids = [s.id for s in v.snap.stages]
    if CLEANUP_STAGE_ID not in ids or DEPLOY_STAGE_ID not in ids or ids.index(CLEANUP_STAGE_ID) > ids.index(DEPLOY_STAGE_ID):
        return None
    cleanup, deploy = (v.snap.stages[ids.index(i)] for i in (CLEANUP_STAGE_ID, DEPLOY_STAGE_ID))
    if v.row_events(deploy.name) and any(e.draft.kind == STARTED for e in v.row_events(deploy.name)):
        return None
    row = v.rows.get(cleanup.name)
    if row is not None and row.state == FINISHED:
        return None
    names = [s.name for s in v.snap.stages if s.name != cleanup.name]
    names.insert(names.index(deploy.name) + 1, cleanup.name)
    return names


def migration_drafts(v: View) -> list[model.EventDraft]:
    """クリーンアップをデプロイの直後へ移す事象の下書き（移さなければ空）。クリーンアップが開いた PR で止まった
    （`precondition` の失敗）状態機械は、失敗を待ちへ戻す事象も足す。"""
    order = _new_order(v)
    if order is None:
        return []
    moved = model.EventDraft(
        DEFINITION_MIGRATED, ACTOR, None, reason="クリーンアップをデプロイの後へ移した", payload={"order": order}, rev=v.rev
    )
    cleanup = next(s.name for s in v.snap.stages if s.id == CLEANUP_STAGE_ID)
    row = v.rows.get(cleanup)
    if row is None or row.state != FAILED:
        return [moved]
    # 開いた PR で止まったクリーンアップは、デプロイの後に流し直すまで結果を使わない（失敗を待ちへ戻す）
    why = "クリーンアップをデプロイの後へ移したので、デプロイの後に流し直す"
    return [moved, model.EventDraft(RETRY_EVENT, CONDUCTOR, cleanup, reason=why, rev=v.rev)]


_UNSEEN: Any = object()


class StageAnswers:
    """工程の事象を新しい順にたどる走査を 1 本だけ持つ（#4513）。

    たどった事象ごとに「最新の承認の回答」と「リリース承認ゲートへの最後の回答とその次の事象」の両方を照らし、最初に当たった
    ものを覚える。`approved` と `release_gate` は、覚えた値があればそれを返し、無ければ止まった所から古い方へ続ける。
    どの順で何回呼んでも、たどる事象の数は工程の事象の数を超えない。
    """

    def __init__(self, v: View, stage: str) -> None:
        self._events = iter(reversed(v.events))
        self.machine_id = v.machine_id
        self._stage = stage
        self._newer: Optional[model.Event] = None
        self._approved: Any = _UNSEEN
        self._gate: Any = _UNSEEN

    def _advance(self) -> bool:
        """事象を 1 つ古い方へ進める。尽きたら覚えていない値を None にして偽を返す。"""
        ev = next(self._events, None)
        if ev is None:
            self._approved = None if self._approved is _UNSEEN else self._approved
            self._gate = None if self._gate is _UNSEEN else self._gate
            return False
        if ev.draft.kind == ANSWERED and ev.draft.row_key == self._stage:
            p = event_payload(ev)
            if self._approved is _UNSEEN and p.get("answer") == invoke.APPROVED:
                self._approved = ev
            if self._gate is _UNSEEN and p.get("kind") == invoke.LISTEN and p.get("gate") == model.release_gate():
                self._gate = (ev, self._newer)
        self._newer = ev
        return True

    def approved(self) -> Optional[model.Event]:
        """工程の最新の承認の回答の事象。無ければ None。"""
        while self._approved is _UNSEEN and self._advance():
            pass
        return self._approved

    def release_gate(self) -> Optional[tuple[model.Event, Optional[model.Event]]]:
        """リリース承認ゲートへの最後の回答と、その次の事象（無ければ None）。回答が無ければ None。"""
        while self._gate is _UNSEEN and self._advance():
            pass
        return self._gate


def released_answer(answers: StageAnswers) -> Optional[Recorded]:
    """リリース承認ゲートへの最後の回答がオーガナイザーの承認で、次の事象が値を書いていれば、その記録済みの値（#4513）。

    値は `product_version` と、同じ事象に書いた `delivery`・`release_store`（無ければ None）。ほかは None。
    """
    found = answers.release_gate()
    if found is None:
        return None
    ev, after = found
    p = event_payload(ev)
    if p.get("answer") != invoke.APPROVED or p.get("by") != ORGANIZER or after is None or after.draft.kind != stages.OUTPUTS_EVENT:
        return None
    q = event_payload(after)
    outputs = result.outputs_of(q)
    store_kind = q.get("release_store")
    return recorded_of(outputs.get(PRODUCT_VERSION), outputs.get(DELIVERY), store_kind if isinstance(store_kind, str) else None)
