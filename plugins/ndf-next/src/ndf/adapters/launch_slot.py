"""LLM と外部 CLI の起動が取る、重い起動の枠（#3368 の 3368-2 の AD3+ の分）。

テストの枠（`ndf.testing.slots`）と同じ置き場・容量を `ndf.slot_pool` で数え、起動 1 本は `launch.weight.llm-cli` の
重みを取る。枠に空きが無いか、使えるメモリがテストの枠の下限を割っていれば待つ。待ちの上限と間隔は
`contracts/limits.json` の `launch.wait.llm-cli` で、越えたら `NoSlot` を送って起動しない。

枠を持つ実行（テストの枠の中など。環境変数 `NDF_TEST_SLOT_HELD`）の子孫は、親の枠の中で動くので取らず待たない。
取った枠は子へ渡す環境変数（`slot_pool.PARENT_ENV`）で受け継がせ、CLI が動かすテストは CLI の重みを含めて数える
（CLI の持つ分でテストが自分を待たない）。

    with launch_slot.held("claude") as slot_env:   # 取れるまで待つ。越えたら NoSlot
        proc = subprocess.Popen(..., env={**env, **slot_env})
        ...
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from filelock import Timeout

from ndf import identity, slot_pool
from ndf.adapters import training_cache
from ndf.adapters.run_log import record
from ndf.contracts import limits
from ndf.testing import limits as test_limits
from ndf.testing import machine, slots

KIND = "llm-cli"
WAIT_POLICY = "launch.wait.llm-cli"

ReadMemory = Callable[[], Optional[int]]


class NotLaunched(Exception):
    """起動の前置きで起動しなかった。`kind` が種類（'refused'・'no_slot'）、`detail`（`str`）が理由。"""

    kind = "refused"

    @property
    def detail(self) -> str:
        return str(self)


class NoSlot(NotLaunched):
    """待ちの上限までに枠が取れなかったか、枠の設定が読めない。`str` が理由。"""

    kind = "no_slot"


def _memory_ok(read_memory: ReadMemory, floor_mib: int) -> bool:
    available = read_memory()
    return available is None or available >= floor_mib


def _try_take(directory: Path, capacity: int, owner: Mapping[str, object]) -> Optional[slot_pool.Taken]:
    try:
        return slot_pool.take(directory, capacity, slot_pool.weight_mib(KIND), owner)
    except Timeout:  # noqa: NDF401 ファイル: 取る判定の排他を待ちきれなければ、空いていないとして次の周で見直す
        return None
    except OSError as exc:
        raise NoSlot(f"重い起動の枠の置き場へ書けない: {exc}") from exc


def wait_policy() -> tuple[float, float]:
    """(待ちの上限の秒, 確かめる間隔の秒)。"""
    return limits.seconds(WAIT_POLICY), limits.value(WAIT_POLICY, "interval")


def _take_once(directory: Path, lim: test_limits.Limits, owner: Mapping[str, object], read_memory: ReadMemory) -> Optional[slot_pool.Taken]:
    """使えるメモリが下限を割っていなければ枠を 1 度取りに行く。取れなければ None。"""
    if not _memory_ok(read_memory, lim.memory_floor_mib):
        return None
    return _try_take(directory, lim.capacity_mib, owner)


def _no_slot_reason(agent: str, waited: float, read_memory: ReadMemory) -> str:
    available = read_memory()
    memory = "測れない" if available is None else f"{available} MiB"
    return f"重い起動の枠を {waited:.0f} 秒待っても {agent} の起動に空かなかった（使えるメモリ: {memory}）"


def _wait_for(agent: str, directory: Path, lim: test_limits.Limits, read_memory: ReadMemory) -> slot_pool.Taken:
    limit, poll = wait_policy()
    owner = {"pid": os.getpid(), "kind": KIND, "agent": agent}
    started = time.monotonic()
    while True:
        taken = _take_once(directory, lim, owner, read_memory)
        waited = time.monotonic() - started
        if taken is not None:
            if waited >= poll:
                record("launch_slot", agent=agent, waited_seconds=round(waited, 1))
            return taken
        if waited >= limit:
            raise NoSlot(_no_slot_reason(agent, waited, read_memory))
        time.sleep(poll)


@contextmanager
def held(
    agent: str,
    *,
    cwd: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    read_memory: Optional[ReadMemory] = None,
) -> Iterator[dict[str, str]]:
    """`agent` の起動 1 本の枠を取って持ち、抜けるときに返す。取れなければ `NoSlot`。`read_memory` の既定は作業マシンの値。

    子へ足す環境変数（親の枠を受け継がせる。枠を取らなかったときは空）を渡す。
    """
    env = os.environ if environ is None else environ
    if env.get(slots.HELD_ENV):
        yield {}
        return
    try:
        lim = test_limits.load_limits(cwd if cwd is not None else Path.cwd())
        directory = slots.slots_dir(env)
        directory.mkdir(parents=True, exist_ok=True)
    except (test_limits.SlotConfigError, identity.IdentityError, OSError) as exc:
        raise NoSlot(f"重い起動の枠を用意できない: {exc}") from exc
    taken = _wait_for(agent, directory, lim, read_memory if read_memory is not None else machine.available_mib)
    try:
        yield slot_pool.parent_env(directory, taken.index)
    except BaseException:
        taken.release(quiet=True)
        raise
    taken.release()


@contextmanager
def gate(agent: str, *, cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> Iterator[dict[str, str]]:
    """起動の前置き: 学習に使わない設定の控えを読み、重い起動の枠を取る。起動しないときは `NotLaunched`。

    `env` は起動先へ渡す環境（既定は今の環境）で、控えを確かめた認証の文脈と照らす。子へ足す環境変数を渡す。
    """
    why = training_cache.refusal(agent, env=env)
    if why is not None:
        raise NotLaunched(why)
    with held(agent, cwd=cwd, environ=env) as slot_env:
        yield slot_env
