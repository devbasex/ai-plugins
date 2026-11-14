"""レーンの conductor を起こす（#3097）: 起こすときの最初の入力を組み、名前つきのラッパー（`ndf relay spawn`）で起こす。

最初の入力はコマンドの並びで、起きた conductor がレーンの進行表を読んで行を実行中にする（E4）。

- 行を始める: `ndf progress new <スプリント名> --mode <モード> --issues <課題>... --parent <レーンの進行表の ID>` で
  スプリントの進行表を作り、返った ID で `ndf lanes row start <計画> <レーン> <行> --sprint <ID>` を打つ。
  `row start` は計画が記録した形の番号と自分のものを比べ、違えば `format_mismatch` で止まる（P3-1）
- 止めた計画で実行中のスプリントを中止する: `ndf progress cancel <スプリントの ID> --reason <理由>`

ラッパーの名前は `<計画>-<レーン>` に固定し、同じレーンを 2 本起こさない（ラッパーが動いていれば `spawn` が断る）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.contracts import result
from ndf.progress import command
from ndf.progress.lane import LaneRow

# 行の入力の args に --mode が無いときのモード
DEFAULT_MODE = "standard"
# レーンの conductor の役（ラッパーの登録簿の role）
ROLE = command.ACTOR
# 止めた計画で実行中のスプリントを中止する理由
STOP_REASON = "計画を止めた"


def wrapper_name(plan: str, lane: str) -> str:
    """レーンのラッパーの名前。"""
    return f"{plan}-{lane}"


def sprint_name(lane: str, row: str) -> str:
    return f"{lane}-{row}"


def start_commands(plan: str, lane: str, lane_table: str, row: LaneRow) -> list[dict[str, Any]]:
    """行を始めるコマンドの並び（`result.command` の形）。`--sprint` は打つ側が `new` の結果の ID で埋める。"""
    args = [str(a) for a in row.args]
    mode = [] if "--mode" in args else ["--mode", DEFAULT_MODE]
    issues = [str(i) for i in row.input["issues"]]
    new = result.command("progress", "new", sprint_name(lane, row.key), *mode, "--issues", *issues, "--parent", lane_table, *args)
    start = result.command("lanes", "row", "start", plan, lane, row.key, "--sprint", ask=("sprint",))
    return [new, start]


def cancel_commands(sprint_id: str) -> list[dict[str, Any]]:
    """止めた計画で実行中のスプリントを中止するコマンドの並び。"""
    return [result.command("progress", "cancel", sprint_id, "--reason", STOP_REASON)]


def prompt(plan: str, lane: str, commands: list[dict[str, Any]]) -> str:
    """conductor へ渡す最初の入力。コマンドの並びを JSON で持つ。"""
    body = json.dumps(commands, ensure_ascii=False)
    return f"計画 {plan} のレーン {lane} の conductor として、次のコマンドを順に打つ（ask の鍵は前の結果で埋める）: {body}"


def spawn(plan: str, lane: str, cwd: Path, first: str, run: Optional[Any] = None) -> dict[str, Any]:
    """名前つきのラッパーでレーンの conductor を起こす。結果 JSON の `status` が ok なら `ok` を真にする。"""
    argv = [
        str(identity.entrypoint(identity.ROOT)),
        "relay",
        "spawn",
        "--name",
        wrapper_name(plan, lane),
        "--cwd",
        str(cwd),
        "--role",
        ROLE,
        "--plan",
        plan,
        first,
    ]
    runner = run if run is not None else subprocess.run
    try:
        done = runner(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        return {"ok": False, "detail": f"ラッパーを起こせない: {exc}"}
    return read_spawn(done.stdout, done.stderr, done.returncode)


def read_spawn(stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
    """ラッパーの出力を読む。標準出力の末尾の行の結果 JSON の `status` が ok なら `ok` を真にする。

    結果 JSON が読めなければ、詳細は標準エラーの末尾（無ければ終了コード）にする。
    """
    lines = stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1]) if lines else {}
    except ValueError:
        out = {}
    ok = out.get("status") == result.OK
    return {"ok": ok, "detail": out.get("summary") if out else (stderr.strip()[-300:] or f"終了コード {returncode}")}
