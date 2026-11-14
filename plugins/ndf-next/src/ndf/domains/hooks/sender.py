"""送信待ちの送り手（#3345 の決定 93）。hook のエントリポイントが `outbox.kick` で切り離して起動する。

    python -P -m ndf.domains.hooks.sender <状態の置き場>

自分の宛先（`SLACK_BOT_TOKEN` と `SLACK_CHANNEL_ID`）の置き場の錠を取り、送信待ちのファイルを古い順に読んで
Slack へ送る（宛先が無ければ送らずに消す）。錠を取れなければ別の送り手が走っているので送らずに終わる。
錠を離した後に一覧を読み直し、まだ試していないファイルがあれば錠を取り直して送る（送っている間に届いた通知を
次の通知まで残さない）。送れなかったファイルは残し、次の起動で送り直す。hook の経路では import しない
（通信のアダプターを読むため）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional, Set

from ndf.adapters import notify
from ndf.contracts import limits
from ndf.domains.hooks import outbox

SLACK_URL = "https://slack.com/api/chat.postMessage"


def message(item: dict[str, Any]) -> str:
    """通知 1 件の本文。"""
    payload = item.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    where = payload.get("cwd") or "（不明）"
    return f"[{item.get('kind')}] {payload.get('text') or ''}\n{where}"  # noqa: NDF501 ファイル: 本文の無い通知は種類だけ


def drain(box: Path, token: str, channel: str, tried: Optional[Set[Path]] = None) -> int:
    """置き場の送信待ちを送り、送った（または送らずに消した）件数を返す。試したファイルを `tried` へ足す。"""
    tried = set() if tried is None else tried
    done = 0
    timeout = limits.seconds("notify.slack")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for path in sorted(box.glob("*.json")):  # noqa: NDF201 上限: 送信待ちは hook の起動 1 回につき 1 件までしか増えない
        if path in tried:
            continue
        tried.add(path)
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # noqa: S112 ファイル: 読めない通知は次の起動で読み直す
            continue
        if token and channel:
            sent = notify.post_json(SLACK_URL, {"channel": channel, "text": message(item)}, headers, timeout)
            if sent is None or not sent.get("ok"):
                continue
        path.unlink(missing_ok=True)
        done += 1
    return done


def _untried(box: Path, tried: Set[Path]) -> bool:
    return any(path not in tried for path in box.glob("*.json"))


def run(state_dir: Path, environ: Any) -> int:
    """錠を取って送り、離した後に残りがあれば取り直す。送った件数を返す。"""
    box = outbox.outbox_dir(state_dir, environ)
    token, channel = environ.get("SLACK_BOT_TOKEN", ""), environ.get("SLACK_CHANNEL_ID", "")  # noqa: NDF502 利用者の入力: 未設定は空
    tried: Set[Path] = set()
    done = 0
    while True:
        fd = outbox.try_lock(box)
        if fd is None:
            return done
        try:
            done += drain(box, token, channel, tried)
        finally:
            outbox.release(fd)
        if not _untried(box, tried):
            return done


def main(argv: list[str]) -> int:
    run(Path(argv[0]), os.environ)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
