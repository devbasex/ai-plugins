"""drive_pause.py: 収束ループの駆動（drive.py）が止まるときの結果の形と終了コード。

`cross-review` と `cross-refactoring` の `scripts/drive.py` が読む。駆動は LLM の判断が要る
地点まで進んで止まり、`step_result` の形の 1 行の JSON を出す。止まった地点は `items[0]` の
`pause` が示し、本文ではなくファイルのパスを載せる。

    {"tool": "cross-review-drive", "status": "gate", "next": "fix", "summary": "...",
     "items": [{"pause": "fix", "prompt_file": "...", "result_file": "...", "round": 3, "cwd": "..."}],
     "metrics": {...}}

| 終了コード | status | pause | 意味 | 起こす側がすること |
| --- | --- | --- | --- | --- |
| 0 | ok | — | 完了（報告は `items[0].report`） | 件数（metrics）を報告へ写す |
| 20 | gate | `fix` | 修正待ち | prompt_file を読んで直し、result_file を書かせて同じコマンドを打ち直す |
| 21 | gate | `sweep` | 最終スイープ待ち | 同上 |
| 22 | gate | `newtext` | 巻き直しの新しい title・body 待ち | 同上 |
| 23 | gate | `cross-review` | 最終ゲートの cross-review 待ち | 同上（`items[0].command` が cross-review の drive） |
| 1 | stopped | — | 中断（`metrics.exit` に元の終了コード） | 理由（summary）を報告する |
| 1 | stopped | — | 収束ループがレビューの成立しないまま終わった（`metrics.final` が `error`。報告は `items[0].report`・`gaps` 無し） | 理由と件数を報告し、後続へ進まない |
| 6 | stopped | — | 最後まで回ったが収束しなかった（`metrics.final` が `max_rounds`・`oscillation`。報告は `items[0].report`・`gaps` に `kind: review`） | 理由と件数を報告する。プランは `on_exit` の `"6"` で行き先を分けられる（無ければ `on_fail`） |
| 6 | stopped | — | 時間の枠に収まらずリファクタリングを飛ばした（`metrics.skipped` に種類と理由。`gaps` に `kind: refactor`。#2047） | 同上 |

`items[0]` の項目: `pause`・`prompt_file`・`result_file`・`round` は必ずある。任意の項目は次の通り。

| 項目 | 意味 |
| --- | --- |
| `cwd` | その pause の作業ディレクトリ。起こす側は worker の作業場所（指示の「作業場所:」と起動の cwd）にこれを使う |
| `command` | 先に打つ駆動のコマンド（pause `cross-review`） |

再開は同じコマンドを打ち直すだけである。進みは各 drive が一時ディレクトリの状態ファイルに持つ。
件数（metrics）の中身は drive ごとに決め、各 drive の docstring が並べる。

ステップの延長（#2047 の決定 4）: drive のステップを流すエンジンは、環境変数 `NDF_STEP_EXTEND_FILE` に延長のファイルの
パスを渡す。駆動とその子は、この試行の開始からの所要の見込み（秒）を `{"seconds": <秒>, "reason": "<1 行>"}` で書き、
エンジンは締め切りを後ろへだけずらす。書くのは `write_extension`、読むのは `read_extension` だけである。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Optional

import step_result as sr

PAUSE_CODES = {"fix": 20, "sweep": 21, "newtext": 22, "cross-review": 23}
MEANINGS = {
    "fix": "修正待ち",
    "sweep": "最終スイープ待ち",
    "newtext": "巻き直しの新しい title・body 待ち",
    "cross-review": "最終ゲートの cross-review 待ち",
}
EXIT_DONE = 0
EXIT_STOPPED = 1
EXTEND_ENV = "NDF_STEP_EXTEND_FILE"


class Stop(Exception):
    """駆動を中断する。code は止まったスクリプトの元の終了コード（`metrics.exit` へ写す）。"""

    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


def pause(tool: str, kind: str, prompt_file: Path | str, result_file: Path | str, round_: int, metrics: dict, **extra) -> dict:
    """止まる地点の結果を組む。extra は items[0] へ足す（例 `cwd`・`command`）。"""
    if kind not in PAUSE_CODES:
        raise ValueError(f"知らない pause: {kind}")
    item = {"pause": kind, "prompt_file": str(prompt_file), "result_file": str(result_file), "round": round_, **extra}
    return sr.result(
        tool,
        "gate",
        f"{MEANINGS[kind]}（round {round_}）。prompt_file の指示で result_file を書き、同じコマンドを打ち直す",
        [item],
        metrics,
        next=kind,
    )


def done(tool: str, summary: str, report: Path | str, metrics: dict) -> dict:
    return sr.result(tool, "ok", summary, [{"report": str(report)}], metrics)


UNCONVERGED_FINALS = ("max_rounds", "oscillation")


def unconverged(tool: str, summary: str, report: Path | str, metrics: dict) -> dict:
    """最後まで回ったが収束しなかった結果。報告と件数は `done` と同じ形で持ち、status は ok にしない（#2143）。
    `final` が max_rounds・oscillation なら欠けの残る結果として `gaps` と終了コード 6 で返す（#2227 の決定 1）。
    それ以外（`error`: 席を選べない・修正のコード関連 CI 失敗など、レビューが成立していない）は終了コード 1 の中断で返し、
    プランの `on_exit` の `"6"` で後続へ進ませない。"""
    if metrics.get("final") not in UNCONVERGED_FINALS:
        return sr.result(tool, "stopped", summary, [{"report": str(report)}], {**metrics, "exit": EXIT_STOPPED})
    gap = {"kind": "review", "name": "converge", "reason": f"final={metrics.get('final')}"}
    return sr.result(tool, "stopped", summary, [{"report": str(report)}], {**metrics, "exit": sr.EXIT_INCOMPLETE}, gaps=[gap])


def incomplete(tool: str, summary: str, metrics: dict, gap: dict) -> dict:
    """欠けを 1 件持つ中断の結果（終了コード 6）。プランは `on_exit` の `"6"` で後続へ進められる（#2047 の I5）。"""
    return sr.result(tool, "stopped", summary, [], metrics, gaps=[gap])


def write_extension(seconds: float, reason: str) -> bool:
    """延長のファイルへ見込みの秒を書く。`NDF_STEP_EXTEND_FILE` が無い（エンジンの外で打った）か書けなければ False。
    今のファイルの秒より小さければ書き換えない（締め切りは前へずらさない。#2047 の I7）。"""
    target = os.environ.get(EXTEND_ENV)
    if not target:
        return False
    got = read_extension(target)
    if got is not None and got[0] >= float(seconds):
        return True
    try:
        Path(target).write_text(json.dumps({"seconds": int(seconds), "reason": str(reason)}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    return True


def read_extension(path: Path | str) -> Optional[tuple[float, str]]:
    """延長のファイルの (秒, 理由)。無い・読めない・秒が正の数でなければ None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    seconds = data.get("seconds") if isinstance(data, dict) else None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    return float(seconds), str(data.get("reason") or "")


def stopped(tool: str, msg: str, metrics: dict, code: int) -> dict:
    return sr.result(tool, "stopped", msg, [], {**metrics, "exit": code})


def exit_code(out: dict) -> int:
    """結果から終了コードを引く（表の通り）。"""
    if out.get("status") == "gate":
        return PAUSE_CODES[out.get("next")]
    if out.get("status") == "ok":
        return EXIT_DONE
    return sr.EXIT_INCOMPLETE if out.get("gaps") else EXIT_STOPPED


def main(tool: str, run: Callable[[], dict], counts: Callable[[], dict]) -> "NoReturn":  # noqa: F821
    """run を進め、結果を 1 行の JSON で出して表の終了コードで終える。"""
    try:
        out = run()
    except Stop as e:
        sr.emit(stopped(tool, str(e), counts(), e.code), EXIT_STOPPED)
    sr.emit(out, exit_code(out))
