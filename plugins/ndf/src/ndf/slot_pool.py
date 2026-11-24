"""重い起動の 1 つのスロット（#3368 の 3368-2）。起動の種類ごとの重み（GiB）の合計をスロットの容量に収める。

テストのスロット（`testing/slots.py`）と、LLM の CLI を起動する外部との接続（`adapters/`）の両方が同じ保存先を数える
下の層で、どちらも import しない。重みは `contracts/limits/launch.weight.<種類>.json` の `gib` にある。

オーナー 1 つにつき番号付きのカーネルのロック（`slot-<番号>.lock`）を持ち、記述（`slot-<番号>.json`）に重みを書く。
オーナーのプロセスが消えるとカーネルがロックを解くので、ロックの取れる記述は数えない。取る判定は保存先の
`pool.lock` の中で行い、2 つの起動が同じ空きを数えない。

スロットを持つ起動（LLM の CLI）の子孫は、環境変数 `NDF_LAUNCH_SLOT_PARENT` で親のスロットを受け継ぐ。子孫が取るスロット（CLI が
動かすテスト）は親の重みを自分の重みに含めて数え、親とほかの CLI の持つ分で待たない（`take` の `absorbed`）。

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
# 取る判定の排他を待つ上限の方針（contracts/limits/）
POOL_LOCK_POLICY = "launch.slot-lock"
# スロットを持つ起動の子孫が受け継ぐ環境変数。値は親のスロットのパスの頭（`slot-<番号>`）
PARENT_ENV = "NDF_LAUNCH_SLOT_PARENT"


def weight_mib(kind: str) -> int:
    """起動の種類 `kind` の重み（MiB）。方針が無ければ `limits.LimitsError`。"""
    return int(limits.value(WEIGHT_POLICY + kind, "gib") * 1024)


def pool_lock_seconds() -> float:
    """取る判定の排他を待つ上限の秒。"""
    return limits.seconds(POOL_LOCK_POLICY)


def lock(path: Path) -> FileLock:
    # ソフトロックへ落とさない（カーネルのロックでないとオーナーの消滅で解けない）。ロックのファイルは消さない
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
    """番号 `index` のスロットのオーナーが生きているか（ロックを待たずに取れなければ真。取れたらすぐ返す）。"""
    probe = try_lock(slot_path(directory, index, ".lock"))
    if probe is None:
        return True
    probe.release()  # オーナーがいない
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


def read_field(path: Path, key: str) -> object:
    """記述のファイル（JSON のオブジェクト）の `key` の値。ファイルかキーが無ければ None。"""
    data = read_json(path)
    return None if data is None else data.get(key)


def parent_env(directory: Path, index: int) -> dict[str, str]:
    """番号 `index` のスロットを持つ起動の子孫へ渡す環境変数。"""
    return {PARENT_ENV: str(slot_path(directory, index))}


def parent_index(directory: Path, environ: Mapping[str, str]) -> int | None:
    """`environ` が受け継いだ親のスロットのうち、保存先 `directory` にあるものの番号。無い・別の保存先なら None。"""
    value = environ.get(PARENT_ENV)
    if not value:
        return None
    path = Path(value)
    try:
        same = path.parent.resolve() == directory.resolve()
    except OSError:  # noqa: NDF401 ファイル: パスを解けなければ親のスロットとして扱わない
        return None
    return slot_index(path) if same else None


def is_int(value: object) -> bool:
    """`value` が整数か（真偽値は整数に数えない）。"""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class Taken:
    """取ったスロット。`release` で記述を消してロックを返す。"""

    directory: Path
    index: int
    weight_mib: int
    _lock: FileLock

    def describe(self, owner: Mapping[str, object]) -> None:
        """オーナーの記述を書き直す。重みは残す。"""
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


def _live_owners(directory: Path) -> dict[int, dict[str, object]]:
    """生きたオーナーの番号と記述。記述が無い・書きかけなら空の記述とする。"""
    owners: dict[int, dict[str, object]] = {}
    for path in directory.glob(f"{SLOT_PREFIX}*.lock"):
        index = slot_index(path)
        if index is None or not is_owned(directory, index):
            continue
        owners[index] = read_json(slot_path(directory, index, ".json")) or {}  # noqa: NDF501 ファイル: 記述が無い・書きかけなら空の記述
    return owners


def _weight(description: Mapping[str, object]) -> int:
    """記述の重み（MiB）。重みが無い・整数でない（真偽値を含む）なら 0（書きかけ）。"""
    value = description.get(WEIGHT_KEY)
    return value if is_int(value) else 0


def _counted(owners: Mapping[int, Mapping[str, object]], absorbed: int | None) -> dict[int, Mapping[str, object]]:
    """取る判定で数えるオーナー。親のスロット `absorbed` と、親と同じ種類（`kind`）のほかのオーナーは数えない。

    子孫の重みは親の分を含み、ほかの同じ種類の起動（ほかの LLM の CLI）は配下のテストの重みで数えられる。
    親の分やほかの CLI の分で待つと、CLI どうしが互いの配下のテストを待ち合って止まる。
    """
    if absorbed not in owners:
        return dict(owners)
    kind = owners[absorbed].get("kind")
    return {
        index: description
        for index, description in owners.items()
        if index != absorbed and (kind is None or description.get("kind") != kind)
    }


def held_mib(directory: Path) -> int:
    """保存先の生きたオーナーの重みの合計（MiB）。"""
    return sum(_weight(description) for description in _live_owners(directory).values())


def take(
    directory: Path,
    capacity_mib: int,
    weight: int,
    owner: Mapping[str, object],
    *,
    absorbed: int | None = None,
    lock_seconds: float | None = None,
) -> Taken | None:
    """重み `weight`（MiB）のスロットを取る。生きたオーナーの重みと足して容量を超えれば None。

    オーナーが 1 つも無ければ容量を超える重みでも取る（重い起動 1 本が永く待たない）。`absorbed` は親のスロットの番号で、
    その重みと、親と同じ種類（`kind`）のほかのオーナーの重みは数えない（子孫の重みが親の分を含む。親やほかの CLI の
    持つ分で子孫が待たない）。「オーナーが無ければ取る」も数えるオーナーだけで見る。
    `lock_seconds` は排他を待つ上限の秒（0 なら待たない。省けば設定の上限）。
    排他を待ちきれないときは `filelock.Timeout`、保存先へ書けないときは `OSError` を送る。
    """
    with lock(directory / POOL_LOCK).acquire(timeout=pool_lock_seconds() if lock_seconds is None else lock_seconds):
        owners = _live_owners(directory)
        counted = _counted(owners, absorbed)
        if counted and sum(_weight(description) for description in counted.values()) + weight > capacity_mib:
            return None
        index, held = _lock_free_index(directory, set(owners))
        taken = Taken(directory, index, weight, held)
        try:
            taken.describe(owner)
        except BaseException:
            held.release()
            raise
        return taken


def _lock_free_index(directory: Path, alive: set[int]) -> tuple[int, FileLock]:
    """生きたオーナーの番号を飛ばし、0 から数えて最初にロックの取れた番号とそのロック。"""
    index = 0
    while True:
        held = None if index in alive else try_lock(slot_path(directory, index, ".lock"))
        if held is not None:
            return index, held
        index += 1
