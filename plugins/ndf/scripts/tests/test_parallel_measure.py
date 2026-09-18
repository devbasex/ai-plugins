"""並列の本数と並行度の測定（`parallel-measure.py`、#621）。

入力のファイル（`--meminfo`・`--cgroup-dir`・`--input`）を差し替えて値を固定する。
**このホストの `/proc/meminfo` も cgroup も読まない。** 読むと、走らせた時刻の空きで
期待値が動く。

`concurrency` の `gh` は `PATH` の先頭へ置いた偽物に差し替え、受けた引数を記録する
（読み取りだけを行うことの検査、AC42）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "parallel-measure.py"


# --- 入力の組み立て ----------------------------------------------------------

def meminfo(tmp_path: Path, *, available_mib: int, swap_total_mib: int,
            swap_free_mib: int, name: str = "meminfo") -> Path:
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


def cgroup(tmp_path: Path, *, oom_kill: int | None = None, memory_max: str | None = None,
           memory_current: int | None = None, name: str = "cgroup") -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    if oom_kill is not None:
        # 実物は `max 0` の行も持つ。`oom_kill` だけを読むことの検査でもある。
        (path / "memory.events").write_text(
            f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n", encoding="utf-8")
    if memory_max is not None:
        (path / "memory.max").write_text(f"{memory_max}\n", encoding="utf-8")
    if memory_current is not None:
        (path / "memory.current").write_text(f"{memory_current}\n", encoding="utf-8")
    return path


def run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )


def keys(out: str) -> dict[str, str]:
    values = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def capacity(tmp_path: Path, *args: str, available_mib: int = 9742,
             swap_total_mib: int = 2047, swap_free_mib: int = 310,
             oom_kill: int | None = 1, memory_max: str | None = "max",
             memory_current: int | None = 0) -> subprocess.CompletedProcess:
    mem = meminfo(tmp_path, available_mib=available_mib, swap_total_mib=swap_total_mib,
                  swap_free_mib=swap_free_mib)
    cg = cgroup(tmp_path, oom_kill=oom_kill, memory_max=memory_max,
                memory_current=memory_current)
    return run("capacity", "--meminfo", str(mem), "--cgroup-dir", str(cg), *args)


# --- capacity: 空きメモリから本数を出す（AC31 / AC33） -----------------------

def test_capacity_reports_the_measured_values(tmp_path: Path) -> None:
    """2026-09-17 のこのホストの値での出力（契約の文書の例）。"""
    proc = capacity(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert keys(proc.stdout) == {
        "mem_available_mib": "9742",
        "swap_total_mib": "2047",
        "swap_free_mib": "310",
        "cgroup_available_mib": "max",
        "oom_kill": "1",
        "oom_kill_increased": "unknown",
        "running": "0",
        "by_memory": "3",
        "allowed": "2",
        "limited_by": "memory,max,swap_low",
    }


def test_capacity_allows_three_when_swap_is_free(tmp_path: Path) -> None:
    proc = capacity(tmp_path, swap_free_mib=2047)
    assert proc.returncode == 0
    assert keys(proc.stdout)["allowed"] == "3"
    assert "swap_low" not in keys(proc.stdout)["limited_by"]


def test_capacity_does_not_limit_swap_at_the_free_percentage_boundary(
        tmp_path: Path) -> None:
    """現状固定: 空き swap が閾値と等しい場合は swap_low にしない。"""
    proc = capacity(
        tmp_path,
        "--swap-free-min-pct", "25",
        swap_total_mib=2000,
        swap_free_mib=500,
    )

    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["allowed"] == "3"
    assert values["limited_by"] == "memory,max"


def test_capacity_skips_swap_check_when_swap_total_is_zero(tmp_path: Path) -> None:
    """現状固定: SwapTotal が 0 のときはスワップ判定を省き、allowed を減らさない。"""
    proc = capacity(tmp_path, swap_total_mib=0, swap_free_mib=0)
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "max"
    assert values["oom_kill"] == "1"
    assert values["allowed"] == "3"
    assert values["limited_by"] == "memory,max"


def test_capacity_never_goes_below_one_lane(tmp_path: Path) -> None:
    """空きが 1 本分に足りなくても 1 を下回らない。0 本では進行が止まる。"""
    proc = capacity(tmp_path, available_mib=3000, swap_free_mib=2047)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["by_memory"] == "0"
    assert values["allowed"] == "1"
    assert values["limited_by"] == "memory,floor"


def test_capacity_counts_running_lanes_into_the_total(tmp_path: Path) -> None:
    """空きは動いている担当の使用量を引いた後の値なので、`running` を足して総本数にする。"""
    proc = capacity(tmp_path, "--running", "2", available_mib=6144, swap_free_mib=2047)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["running"] == "2"
    assert values["by_memory"] == "4"
    assert values["allowed"] == "3"  # 上限。追加できるのは 1 本
    assert values["limited_by"] == "max"


def test_capacity_takes_the_defaults_from_the_arguments(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--per-lane-mib", "1024", "--max", "8", swap_free_mib=2047)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["by_memory"] == "7"  # ⌊(9742 − 2048) ÷ 1024⌋
    assert values["allowed"] == "7"
    assert values["limited_by"] == "memory"


def test_capacity_takes_the_reserve_from_the_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--reserve-mib", "0", swap_free_mib=2047)
    assert keys(proc.stdout)["by_memory"] == "4"


# --- capacity: cgroup の残り（AC31 / AC33） ---------------------------------

def test_capacity_uses_the_cgroup_limit_when_it_is_smaller(tmp_path: Path) -> None:
    """cgroup に上限があるホストでは、VM の空きではなく cgroup の残りが決める。"""
    proc = capacity(tmp_path, swap_free_mib=2047,
                    memory_max=str(8 * 1024 * 1024 * 1024),
                    memory_current=4 * 1024 * 1024 * 1024)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "4096"
    assert values["by_memory"] == "1"  # ⌊(4096 − 2048) ÷ 2048⌋


def test_capacity_ignores_the_cgroup_limit_when_it_is_max(tmp_path: Path) -> None:
    proc = capacity(tmp_path, swap_free_mib=2047, memory_max="max", memory_current=0)
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "max"
    assert values["by_memory"] == "3"


def test_capacity_continues_when_the_cgroup_files_are_absent(tmp_path: Path) -> None:
    proc = capacity(tmp_path, swap_free_mib=2047, memory_max=None, memory_current=None)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "unknown"
    assert values["by_memory"] == "3"


def test_capacity_continues_when_memory_max_is_not_numeric(tmp_path: Path) -> None:
    """現状固定: memory.max が数値でないときは unknown として継続する。"""
    proc = capacity(tmp_path, swap_free_mib=2047, memory_max="not-a-number")
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "unknown"
    assert values["oom_kill"] == "1"
    assert values["allowed"] == "3"
    assert values["limited_by"] == "memory,max"


def test_capacity_continues_when_memory_current_is_not_numeric(tmp_path: Path) -> None:
    """現状固定: memory.current が数値でないときは unknown として継続する。"""
    cg = cgroup(tmp_path, oom_kill=1, memory_max=str(8 * 1024 * 1024 * 1024))
    (cg / "memory.current").write_text("not-a-number\n", encoding="utf-8")
    mem = meminfo(tmp_path, available_mib=9742, swap_total_mib=2047, swap_free_mib=2047)
    proc = run("capacity", "--meminfo", str(mem), "--cgroup-dir", str(cg))
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "unknown"
    assert values["oom_kill"] == "1"
    assert values["allowed"] == "3"
    assert values["limited_by"] == "memory,max"


def test_capacity_floors_the_cgroup_remainder_at_zero(tmp_path: Path) -> None:
    """使用量が上限を超えている（負の残り）ときも 0 として扱い、落ちない。"""
    proc = capacity(tmp_path, swap_free_mib=2047,
                    memory_max=str(1024 * 1024 * 1024),
                    memory_current=2 * 1024 * 1024 * 1024)
    assert proc.returncode == 0
    assert keys(proc.stdout)["cgroup_available_mib"] == "0"


# --- capacity: 測れない環境（AC32） -----------------------------------------

def test_capacity_exits_three_when_meminfo_is_absent(tmp_path: Path) -> None:
    proc = run("capacity", "--meminfo", str(tmp_path / "no-such-file"),
               "--cgroup-dir", str(cgroup(tmp_path, oom_kill=1)))
    assert proc.returncode == 3
    assert "allowed=" not in proc.stdout
    assert proc.stdout.strip() == ""
    assert len(proc.stderr.strip().splitlines()) == 1


def test_capacity_exits_three_when_mem_available_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "partial"
    path.write_text("MemTotal: 21000000 kB\n", encoding="utf-8")
    proc = run("capacity", "--meminfo", str(path),
               "--cgroup-dir", str(cgroup(tmp_path, oom_kill=1)))
    assert proc.returncode == 3
    assert proc.stdout.strip() == ""


def test_capacity_continues_when_memory_events_is_absent(tmp_path: Path) -> None:
    proc = capacity(tmp_path, swap_free_mib=2047, oom_kill=None)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["oom_kill"] == "unknown"
    assert values["oom_kill_increased"] == "unknown"
    assert values["allowed"] == "3"


def test_capacity_continues_when_oom_kill_is_not_numeric(tmp_path: Path) -> None:
    """現状固定: memory.events の oom_kill が数値でないときは unknown として継続する。"""
    cg = cgroup(tmp_path, memory_max="max", memory_current=0)
    (cg / "memory.events").write_text("oom_kill not-a-number\n", encoding="utf-8")
    mem = meminfo(tmp_path, available_mib=9742, swap_total_mib=2047, swap_free_mib=2047)
    proc = run("capacity", "--meminfo", str(mem), "--cgroup-dir", str(cg))
    assert proc.returncode == 0, proc.stderr
    values = keys(proc.stdout)
    assert values["cgroup_available_mib"] == "max"
    assert values["oom_kill"] == "unknown"
    assert values["allowed"] == "3"
    assert values["limited_by"] == "memory,max"


def test_capacity_rejects_a_negative_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "-1")
    assert proc.returncode == 2


def test_capacity_rejects_a_non_integer_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--running", "x")
    assert proc.returncode == 2


def test_capacity_rejects_an_unknown_argument(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--no-such-option", "1")
    assert proc.returncode == 2


def test_capacity_rejects_per_lane_mib_zero(tmp_path: Path) -> None:
    """現状固定: 型検査を通る --per-lane-mib 0 は run_capacity が拒否する。

    `non_negative_int` は 0 を通すため、0 を弾くのは run_capacity の
    `--per-lane-mib は 1 以上である` である。終了コード 2・標準出力は空・
    エラーが当該引数を識別できることを固定する。
    """
    proc = capacity(tmp_path, "--per-lane-mib", "0")
    assert proc.returncode == 2
    assert proc.stdout.strip() == ""
    assert "--per-lane-mib" in proc.stderr


# --- capacity: OOM Killer の回数（AC35） ------------------------------------

def test_capacity_lowers_the_count_when_oom_kill_increased(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", "3",
                    oom_kill=2, swap_free_mib=2047)
    assert proc.returncode == 0
    values = keys(proc.stdout)
    assert values["oom_kill_increased"] == "yes"
    assert values["allowed"] == "2"  # 今の本数 − 1
    assert "oom_kill_increased" in values["limited_by"]


def test_capacity_says_no_when_oom_kill_did_not_increase(tmp_path: Path) -> None:
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", "3",
                    oom_kill=1, swap_free_mib=2047)
    values = keys(proc.stdout)
    assert values["oom_kill_increased"] == "no"
    assert "oom_kill_increased" not in values["limited_by"]


@pytest.mark.parametrize("running", ["0", "1"])
def test_capacity_allows_zero_only_on_the_review_that_saw_the_increase(
        tmp_path: Path, running: str) -> None:
    """`running` が 0 か 1 のとき、下限の 1 を当てると「今の本数 − 1 以下」を満たせない。"""
    proc = capacity(tmp_path, "--oom-baseline", "1", "--running", running,
                    oom_kill=2, swap_free_mib=2047)
    values = keys(proc.stdout)
    assert values["allowed"] == "0"
    assert "floor" not in values["limited_by"]


def test_capacity_without_a_baseline_does_not_judge(tmp_path: Path) -> None:
    proc = capacity(tmp_path, oom_kill=5, swap_free_mib=2047)
    values = keys(proc.stdout)
    assert values["oom_kill"] == "5"
    assert values["oom_kill_increased"] == "unknown"
    assert values["allowed"] == "3"


# --- concurrency（AC40） -----------------------------------------------------

INTERVALS = [
    # 重なる 2 本（1 時間）
    {"number": 1, "createdAt": "2026-09-01T00:00:00Z",
     "mergedAt": "2026-09-01T02:00:00Z", "closedAt": "2026-09-01T02:00:00Z"},
    {"number": 2, "createdAt": "2026-09-01T01:00:00Z",
     "mergedAt": "2026-09-01T03:00:00Z", "closedAt": "2026-09-01T03:00:00Z"},
    # 端が接するだけ（重ならない）。開いたまま
    {"number": 3, "createdAt": "2026-09-01T03:00:00Z",
     "mergedAt": None, "closedAt": None},
]


def concurrency(tmp_path: Path, *args: str, data=INTERVALS) -> subprocess.CompletedProcess:
    path = tmp_path / "prs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return run("concurrency", "--input", str(path),
               "--now", "2026-09-01T04:00:00Z", *args)


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
    """同じ時刻に閉じる区間と開く区間は重ならない（閉じるほうを先に数える）。"""
    proc = concurrency(tmp_path, data=[
        {"number": 1, "createdAt": "2026-09-01T00:00:00Z",
         "mergedAt": "2026-09-01T01:00:00Z", "closedAt": None},
        {"number": 2, "createdAt": "2026-09-01T01:00:00Z",
         "mergedAt": "2026-09-01T02:00:00Z", "closedAt": None},
    ])
    values = keys(proc.stdout)
    assert values["overlap_minutes"] == "0"
    assert values["concurrency_pct"] == "0.0"
    assert values["max_open"] == "1"


def test_concurrency_uses_closed_at_when_not_merged(tmp_path: Path) -> None:
    proc = concurrency(tmp_path, data=[
        {"number": 1, "createdAt": "2026-09-01T00:00:00Z",
         "mergedAt": None, "closedAt": "2026-09-01T01:00:00Z"},
    ])
    values = keys(proc.stdout)
    assert values["end"] == "2026-09-01T01:00:00Z"
    assert values["span_minutes"] == "60"


def test_concurrency_rounds_half_up(tmp_path: Path) -> None:
    """秒の比を小数 1 桁へ四捨五入する。分へ丸めた値からは求めない。"""
    proc = concurrency(tmp_path, data=[
        {"number": 1, "createdAt": "2026-09-01T00:00:00Z",
         "mergedAt": "2026-09-01T00:00:25Z", "closedAt": None},
        {"number": 2, "createdAt": "2026-09-01T00:00:00Z",
         "mergedAt": "2026-09-01T00:01:20Z", "closedAt": None},
    ])
    values = keys(proc.stdout)
    # 重なり 25 秒 ÷ 期間 80 秒 = 31.25% → 31.3（ROUND_HALF_UP）
    assert values["overlap_minutes"] == "0"
    assert values["concurrency_pct"] == "31.3"


def test_concurrency_handles_a_zero_span(tmp_path: Path) -> None:
    proc = concurrency(tmp_path, data=[
        {"number": 1, "createdAt": "2026-09-01T00:00:00Z",
         "mergedAt": "2026-09-01T00:00:00Z", "closedAt": None},
    ])
    assert proc.returncode == 0
    assert keys(proc.stdout)["concurrency_pct"] == "0.0"


def test_concurrency_rejects_an_empty_argument_list(tmp_path: Path) -> None:
    assert run("concurrency").returncode == 2


def test_concurrency_rejects_an_unreadable_input(tmp_path: Path) -> None:
    assert run("concurrency", "--input", str(tmp_path / "none.json")).returncode == 2


def concurrency_raw(tmp_path: Path, text: str) -> subprocess.CompletedProcess:
    path = tmp_path / "prs.json"
    path.write_text(text, encoding="utf-8")
    return run("concurrency", "--input", str(path), "--now", "2026-09-01T04:00:00Z")


def test_concurrency_rejects_an_input_that_is_not_json(tmp_path: Path) -> None:
    proc = concurrency_raw(tmp_path, "{not json")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.startswith("--input が JSON ではない: ")


def test_concurrency_rejects_an_empty_array(tmp_path: Path) -> None:
    proc = concurrency(tmp_path, data=[])
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() == "--input は 1 件以上の配列である"


def test_concurrency_rejects_an_input_that_is_not_an_array(tmp_path: Path) -> None:
    proc = concurrency(tmp_path, data={"number": 1})
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() == "--input は 1 件以上の配列である"


def test_concurrency_rejects_a_record_without_created_at(tmp_path: Path) -> None:
    proc = concurrency(tmp_path, data=[{"number": 1, "mergedAt": None}])
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() == "createdAt が無い: {'number': 1, 'mergedAt': None}"


def test_concurrency_rejects_a_created_at_that_is_not_iso8601(tmp_path: Path) -> None:
    proc = concurrency(tmp_path, data=[{"number": 1, "createdAt": "yesterday"}])
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert proc.stderr.strip() == "createdAt が ISO8601 ではない: yesterday"


# --- concurrency: `gh` は読むだけ（AC42） -----------------------------------

def fake_gh(tmp_path: Path, *, fail: bool = False) -> tuple[dict, Path]:
    """`PATH` の先頭へ置く偽の `gh`。受けた引数を 1 行ずつ控える。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh-args.log"
    body = (
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
    )
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
