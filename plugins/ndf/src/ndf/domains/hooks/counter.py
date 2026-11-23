"""セッションの数え手（#3345 の決定 92・IH5）。

セッションごとの状態のファイル 1 つ（`<状態の保存先>/hooks/sessions/<session_id>.json`）で、利用者の入力ごとの
Stop の変更要求の合計と、同じ拒否の回数と、guard が Tool の呼び出しごとに覚える値（`memo`。#3995）を持つ。数え直すのは利用者の入力だけで、中継が送った入力は数え直さない
（中継は送る前に `mark_relay` でマーカーを立てる）。

排他は `fcntl` で取り、待つのは `hook.counter-lock` の秒まで。取れない・ファイルが壊れているときは数えずに通す
（変更を要求せず、拒否は案内へ下げる）。
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
        path = tables.hooks_dir(state_dir) / "sessions" / f"{session_id}.json"
        limit = tables.policy_value("hook.counter-lock", "seconds") if wait is None else wait
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:  # 状態の保存先に書けなければ数えずに通す
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
        """今の入力の後の変更要求が上限の手前なら 1 つ数えて真。数えない数え手は偽（変更を要求しない）。"""
        if not self.usable or self.data["pushbacks"] >= tables.policy_value("hook.pushback", "attempts"):
            return False
        self.data["pushbacks"] += 1
        return True

    def pushback_left(self) -> bool:
        """今の入力の後の変更要求が上限の手前か。数えない（呼んでも回数は増えない）。数えない数え手は偽。"""
        return self.usable and self.data["pushbacks"] < tables.policy_value("hook.pushback", "attempts")

    def mark_model_wait(self) -> None:
        """今の入力の記録に、軽いモデルが待ちと判じたフラグを置く。数えない数え手は何もしない。

        `model_wait` は `_fresh` に足さない（足すとフラグを持たない今の状態のファイルを壊れたとして読む）。
        """
        if self.usable:
            self.data["model_wait"] = True

    def model_waited(self) -> bool:
        """今の入力で軽いモデルが待ちと判じたか。フラグが無い・数えない数え手は偽。"""
        return self.usable and self.data.get("model_wait") is True

    def allow_deny(self, key: str) -> bool:
        """同じ拒否のキーの回数が上限の手前なら 1 つ数えて真。数えない数え手は偽（案内へ下げる）。"""
        denies = self.data["denies"]
        if not self.usable or denies.get(key, 0) >= tables.policy_value("hook.deny-repeat", "attempts"):  # noqa: NDF502 ファイル: 初めてのキーは 0 回
            return False
        denies[key] = denies.get(key, 0) + 1  # noqa: NDF502 ファイル: 初めてのキーは 0 回
        return True

    def bump(self, key: str) -> int:
        """guard が数える事柄（同じ範囲を読んだ回数など）を 1 つ増やし、増やした後の回数。数えない数え手は 0。"""
        if not self.usable:
            return 0
        counts = self.data["counts"]
        counts[key] = counts.get(key, 0) + 1  # noqa: NDF502 ファイル: 初めてのキーは 0 回
        return counts[key]

    def remember(self, key: str, item: str, value: dict, keep: int) -> None:
        """`memo[key]` に今の入力の番号を添えて `item` の値を置く。入力の替わった値と、`keep` を超えた古い値を消す。

        `memo` は `_fresh` に足さない（足すと `memo` を持たない今の状態のファイルを壊れたとして読む）。数えない数え手は何もしない。
        """
        if not self.usable:
            return
        memo = self.data.get("memo")
        if not isinstance(memo, dict):
            memo = self.data["memo"] = {}
        table = memo.get(key)
        if not isinstance(table, dict):
            table = {}
        turn = self.data["turn"]
        kept = _this_turn(table, turn)
        kept.pop(item, None)
        kept[item] = {**value, "turn": turn}
        memo[key] = dict(list(kept.items())[-keep:])

    def take(self, key: str, item: str) -> Optional[dict]:
        """`memo[key]` の `item` の値を取り出して消す。無い・数えない数え手・形が違えば None。"""
        memo = self.data.get("memo")
        if not self.usable or not isinstance(memo, dict):
            return None
        table = memo.get(key)
        if not isinstance(table, dict):
            return None
        value = table.pop(item, None)
        if not isinstance(value, dict):
            return None
        return value

    def reset(self, source: str = "human") -> str:
        """入力を受けた。中継のマーカーがあるか中継の入力ならマーカーを消すだけ。人の入力なら数え直す。消したほうを返す。"""
        if source == "relay" or self.data["relay_mark"]:
            self.data["relay_mark"] = False
            return "relay"
        self.data.pop("model_wait", None)
        self.data.update(_fresh(), turn=self.data["turn"] + 1)
        return "human"

    def mark_relay(self) -> None:
        """中継が次の入力を送る前に立てるマーカー。"""
        self.data["relay_mark"] = True


def mark_relay(session_id: str, state_dir: Path) -> bool:
    """中継の側からマーカーを立てる。立てられたら真。"""
    with Counter.open(session_id, state_dir) as counter:
        if not counter.usable:
            return False
        counter.mark_relay()
        return True


def _this_turn(table: dict, turn: int) -> dict:
    """`table` のうち、今の入力の番号 `turn` を持つ値だけを残した写しを返す。"""
    return {k: v for k, v in table.items() if isinstance(v, dict) and v.get("turn") == turn}


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
