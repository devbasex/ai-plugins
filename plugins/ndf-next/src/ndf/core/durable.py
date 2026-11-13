"""DBOS の殻。DBOS を import するのはこのモジュールだけである。

耐久ワークフローと耐久ステップを使う層は、DBOS の型と関数をここから受ける。判断の規則はここに置かず、
`ndf.core.loop` の判断の関数に置く。

- 記録の置き場は呼び手が URL（`system_database_url`）で渡す。SQLite と PostgreSQL を URL の差し替えで扱う
- 実行者の ID（`executor_id`）で回復を分ける。同じ ID とアプリの版で起動すると、途中の耐久ワークフローを
  済んだ耐久ステップを流し直さずに続ける
- アプリの版は DBOS が耐久ワークフローの関数のソースから求める値のまま使う（固定の値にしない）
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from dbos import DBOS, DBOSClient, DBOSConfig, SetWorkflowID

R = TypeVar("R")

# DBOS の記録に載せるログの下限。標準出力は結果 JSON の 1 行に使うため、DBOS の INFO を出さない
LOG_LEVEL = "WARNING"

# 終わっていない耐久ワークフローの状態（落ちた後の回復では ENQUEUED に見えることがある）
UNFINISHED = frozenset({"PENDING", "ENQUEUED"})


def workflow(fn: Callable[..., R]) -> Callable[..., R]:
    """関数を耐久ワークフローにする。中で呼んだ耐久ステップの結果が記録に残る。"""
    return DBOS.workflow()(fn)


def step(fn: Callable[..., R]) -> Callable[..., R]:
    """関数を耐久ステップにする。済んだ呼び出しは回復のときに流し直さず、記録した結果を返す。"""
    return DBOS.step()(fn)


def launch(database_url: str, executor_id: str, name: str) -> None:
    """DBOS を起こし、同じ実行者の ID の途中の耐久ワークフローを回復する。

    耐久ワークフローと耐久ステップの関数は、呼ぶ前に定義しておく（回復が名前で引くため）。
    """
    config: DBOSConfig = {
        "name": name,
        "system_database_url": database_url,
        "executor_id": executor_id,
        "log_level": LOG_LEVEL,
    }
    DBOS(config=config)
    DBOS.launch()


def shutdown() -> None:
    """DBOS を止める。耐久ワークフローと耐久ステップの定義は残す。"""
    DBOS.destroy(destroy_registry=False)


def start(fn: Callable[..., R], workflow_id: str, *args: Any) -> R:
    """ID を決めて耐久ワークフローを流し、結果を返す。同じ ID が終わっていれば、記録した結果を返す。"""
    with SetWorkflowID(workflow_id):
        return fn(*args)


def result(workflow_id: str) -> Any:
    """耐久ワークフローが終わるまで待ち、結果を返す（回復で流れている途中のものも待つ）。"""
    return DBOS.retrieve_workflow(workflow_id).get_result()


def status(workflow_id: str) -> str | None:
    """耐久ワークフローの状態（`PENDING`・`SUCCESS`・`ERROR`・`CANCELLED` など）。記録に無ければ None。"""
    found = DBOS.get_workflow_status(workflow_id)
    return None if found is None else found.status


def current_id() -> str | None:
    """流れている耐久ワークフローの ID。耐久ワークフローの外では None。"""
    return DBOS.workflow_id


def send(workflow_id: str, message: Any, topic: str) -> None:
    """耐久ワークフローへ話題 `topic` のメッセージを送る（DBOS を起こした後）。"""
    DBOS.send(workflow_id, message, topic)


def recv(topic: str, timeout_seconds: float) -> Any:
    """話題 `topic` のメッセージを待つ。期限までに来なければ None。耐久ワークフローの中でだけ呼ぶ。"""
    return DBOS.recv(topic, timeout_seconds=timeout_seconds)


def cancel_before_launch(database_url: str, workflow_id: str) -> None:
    """DBOS を起こさずに耐久ワークフローとその子を取り消す。

    `launch` の前に呼ぶ。起こした後に取り消しても、回復で流れ始めた耐久ステップは止まらない。
    """
    client = DBOSClient(system_database_url=database_url)
    try:
        client.cancel_workflow(workflow_id, cancel_children=True)
    finally:
        client.destroy()
