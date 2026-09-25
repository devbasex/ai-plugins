"""drive_pause.py: 収束ループの駆動（drive.py）が止まるときの結果の形と終了コード。

`cross-review` と `cross-refactoring` の `scripts/drive.py` が読む。駆動は LLM の判断が要る
地点まで進んで止まり、`step_result` の形の 1 行の JSON を出す。止まった地点は `items[0]` の
`pause` が示し、本文ではなくファイルのパスを載せる。

    {"tool": "cross-review-drive", "status": "gate", "next": "fix", "summary": "...",
     "items": [{"pause": "fix", "prompt_file": "...", "result_file": "...", "round": 3}],
     "metrics": {...}}

| 終了コード | status | pause | 意味 | 起こす側がすること |
| --- | --- | --- | --- | --- |
| 0 | ok | — | 完了（報告は `items[0].report`） | 件数（metrics）を報告へ写す |
| 20 | gate | `fix` | 修正待ち | prompt_file を読んで直し、result_file を書かせて同じコマンドを打ち直す |
| 21 | gate | `sweep` | 最終スイープ待ち | 同上 |
| 22 | gate | `newtext` | 巻き直しの新しい title・body 待ち | 同上 |
| 23 | gate | `cross-review` | 最終ゲートの cross-review 待ち | 同上（`items[0].command` が cross-review の drive） |
| 1 | stopped | — | 中断（`metrics.exit` に元の終了コード） | 理由（summary）を報告する |

再開は同じコマンドを打ち直すだけである。進みは各 drive が一時ディレクトリの状態ファイルに持つ。
件数（metrics）の中身は drive ごとに決め、各 drive の docstring が並べる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import step_result as sr

PAUSE_CODES = {"fix": 20, "sweep": 21, "newtext": 22, "cross-review": 23}
MEANINGS = {"fix": "修正待ち", "sweep": "最終スイープ待ち", "newtext": "巻き直しの新しい title・body 待ち",
            "cross-review": "最終ゲートの cross-review 待ち"}
EXIT_DONE = 0
EXIT_STOPPED = 1


class Stop(Exception):
    """駆動を中断する。code は止まったスクリプトの元の終了コード（`metrics.exit` へ写す）。"""

    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def pause(tool: str, kind: str, prompt_file: Path | str, result_file: Path | str, round_: int,
          metrics: dict, **extra) -> dict:
    """止まる地点の結果を組む。extra は items[0] へ足す（例 `command`）。"""
    if kind not in PAUSE_CODES:
        raise ValueError(f"知らない pause: {kind}")
    item = {"pause": kind, "prompt_file": str(prompt_file), "result_file": str(result_file),
            "round": round_, **extra}
    return sr.result(tool, "gate", f"{MEANINGS[kind]}（round {round_}）。prompt_file の指示で result_file を書き、"
                     "同じコマンドを打ち直す", [item], metrics, next=kind)


def done(tool: str, summary: str, report: Path | str, metrics: dict) -> dict:
    return sr.result(tool, "ok", summary, [{"report": str(report)}], metrics)


def stopped(tool: str, msg: str, metrics: dict, code: int) -> dict:
    return sr.result(tool, "stopped", msg, [], {**metrics, "exit": code})


def exit_code(out: dict) -> int:
    """結果から終了コードを引く（表の通り）。"""
    if out.get("status") == "gate":
        return PAUSE_CODES[out.get("next")]
    return EXIT_DONE if out.get("status") == "ok" else EXIT_STOPPED


def main(tool: str, run: Callable[[], dict], counts: Callable[[], dict]) -> "NoReturn":  # noqa: F821
    """run を進め、結果を 1 行の JSON で出して表の終了コードで終える。"""
    try:
        out = run()
    except Stop as e:
        sr.emit(stopped(tool, str(e), counts(), e.code), EXIT_STOPPED)
    sr.emit(out, exit_code(out))
