"""LLM と外部 CLI の起動が取る、重い起動のスロット（#3368 の 3368-2 の AD3+ の分）。

テストのスロット（`ndf.testing.slots`）と同じ保存先・容量を `ndf.slot_pool` で数え、起動 1 本は `launch.weight.llm-cli` の
重みを取る。スロットに空きが無いか、使えるメモリがテストのスロットの下限を割っていれば待つ。待ちの上限と間隔は
`contracts/limits.json` の `launch.wait.llm-cli` で、越えたら `NoSlot` を送って起動しない。

スロットを持つ実行（テストのスロットの中など。環境変数 `NDF_TEST_SLOT_HELD`）の子孫は、親のスロットの中で動くので取らず待たない。
取ったスロットは子へ渡す環境変数（`slot_pool.PARENT_ENV`）で受け継がせ、CLI が動かすテストは CLI の重みを含めて数える
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
    """待ちの上限までにスロットが取れなかったか、スロットの設定が読めない。`str` が理由。"""

    kind = "no_slot"


def _memory_ok(read_memory: ReadMemory, floor_mib: int) -> bool:
    available = read_memory()
    return available is None or available >= floor_mib


def _try_take(directory: Path, capacity: int, owner: Mapping[str, object], lock_seconds: float) -> Optional[slot_pool.Taken]:
    try:
        return slot_pool.take(directory, capacity, slot_pool.weight_mib(KIND), owner, lock_seconds=lock_seconds)
    except Timeout:  # noqa: NDF401 ファイル: 取る判定の排他を待ちきれなければ、空いていないとして次の周で見直す
        return None
    except OSError as exc:
        raise NoSlot(f"重い起動のスロットの保存先へ書けない: {exc}") from exc


def wait_policy() -> tuple[float, float]:
    """(待ちの上限の秒, 確かめる間隔の秒)。"""
    return limits.seconds(WAIT_POLICY), limits.value(WAIT_POLICY, "interval")


def _take_once(
    directory: Path, lim: test_limits.Limits, owner: Mapping[str, object], read_memory: ReadMemory, lock_seconds: float
) -> Optional[slot_pool.Taken]:
    """使えるメモリが下限を割っていなければスロットを 1 度取りに行く（プールの排他は `lock_seconds` 秒まで待つ）。取れなければ None。"""
    if not _memory_ok(read_memory, lim.memory_floor_mib):
        return None
    return _try_take(directory, lim.capacity_mib, owner, lock_seconds)


def _no_slot_reason(agent: str, waited: float, read_memory: ReadMemory) -> str:
    available = read_memory()
    memory = "測れない" if available is None else f"{available} MiB"
    return f"重い起動のスロットを {waited:.0f} 秒待っても {agent} の起動に空かなかった（使えるメモリ: {memory}）"


def _lock_and_sleep(seconds: float, left: float, bounded: bool) -> float:
    """排他を待つ秒・眠る秒 `seconds` を、呼び手が上限を決めたとき（`bounded`）だけ残りの `left` 秒に収める。"""
    return min(seconds, left) if bounded else seconds


def _wait_for(
    agent: str, directory: Path, lim: test_limits.Limits, read_memory: ReadMemory, limit: Optional[float] = None
) -> slot_pool.Taken:
    """スロットを `limit` 秒まで待って取る（None なら設定の上限、0 なら 1 度だけ取りに行き、取れなければ `NoSlot`）。

    呼び手が上限を決めたときは、プールの排他を待つ秒と眠る秒も残りの秒に収める（#4000 の決定 8。上限 0 なら排他も待たない）。
    """
    policy_limit, poll = wait_policy()
    bounded = limit is not None
    if limit is None:
        limit = policy_limit
    owner = {"pid": os.getpid(), "kind": KIND, "agent": agent}
    started = time.monotonic()
    while True:
        left = max(0.0, limit - (time.monotonic() - started))
        lock_seconds = _lock_and_sleep(slot_pool.POOL_LOCK_SECONDS, left, bounded)
        taken = _take_once(directory, lim, owner, read_memory, lock_seconds)
        waited = time.monotonic() - started
        if taken is not None:
            if waited >= poll:
                record("launch_slot", agent=agent, waited_seconds=round(waited, 1))
            return taken
        if waited >= limit:
            raise NoSlot(_no_slot_reason(agent, waited, read_memory))
        time.sleep(_lock_and_sleep(poll, limit - waited, bounded))


@contextmanager
def held(
    agent: str,
    *,
    cwd: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
    read_memory: Optional[ReadMemory] = None,
    wait: Optional[float] = None,
) -> Iterator[dict[str, str]]:
    """`agent` の起動 1 本のスロットを取って持ち、抜けるときに返す。取れなければ `NoSlot`。`read_memory` の既定は作業マシンの値。

    `wait` は待つ上限の秒（None なら設定の上限、0 なら待たない）。親のスロットを受け継ぐときは今までどおり待たない。
    子へ足す環境変数（親のスロットを受け継がせる。スロットを取らなかったときは空）を渡す。
    """
    env = os.environ if environ is None else environ
    if env.get(slots.HELD_ENV):
        yield {}
        return
    try:
        lim = test_limits.load_limits(cwd if cwd is not None else Path.cwd())
        directory, _retired = slots.slots_location(env)
        directory.mkdir(parents=True, exist_ok=True)
    except (test_limits.SlotConfigError, identity.IdentityError, OSError) as exc:
        raise NoSlot(f"重い起動のスロットを用意できない: {exc}") from exc
    taken = _wait_for(agent, directory, lim, read_memory if read_memory is not None else machine.available_mib, wait)
    try:
        yield slot_pool.parent_env(directory, taken.index)
    except BaseException:
        taken.release(quiet=True)
        raise
    taken.release()


@contextmanager
def gate(
    agent: str,
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    wait: Optional[float] = None,
    ensure: bool = True,
) -> Iterator[dict[str, str]]:
    """起動の前置き: 学習に使わない設定のスナップショットを読み、重い起動のスロットを取る。起動しないときは `NotLaunched`。

    `env` は起動先へ渡す環境（既定は今の環境）で、バックアップを確かめた認証の文脈と照らす。子へ足す環境変数を渡す。
    `wait` はスロットを待つ上限の秒（None なら設定の上限、0 なら待たずに `NoSlot`）。
    バックアップはスロットを待つ前と、取った後の起動の直前に読む（待つ間にバックアップが期限を過ぎるか、拒否へ変わることがある）。
    バックアップが無いか期限切れ・取り消し済み・認証の文脈が違うなら、断らずにその場で確かめて記録する（`training.ensure`。#3907）。
    `ensure` が偽なら確かめ直さず（アカウントの設定の API を呼ばず、設定も変えない）、その場合は `NotLaunched` を送る
    （#4000 の Stop の判定など、記録が有効なときだけ起動する呼び手）。
    取った後に拒否へ変われば、スロットを返して `NotLaunched` を送る。
    """
    _refuse_if_needed(agent, env, ensure)
    with held(agent, cwd=cwd, environ=env, wait=wait) as slot_env:
        _refuse_if_needed(agent, env, ensure)
        yield slot_env


def _refuse_if_needed(agent: str, env: Optional[Mapping[str, str]], ensure: bool = True) -> None:
    why = _why(agent, env, ensure)
    if why is not None:
        raise NotLaunched(why)


def _why(agent: str, env: Optional[Mapping[str, str]], ensure: bool) -> Optional[str]:
    """起動を断る理由を返す。断らないなら None。"""
    verdict = training_cache.verdict(agent, env=env)
    if verdict.stale and not ensure:
        return verdict.why
    if verdict.stale:
        # バックアップが使えず確かめ直せば直りうるなら、その場で確かめて記録する（#3907）。httpx を読むのはこのときだけ
        from ndf.adapters import training

        return training.ensure(agent, env=env)
    return training_cache.refusal(agent, env=env)
