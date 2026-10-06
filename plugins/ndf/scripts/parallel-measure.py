#!/usr/bin/env python3
"""並列の本数と並行度を測る（#621）。

進行側が担当（作業ツリー 1 つ分）を起動する前に**起動してよい本数**を出し、スプリントを
閉じるときに**並行度**を出す。2 つとも読み取りだけを行い、ファイルへも GitHub へも
書き込まない。

    python3 parallel-measure.py capacity [--running N] [--oom-baseline N] ...
    python3 parallel-measure.py concurrency <PR番号>... [--repo OWNER/REPO]

**測るだけで、担当の起動は止めない**（設計の決定 8）。担当は Agent ツールで起動され、
サブエージェントは本体のプロセスの中で動くため、フックで本数を数える手がかりが無い。
数えられないものを拒否の条件にすると、止める必要の無い起動を止めるか、止めないまま
「機械が見ている」と読まれる。

**本数の既定値（予備・1 本の見込み・1 本の下限・山への余裕・足す数・圧の閾値・標本の間隔）を
持つのはこのファイルだけである**
（設計の決定 7）。文書は値を写さず、引数の名前だけを書く。実際に測った値は実行計画の
「測った値」の表が残し、初期値を直す根拠になる。

**1 本は担当 1 つ（作業ツリー 1 つ、G3 の supervisor 1 つ）である。** その中で動く worker は
1 本の重さに含め、`--running` には数えない。

`capacity` は判定の区分を 3 つに分ける（#780）: `oom_kill` が起点より増えたら `shrink`（1 本減らす）、
いまメモリの圧（PSI の `some avg10`、無ければスワップ I/O）が閾値を超えていたら `hold`（足さない）、
それ以外は `grow`（空きと 1 本の重さから足す）。1 本の重さは cgroup の `anon` の起点からの増分を
動いている本数で割って測り、測れないときだけ見込みを使う。総本数の上限は `--max` を渡したときだけ掛かる。

終了コード:

    capacity      0 測れた / 2 引数の誤り / 3 `--meminfo` を読めない
    concurrency   0 測れた / 1 `gh pr view` が失敗した / 2 引数の誤り
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import time
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import clock  # noqa: E402  時刻の読み取り（#1142 の L0）
import deps  # noqa: E402  外部パッケージの環境（#1142 の決定 17）

deps.require("procs", "durable")  # cgroup の位置と使用量は procs（psutil の包み）が読む
import procs  # noqa: E402
import gh_parts  # noqa: E402  GitHub の読み取り（#1142 の L0）

# --- 既定値（ここだけが持つ） -----------------------------------------------
# 予備: 同じ VM に常駐する他のプロセスの揺れ。既に動いている進行側の本体と担当は
# MemAvailable から引かれているため含めない（#780）。
DEFAULT_RESERVE_MIB = 1024
# 1 本の見込み: 実測を使えない（0 本・起点なし・anon を読めない）ときの値。#621 の実測
# 約 1.2GiB に余裕を足した暫定値で、「測った値」の表の 1 本の実測が溜まったら直す。
DEFAULT_PER_LANE_MIB = 1536
# 1 本の重さの下限: 実測が小さく出ても、これを割らない。
DEFAULT_PER_LANE_MIN_MIB = 512
# 山への余裕: 実測の平均に掛け、テストやビルドの山を見込む。
DEFAULT_PEAK_FACTOR = Decimal("1.5")
# 1 回の見直しで足す数の上限: 担当の重さは動き出すまで測れないため、足したら次の見直しで
# 測ってからまた足す。総本数の上限ではない。
DEFAULT_MAX_ADD = 2
# 圧の閾値: PSI の some avg10（%）。超えたら hold。
DEFAULT_PSI_SOME_MAX = Decimal("10")
# PSI が無いときのスワップ I/O の閾値（pswpin + pswpout のページ/秒）。超えたら hold。
DEFAULT_SWAP_IO_MAX_PAGES_PER_SEC = 512
# スワップ I/O の 2 回の読み取りの間隔（秒）。
DEFAULT_SAMPLE_SECONDS = Decimal("2")

DEFAULT_MEMINFO = "/proc/meminfo"
GH_JSON_FIELDS = "number,createdAt,mergedAt,closedAt"

UNKNOWN = "unknown"
NO_LIMIT = "max"


class Usage(Exception):
    """引数の誤り（終了コード 2）。"""


class NotMeasurable(Exception):
    """測る元を読めない（終了コード 3）。"""


def non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"整数ではない: {value}")
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"負の値は受け取らない: {value}")
    return parsed


def positive_int(value: str) -> int:
    parsed = non_negative_int(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError(f"1 以上である: {value}")
    return parsed


def _decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"数ではない: {value}")
    if not parsed.is_finite():
        raise argparse.ArgumentTypeError(f"有限の数ではない: {value}")
    return parsed


def non_negative_decimal(value: str) -> Decimal:
    parsed = _decimal(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"負の値は受け取らない: {value}")
    return parsed


def positive_decimal(value: str) -> Decimal:
    parsed = _decimal(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(f"0 より大きい値である: {value}")
    return parsed


def emit_pairs(pairs: list[tuple[str, object]]) -> None:
    """`キー=値` を 1 行ずつ、渡された順に出す。"""
    for key, value in pairs:
        print(f"{key}={value}")


# --- capacity ---------------------------------------------------------------


def read_meminfo(path: Path) -> dict[str, int]:
    """`/proc/meminfo` を kB の辞書として読む。`MemAvailable` が無ければ測れない。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NotMeasurable(f"{path} を読めない: {exc.strerror}")
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if not fields:
            continue
        try:
            values[key.strip()] = int(fields[0])
        except ValueError:
            continue
    if "MemAvailable" not in values:
        raise NotMeasurable(f"{path} に MemAvailable が無い")
    return values


def resolve_cgroup_dir(given: Optional[str], *, root: Optional[Path] = None, proc_cgroup: Optional[Path] = None) -> Path:
    """`--cgroup-dir` が無いときだけ、自分の cgroup の位置を導く（`procs.cgroup_dir`）。

    コンテナの中では `/sys/fs/cgroup` がそのまま自分の cgroup だが、ホストでは
    `/proc/self/cgroup` の `0::<path>` が指す下にある。

    **`memory.events` の有無でこの 2 つを見分けられる。** kernel は `memory.*` を
    `CFTYPE_NOT_ON_ROOT` で置くため、cgroup v2 の根には `memory.events` が無い。
    あるということは、その位置がすでに根ではない＝自分の cgroup である。

    `root` と `proc_cgroup` はチェックのための差し替え口で、既定は `procs` の 2 つの定数である。
    """
    if given is not None:
        return Path(given)
    return procs.cgroup_dir(
        procs.CGROUP_ROOT if root is None else Path(root), procs.PROC_SELF_CGROUP if proc_cgroup is None else Path(proc_cgroup)
    )


def cgroup_available_mib(mem: procs.CgroupMemory) -> object:
    """cgroup の残り。上限が無ければ `max`、読めなければ `unknown`。"""
    if mem.unlimited:
        return NO_LIMIT
    if mem.limit is None or mem.current is None:
        return UNKNOWN
    return max(0, (mem.limit - mem.current) // (1024 * 1024))


def lanes_by_memory(available_mib: int, running: int, reserve_mib: int, per_lane_mib: int) -> int:
    """空きから導いた総本数。

    空きは動いている担当の使用量を引いた後の値なので、割った値は**追加できる本数**である。
    `running` を足して総本数にしてから上限と比べる。
    """
    return running + max(0, (available_mib - reserve_mib) // per_lane_mib)


class PerLane(NamedTuple):
    observed_mib: Optional[int]  # 実測。使えなければ None
    used_mib: int  # 判定に使った 1 本の重さ


class Pressure(NamedTuple):
    source: str  # psi / swap_io / unknown
    value: str  # 出力の pressure の値
    exceeded: Optional[str]  # 超えたときの limited_by の値。超えなければ None


class Decision(NamedTuple):
    verdict: str  # grow / hold / shrink
    allowed: int
    limited_by: list[str]


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def per_lane_weight(
    anon_mib: Optional[Decimal],
    baseline_mib: Optional[int],
    running: int,
    *,
    peak_factor: Decimal,
    min_mib: int,
    default_mib: int,
) -> PerLane:
    """1 本の重さ。実測は起点からの anon の増分を本数で割って切り上げ、使う値は丸める前の差に
    山への余裕を掛けてから切り上げる（決定 3）。差が負なら 0 とする。実測を使えなければ見込み。"""
    if anon_mib is None or baseline_mib is None or running < 1:
        return PerLane(None, default_mib)
    growth = max(Decimal(0), anon_mib - baseline_mib)
    observed = _ceil(growth / running)
    used = max(min_mib, _ceil(growth * peak_factor / running))
    return PerLane(observed, used)


def read_pressure(
    psi_path: Path,
    vmstat_path: Path,
    vmstat_after_path: Path,
    *,
    psi_some_max: Decimal,
    swap_io_max_pages_per_sec: int,
    sample_seconds: Decimal,
) -> Pressure:
    """いまの圧。PSI が読めればそれだけを見て待たずに返し、無ければ vmstat を間隔を挟んで 2 回読む。"""
    avg10 = procs.memory_pressure_some_avg10(psi_path)
    if avg10 is not None:
        exceeded = "hold_psi" if Decimal(avg10) > psi_some_max else None
        return Pressure("psi", f"psi:{avg10}", exceeded)
    before = procs.swap_io_pages(vmstat_path)
    if before is None:
        return Pressure(UNKNOWN, UNKNOWN, None)
    if sample_seconds > 0:
        time.sleep(float(sample_seconds))
    after = procs.swap_io_pages(vmstat_after_path)
    if after is None:
        return Pressure(UNKNOWN, UNKNOWN, None)
    # 間隔 0 は検査だけが渡す。差をそのまま毎秒の値として扱う（決定 6）。
    rate = Decimal(max(0, after - before)) / (sample_seconds if sample_seconds > 0 else 1)
    exceeded = "hold_swap_io" if rate > swap_io_max_pages_per_sec else None
    return Pressure("swap_io", f"swap_io:{rate.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}", exceeded)


def decide(
    running: int,
    addable: int,
    *,
    oom_kill_increased: str,
    pressure: Pressure,
    max_add: int,
    max_lanes: Optional[int],
) -> Decision:
    """区分を shrink → hold → grow の順に 1 つ決め、その後に --max と下限 1 を共通に当てる（決定 2）。"""
    limited_by: list[str] = []
    if oom_kill_increased == "yes":
        verdict = "shrink"
        allowed = max(0, running - 1)
    elif pressure.exceeded is not None:
        verdict = "hold"
        allowed = running
    else:
        verdict = "grow"
        allowed = running + min(max_add, addable)
        limited_by.append("memory")
        if max_add < addable:
            limited_by.append("max_add")
    if max_lanes is not None and allowed > max_lanes:
        allowed = max_lanes
        limited_by.append(NO_LIMIT)
    if verdict == "hold":
        limited_by.append(pressure.exceeded)
    if verdict == "shrink":
        limited_by.append("oom_kill_increased")
    elif allowed < 1:
        allowed = 1
        limited_by.append("floor")
    return Decision(verdict, allowed, limited_by)


def run_capacity(args: argparse.Namespace) -> int:
    if args.swap_free_min_pct is not None:
        print("--swap-free-min-pct は廃止した（#780）。スワップの残量は判定に使わず、値を無視する", file=sys.stderr)
    mem = read_meminfo(Path(args.meminfo))
    mem_available_mib = mem["MemAvailable"] // 1024
    swap_total_mib = mem.get("SwapTotal", 0) // 1024
    swap_free_mib = mem.get("SwapFree", 0) // 1024

    cgroup = procs.cgroup_memory(resolve_cgroup_dir(args.cgroup_dir))
    cgroup_available = cgroup_available_mib(cgroup)
    oom_kill = UNKNOWN if cgroup.oom_kill is None else cgroup.oom_kill
    anon_mib = None if cgroup.anon is None else Decimal(cgroup.anon) / (1024 * 1024)

    if oom_kill == UNKNOWN or args.oom_baseline is None:
        oom_kill_increased = UNKNOWN
    else:
        oom_kill_increased = "yes" if oom_kill > args.oom_baseline else "no"

    # cgroup に上限があるホストでは、VM に空きがあっても cgroup の残りを超えた時点で落ちる。
    budget_mib = mem_available_mib
    if isinstance(cgroup_available, int):
        budget_mib = min(budget_mib, cgroup_available)

    per_lane = per_lane_weight(
        anon_mib,
        args.anon_baseline_mib,
        args.running,
        peak_factor=args.peak_factor,
        min_mib=args.per_lane_min_mib,
        default_mib=args.per_lane_mib,
    )
    pressure = read_pressure(
        Path(args.psi),
        Path(args.vmstat),
        Path(args.vmstat if args.vmstat_after is None else args.vmstat_after),
        psi_some_max=args.psi_some_max,
        swap_io_max_pages_per_sec=args.swap_io_max_pages_per_sec,
        sample_seconds=args.sample_seconds,
    )
    by_memory = lanes_by_memory(budget_mib, args.running, args.reserve_mib, per_lane.used_mib)
    decision = decide(
        args.running,
        by_memory - args.running,
        oom_kill_increased=oom_kill_increased,
        pressure=pressure,
        max_add=args.max_add,
        max_lanes=args.max_lanes,
    )

    emit_pairs(
        [
            ("mem_available_mib", mem_available_mib),
            ("swap_total_mib", swap_total_mib),
            ("swap_free_mib", swap_free_mib),
            ("cgroup_available_mib", cgroup_available),
            ("oom_kill", oom_kill),
            ("oom_kill_increased", oom_kill_increased),
            ("running", args.running),
            ("by_memory", by_memory),
            ("allowed", decision.allowed),
            ("limited_by", ",".join(decision.limited_by)),
            ("cgroup_anon_mib", UNKNOWN if cgroup.anon is None else cgroup.anon // (1024 * 1024)),
            ("per_lane_observed_mib", UNKNOWN if per_lane.observed_mib is None else per_lane.observed_mib),
            ("per_lane_used_mib", per_lane.used_mib),
            ("verdict", decision.verdict),
            ("pressure", pressure.value),
        ]
    )
    return 0


# --- concurrency ------------------------------------------------------------


def parse_time(value: object, *, what: str) -> _dt.datetime:
    if not isinstance(value, str):
        # `--input` の JSON は数値も真偽も配列も持てる。素通しすると `value.strip()`
        # が `AttributeError` を出し、`gh pr view` の失敗と同じ終了コード 1 で落ちる。
        raise Usage(f"{what} が文字列ではない: {value!r}")
    parsed = clock.parse(value, naive="utc")
    if parsed is None:
        raise Usage(f"{what} が ISO8601 ではない: {value}")
    return parsed.astimezone(_dt.timezone.utc)


def format_time(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_pull_requests(numbers: list[int], repo: Optional[str]) -> list[dict]:
    """`gh pr view` で読むだけ。書き込む副コマンドは呼ばない。"""
    records = []
    for number in numbers:
        # 起動できない（`gh` が無い）のも `gh pr view` の失敗である。空の配列を返すと
        # `measure_spans` の `min()` が空列で落ちるため、ここで終了コード 1 にする。
        r = gh_parts.view_json("pr", number, GH_JSON_FIELDS, repo)
        if r.returncode != 0:
            print(f"gh pr view {number} が失敗した: {r.stderr.strip()}", file=sys.stderr)
            raise SystemExit(1)
        records.append(json.loads(r.stdout))
    return records


def intervals(records: list[dict], now: _dt.datetime) -> list[tuple[_dt.datetime, _dt.datetime]]:
    """期間は `createdAt` から、`mergedAt`・`closedAt`・`--now` の最初に値のあるものまで。"""
    spans = []
    for record in records:
        created = record.get("createdAt")
        if not created:
            raise Usage(f"createdAt が無い: {record}")
        start = parse_time(created, what="createdAt")
        end = now
        for key in ("mergedAt", "closedAt"):
            value = record.get(key)
            if value:
                end = parse_time(value, what=key)
                break
        spans.append((start, max(start, end)))
    return spans


def build_events(
    spans: list[tuple[_dt.datetime, _dt.datetime]],
) -> list[tuple[_dt.datetime, int]]:
    """期間を開始と終了の事象列に変換する。

    同じ時刻に閉じる期間と開く期間は重ならない。閉じるほうを先に数えるため、
    事象の並びで終わり（−1）を始まり（+1）より前に置く。
    """
    events: list[tuple[_dt.datetime, int]] = []
    for start, end in spans:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda event: (event[0], event[1]))
    return events


def scan_events(events: list[tuple[_dt.datetime, int]]) -> tuple[int, float]:
    """開いている本数を時刻順に数え、2 本以上だった秒を足す。"""
    open_count = 0
    max_open = 0
    overlap_seconds = 0.0
    previous: Optional[_dt.datetime] = None
    for moment, delta in events:
        if previous is not None and open_count >= 2:
            overlap_seconds += (moment - previous).total_seconds()
        open_count += delta
        max_open = max(max_open, open_count)
        previous = moment
    return max_open, overlap_seconds


def summarize_measurement(
    spans: list[tuple[_dt.datetime, _dt.datetime]],
    max_open: int,
    overlap_seconds: float,
) -> dict[str, object]:
    """期間と走査結果を表示用の集計値に整形する。"""
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    span_seconds = (end - start).total_seconds()
    if span_seconds > 0:
        pct = (Decimal(overlap_seconds) / Decimal(span_seconds) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    else:
        pct = Decimal("0.0")
    return {
        "start": format_time(start),
        "end": format_time(end),
        "span_minutes": int(span_seconds // 60),
        "overlap_minutes": int(overlap_seconds // 60),
        "concurrency_pct": pct,
        "max_open": max_open,
    }


def measure_spans(spans: list[tuple[_dt.datetime, _dt.datetime]]) -> dict[str, object]:
    """期間を並行度の集計値へ変換する。"""
    events = build_events(spans)
    max_open, overlap_seconds = scan_events(events)
    return summarize_measurement(spans, max_open, overlap_seconds)


def run_concurrency(args: argparse.Namespace) -> int:
    now = parse_time(args.now, what="--now") if args.now else clock.now(utc=True)
    if args.input:
        try:
            records = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except OSError as exc:
            raise Usage(f"--input を読めない: {exc}")
        except json.JSONDecodeError as exc:
            raise Usage(f"--input が JSON ではない: {exc}")
        if not isinstance(records, list) or not records:
            raise Usage("--input は 1 件以上の配列である")
        if not all(isinstance(record, dict) for record in records):
            # 素通しすると `intervals` の `record.get` が `AttributeError` を出し、
            # `gh pr view` の失敗と同じ終了コード 1 で落ちる。
            raise Usage("--input の要素は object である")
    else:
        if not args.numbers:
            raise Usage("Pull Request の番号を 1 つ以上渡す")
        records = fetch_pull_requests(args.numbers, args.repo)

    measured = measure_spans(intervals(records, now))
    emit_pairs([("prs", len(records))] + list(measured.items()))
    return 0


# --- 入口 -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="parallel-measure.py", description="並列の本数と並行度を測る（#621）")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capacity", help="起動してよい本数を出す")
    cap.add_argument("--running", type=non_negative_int, default=0, help="いま動いている担当（作業ツリー 1 つ分）の数")
    cap.add_argument("--oom-baseline", type=non_negative_int, default=None, help="実行計画に控えた oom_kill の起点")
    cap.add_argument("--anon-baseline-mib", type=non_negative_int, default=None, help="実行計画に控えた anon の起点（MiB）")
    cap.add_argument("--reserve-mib", type=non_negative_int, default=DEFAULT_RESERVE_MIB, help="予備メモリ（MiB）")
    cap.add_argument("--per-lane-mib", type=positive_int, default=DEFAULT_PER_LANE_MIB, help="実測を使えないときの 1 本の見込み（MiB）")
    cap.add_argument("--per-lane-min-mib", type=positive_int, default=DEFAULT_PER_LANE_MIN_MIB, help="1 本の重さの下限（MiB）")
    cap.add_argument("--peak-factor", type=positive_decimal, default=DEFAULT_PEAK_FACTOR, help="実測に掛ける山への余裕")
    cap.add_argument("--max-add", type=non_negative_int, default=DEFAULT_MAX_ADD, help="1 回の見直しで足す数の上限")
    cap.add_argument("--max", dest="max_lanes", type=non_negative_int, default=None, help="総本数の上限。渡したときだけ効く")
    cap.add_argument("--psi-some-max", type=non_negative_decimal, default=DEFAULT_PSI_SOME_MAX, help="PSI の some avg10 の閾値")
    cap.add_argument(
        "--swap-io-max-pages-per-sec",
        type=non_negative_int,
        default=DEFAULT_SWAP_IO_MAX_PAGES_PER_SEC,
        help="スワップ I/O の閾値（ページ/秒）",
    )
    cap.add_argument(
        "--sample-seconds", type=non_negative_decimal, default=DEFAULT_SAMPLE_SECONDS, help="vmstat の 2 回の読み取りの間隔（秒）"
    )
    cap.add_argument("--meminfo", default=DEFAULT_MEMINFO)
    cap.add_argument("--cgroup-dir", default=None)
    cap.add_argument("--psi", default=str(procs.PROC_PRESSURE_MEMORY), help="PSI を読む元")
    cap.add_argument("--vmstat", default=str(procs.PROC_VMSTAT), help="スワップ I/O の 1 回目を読む元")
    cap.add_argument("--vmstat-after", default=None, help="スワップ I/O の 2 回目を読む元（既定は --vmstat と同じ）")
    cap.add_argument("--swap-free-min-pct", default=None, help=argparse.SUPPRESS)  # 廃止（#780）。知らせて無視する
    cap.set_defaults(handler=run_capacity)

    con = sub.add_parser("concurrency", help="並行度と最大同時本数を出す")
    con.add_argument("numbers", nargs="*", type=non_negative_int, help="対象の Pull Request の番号")
    con.add_argument("--repo", default=None)
    con.add_argument("--input", default=None, help="gh を呼ばず、番号と時刻の JSON を読む")
    con.add_argument("--now", default=None, help="開いたままの Pull Request の期間の終わり")
    con.set_defaults(handler=run_concurrency)
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except Usage as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except NotMeasurable as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
