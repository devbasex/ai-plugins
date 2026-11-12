"""`supervise.py advance`: 判断の要らない NDF の手順を続けて打つ（手順の連続実行。#2909 の F5）。

    supervise.py advance --step "<コマンド>"... [--from <番号>]

- 手順は渡した順に 1 つずつ打ち、前の手順の結果 JSON の `status` が `ok` のときだけ次を打つ（I3）。`gate` か
  `stopped` で止まり、止まった理由と続きのコマンド（`--from`）を `next` に載せる
- 打てるのは NDF のプラグインの `scripts/` と `skills/*/scripts/` のスクリプトだけで、シェルを通さない（I4）。
  先頭はスクリプトのパスか、`python3`・`bash` などの後にそのパス。中継の副命令（`RELAYS`。`--` の後のコマンドを
  そのまま走らせるもの）と `advance` 自身は打たない。外のコマンドか中継の副命令が 1 つでもあれば、何も打たずに 2 で止まる
- 長く待つ手順（`supervise.py wait`・`merged-steps.py merge-when-green`）を含む並びは、呼ぶ側が背景で打つ。
  `advance` は自分の上限の時間を持たない
- 工程の起動（`supervise.py queue`・`run`）を `--step` に入れた呼び出しは、Bash の hook（`hook_lib/shell_checks.starts_plan`）が
  工程の起動として判定する（I9）

終了コード: すべて ok = 0 / 子が gate = 子の終了コード（10〜29）/ 子が stopped か結果 JSON を返さない = 1 /
外のコマンド・中継の副命令・`--from` の誤り = 2。
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import step_read
import step_result

TOOL = "advance"
ROOT_MARKER = Path("scripts") / "supervise.py"  # NDF のプラグインの根の目印（このファイルの入口）
# `--` の後のコマンドをそのまま走らせる副命令（プラグインの scripts/ からのパス, 副命令）。
# `argparse.REMAINDER` を持つ副命令はすべてここに載せる（テストで縛る）
RELAYS = frozenset({("lib/gh_parts.py", "body-lock"), ("lib/post_queue.py", "retry"), ("supervise.py", "advance")})
INTERPRETERS = {"python3": "python", "python": "python", "bash": "bash", "sh": "bash"}
SKIPPED = "飛ばした（--from）"


class StepRefused(ValueError):
    """打てない手順（NDF のスクリプトの外・中継の副命令・語に割れない）。"""


def _ndf_plugin_root(path: Path) -> Path | None:
    """パスを含む NDF のプラグインの根（`scripts/supervise.py` と `skills/` を持つディレクトリ）。無ければ None。"""
    for d in path.parents:
        if (d / ROOT_MARKER).is_file() and (d / "skills").is_dir():
            return d
    return None


def _script_rel(path: Path) -> str | None:
    """NDF のスクリプトなら、プラグインの `scripts/` か `skills/<名>/scripts/` からの相対パス。外なら None。"""
    root = _ndf_plugin_root(path)
    if root is None:
        return None
    parts = path.relative_to(root).parts
    if len(parts) >= 2 and parts[0] == "scripts":
        return "/".join(parts[1:])
    if len(parts) >= 4 and parts[0] == "skills" and parts[2] == "scripts":
        return "/".join(parts)
    return None


def _subcommand(args: list[str]) -> str | None:
    return next((a for a in args if not a.startswith("-")), None)


def plan_step(step: str, cwd: Path) -> list[str]:
    """手順の文字列を、シェルを通さずに打つ argv にする。打てなければ StepRefused。"""
    try:
        words = shlex.split(step)
    except ValueError as e:
        raise StepRefused(f"語に割れない: {e}") from None
    if not words:
        raise StepRefused("空の手順")
    kind = INTERPRETERS.get(Path(words[0]).name)
    if kind:
        words = words[1:]
        while words and words[0].startswith("-"):  # python3 -u など
            words = words[1:]
    if not words:
        raise StepRefused("スクリプトのパスが無い")
    path = (cwd / words[0]).resolve()
    rel = _script_rel(path) if path.is_file() else None
    if rel is None:
        raise StepRefused(f"NDF のスクリプトでない: {words[0]}")
    if (rel, _subcommand(words[1:])) in RELAYS:
        raise StepRefused(f"中継の副命令は打たない: {rel} {_subcommand(words[1:])}")
    kind = kind or ("python" if path.suffix == ".py" else "bash")
    head = [sys.executable] if kind == "python" else ["bash"]
    return [*head, str(path), *words[1:]]


def _continue_cmd(steps: list[str], start: int) -> str:
    args = " ".join(f"--step {shlex.quote(s)}" for s in steps)
    return f"supervise.py advance {args} --from {start}"


def _advance_stopped(summary: str, code: int, gaps: list[dict], process: list[dict], metrics: dict, next_: str | None) -> tuple[dict, int]:
    return step_result.result(TOOL, "stopped", summary, [], metrics, next=next_, gaps=gaps, process=process), code


def cmd_advance(steps: list[str], start: int = 1, cwd: str | None = None) -> tuple[dict, int]:
    """手順を順に打ち、`(結果, 終了コード)` を返す。"""
    here = Path(cwd or ".").resolve()
    metrics = {"steps": len(steps), "ran": 0, "stopped_at": None}
    if not steps or not 1 <= start <= len(steps):
        why = "--step を 1 つ以上渡す" if not steps else f"--from は 1〜{len(steps)}: {start}"
        return _advance_stopped(
            why, step_result.EXIT_UNREADABLE, [{"kind": "argument", "name": "--from", "reason": why}], [], metrics, None
        )
    argvs, refused = [], []
    for i, s in enumerate(steps, 1):  # 打つ前にすべて確かめる（I4）
        try:
            argvs.append(plan_step(s, here))
        except StepRefused as e:
            refused.append({"kind": "step", "name": f"{i}: {s}", "reason": str(e)})
    if refused:
        why = f"打てない手順が {len(refused)} 件。何も打たなかった"
        next_ = "NDF のスクリプトの外のコマンドと中継の副命令を --step から外し、それらは別に打つ"
        return _advance_stopped(why, step_result.EXIT_UNREADABLE, refused, [], metrics, next_)
    process = [{"run": s, "result": SKIPPED} for s in steps[: start - 1]]
    for i in range(start, len(steps) + 1):
        p = subprocess.run(argvs[i - 1], cwd=here, stdout=subprocess.PIPE, text=True)
        child = step_read.read_completed(p, tool=Path(argvs[i - 1][1]).name)
        metrics["ran"] += 1
        process.append(
            {"run": steps[i - 1], "result": child["summary"] or child["status"], "status": child["status"], "code": p.returncode}
        )
        if child["status"] == "ok":
            continue
        metrics["stopped_at"] = i
        why = child.get("next") or child["summary"]
        if child["status"] == "gate":
            next_ = f"{why}。続き: {_continue_cmd(steps, i + 1)}" if i < len(steps) else why
            return step_result.result(TOOL, "gate", f"手順 {i} が判断を返した", [], metrics, next=next_, process=process), p.returncode
        gaps = child.get("gaps") or [{"kind": "step", "name": f"{i}: {steps[i - 1]}", "reason": why}]
        return _advance_stopped(
            f"手順 {i} が止まった", step_result.EXIT_VIOLATION, gaps, process, metrics, f"{why}。打ち直し: {_continue_cmd(steps, i)}"
        )
    return step_result.result(TOOL, "ok", f"手順 {len(steps)} 件を打った（打った {metrics['ran']}）", [], metrics, process=process), 0
