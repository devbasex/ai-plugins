"""並列の本数と並行度の測定（`parallel-measure.py`、#621）。

入力のファイル（`--meminfo`・`--cgroup-dir`・`--psi`・`--vmstat`・`--input`）を差し替えて値を固定する。
**このホストの `/proc/meminfo` も cgroup も PSI も読まない。** 読むと、走らせた時刻の空きで
期待値が動く。

`concurrency` の `gh` は `PATH` の先頭へ置いた偽物に差し替え、受けた引数を記録する
（読み取りだけを行うことのチェック、AC42）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "parallel-measure.py"


def _load_module():
    """`--cgroup-dir` の既定の解決だけは関数を直に呼んでチェックする。

    ファイル名に `-` を含むため `import` できない。`__main__` の分岐は走らない。
    """
    spec = importlib.util.spec_from_file_location("parallel_measure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parallel_measure = _load_module()


# --- 入力の組み立て ----------------------------------------------------------


def meminfo(tmp_path: Path, *, available_mib: int, swap_total_mib: int, swap_free_mib: int, name: str = "meminfo") -> Path:
    """`/proc/meminfo` の体裁の入力。値は kB で書く。"""
    path = tmp_path / name
    path.write_text(
        "MemTotal:       21000000 kB\n"
        f"MemAvailable:   {available_mib * 1024} kB\n"
        f"SwapTotal:      {swap_total_mib * 1024} kB\n"
        f"SwapFree:       {swap_free_mib * 1024} kB\n",
        encoding="utf-8",
    )
    return path


def cgroup(
    tmp_path: Path,
    *,
    oom_kill: int | None = None,
    memory_max: str | None = None,
    memory_current: int | None = None,
    anon_mib: int | None = None,
    name: str = "cgroup",
) -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    if oom_kill is not None:
        # 実物は `max 0` の行も持つ。`oom_kill` だけを読むことのチェックでもある。
        (path / "memory.events").write_text(f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n", encoding="utf-8")
    if memory_max is not None:
        (path / "memory.max").write_text(f"{memory_max}\n", encoding="utf-8")
    if memory_current is not None:
        (path / "memory.current").write_text(f"{memory_current}\n", encoding="utf-8")
    if anon_mib is not None:
        # 実物は `anon` の前後に `file` などの行を持つ。
        (path / "memory.stat").write_text(f"anon {anon_mib * 1024 * 1024}\nfile 4096\nkernel 0\n", encoding="utf-8")
    return path


def psi(tmp_path: Path, avg10: str, *, name: str = "psi") -> Path:
    """`/proc/pressure/memory` の体裁の入力。"""
    path = tmp_path / name
    path.write_text(
        f"some avg10={avg10} avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=1\n",
        encoding="utf-8",
    )
    return path


def vmstat(tmp_path: Path, pages: int, *, name: str) -> Path:
    """`/proc/vmstat` の体裁の入力。`pswpin + pswpout` が `pages` になる。"""
    path = tmp_path / name
    path.write_text(f"nr_free_pages 1\npswpin {pages // 2}\npswpout {pages - pages // 2}\npgfault 9\n", encoding="utf-8")
    return path


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def keys(out: str) -> dict[str, str]:
    values = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def capacity(
    tmp_path: Path,
    *args: str,
    available_mib: int = 9742,
    swap_total_mib: int = 2047,
    swap_free_mib: int = 310,
    oom_kill: int | None = 1,
    memory_max: str | None = "max",
    memory_current: int | None = 0,
    anon_mib: int | None = 2193,
    psi_avg10: str | None = "0.00",
    swap_io: tuple[int, int] | None = None,
) -> subprocess.CompletedProcess:
    """圧の元（PSI・vmstat）も必ず差し替える。`psi_avg10=None` は PSI を読めない入力、
    `swap_io=(1 回目, 2 回目)` は vmstat の 2 回の読み取り（間隔 0）である。"""
    mem = meminfo(tmp_path, available_mib=available_mib, swap_total_mib=swap_total_mib, swap_free_mib=swap_free_mib)
    cg = cgroup(tmp_path, oom_kill=oom_kill, memory_max=memory_max, memory_current=memory_current, anon_mib=anon_mib)
    pressure = ["--psi", str(psi(tmp_path, psi_avg10) if psi_avg10 is not None else tmp_path / "no-psi")]
    if swap_io is None:
        pressure += ["--vmstat", str(tmp_path / "no-vmstat")]
    else:
        pressure += [
            "--vmstat",
            str(vmstat(tmp_path, swap_io[0], name="vmstat-before")),
            "--vmstat-after",
            str(vmstat(tmp_path, swap_io[1], name="vmstat-after")),
            "--sample-seconds",
            "0",
        ]
    return run("capacity", "--meminfo", str(mem), "--cgroup-dir", str(cg), *pressure, *args)


# --- capacity: 空きと 1 本の重さから本数を出す（grow） ------------------------


def test_capacity_reports_the_measured_values(tmp_path: Path) -> None:
    """2026-09-17 のこのホストの値での出力（契約の文書の例）。既存の 10 キーが先に、新しい 5 キーが末尾に並ぶ。"""
    proc = capacity(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert list(keys(proc.stdout).items()) == [
        ("mem_available_mib", "9742"),
        ("swap_total_mib", "2047"),
        ("swap_free_mib", "310"),
        ("cgroup_available_mib", "max"),
        ("oom_kill", "1"),
        ("oom_kill_increased", "unknown"),
        ("running", "0"),
        ("by_memory", "5"),  # ⌊(9742 − 1024) ÷ 1536⌋
        ("allowed", "2"),
        ("limited_by", "memory,max_add"),
        ("cgroup_anon_mib", "2193"),
        ("per_lane_observed_mib", "unknown"),
        ("per_lane_used_mib", "1536"),
        ("verdict", "grow"),
        ("pressure", "psi:0.00"),
    ]


# 依頼（#780）の試算の 3 行。起点 2193、担当 1 本が 1.2GiB を使ったと仮定した見直しの列。
TRIAL_ROWS = [
    # running, anon, 空き, observed, used, allowed, limited_by
    ("0", 2193, 5793, "unknown", "1536", "2", "memory,max_add"),
    ("2", 4650, 3336, "1229", "1843", "3", "memory"),
    ("3", 5880, 2106, "1229", "1844", "3", "memory"),
]


@pytest.mark.parametrize("running,anon,available,observed,used,allowed,limited_by", TRIAL_ROWS)
def test_capacity_follows_the_trial_rows(
    tmp_path: Path, running: str, anon: int, available: int, observed: str, used: str, allowed: str, limited_by: str
) -> None:
    """PSI 0.00・SwapFree 0 でも swap_low が付かず、開始は 2 本、見直しで 3 本まで伸びる。"""
    proc = capacity(
        tmp_path,
        "--running",
        running,
        "--anon-baseline-mib",
        "2193",
        available_mib=available,
        swap_free_mib=0,
        anon_mib=anon,
    )
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["per_lane_observed_mib"] == observed
    assert values["per_lane_used_mib"] == used
    assert values["allowed"] == allowed
    assert values["limited_by"] == limited_by
    assert values["verdict"] == "grow"


@pytest.mark.parametrize("swap_total_mib,swap_free_mib", [(2047, 0), (2047, 2047), (0, 0)])
def test_capacity_ignores_the_free_swap(tmp_path: Path, swap_total_mib: int, swap_free_mib: int) -> None:
    """スワップの残量は過去の履歴で、いまの圧ではない。どの値でも本数を変えない。"""
    proc = capacity(tmp_path, swap_total_mib=swap_total_mib, swap_free_mib=swap_free_mib)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["allowed"] == "2"
    assert values["limited_by"] == "memory,max_add"
    assert "swap_low" not in proc.stdout


def test_capacity_has_no_total_cap_without_max(tmp_path: Path) -> None:
    """空き 12GiB・3 本・1 本の重さ 1843 なら 5 本。足す数は --max-add が抑える。"""
    proc = capacity(tmp_path, "--running", "3", "--anon-baseline-mib", "2000", available_mib=12288, anon_mib=2000 + 3686)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["per_lane_used_mib"] == "1843"
    assert values["by_memory"] == "9"  # 3 + ⌊(12288 − 1024) ÷ 1843⌋
    assert values["allowed"] == "5"
    assert values["limited_by"] == "memory,max_add"


def test_capacity_caps_the_total_only_when_max_is_given(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "3", "--max", "4", available_mib=12288)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["allowed"] == "4"
    assert values["limited_by"] == "memory,max_add,max"


def test_capacity_does_not_mark_max_add_when_it_equals_the_addable(tmp_path: Path) -> None:
    """足せる数と --max-add が等しいときは、--max-add を上げても増えないため max_add を付けない。"""
    proc = capacity(tmp_path, available_mib=1024 + 1536 * 2)
    values = keys(proc.stdout)
    assert values["allowed"] == "2"
    assert values["limited_by"] == "memory"


def test_capacity_never_goes_below_one_lane(tmp_path: Path) -> None:
    """空きが 1 本分に足りなくても 1 を下回らない。0 本では進行が止まる。"""
    proc = capacity(tmp_path, available_mib=2000)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["by_memory"] == "0"
    assert values["allowed"] == "1"
    assert values["limited_by"] == "memory,floor"


def test_capacity_accepts_zero_max_but_floors_allowed_at_one(tmp_path: Path) -> None:
    """上限 0 も受理するが、shrink でなければ allowed は 1 を下回らない。"""
    proc = capacity(tmp_path, "--max", "0")
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["allowed"] == "1"
    assert values["limited_by"] == "memory,max_add,max,floor"


def test_capacity_floors_zero_max_add_at_one(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--max-add", "0")
    values = keys(proc.stdout)
    assert values["allowed"] == "1"
    assert values["limited_by"] == "memory,max_add,floor"


@pytest.mark.parametrize(
    "args,key,expected",
    [
        (("--reserve-mib", "0"), "by_memory", "6"),  # ⌊9742 ÷ 1536⌋
        (("--per-lane-mib", "1024"), "by_memory", "8"),  # ⌊(9742 − 1024) ÷ 1024⌋
        (("--max-add", "4"), "allowed", "4"),
        (("--running", "1", "--anon-baseline-mib", "2093", "--per-lane-min-mib", "100"), "per_lane_used_mib", "150"),
        (("--running", "1", "--anon-baseline-mib", "2093", "--peak-factor", "8"), "per_lane_used_mib", "800"),
        (("--psi-some-max", "0"), "verdict", "hold"),
    ],
)
def test_capacity_takes_the_defaults_from_the_arguments(tmp_path: Path, args: tuple[str, ...], key: str, expected: str) -> None:
    """初期値はスクリプトの定数が持ち、引数で上書きできる。"""
    proc = capacity(tmp_path, *args, psi_avg10="0.01")
    assert proc.returncode == 0, proc.stderr
    assert keys(proc.stdout)[key] == expected


# --- capacity: 1 本の重さ ---------------------------------------------------


def test_capacity_floors_the_lane_weight_at_the_minimum(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "1", "--anon-baseline-mib", "2093")
    values = keys(proc.stdout)
    assert values["per_lane_observed_mib"] == "100"
    assert values["per_lane_used_mib"] == "512"  # 100 × 1.5 は下限を割る


def test_capacity_treats_a_negative_growth_as_zero(tmp_path: Path) -> None:
    """起点の後に同じコンテナの他のセッションが終わると、差が負になる。"""
    proc = capacity(tmp_path, "--running", "1", "--anon-baseline-mib", "3000", anon_mib=1000)
    values = keys(proc.stdout)
    assert values["per_lane_observed_mib"] == "0"
    assert values["per_lane_used_mib"] == "512"


@pytest.mark.parametrize(
    "args,anon_mib",
    [
        (("--running", "2"), 4650),  # 起点が無い（既存の実行計画）
        (("--running", "0", "--anon-baseline-mib", "2193"), 4650),  # 0 本
        (("--running", "2", "--anon-baseline-mib", "2193"), None),  # anon を読めない
    ],
)
def test_capacity_uses_the_estimate_when_the_weight_cannot_be_measured(tmp_path: Path, args: tuple[str, ...], anon_mib: int | None) -> None:
    proc = capacity(tmp_path, *args, anon_mib=anon_mib)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_anon_mib"] == ("unknown" if anon_mib is None else str(anon_mib))
    assert values["per_lane_observed_mib"] == "unknown"
    assert values["per_lane_used_mib"] == "1536"


@pytest.mark.parametrize("option", ["--per-lane-mib", "--per-lane-min-mib", "--peak-factor"])
def test_capacity_rejects_a_zero_lane_weight(tmp_path: Path, option: str) -> None:
    """1 本の重さが 0 になる引数は割れないため、引数の誤り（終了コード 2）にする。"""
    proc = capacity(tmp_path, option, "0")
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""
    assert option in proc.stderr


# --- capacity: いまの圧（hold） ---------------------------------------------


@pytest.mark.parametrize("running,allowed,limited_by", [("0", "1", "hold_psi,floor"), ("2", "2", "hold_psi")])
def test_capacity_holds_when_psi_exceeds_the_threshold(tmp_path: Path, running: str, allowed: str, limited_by: str) -> None:
    """圧があれば今の本数を超えて足さない。減らすのは shrink だけである。"""
    proc = capacity(tmp_path, "--running", running, psi_avg10="10.01")
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["verdict"] == "hold"
    assert values["allowed"] == allowed
    assert values["limited_by"] == limited_by
    assert values["pressure"] == "psi:10.01"


def test_capacity_does_not_hold_when_psi_equals_the_threshold(tmp_path: Path) -> None:
    proc = capacity(tmp_path, psi_avg10="10.00")
    assert keys(proc.stdout)["verdict"] == "grow"


def test_capacity_holds_when_swap_io_exceeds_the_threshold_without_psi(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "2", psi_avg10=None, swap_io=(1000, 1513))
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["verdict"] == "hold"
    assert values["allowed"] == "2"
    assert values["limited_by"] == "hold_swap_io"
    assert values["pressure"] == "swap_io:513.0"


def test_capacity_does_not_hold_when_swap_io_equals_the_threshold(tmp_path: Path) -> None:
    proc = capacity(tmp_path, psi_avg10=None, swap_io=(1000, 1512))
    values = keys(proc.stdout)
    assert values["verdict"] == "grow"
    assert values["pressure"] == "swap_io:512.0"


def test_capacity_divides_the_swap_io_by_the_interval(tmp_path: Path) -> None:
    """60 ページを 0.1 秒で割ると毎秒 600 ページで、閾値を超える。"""
    proc = capacity(tmp_path, "--sample-seconds", "0.1", psi_avg10=None, swap_io=(1000, 1060))
    values = keys(proc.stdout)
    assert values["verdict"] == "hold"
    assert values["pressure"] == "swap_io:600.0"


def test_capacity_grows_when_the_pressure_is_unknown(tmp_path: Path) -> None:
    """PSI も vmstat も読めなければ圧は判定できないが、測定は拒否しない。"""
    proc = capacity(tmp_path, psi_avg10=None)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["verdict"] == "grow"
    assert values["pressure"] == "unknown"
    assert values["allowed"] == "2"


def test_capacity_ignores_the_retired_swap_free_option(tmp_path: Path) -> None:
    """廃止した --swap-free-min-pct は値を問わず受け、標準エラーへ 1 行知らせて無視する。"""
    plain = capacity(tmp_path, swap_free_mib=0)
    for value in ("25", "not-a-number"):
        proc = capacity(tmp_path, "--swap-free-min-pct", value, swap_free_mib=0)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == plain.stdout
        assert len(proc.stderr.strip().splitlines()) == 1
        assert "--swap-free-min-pct" in proc.stderr


# --- capacity: cgroup の残り（AC31 / AC33） ---------------------------------


def test_capacity_uses_the_cgroup_limit_when_it_is_smaller(tmp_path: Path) -> None:
    """cgroup に上限があるホストでは、VM の空きではなく cgroup の残りが決める。"""
    proc = capacity(tmp_path, memory_max=str(8 * 1024 * 1024 * 1024), memory_current=4 * 1024 * 1024 * 1024)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "4096"
    assert values["by_memory"] == "2"  # ⌊(4096 − 1024) ÷ 1536⌋


def test_capacity_ignores_the_cgroup_limit_when_it_is_max(tmp_path: Path) -> None:
    proc = capacity(tmp_path, memory_max="max", memory_current=0)
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "max"
    assert values["by_memory"] == "5"


def test_capacity_continues_when_the_cgroup_files_are_absent(tmp_path: Path) -> None:
    proc = capacity(tmp_path, memory_max=None, memory_current=None, anon_mib=None)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "unknown"
    assert values["cgroup_anon_mib"] == "unknown"
    assert values["by_memory"] == "5"


def test_capacity_continues_when_memory_max_is_not_numeric(tmp_path: Path) -> None:
    """memory.max が数値でないときは unknown として継続する。"""
    proc = capacity(tmp_path, memory_max="not-a-number")
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "unknown"
    assert values["oom_kill"] == "1"
    assert values["allowed"] == "2"
    assert values["limited_by"] == "memory,max_add"


def test_capacity_continues_when_memory_current_is_not_numeric(tmp_path: Path) -> None:
    """memory.current が数値でないときは unknown として継続する。"""
    cg = cgroup(tmp_path, oom_kill=1, memory_max=str(8 * 1024 * 1024 * 1024))
    (cg / "memory.current").write_text("not-a-number\n", encoding="utf-8")
    proc = capacity(tmp_path, memory_max=None, memory_current=None)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "unknown"
    assert values["oom_kill"] == "1"
    assert values["allowed"] == "2"


def test_capacity_floors_the_cgroup_remainder_at_zero(tmp_path: Path) -> None:
    """使用量が上限を超えている（負の残り）ときも 0 として扱い、落ちない。"""
    proc = capacity(tmp_path, memory_max=str(1024 * 1024 * 1024), memory_current=2 * 1024 * 1024 * 1024)
    assert proc.returncode == 0
    assert keys(proc.stdout)["cgroup_available_mib"] == "0"


# --- capacity: `--cgroup-dir` の既定の解決 -----------------------------------
# 自分の cgroup を測るための解決である。ホストでは `/sys/fs/cgroup` が kernel の根で、
# `memory.events` を持たない（`CFTYPE_NOT_ON_ROOT`）ため `/proc/self/cgroup` へ回る。
# コンテナの中では `/sys/fs/cgroup` がすでに自分の cgroup なのでそのまま使う。


def proc_cgroup(tmp_path: Path, path: str, *, name: str = "proc-cgroup") -> Path:
    """`/proc/self/cgroup`（cgroup v2）の体裁の入力。"""
    target = tmp_path / name
    target.write_text(f"0::{path}\n", encoding="utf-8")
    return target


def test_resolve_cgroup_dir_returns_the_given_directory(tmp_path: Path) -> None:
    """`--cgroup-dir` が渡されたら、どちらの入力も読まない。"""
    given = tmp_path / "given"
    assert (
        parallel_measure.resolve_cgroup_dir(
            str(given),
            root=cgroup(tmp_path, oom_kill=1, name="root"),
            proc_cgroup=proc_cgroup(tmp_path, "/user.slice/session.scope"),
        )
        == given
    )


def test_resolve_cgroup_dir_uses_the_root_when_it_holds_memory_events(tmp_path: Path) -> None:
    """`memory.events` がある `/sys/fs/cgroup` は、すでに自分の cgroup である。"""
    root = cgroup(tmp_path, oom_kill=1, name="root")
    assert (
        parallel_measure.resolve_cgroup_dir(
            None,
            root=root,
            proc_cgroup=proc_cgroup(tmp_path, "/user.slice/session.scope"),
        )
        == root
    )


def test_resolve_cgroup_dir_derives_from_proc_when_the_root_has_no_events(tmp_path: Path) -> None:
    """kernel の根には `memory.events` が無い。`0::<path>` の下を測る。"""
    root = tmp_path / "root"
    leaf = cgroup(root, oom_kill=2, name="user.slice")
    assert (
        parallel_measure.resolve_cgroup_dir(
            None,
            root=root,
            proc_cgroup=proc_cgroup(tmp_path, "/user.slice"),
        )
        == leaf
    )


def test_resolve_cgroup_dir_uses_the_root_when_proc_says_the_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert (
        parallel_measure.resolve_cgroup_dir(
            None,
            root=root,
            proc_cgroup=proc_cgroup(tmp_path, "/"),
        )
        == root
    )


def test_resolve_cgroup_dir_uses_the_root_when_proc_is_unreadable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert (
        parallel_measure.resolve_cgroup_dir(
            None,
            root=root,
            proc_cgroup=tmp_path / "none",
        )
        == root
    )


# --- capacity: 測れない環境（AC32） -----------------------------------------


def test_capacity_exits_three_when_meminfo_is_absent(tmp_path: Path) -> None:
    proc = run("capacity", "--meminfo", str(tmp_path / "no-such-file"), "--cgroup-dir", str(cgroup(tmp_path, oom_kill=1)))
    assert proc.returncode == 3
    assert "allowed=" not in proc.stdout
    assert proc.stdout.strip() == ""
    assert len(proc.stderr.strip().splitlines()) == 1


def test_capacity_exits_three_when_mem_available_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "partial"
    path.write_text("MemTotal: 21000000 kB\n", encoding="utf-8")
    proc = run("capacity", "--meminfo", str(path), "--cgroup-dir", str(cgroup(tmp_path, oom_kill=1)))
    assert proc.returncode == 3
    assert proc.stdout.strip() == ""


def test_capacity_continues_when_memory_events_is_absent(tmp_path: Path) -> None:
    proc = capacity(tmp_path, oom_kill=None)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["oom_kill"] == "unknown"
    assert values["oom_kill_increased"] == "unknown"
    assert values["allowed"] == "2"


def test_capacity_continues_when_oom_kill_is_not_numeric(tmp_path: Path) -> None:
    """memory.events の oom_kill が数値でないときは unknown として継続する。"""
    cg = cgroup(tmp_path, memory_max="max", memory_current=0)
    (cg / "memory.events").write_text("oom_kill not-a-number\n", encoding="utf-8")
    proc = capacity(tmp_path, oom_kill=None)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "max"
    assert values["oom_kill"] == "unknown"
    assert values["allowed"] == "2"
    assert values["limited_by"] == "memory,max_add"


def test_capacity_rejects_a_negative_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "-1")
    assert proc.returncode == 2


def test_capacity_rejects_a_non_integer_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "x")
    assert proc.returncode == 2


def test_capacity_rejects_an_unknown_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--no-such-option", "1")
    assert proc.returncode == 2


# --- capacity: OOM Killer の回数（shrink、AC35） ----------------------------


def test_capacity_lowers_the_count_when_oom_kill_increased(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", "3", oom_kill=2)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["oom_kill_increased"] == "yes"
    assert values["verdict"] == "shrink"
    assert values["allowed"] == "2"  # 今の本数 − 1
    assert values["limited_by"] == "oom_kill_increased"


def test_capacity_shrinks_even_when_the_pressure_is_high(tmp_path: Path) -> None:
    """落ちたことは圧より強い事実である。"""
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", "3", oom_kill=2, psi_avg10="50.00")
    values = keys(proc.stdout)
    assert values["verdict"] == "shrink"
    assert values["allowed"] == "2"
    assert values["limited_by"] == "oom_kill_increased"


def test_capacity_says_no_when_oom_kill_did_not_increase(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", "3", oom_kill=1)
    values = keys(proc.stdout)
    assert values["oom_kill_increased"] == "no"
    assert "oom_kill_increased" not in values["limited_by"]


@pytest.mark.parametrize("running", ["0", "1"])
def test_capacity_allows_zero_only_on_the_review_that_saw_the_increase(tmp_path: Path, running: str) -> None:
    """`running` が 0 か 1 のとき、下限の 1 を当てると「今の本数 − 1 以下」を満たせない。"""
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", running, oom_kill=2)
    values = keys(proc.stdout)
    assert values["allowed"] == "0"
    assert "floor" not in values["limited_by"]


def test_capacity_without_a_baseline_does_not_judge(tmp_path: Path) -> None:
    proc = capacity(tmp_path, oom_kill=5)
    values = keys(proc.stdout)
    assert values["oom_kill"] == "5"
    assert values["oom_kill_increased"] == "unknown"
    assert values["allowed"] == "2"


# --- concurrency（AC40） -----------------------------------------------------

INTERVALS = [
    # 重なる 2 本（1 時間）
    {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": "2026-09-01T02:00:00Z", "closedAt": "2026-09-01T02:00:00Z"},
    {"number": 2, "createdAt": "2026-09-01T01:00:00Z", "mergedAt": "2026-09-01T03:00:00Z", "closedAt": "2026-09-01T03:00:00Z"},
    # 端が接するだけ（重ならない）。開いたまま
    {"number": 3, "createdAt": "2026-09-01T03:00:00Z", "mergedAt": None, "closedAt": None},
]


def concurrency(tmp_path: Path, *args: str, data=INTERVALS) -> subprocess.CompletedProcess:
    path = tmp_path / "prs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return run("concurrency", "--input", str(path), "--now", "2026-09-01T04:00:00Z", *args)


def test_concurrency_measures_the_overlap(tmp_path: Path) -> None:
    proc = concurrency(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert keys(proc.stdout) == {
        "prs": "3",
        "start": "2026-09-01T00:00:00Z",
        "end": "2026-09-01T04:00:00Z",
        "span_minutes": "240",
        "overlap_minutes": "60",
        "concurrency_pct": "25.0",
        "max_open": "2",
    }


def test_concurrency_counts_a_touching_pair_as_no_overlap(tmp_path: Path) -> None:
    """同じ時刻に閉じる期間と開く期間は重ならない（閉じるほうを先に数える）。"""
    proc = concurrency(
        tmp_path,
        data=[
            {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": "2026-09-01T01:00:00Z", "closedAt": None},
            {"number": 2, "createdAt": "2026-09-01T01:00:00Z", "mergedAt": "2026-09-01T02:00:00Z", "closedAt": None},
        ],
    )
    values = keys(proc.stdout)
    assert values["overlap_minutes"] == "0"
    assert values["concurrency_pct"] == "0.0"
    assert values["max_open"] == "1"


def test_concurrency_uses_closed_at_when_not_merged(tmp_path: Path) -> None:
    proc = concurrency(
        tmp_path,
        data=[
            {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": None, "closedAt": "2026-09-01T01:00:00Z"},
        ],
    )
    values = keys(proc.stdout)
    assert values["end"] == "2026-09-01T01:00:00Z"
    assert values["span_minutes"] == "60"


def test_concurrency_rounds_half_up(tmp_path: Path) -> None:
    """秒の比を小数 1 桁へ四捨五入する。分へ丸めた値からは求めない。"""
    proc = concurrency(
        tmp_path,
        data=[
            {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": "2026-09-01T00:00:25Z", "closedAt": None},
            {"number": 2, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": "2026-09-01T00:01:20Z", "closedAt": None},
        ],
    )
    values = keys(proc.stdout)
    # 重なり 25 秒 ÷ 期間 80 秒 = 31.25% → 31.3（ROUND_HALF_UP）
    assert values["overlap_minutes"] == "0"
    assert values["concurrency_pct"] == "31.3"


def test_concurrency_handles_a_zero_span(tmp_path: Path) -> None:
    proc = concurrency(
        tmp_path,
        data=[
            {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": "2026-09-01T00:00:00Z", "closedAt": None},
        ],
    )
    assert proc.returncode == 0
    assert keys(proc.stdout)["concurrency_pct"] == "0.0"


def test_concurrency_rejects_an_empty_argument_list(tmp_path: Path) -> None:
    assert run("concurrency").returncode == 2


def test_concurrency_rejects_an_unreadable_input(tmp_path: Path) -> None:
    assert run("concurrency", "--input", str(tmp_path / "none.json")).returncode == 2


@pytest.mark.parametrize("data", [[1], ["x"], [None], [{"createdAt": "2026-09-01T00:00:00Z"}, 2]])
def test_concurrency_rejects_a_non_object_element(tmp_path: Path, data: list) -> None:
    """要素が object でない入力は、入力の誤りとして終了コード 2 で弾く。

    素通しすると `record.get` が `AttributeError` を出し、`gh pr view` の失敗と
    同じ終了コード 1 で落ちる。
    """
    proc = concurrency(tmp_path, data=data)
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "--input" in proc.stderr


@pytest.mark.parametrize(
    "record",
    [
        {"number": 1, "createdAt": 20260901, "mergedAt": None, "closedAt": None},
        {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": 20260901, "closedAt": None},
        {"number": 1, "createdAt": "2026-09-01T00:00:00Z", "mergedAt": None, "closedAt": ["2026-09-01T01:00:00Z"]},
        {"number": 1, "createdAt": True, "mergedAt": None, "closedAt": None},
    ],
)
def test_concurrency_rejects_a_non_string_time(tmp_path: Path, record: dict) -> None:
    """時刻の欄が文字列でない入力も、入力の誤りとして終了コード 2 で弾く。

    素通しすると `parse_time` の `value.strip()` が `AttributeError` を出し、
    `gh pr view` の失敗と同じ終了コード 1 で落ちる。
    """
    proc = concurrency(tmp_path, data=[record])
    assert proc.returncode == 2, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr


# --- concurrency: `gh` は読むだけ（AC42） -----------------------------------


def fake_gh(tmp_path: Path, *, fail: bool = False) -> tuple[dict, Path]:
    """`PATH` の先頭へ置く偽の `gh`。受けた引数を 1 行ずつ控える。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-args.log"
    body = f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log}"\n'
    if fail:
        body += 'echo "boom" >&2\nexit 1\n'
    else:
        body += (
            'printf \'{"number": 1, "createdAt": "2026-09-01T00:00:00Z", '
            '"mergedAt": "2026-09-01T01:00:00Z", "closedAt": "2026-09-01T01:00:00Z"}\\n\'\n'
        )
    gh = bin_dir / "gh"
    gh.write_text(body, encoding="utf-8")
    gh.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return env, log


def test_concurrency_only_reads_through_gh(tmp_path: Path) -> None:
    env, log = fake_gh(tmp_path)
    proc = run("concurrency", "1", "--repo", "devbasex/ai-plugins", env=env)
    assert proc.returncode == 0, proc.stderr
    recorded = log.read_text(encoding="utf-8").splitlines()
    assert recorded, "gh が呼ばれていない"
    for line in recorded:
        assert line.startswith("pr view "), line
        assert "--json number,createdAt,mergedAt,closedAt" in line
    assert keys(proc.stdout)["prs"] == "1"


def test_concurrency_exits_one_when_gh_fails(tmp_path: Path) -> None:
    env, _ = fake_gh(tmp_path, fail=True)
    proc = run("concurrency", "7", env=env)
    assert proc.returncode == 1
    assert "7" in proc.stderr


def test_concurrency_exits_one_when_gh_is_absent(tmp_path: Path) -> None:
    """`gh` が `PATH` に無いときも、`gh` の失敗と同じ終了コード 1 で終わる。

    空の配列を `measure` へ渡すと `min()` が `ValueError` を投げ、traceback で落ちる。
    """
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    env = dict(os.environ, PATH=str(empty_bin))
    proc = run("concurrency", "1", env=env)
    assert proc.returncode == 1, proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "gh" in proc.stderr
