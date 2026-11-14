"""セッションの数え手（#3345 の決定 92・IH5）。

セッションごとの状態のファイル 1 つ（`<状態の置き場>/hooks/sessions/<session_id>.json`）で、利用者の入力ごとの
Stop の差し戻しの合計と、同じ拒否の回数を持つ。数え直すのは利用者の入力だけで、中継が送った入力は数え直さない
（中継は送る前に `mark_relay` で印を立てる）。

排他は `fcntl` で取り、待つのは `hook.counter-lock` の秒まで。取れない・ファイルが壊れているときは数えずに通す
（差し戻さず、拒否は案内へ下げる）。
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from ndf.domains.hooks import tables
from ndf.domains.relay import signals

SESSION_RE = re.compile(r"^[A-Za-z0-9-]{1,128}$")


def _fresh() -> dict[str, Any]:
    return {"turn": 0, "pushbacks": 0, "denies": {}, "counts": {}, "relay_mark": False}


class Counter:
    """開いている間は排他を持つ。`close` で書いて離す。`usable` が偽なら数えない。"""

    def __init__(self, path: Optional[Path], data: dict[str, Any], fd: Optional[int]) -> None:
        self.path = path
        self.data = data
        self._fd = fd

    @property
    def usable(self) -> bool:
        return self._fd is not None

    @classmethod
    def unusable(cls) -> "Counter":
        """数えない数え手（排他も状態のファイルも持たない）。"""
        return cls(None, _fresh(), None)

    @classmethod
    def open(cls, session_id: object, state_dir: Path, wait: Optional[float] = None) -> "Counter":
        """セッションの数え手を開く。識別子が形に合わない・排他を取れないときは数えない数え手を返す。"""
        if not isinstance(session_id, str) or not SESSION_RE.match(session_id):
            return cls.unusable()
        path = state_dir / "hooks" / "sessions" / f"{session_id}.json"
        limit = tables.policy_value("hook.counter-lock", "seconds") if wait is None else wait
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:  # 状態の置き場に書けなければ数えずに通す
            return cls.unusable()
        if not signals.lock(fd, limit):
            os.close(fd)
            return cls.unusable()
        data = _read(fd)
        if data is None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            return cls.unusable()
        return cls(path, data, fd)

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.ftruncate(self._fd, 0)
            os.write(self._fd, json.dumps(self.data, ensure_ascii=False).encode("utf-8"))
        finally:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "Counter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def allow_pushback(self) -> bool:
        """今の入力の後の差し戻しが上限の手前なら 1 つ数えて真。数えない数え手は偽（差し戻さない）。"""
        if not self.usable or self.data["pushbacks"] >= tables.policy_value("hook.pushback", "attempts"):
            return False
        self.data["pushbacks"] += 1
        return True

    def allow_deny(self, key: str) -> bool:
        """同じ拒否の鍵の回数が上限の手前なら 1 つ数えて真。数えない数え手は偽（案内へ下げる）。"""
        denies = self.data["denies"]
        if not self.usable or denies.get(key, 0) >= tables.policy_value("hook.deny-repeat", "attempts"):  # noqa: NDF502 ファイル: 初めての鍵は 0 回
            return False
        denies[key] = denies.get(key, 0) + 1  # noqa: NDF502 ファイル: 初めての鍵は 0 回
        return True

    def bump(self, key: str) -> int:
        """guard が数える事柄（同じ範囲を読んだ回数など）を 1 つ増やし、増やした後の回数。数えない数え手は 0。"""
        if not self.usable:
            return 0
        counts = self.data["counts"]
        counts[key] = counts.get(key, 0) + 1  # noqa: NDF502 ファイル: 初めての鍵は 0 回
        return counts[key]

    def reset(self, source: str = "human") -> str:
        """入力を受けた。中継の印があるか中継の入力なら印を消すだけ。人の入力なら数え直す。消したほうを返す。"""
        if source == "relay" or self.data["relay_mark"]:
            self.data["relay_mark"] = False
            return "relay"
        self.data.update(_fresh(), turn=self.data["turn"] + 1)
        return "human"

    def mark_relay(self) -> None:
        """中継が次の入力を送る前に立てる印。"""
        self.data["relay_mark"] = True


def mark_relay(session_id: str, state_dir: Path) -> bool:
    """中継の側から印を立てる。立てられたら真。"""
    with Counter.open(session_id, state_dir) as counter:
        if not counter.usable:
            return False
        counter.mark_relay()
        return True


def _read(fd: int) -> Optional[dict[str, Any]]:
    raw = b""
    while chunk := os.read(fd, 65536):
        raw += chunk
    if not raw.strip():
        return _fresh()
    try:
        data = json.loads(raw)
    except ValueError:  # noqa: NDF401 ファイル: 壊れた状態のファイルは数えずに通す
        return None
    if not isinstance(data, dict) or set(_fresh()) - set(data):
        return None
    return data
