"""レーンの conductor を起こす（#3097）: 起こすときの最初の入力を組み、名前つきのラッパー（`ndf relay spawn`）で起こす。

最初の入力はコマンドの並びで、起きた conductor がレーンの進行表を読んで行を実行中にする（E4）。

- 行を始める: `ndf progress new <スプリント名> --mode <モード> --issues <課題>... --parent <レーンの進行表の ID>` で
  スプリントの進行表を作り、返った ID で `ndf lanes row start <計画> <レーン> <行> --sprint <ID>` を打つ。
  `row start` は計画が記録した形の番号と自分のものを比べ、違えば `format_mismatch` で止まる（P3-1）。
  スプリント名は `<計画>-<レーン>-<行>-<要約>` にし、別の計画が同じレーン名を使っても、名前の区切りが曖昧な組
  （計画 `dev-api`／レーン `tests` と計画 `dev`／レーン `api-tests` など）でも重ならない
- 起こせるのはスプリントの行（`kind: sprint`）だけで、開発ループの行（`kind: loop`）は起こす入力を組めないので
  `Unsupported` で断る（ndf-next に開発ループを始めるコマンドがまだ無い）
- 答えの後の next を打つ: `ndf lanes row resumed <スプリントの ID>` で答えの後の next の印を書いてから、
  `ndf progress next <スプリントの ID>` で承認ゲートの続きを流す（3346-5）
- 止めた計画で実行中のスプリントを中止する: `ndf progress cancel <スプリントの ID> --reason <理由>` の後に
  `ndf lanes row end <スプリントの ID> --outcome cancelled` で行を中止にする（`progress cancel` はレーンの行を書かない）

ラッパーの名前は `<計画>-<レーン>-<要約>` に固定し、同じレーンを 2 本起こさない（ラッパーが動いていれば `spawn` が断る）。
要約はリポジトリ・計画・レーンの 3 つから作る。ラッパーの登録簿は利用者単位で共通なので、別のリポジトリが同じ
計画名・レーン名を使っても、名前の区切りが曖昧な組（`a-b` と `a` / `b-c` など）でも重ならない。
ラッパーは子の conductor が終わるまで動き続けるので、起こす前に（行を始めるときも中止のときも）同じ名前の
ラッパーを `ndf relay stop` で止め、止まったのを確かめてから起こす（`stop`）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.contracts import result
from ndf.progress import command
from ndf.progress.lane import CANCELLED, LaneRow

# 行の入力の args に --mode が無いときのモード
DEFAULT_MODE = "standard"
# レーンの conductor の役（ラッパーの登録簿の role）
ROLE = command.ACTOR
# 止めた計画で実行中のスプリントを中止する理由
STOP_REASON = "計画を止めた"
# ラッパーの名前の長さの上限（中継の名前の形 `relay.signals.NAME_RE`）と、末尾に付ける要約の桁数
WRAPPER_NAME_MAX = 64
WRAPPER_DIGEST_LENGTH = 12


def _digest(*parts: str) -> str:
    """区切りが曖昧にならないよう、値の並びを JSON にしてから要約する。"""
    return hashlib.sha256(json.dumps(list(parts)).encode()).hexdigest()[:WRAPPER_DIGEST_LENGTH]


def wrapper_name(repo: str, plan: str, lane: str) -> str:
    """レーンのラッパーの名前。読める部分（`<計画>-<レーン>`）に、リポジトリ・計画・レーンの要約を付ける。"""
    digest = _digest(repo, plan, lane)
    readable = f"{plan}-{lane}"[: WRAPPER_NAME_MAX - WRAPPER_DIGEST_LENGTH - 1]
    return f"{readable}-{digest}"


class Unsupported(Exception):
    """起こす入力を組めない行（開発ループの行など）。打ち直しても起こせない。"""


def sprint_name(plan: str, lane: str, row: str) -> str:
    """行のスプリント名。読める部分（`<計画>-<レーン>-<行>`）に計画・レーン・行の要約を付け、区切りが曖昧な組でも重ならない。"""
    return f"{plan}-{lane}-{row}-{_digest(plan, lane, row)}"


def start_commands(plan: str, lane: str, lane_table: str, row: LaneRow) -> list[dict[str, Any]]:
    """行を始めるコマンドの並び（`result.command` の形）。`--sprint` は打つ側が `new` の結果の ID で埋める。

    スプリントの行でなければ `Unsupported`。
    """
    if row.input.get("kind") != "sprint":
        raise Unsupported(f"行 {lane}/{row.key} は {row.input.get('kind')} の行で、起こす入力を組めない（起こせるのは sprint の行だけ）")
    args = [str(a) for a in row.args]
    mode = [] if "--mode" in args else ["--mode", DEFAULT_MODE]
    issues = [str(i) for i in row.input["issues"]]
    name = sprint_name(plan, lane, row.key)
    new = result.command("progress", "new", name, *mode, "--issues", *issues, "--parent", lane_table, *args)
    start = result.command("lanes", "row", "start", plan, lane, row.key, "--sprint", ask=("sprint",))
    return [new, start]


def cancel_commands(sprint_id: str) -> list[dict[str, Any]]:
    """止めた計画で実行中のスプリントを中止し、レーンの行を中止にするコマンドの並び。"""
    cancel = result.command("progress", "cancel", sprint_id, "--reason", STOP_REASON)
    end = result.command("lanes", "row", "end", sprint_id, "--outcome", CANCELLED)
    return [cancel, end]


def resume_commands(sprint_id: str) -> list[dict[str, Any]]:
    """答えが載った行で、答えの後の next の印を書いてから next を打つコマンドの並び。"""
    mark = result.command("lanes", "row", "resumed", sprint_id)
    nxt = result.command("progress", "next", sprint_id)
    return [mark, nxt]


def prompt(plan: str, lane: str, commands: list[dict[str, Any]]) -> str:
    """conductor へ渡す最初の入力。コマンドの並びを JSON で持つ。"""
    body = json.dumps(commands, ensure_ascii=False)
    return f"計画 {plan} のレーン {lane} の conductor として、次のコマンドを順に打つ（ask の鍵は前の結果で埋める）: {body}"


def spawn(repo: str, plan: str, lane: str, cwd: Path, first: str, run: Optional[Any] = None) -> dict[str, Any]:
    """名前つきのラッパーでレーンの conductor を起こす。結果 JSON の `status` が ok なら `ok` を真にする。"""
    argv = [
        str(identity.entrypoint(identity.ROOT)),
        "relay",
        "spawn",
        "--name",
        wrapper_name(repo, plan, lane),
        "--cwd",
        str(cwd),
        "--role",
        ROLE,
        "--plan",
        plan,
        first,
    ]
    return _relay(argv, run, "ラッパーを起こせない")


def stop(repo: str, plan: str, lane: str, run: Optional[Any] = None) -> dict[str, Any]:
    """レーンのラッパーを止め、止まったのを確かめる（`ndf relay stop` は錠が空くまで待つ）。動いていなければ ok。"""
    argv = [str(identity.entrypoint(identity.ROOT)), "relay", "stop", wrapper_name(repo, plan, lane)]
    return _relay(argv, run, "ラッパーを止められない")


def _relay(argv: list[str], run: Optional[Any], failure: str) -> dict[str, Any]:
    runner = run if run is not None else subprocess.run
    try:
        done = runner(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        return {"ok": False, "detail": f"{failure}: {exc}"}
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
