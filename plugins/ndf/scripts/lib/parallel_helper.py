"""一時レーンの組み立て: 起こす入力（`helper_spawn`）・承認資料の場所（`present_approval`）・流すもの（`helper_task`）。`parallel_run` が使う。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import durable
import parallel_lane as pl
import parallel_release as pr
from parallel_files import RELAY

HERE = RELAY.parent
SUPERVISE = f"python3 {HERE / 'supervise.py'}"


def helper_spawn(name: str, n: int, k: int, pending: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """一時レーンを起こす入力（起動の耐久ステップが打つ）: `argv`（`relay.py` へ渡す引数の列）と `cwd`（打つ場所）。
    pending はループ名で、保留したループの 1 回の残りを流す一時レーン（異常系 11）。cwd は別のリポジトリのループのリポジトリで、
    一時レーンをそこで起こす（`loop pending` がそのループの記録を読む。#2733）。"""
    what = f"pending {pending}" if pending else f"{n} {k}"
    helper = pr.helper_name(name, n)
    # 終わりに自分のラッパーを止める（判断 fca6fe464619 の (a)）。止めないと対話の claude が入力待ちで残り、続きの一時レーンが断られる
    end = f"[ndf-parallel] helper-done {name} の行を出し、python3 {RELAY} stop {helper} で自分のラッパーを止めたとき"
    first = f"/goal /ndf:development-workflow --parallel-helper {name} {what} -- 終わり: {end}"
    return {"argv": ["spawn", "--name", helper, "--role", "helper", "--plan", name, "--json", "--", first], "cwd": cwd}


def spawn_line(spec: dict) -> str:
    """起動の入力（`helper_spawn` の値）をオーガナイザーが打つ 1 行にする。起動の印の無い古い記録の道（`metrics.spawn`）だけが使う。"""
    import shlex

    argv = list(spec["argv"])
    line = f"python3 {RELAY} {' '.join(argv[:-1])} '{argv[-1]}'"
    return f"cd {shlex.quote(spec['cwd'])} && {line}" if spec.get("cwd") else line


def present_approval(d: Path) -> str:
    """提示のプランの承認資料の絶対パス（ステップ `present` の `--approval` と `cwd` から読む）。読めなければ空。"""
    import re
    import shlex

    try:
        steps = json.loads((Path(d) / "present.json").read_text(encoding="utf-8")).get("steps") or []
    except (OSError, ValueError):
        return ""
    step = next((x for x in steps if isinstance(x, dict) and x.get("id") == "present"), {})
    m = re.search(r"--approval (\S+)", step.get("cmd") or "")
    if not m:
        return ""
    path = Path(shlex.split(m.group(1))[0])
    return shlex.quote(str(path if path.is_absolute() else Path(step.get("cwd") or ".") / path))


def helper_task(main: Path, name: str, n: int, k: int) -> dict:
    """一時レーン（`--parallel-helper <計画名> <連番> <提示>`）が流すもの。最後の起動の指示（イベント `pause` か、
    リリースの途中の知らせに添えた `release`）が同じ連番と提示の `confirmed`（`carried` を持たない）なら確定の後の 3 手、
    そうでなければ提示。

    - 提示（`phase: present`）: `pipeline` は提示のプラン → 本番のプランの queue と待ち。終われば提示のプランがリリース承認ゲート で
      止まったかを見て、止まっていれば `gate2`（リリース承認ゲート の依頼を送る）、流れ切れば `settle` を打つ
    - 確定の後（`phase: confirmed`）: `pipeline` は答えの記録 → 提示のプランを `approve` から → 本番のプランの queue と待ち。
      終われば `settle` を打つ。`approve` が承認ゲート（10）で終われば queue を打たずに問いとして送る"""
    ev = durable.event_from(pl.KIND, pl.record_identity(main, name), pl.workflow_id(name), "pause", 0, start=pl.record_start(main)) or {}
    launch = ev if ev.get("action") in ("release", "confirmed") else ev.get("release") or {}
    d = pr.release_dir(main, name, n, k)
    py = f"python3 {HERE / 'parallel.py'}"
    out = {"n": n, "k": k, "dir": str(d), "gate2": f"{py} request-gate2 {name} {n} {k} --report {present_approval(d)}"}
    # 出し直しを省いた確定（`carried`）は提示と同じ流し方にする。提示のプランが確かめ直して承認を書く（#3020 の決定 3）
    if launch.get("action") == "confirmed" and launch.get("n") == n and launch.get("k") == k and not launch.get("carried"):
        done = d / "release-done.json"
        gate = pr.record_answer_cmd(d, n, k)
        steps = [gate, f"{SUPERVISE} run {d / 'present.json'} --from approve", f"{SUPERVISE} queue {d / 'release.json'} --done {done}"]
        return {**out, "phase": "confirmed", "pipeline": " && ".join([*steps, f"{SUPERVISE} wait {done}"]),
                "settle": f"{py} settle {name} {n} --done {done}"}  # fmt: skip
    done = d / "done.json"
    return {**out, "phase": "present", "pipeline": f"{pr.pipeline_cmd(SUPERVISE, d)} && {SUPERVISE} wait {done}",
            "settle": f"{py} settle {name} {n} --done {done}"}  # fmt: skip
