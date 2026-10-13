#!/usr/bin/env python3
"""SubagentStop の hook: 終わったサブエージェント 1 回を Claude の使用量の帳簿へ 1 行書く（#1953 の決定 4）。

入力は Claude Code の SubagentStop の JSON（`session_id`・`agent_id`・`agent_type`・`agent_transcript_path`・`cwd`）。
会話の記録の assistant の使用量を `message.id` ごとに 1 回足し、`source=subagent`・`kind=subagent` の行にする。
出力は無く、常に終了コード 0（hook の失敗で会話を止めない）。標準ライブラリだけで動く（hook は python3 を直に起動する）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))


def main() -> int:
    try:
        import usage_capture

        data = json.loads(sys.stdin.read() or "{}")
        usage_capture.record_subagent(data if isinstance(data, dict) else {})
    except Exception as e:  # noqa: BLE001  hook の失敗で会話を止めない
        print(f"usage-subagent: 記録できない: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
