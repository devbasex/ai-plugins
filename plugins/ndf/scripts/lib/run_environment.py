"""実行環境の調査: 動く場所・CPU・メモリ・レーンとプランの数を読み、同時の数の候補を返す（#2505 の決定 9・10）。

読むだけの部品で、ファイルへ書かない（宣言へ書くのは人で、Pull Request として承認ゲートを通る。C7）。`loop start` と
`parallel.py start` のたびに通り、読めない項目は `unreadable` に載せて進む（開発ループを止めない）。候補は 8 コア・30 GiB で
プラン 6 本（2026-09-25 の実測）の比から導く。人の読める量は式に入らない。
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Callable, Optional

import clock
import procs
from pace import PaceError, read_loop

BASE_CORES, BASE_GIB, BASE_PLANS = 8, 30, 6  # 実測の起点
CONTAINER_CGROUP = re.compile(r"docker|kubepods|containerd|lxc")
PARALLEL_PLANS = Path(".ndf") / "parallel"


def place(proc_cgroup: Path = procs.PROC_SELF_CGROUP, dockerenv: Path = Path("/.dockerenv")) -> str:
    """`factory`（実行ジョブの中）・`container`・`host` のどれか。"""
    if os.environ.get("NDF_FACTORY_RUN"):
        return "factory"
    if dockerenv.exists():
        return "container"
    return "container" if CONTAINER_CGROUP.search(proc_cgroup.read_text(encoding="utf-8")) else "host"


def cpus(cgroup: Optional[Path] = None) -> float:
    """使える CPU。割り当て（`sched_getaffinity`）と cgroup の `cpu.max` の小さい方。"""
    # os.cpu_count はマシン全体の数でコンテナの制限を見ない。process_cpu_count は Python 3.13 以降
    have = float(len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count() or 0)
    try:
        quota, period = (Path(cgroup or procs.cgroup_dir()) / "cpu.max").read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return have  # cgroup が無い環境
    return have if quota == "max" else min(have, int(quota) / int(period))


def memory_gib(cgroup: Optional[Path] = None) -> float:
    """使えるメモリ（GiB）。マシンの合計と cgroup の `memory.max` の小さい方。"""
    total = procs.memory_available()[1]
    limit = procs.cgroup_memory(cgroup).limit
    return round((min(total, limit) if limit else total) / 2**30, 1)


def running_lanes(main: Path) -> int:
    """動いている並列の計画のレーンの数の和。無ければ 1。"""
    root = Path(main) / PARALLEL_PLANS
    names = sorted(p.name for p in root.iterdir() if (p / "plan.json").is_file()) if root.is_dir() else []
    if not names:
        return 1
    import parallel_lane as pl  # 計画があるときだけ読む
    import parallel_plan as pp

    return max(1, sum(len(pp.load_valid_plan(main, n).lanes) for n in names if pl.run_status(main, n) in pl.RUNNING))


def running_plans() -> int:
    """動いているプランの数（`supervise.py queue` のプロセスの数）。"""
    return procs.count_cmdline("supervise.py", "queue")


def candidates(cores: float, gib: float, lanes: int) -> dict:
    """実測の 1 点から比で導く候補。ラッパーの数は今の既定の比（3 / 6）を保ち、課題の数は 1 周回が課題ごとに 1 本のプランを
    同時に流すためレーンの数で割る。"""
    plans = max(1, min(math.floor(cores * BASE_PLANS / BASE_CORES), math.floor(round(gib) * BASE_PLANS / BASE_GIB)))
    return {"max_issues": max(1, plans // max(1, lanes)), "max_plans": plans, "max_named": max(1, plans // 2)}


def current_values(root: Path) -> dict:
    """今の値。読めなければ既定。"""
    import parallel_plan as pp

    try:
        issues = read_loop(root)["max_issues"]
    except PaceError:
        issues = 3
    return {"max_issues": issues, "max_plans": pp.max_plans()[0], "max_named": pp.named_limit()[0]}


def survey(root: Path, lanes: Optional[int] = None) -> dict:
    """実行環境の報告を返す。`lanes` は開始する計画のレーンの数（まだ動いていないため計画の値を渡す）。"""
    unreadable: list[str] = []

    def read(name: str, fn: Callable, fallback=None):
        try:
            return fn()
        except Exception:  # 調査の失敗で開発ループを止めない（I8）。失敗は報告の unreadable に残す
            unreadable.append(name)
            return fallback

    where = read("place", place, "unknown")
    cores = read("cpus", cpus)
    gib = read("memory", memory_gib)
    found = lanes if lanes is not None else read("lanes", lambda: running_lanes(root), 1)
    running = read("plans", running_plans)
    current = read("current", lambda: current_values(root), {"max_issues": 3, "max_plans": BASE_PLANS, "max_named": 3})
    if cores and gib:
        got = candidates(cores, gib, found)
        reason = (
            f"{cores:g} コア・{gib:g} GiB からプラン {got['max_plans']} 本。レーン {found} 本で割って課題 {got['max_issues']}。"
            "承認で読める量は別に見る"
        )
    else:
        got, reason = dict(current), "CPU かメモリを読めないため今の値を候補にした"
    return {
        "at": clock.now_iso("utc"),
        "place": where,
        "cpus": cores,
        "memory_gib": gib,
        "lanes": found,
        "plans": running,
        "candidates": got,
        "current": current,
        "reason": reason,
        "unreadable": unreadable,
    }


def differs(report: dict) -> bool:
    return report["candidates"] != report["current"]


def lines(report: dict) -> list[str]:
    """候補と今の値が違うときの 1 行。"""
    if not differs(report):
        return []
    a, b = report["candidates"], report["current"]
    return [
        f"実行環境の候補: 課題 {a['max_issues']}・プラン {a['max_plans']}・ラッパー {a['max_named']}"
        f"（今の値: {b['max_issues']}・{b['max_plans']}・{b['max_named']}。{report['reason']}）。"
        "変えるなら .ndf/pace.json の loop.max_issues と環境変数を人が直す"
    ]
