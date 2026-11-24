"""ステートマシンの ID から、判定のステップの工程の入力を組む（`ndf records mvv gate --machine`。#3959 の決定 5・#4055）。

プランの判定のステップが `NDF_STAGE_INPUT` に入れるのと同じ値（`invoke.resolve_inputs` の結果）を返す。プランの外
（supervisor が回すフェーズ）でも、同じ入力で判定を打てるようにするためである。保存先は読むだけで書かない
（作成・旧い形の退避・表の作成・バックアップをせず、要るときは `store.StoreNotReady` で止まる）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ndf.progress import command, invoke, model, store


def stage_inputs_of(cwd: Path, machine_id: str, step_id: str) -> dict[str, Any]:
    """ステートマシン `machine_id` の最新の定義から、ステップ `step_id` を持つ工程の入力を文脈から組む。

    - ステートマシンが無い・打った側と別のリポジトリのもの・モードがそのステップを持たないときは `model.Refused`
    - 保存先か DB が無い・用意や移行が要るときは `store.StoreNotReady`（何も変えない）
    - 入力のキーが文脈に無ければ `invoke.StageInputMissing`、値の形が違えば `invoke.StageInputInvalid`（プランと同じく判定しない）

    リポジトリの突き合わせは、定義も事象も読む前に行う。`NDF_PROGRESS_DB` で共有する DB では ID だけで読めるが、後続の判定は
    打った側の GitHub 接続で PR へ書くためである。拒否の文には、どちらのリポジトリの識別も載せない（所属を漏らさない）。
    文脈の `design_pr` などの、工程の入力に無いキーは足さない（プランの経路と判定する PR が分かれないように）。
    """
    root = command.store_root_of(cwd)
    url = store.default_url(root)
    with store.open_existing(url, root) as st, store.transaction(st) as conn:
        meta = store.read_machine(conn, machine_id)
        if meta is None:
            raise model.Refused(f"ステートマシン {machine_id} は無い")
        if meta["repo"] != command.repo_id(cwd, root):
            raise model.Refused(f"ステートマシン {machine_id} はこのリポジトリのものでない")
        current = command.read_current(conn, machine_id)
    found = [s for s in current.snapshot.stages if any(step.id == step_id for step in s.steps)]
    if not found:
        raise model.Refused(f"ステートマシンのモード {current.snapshot.mode} は判定のステップ {step_id} を持たない")
    return invoke.resolve_inputs(found[0], command.machine_context(current))
