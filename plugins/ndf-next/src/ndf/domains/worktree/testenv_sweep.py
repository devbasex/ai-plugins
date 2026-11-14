"""テスト環境の見回りと公開: 使われていない環境を止める（`reap`）・外部へ公開する（`expose`）・公開を閉じる（`unexpose`）。

- `reap`: 最後に使った時刻から `idle` 秒を過ぎた割り当ての環境を `compose stop` で止める（データは残す）。テストの実行中
  （目印を握っている）の環境は止めない
- `expose`: 宣言の `testenv.expose`（`enabled`・`public_tag`・`base_domain`・`open_command`）に従って、載っている基準が
  公開を許す基準と同じ環境だけを公開する。公開できるのは同時に 1 本（台帳の排他の中で判定する）
  開く手段が失敗したときも閉じる手段で閉じてから記録を閉じる（閉じられなければ公開中の記録を残す）
- `unexpose`: 宣言の `close_command` で閉じてから台帳を閉じる。閉じる手段が無い・失敗したら台帳を閉じない
  （口が開いたまま閉じたと記録すると、別の環境の公開とスロットの再利用が通る）
- `close_for_removal`: worktree を外す前に公開を閉じ、テスト環境を破棄する（`merged` と `cleanup` が使う）
"""

from __future__ import annotations

import calendar
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from filelock import FileLock, Timeout

from ndf.domains.worktree import ledger, outcomes, testenv
from ndf.domains.worktree.testenv import Target, TestenvError

# 公開の既定の期限
DEFAULT_TTL = "8h"
# 公開を開く・閉じる操作の排他（台帳の隣の置き場）と、その待ちの上限（秒。開く手段の所要を含む）
EXPOSE_LOCK = "expose.lock"
EXPOSE_LOCK_WAIT = 300


def _age(stamp: Any) -> Optional[float]:
    if not isinstance(stamp, str):
        return None
    try:
        return time.time() - calendar.timegm(time.strptime(stamp, ledger.STAMP))
    except ValueError:  # noqa: NDF401 ファイル: 台帳の時刻が読めない行は止めない側へ倒す
        return None


def reap(t: Target, idle: float) -> list[dict[str, Any]]:
    """使われていない環境を止め、1 件ごとの結果を返す。"""
    if not t.shell.has_docker():
        return []
    items = []
    for row in t.book.rows():
        age = _age(row.get("last_used_at"))
        if not ledger.active(row) or age is None or age < idle:
            continue
        items.append(_stop_idle(t, row))
    return items


def _stop_idle(t: Target, row: dict[str, Any]) -> dict[str, Any]:
    env = row["environment"]
    item = {"kind": "testenv", "name": env, "worktree": row["worktree"]}
    try:
        with FileLock(str(testenv.inuse_lock(t, env)), timeout=0):
            rc = testenv.compose_row(t, row, "stop")
    except Timeout:
        return {**item, "result": outcomes.KEPT, "reason": "テストの実行中"}
    return {
        **item,
        "result": outcomes.STOPPED if rc == 0 else outcomes.KEPT,
        **({} if rc == 0 else {"reason": f"止められない（終了コード {rc}）"}),
    }


def _open_row(rows: list[dict[str, Any]], worktree: str) -> Optional[dict[str, Any]]:
    """worktree の、開いている公開を持つ最後の割り当て（解放の後も閉じるまで残る）。"""
    return ledger.latest(rows, worktree, ledger.exposing)


def _config(t: Target) -> dict[str, Any]:
    conf = t.at("expose")
    if not isinstance(conf, dict) or conf.get("enabled") is not True:
        raise TestenvError("拒否: testenv.expose.enabled が有効でない")
    if not conf.get("public_tag") or not conf.get("base_domain"):
        raise TestenvError("拒否: testenv.expose.public_tag と base_domain が要る")
    if not conf.get("open_command") or not conf.get("close_command"):
        raise TestenvError("公開の手段が宣言されていない（testenv.expose.open_command と close_command が要る）")
    return conf


def _claim(t: Target, url: str, ttl: str) -> bool:
    """公開の記録を置く。ほかの worktree が公開中なら置かずに偽（同時に開けるのは 1 本）。"""
    with t.book.editing() as rows:
        if any(ledger.exposing(r) and r.get("worktree") != t.key for r in rows):
            return False
        row = ledger.latest(rows, t.key, ledger.active)
        if row is None:
            return False
        row["expose"] = {"url": url, "ttl": ttl, "opened_at": ledger.now(), "closed_at": None}
        return True


def _expose_values(url: str, host: str, row: dict[str, Any]) -> dict[str, str]:
    """公開の手段（宣言の `open_command`・`close_command`）へ渡す値。開く側と閉じる側が同じ名前で渡す。"""
    return {
        "NDF_EXPOSE_URL": url,
        "NDF_EXPOSE_HOST": host,
        "NDF_EXPOSE_ENVIRONMENT": str(row["environment"]),
        "NDF_EXPOSE_SLOT": str(row["slot"]),
    }


def _close(row: dict[str, Any]) -> None:
    row["expose"]["closed_at"] = ledger.now()


@contextmanager
def _exposing_lock(t: Target) -> Iterator[None]:
    """公開を開く・閉じる操作の排他（記録の取得から手段の完了まで。開く途中に閉じる側が記録を閉じないように）。"""
    t.book.path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(t.book.path.parent / EXPOSE_LOCK), timeout=EXPOSE_LOCK_WAIT):
            yield
    except Timeout as exc:
        raise TestenvError(f"拒否: 別の公開操作が {EXPOSE_LOCK_WAIT} 秒を過ぎても終わらない") from exc


def expose(t: Target) -> str:
    """公開して URL を返す。既に開いていればその URL。拒むときは `TestenvError`。"""
    with _exposing_lock(t):
        return _expose(t)


def _expose(t: Target) -> str:
    conf = _config(t)
    row = t.book.current(t.key)
    if row is None:
        raise TestenvError("拒否: 割り当てが無い")
    opened = _open_row(t.book.rows(), t.key)
    if opened is not None:
        return str(opened["expose"]["url"])
    if row.get("golden_tag") != conf["public_tag"]:
        raise TestenvError(f"拒否: 載っている基準（{row.get('golden_tag') or 'なし'}）が公開を許す基準（{conf['public_tag']}）と違う")
    host = f"wt{row['slot']}.{conf['base_domain']}"
    url = f"https://{host}"
    if not _claim(t, url, str(conf["ttl"]) if conf.get("ttl") else DEFAULT_TTL):
        raise TestenvError("拒否: 別のテスト環境が公開中（同時に開けるのは 1 本）")
    values = _expose_values(url, host, row)
    if t.shell.sh(str(conf["open_command"]), t.path, values).returncode != 0:
        # 開く手段が口を作った後で失敗したかもしれない。閉じる手段で閉じられたときだけ記録を閉じる
        try:
            _unexpose(t)
        except TestenvError as exc:
            raise TestenvError(f"公開の手段が失敗した。閉じたかを確かめられないため公開中の記録を残した: {exc}") from exc
        raise TestenvError("公開の手段が失敗した。閉じる手段で閉じて記録を戻した")
    return url


def unexpose(t: Target) -> Optional[str]:
    """公開を閉じ、閉じた URL を返す（開いていなければ `None`）。"""
    with _exposing_lock(t):
        return _unexpose(t)


def _unexpose(t: Target) -> Optional[str]:
    row = _open_row(t.book.rows(), t.key)
    if row is None:
        return None
    url = str(row["expose"]["url"])
    conf = t.at("expose")
    command = conf.get("close_command") if isinstance(conf, dict) else None
    if not command:
        raise TestenvError(
            f"公開を閉じる手段が宣言されていない（testenv.expose.close_command）。台帳は閉じていない: {row['environment']} {url}"
        )
    values = _expose_values(url, url.split("://", 1)[-1], row)
    where = t.path if t.path.is_dir() else t.main_dir
    if t.shell.sh(str(command), where, values).returncode != 0:
        raise TestenvError(f"公開を閉じる手段が失敗した。台帳は閉じていない: {row['environment']} {url}")
    if not t.book.update(t.key, _close, pick=ledger.exposing):
        raise TestenvError(f"台帳を閉じられない。unexpose を打ち直す: {url}")
    return url


def close_for_removal(path: Path) -> Optional[str]:
    """worktree を外す前に公開を閉じてテスト環境を破棄する（定義と閉じる手段は worktree にある）。できなければ理由を返す。"""
    t = testenv.target(path) if path.is_dir() else None
    if t is None:
        return None
    try:
        unexpose(t)
        testenv.down(t)
    except TestenvError as exc:
        return f"テスト環境を閉じられない: {exc}"
    return None
