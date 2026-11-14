"""送信待ちの送り手（#3345 の決定 93）。hook のエントリポイントが `outbox.kick` で切り離して起動する。

    python -P -m ndf.domains.hooks.sender <状態の置き場>

送信待ちのファイルを古い順に読み、Slack へ送る（`SLACK_BOT_TOKEN` と `SLACK_CHANNEL_ID` が無ければ送らずに消す）。
送れなかったファイルは残し、次の起動で送り直す。hook の経路では import しない（通信のアダプターを読むため）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

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


def drain(state_dir: Path, token: str, channel: str) -> int:
    """送信待ちを送り、送った（または送らずに消した）件数を返す。"""
    box = outbox.outbox_dir(state_dir)
    done = 0
    timeout = limits.seconds("notify.slack")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for path in sorted(box.glob("*.json")):  # noqa: NDF201 上限: 送信待ちは hook の起動 1 回につき 1 件までしか増えない
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


def main(argv: list[str]) -> int:
    state_dir = Path(argv[0])
    box = outbox.outbox_dir(state_dir)
    box.mkdir(parents=True, exist_ok=True)
    (box / outbox.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    try:
        drain(state_dir, os.environ.get("SLACK_BOT_TOKEN", ""), os.environ.get("SLACK_CHANNEL_ID", ""))
    finally:
        (box / outbox.PID_FILE).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
