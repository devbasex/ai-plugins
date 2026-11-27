"""モデルと effort の実験の記録の保存先（#2944）。ファイルの名前・月・置き換えの書き込みと、使われなくなったリポジトリの片付け。

記録は保存先の下のリポジトリごとのディレクトリ（`<repo>`）に置く。割り付けと自分のリポジトリの月の掃除は `model_choice` が行い、
ここは名前の規則と、保存先全体を月に 1 回片付ける処理（`sweep`）を持つ。片付けは、ほかのリポジトリのロックを待たない。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextlib import ExitStack, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from filelock import FileLock, Timeout

from ndf.contracts import facts

# 表 ndf-files の行（保存先は使用量ログと同じ）
LEDGER = "launch-experiments"
LEDGER_LOCK = "launch-experiments-lock"
WEIGHTS = "launch-weights"
WEIGHTS_LOCK = "launch-weights-lock"
PRUNED = "launch-pruned"
PRUNED_LOCK = "launch-pruned-lock"
SWEPT = "launch-swept"
SWEPT_LOCK = "launch-swept-lock"
# 月ごとのファイルの名前の、月の位置と、リポジトリごとのディレクトリの名前の位置
MONTH = "<YYYY-MM>"
REPO = "<repo>"
# リポジトリのディレクトリの名前（model_choice.repo_store）。ほかの名前のものとシンボリックリンクは片付けない
_REPO_DIR = re.compile(r"[0-9a-f]{16}")
_MONTH_FORM = re.compile(r"\d{4}-\d{2}")
# 片付けで待たずに取るロックと、役目を終えたディレクトリから消すファイル
_TIDY_LOCKS = (LEDGER_LOCK, WEIGHTS_LOCK, PRUNED_LOCK)
_TIDY_FILES = (WEIGHTS, PRUNED, *_TIDY_LOCKS)

log = logging.getLogger(__name__)


def month(at: datetime) -> str:
    return f"{at.astimezone(UTC):%Y-%m}"


def month_start(name: str, ahead: int = 0) -> datetime:
    year, mon = divmod(int(name[:4]) * 12 + int(name[5:]) - 1 + ahead, 12)
    return datetime(year, mon + 1, 1, tzinfo=UTC)


def store_file(root: Path, file_id: str) -> Path:
    """ディレクトリ `root`（リポジトリのディレクトリか保存先）の中のファイル。"""
    return root / Path(facts.value("ndf-files", file_id, "path")).name


def write_replacing(path: Path, text: str) -> None:
    """一時ファイルに書いてから置き換える。一時ファイルは作るときから所有者だけが読み書きできる（0600）。

    出現率のファイルはリポジトリの絶対パスを持つので、umask で広がった権限のファイルに置き換えない。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def done_in(done: Path, name: str) -> bool:
    """処理を終えた月を書くファイル `done`（`launch-pruned` か `launch-swept`）が、月 `name` を持つか。"""
    return done.is_file() and done.read_text(encoding="utf-8").strip() == name


def locked_now(lock: FileLock) -> bool:
    """錠を待たずに取る。ほかの起動が持っていれば偽。"""
    try:
        lock.acquire()
    except Timeout:
        log.info("ほかの起動が %s を持っているので、待たずに先へ進む", lock.lock_file)
        return False
    return True


def month_files(root: Path) -> dict[str, Path]:
    """リポジトリのディレクトリ `root` にある月のファイル（月 → パス）。"""
    head, tail = Path(facts.value("ndf-files", LEDGER, "path")).name.split(MONTH)
    found: dict[str, Path] = {}
    for path in root.glob(f"{head}*{tail}"):
        name = path.name[len(head) : len(path.name) - len(tail)]
        if _MONTH_FORM.fullmatch(name) and not path.is_symlink():
            found[name] = path
    return found


def drop_months(root: Path, edge: datetime) -> dict[str, Path]:
    """`root` の月のファイルのうち `edge` より前に終わった月を消し、残った月を返す。"""
    found = month_files(root)
    for name in [m for m in found if month_start(m, 1) <= edge]:
        found.pop(name).unlink(missing_ok=True)
    return found


def past(entry: Mapping[str, Any], now: datetime) -> bool:
    """出現率の項目が、計算した日時から項目の `window_days` を過ぎたか。"""
    return datetime.fromisoformat(entry["at"]) + timedelta(days=entry["allocation"]["window_days"]) < now


def prune_weights(root: Path, now: datetime) -> Optional[int]:
    """期限を過ぎた出現率の項目を消し、残りの数を返す（ファイルが無ければ 0、壊れていれば None）。出現率のロックの中で呼ぶ。"""
    path = store_file(root, WEIGHTS)
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        kept = [e for e in data["entries"] if not past(e, now)]
    except (ValueError, KeyError, TypeError):  # noqa: NDF401 ファイル: 壊れた出現率のファイルは出現率の計算が作り直す
        return None
    if len(kept) != len(data["entries"]):
        write_replacing(path, json.dumps({**data, "entries": kept}, ensure_ascii=False))
    return len(kept)


def sweep(base: Path, now: datetime, own: Path, edge: datetime) -> None:
    """保存先 `base` の自分（`own`）以外のリポジトリを `_tidy` で片付ける。錠を待たずに取れた起動が、月に 1 回（`launch-swept`）行う。

    `edge` はどの設定でも使わない古さの境。今月か先月に自分の月の掃除を終えたリポジトリは、`launch-pruned` を読むだけで飛ばす。
    自分の掃除は `edge` より新しい境で月を消し、出現率の項目も消すので、片付けで消すものが無いためである。1 つのリポジトリで
    失敗しても警告を出して残りを続け、今月を書く。失敗した分は翌月にやり直す。起動のたびに保存先全体を読み直さないためである。
    """
    done, name = store_file(base, SWEPT), month(now)
    if done_in(done, name):
        return
    lock = FileLock(str(store_file(base, SWEPT_LOCK)), timeout=0)
    if not locked_now(lock):
        return
    try:
        if done_in(done, name):
            return
        with os.scandir(base) as found:
            dirs = [Path(e.path) for e in found if _REPO_DIR.fullmatch(e.name) and e.is_dir(follow_symlinks=False)]
        recent = (name, month(month_start(name, -1)))
        for other in [d for d in dirs if d != own]:
            try:
                _tidy(other, now, edge, recent)
            except OSError as exc:
                log.warning("使われなくなったリポジトリの実験の記録を片付けられない（翌月にやり直す）: %s: %s", other, exc)
        write_replacing(done, name)
    finally:
        lock.release()


def _tidy(root: Path, now: datetime, edge: datetime, recent: tuple[str, ...]) -> None:
    """ほかのリポジトリ `root` から、`edge` より前に終わった月のファイルと、期限を過ぎた出現率の項目を消す。

    月のファイルは、もう追記されない古さなので、ロックを取らずに名前の月だけで消す。出現率の項目は、ロックを取ってから読んで消す。
    ロックはどれも待たずに取り、1 つでも使用中なら何もしない（使っているリポジトリは、自分の月の掃除で項目を消す）。月のファイルも
    出現率の項目も残らなければ役目を終えたとみなし、状態とロックのファイルとディレクトリを消す。ロックのファイルを消しても
    排他は分かれない。filelock は錠を取った後に、そのファイルが消されていれば（リンク数 0）取り直すためである。
    """
    pruned = store_file(root, PRUNED)
    if pruned.is_file() and pruned.read_text(encoding="utf-8").strip() in recent:
        return  # 今月か先月に自分の月の掃除を終えたリポジトリには、消すものが無い
    drop_months(root, edge)
    locks = [FileLock(str(store_file(root, name)), timeout=0) for name in _TIDY_LOCKS]
    with ExitStack() as held:
        for lock in locks:
            if not locked_now(lock):
                return
            held.callback(lock.release)
        if prune_weights(root, now) == 0 and not month_files(root):
            for name in _TIDY_FILES:
                store_file(root, name).unlink(missing_ok=True)
            with suppress(OSError):  # noqa: NDF402 ファイル: 片付けの知らない名前のファイルが残るディレクトリは消さない
                root.rmdir()
