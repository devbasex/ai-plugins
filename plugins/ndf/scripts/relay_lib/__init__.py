"""NDF のラッパー: カットポイントで claude を起動し直す（#895）。中身は #1142 の C6 でこのパッケージへ分けた。

副命令:

| 副命令 | 役割 |
| --- | --- |
| `run [claude の引数 ...]` | 端末の前景に常駐し、claude を擬似端末の子として起動する。合図を受けたら子へ `/exit` を入力し、プラグインを更新して次の区間を起動する。ラッパーが要らない起動は本物の claude をそのまま exec する（素通し） |
| `stop` | 動いているラッパーすべてに停止の合図を置く |
| `mark` | Stop hook の本体。最後の応答の `ndf-next` のブロックを合図 `next.json` へ写す |
| `install` / `uninstall` / `status` | `/ndf:install-wrapper` の本体。複製（ランチャーとバージョンディレクトリ）とラッパーの rc を `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/` に置き、シェルの設定へ読み込みの 1 行を足す・外す・状態を示す（#928） |
| `startup` | SessionStart hook の本体。在る複製を今の版で置き直し（版は後退させない）、10.17.4〜10.17.6 が自動で足した囲みを 1 度だけ知らせる。シェルの設定は書かない |
| `question open` / `question close` | `AskUserQuestion` の `PreToolUse` / `PostToolUse` hook の本体。質問の表示中の合図を作る・消す（関門を越えない守り） |
| `is-child` | ラッパーの直接の子の claude から呼ばれていれば 0 |
| `notice` | カットポイントの告知。1 行目に `relay` か `outside`（`is-child` と同じ判定）、2 行目に告知の 1 文を出す（#980）。外のときの 2 行目は理由（ラッパーが無い・終わっている・直接の子でない）で変わる（#1016） |

**標準ライブラリだけで書く**（バージョンディレクトリに入る `lib/clock.py`・`lib/jsonio.py` を除く。I2）。合図と作業ディレクトリの形
（`next.json` のキーと `NDF_RELAY_DIR` のファイル）は版をまたいで変えない。hook は区間ごとに新しい版で動き、
動いているラッパーは古い版のままでありうるためである。動いているラッパーは起動時にこのパッケージの全モジュールを
import するため、途中でバージョンディレクトリが替わっても別の版を読まない（決定 5）。

モジュール: `common`（定数と小さな関数）・`proc`（親のたどり）・`record`（`log.jsonl` と `next.json`）・
`mark`（hook の本体）・`claude`（本物の claude と会話の記録）・`terminal`（端末と子）・`run`（`Relay`）・
`shellrc`（シェルの設定の囲み）・`version_dir`（複製）・`install`（導入の副命令）。

規約は skills/development-workflow/references/relay.md にある。
"""
from __future__ import annotations

import sys

from . import install, mark, proc, run


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: relay.py run|stop|mark|install|uninstall|status|startup|question open|close|is-child|notice",
              file=sys.stderr)
        return 2
    sub, rest = argv[0], argv[1:]
    if sub == "mark":
        try:
            return mark.cmd_mark()
        except Exception:  # hook は Stop を止めない
            return 0
    commands = {
        "run": lambda: run.cmd_run(rest),
        "stop": mark.cmd_stop,
        "install": install.cmd_install,
        "uninstall": install.cmd_uninstall,
        "status": install.cmd_status,
        "startup": install.cmd_startup,
        "question": lambda: mark.cmd_question(rest[0] if rest else ""),
        # 文脈量の hook（token-guard.sh）が使う。ラッパーが動いていて、hook を呼んだ claude が
        # ラッパーの直接の子なら 0（親のたどりは mark と同じ。間の bash / sh は claude でないので飛ぶ）
        "is-child": lambda: 0 if proc.under_relay() else 1,
        "notice": mark.cmd_notice,
    }
    if sub in commands:
        return commands[sub]()
    print(f"relay.py: 未知の副命令 {sub}", file=sys.stderr)
    return 2
