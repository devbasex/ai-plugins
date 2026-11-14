"""重い起動の 1 つの枠（#3368 の 3368-2）。起動の種類ごとの重み（GiB）の合計を枠の容量に収める。

テストの枠（`testing/slots.py`）と、LLM の CLI を起こす外部との接続（`adapters/`）の両方が同じ置き場を数える
下の層で、どちらも import しない。重みは `contracts/limits.json` の方針 `launch.weight.<種類>` の `gib` にある。

持ち主 1 つにつき番号付きのカーネルのロック（`slot-<番号>.lock`）を持ち、記述（`slot-<番号>.json`）に重みを書く。
持ち主のプロセスが消えるとカーネルがロックを解くので、ロックの取れる記述は数えない。取る判定は置き場の
`pool.lock` の中で行い、2 つの起動が同じ空きを数えない。

    taken = slot_pool.take(directory, capacity_mib, slot_pool.weight_mib("llm-cli"), {"pid": os.getpid()})
    if taken is not None:          # 空きが無ければ None（呼び手が待つ）
        try: ...                   # 起動して終わりを待つ
        finally: taken.release()
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from ndf.contracts import limits

SLOT_PREFIX = "slot-"
POOL_LOCK = "pool.lock"
WEIGHT_KEY = "weight_mib"
# 重みの方針の名前の頭（`launch.weight.test`・`launch.weight.llm-cli` など）
WEIGHT_POLICY = "launch.weight."
# 取る判定の排他を待つ上限の秒
POOL_LOCK_SECONDS = 10.0


def weight_mib(kind: str) -> int:
    """起動の種類 `kind` の重み（MiB）。方針が無ければ `limits.LimitsError`。"""
    return int(limits.value(WEIGHT_POLICY + kind, "gib") * 1024)


def lock(path: Path) -> FileLock:
    # ソフトロックへ落とさない（カーネルのロックでないと持ち主の消滅で解けない）。ロックのファイルは消さない
    return FileLock(path, thread_local=False, fallback_to_soft=False, preserve_lock_file=True)


def try_lock(path: Path) -> FileLock | None:
    """`path` のロックを待たずに取る。ほかが持っていれば None。"""
    held = lock(path)
    try:
        held.acquire(timeout=0)
    except Timeout:  # noqa: NDF401 ファイル: ほかの実行がロックを持っていれば空いていない
        return None
    return held


def is_owned(directory: Path, index: int) -> bool:
    """番号 `index` の枠の持ち主が生きているか（ロックを待たずに取れなければ真。取れたらすぐ返す）。"""
    probe = try_lock(slot_path(directory, index, ".lock"))
    if probe is None:
        return True
    probe.release()  # 持ち主がいない
    return False


def slot_path(directory: Path, index: int, suffix: str = "") -> Path:
    return directory / f"{SLOT_PREFIX}{index}{suffix}"


def slot_index(path: Path) -> int | None:
    digits = path.stem.removeprefix(SLOT_PREFIX)
    return int(digits) if digits.isdigit() else None


def replace_text(path: Path, text: str) -> None:
    """一時ファイルへ書いてから置き換える（読む側が書きかけを見ない）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json(path: Path, data: Mapping[str, object]) -> None:
    """JSON のオブジェクトを 1 行で、一時ファイルへ書いてから置き換える（読む側が書きかけを見ない）。"""
    replace_text(path, json.dumps(data, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 記述が無いか書きかけなら記述が無いとして扱う
        return None
    return data if isinstance(data, dict) else None


@dataclass
class Taken:
    """取った枠。`release` で記述を消してロックを返す。"""

    directory: Path
    index: int
    weight_mib: int
    _lock: FileLock

    def describe(self, owner: Mapping[str, object]) -> None:
        """持ち主の記述を書き直す。重みは残す。"""
        write_json(slot_path(self.directory, self.index, ".json"), {**owner, WEIGHT_KEY: self.weight_mib})

    def release(self, *, quiet: bool = False) -> None:
        """`quiet` なら記述を消せなくても送らない（別の例外の後始末のとき）。"""
        try:
            slot_path(self.directory, self.index, ".json").unlink(missing_ok=True)
        except OSError:
            if not quiet:
                raise
        finally:
            self._lock.release()


def _live_weights(directory: Path) -> tuple[int, set[int]]:
    """生きた持ち主の重みの合計と、生きた持ち主の番号。重みの無い記述（書きかけ）は 0 と数える。"""
    total, alive = 0, set()
    for path in directory.glob(f"{SLOT_PREFIX}*.lock"):
        index = slot_index(path)
        if index is None:
            continue
        if not is_owned(directory, index):
            continue
        alive.add(index)
        data = read_json(slot_path(directory, index, ".json"))
        value = None if data is None else data.get(WEIGHT_KEY)
        total += value if isinstance(value, int) and not isinstance(value, bool) else 0
    return total, alive


def held_mib(directory: Path) -> int:
    """置き場の生きた持ち主の重みの合計（MiB）。"""
    return _live_weights(directory)[0]


def take(directory: Path, capacity_mib: int, weight: int, owner: Mapping[str, object]) -> Taken | None:
    """重み `weight`（MiB）の枠を取る。生きた持ち主の重みと足して容量を超えれば None。

    持ち主が 1 つも無ければ容量を超える重みでも取る（重い起動 1 本が永く待たない）。
    排他を待ちきれないときは `filelock.Timeout`、置き場へ書けないときは `OSError` を送る。
    """
    with lock(directory / POOL_LOCK).acquire(timeout=POOL_LOCK_SECONDS):
        total, alive = _live_weights(directory)
        if alive and total + weight > capacity_mib:
            return None
        index = 0
        while True:
            held = None if index in alive else try_lock(slot_path(directory, index, ".lock"))
            if held is not None:
                break
            index += 1
        taken = Taken(directory, index, weight, held)
        try:
            taken.describe(owner)
        except BaseException:
            held.release()
            raise
        return taken
