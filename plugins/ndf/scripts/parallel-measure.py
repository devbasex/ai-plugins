#!/usr/bin/env python3
"""並列の本数と並行度を測る（#621）。

進行側が担当（作業ツリー 1 つ分）を起動する前に**起動してよい本数**を出し、ミッションを
閉じるときに**並行度**を出す。2 つとも読み取りだけを行い、ファイルへも GitHub へも
書き込まない。

    python3 parallel-measure.py capacity [--running N] [--oom-baseline N] ...
    python3 parallel-measure.py concurrency <PR番号>... [--repo OWNER/REPO]

**測るだけで、担当の起動は止めない**（設計の決定 8）。担当は Agent ツールで起動され、
サブエージェントは本体のプロセスの中で動くため、フックで本数を数える手がかりが無い。
数えられないものを拒否の条件にすると、止める必要の無い起動を止めるか、止めないまま
「機械が見ている」と読まれる。

**本数の既定値（予備・1 本の見込み・上限・スワップの閾値）を持つのはこのファイルだけである**
（設計の決定 7）。文書は値を写さず、引数の名前だけを書く。実際に測った値は実行計画の
「測った値」の表が残し、初期値を直す根拠になる。

**1 本は担当 1 つ（作業ツリー 1 つ、G3 の supervisor 1 つ）である。** その中で動く worker は
1 本の見込みに含め、`--running` には数えない。

終了コード:

    capacity      0 測れた / 2 引数の誤り / 3 `--meminfo` を読めない
    concurrency   0 測れた / 1 `gh pr view` が失敗した / 2 引数の誤り
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

# --- 既定値（ここだけが持つ） -----------------------------------------------
# 予備: 進行側の claude 本体（約 470MiB）と、同じ VM に常駐する他のプロセスの揺れ。
DEFAULT_RESERVE_MIB = 2048
# 1 本の見込み: 5〜6 本で落ちた後の cgroup の最大使用量 7.4GiB（#621）から、
# 1 本あたり約 1.2GiB に、テストとコンテナの山を足した。
DEFAULT_PER_LANE_MIB = 2048
# 上限: 5〜6 本で 2 回落ち、3 本では落ちなかった（#621）。収束レビューが共有する CLI も
# 同じ上限で抑える。
DEFAULT_MAX_LANES = 3
# スワップの空きの閾値（%）。下回れば 1 減らす。
DEFAULT_SWAP_FREE_MIN_PCT = 25

DEFAULT_MEMINFO = "/proc/meminfo"
DEFAULT_CGROUP_DIR = "/sys/fs/cgroup"
DEFAULT_PROC_CGROUP = "/proc/self/cgroup"
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


def emit(pairs: list[tuple[str, object]]) -> None:
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


def resolve_cgroup_dir(given: Optional[str], *, root: Optional[Path] = None,
                       proc_cgroup: Optional[Path] = None) -> Path:
    """`--cgroup-dir` が無いときだけ、自分の cgroup の位置を導く。

    コンテナの中では `/sys/fs/cgroup` がそのまま自分の cgroup だが、ホストでは
    `/proc/self/cgroup` の `0::<path>` が指す下にある。

    **`memory.events` の有無でこの 2 つを見分けられる。** kernel は `memory.*` を
    `CFTYPE_NOT_ON_ROOT` で置くため、cgroup v2 の根には `memory.events` が無い。
    あるということは、その位置がすでに根ではない＝自分の cgroup である。

    `root` と `proc_cgroup` はチェックのための差し替え口で、既定は上の 2 つの定数である。
    """
    if given is not None:
        return Path(given)
    root = Path(DEFAULT_CGROUP_DIR) if root is None else Path(root)
    proc_cgroup = Path(DEFAULT_PROC_CGROUP) if proc_cgroup is None else Path(proc_cgroup)
    if (root / "memory.events").exists():
        return root
    try:
        for line in proc_cgroup.read_text(encoding="utf-8").splitlines():
            if line.startswith("0::"):
                relative = line[3:].strip().lstrip("/")
                if relative:
                    return root / relative
    except OSError:
        pass
    return root


def read_oom_kill(cgroup_dir: Path) -> object:
    """`memory.events` の `oom_kill`。読めなければ `unknown` で続ける。"""
    try:
        text = (cgroup_dir / "memory.events").read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN
    for line in text.splitlines():
        fields = line.split()
        # `max 0` の行もあるため、キーの一致で選ぶ。
        if len(fields) == 2 and fields[0] == "oom_kill":
            try:
                return int(fields[1])
            except ValueError:
                return UNKNOWN
    return UNKNOWN


def read_cgroup_available_mib(cgroup_dir: Path) -> object:
    """cgroup の残り。上限が無ければ `max`、読めなければ `unknown`。"""
    try:
        limit = (cgroup_dir / "memory.max").read_text(encoding="utf-8").strip()
    except OSError:
        return UNKNOWN
    if limit == NO_LIMIT:
        return NO_LIMIT
    try:
        limit_bytes = int(limit)
        current = int((cgroup_dir / "memory.current").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return UNKNOWN
    return max(0, (limit_bytes - current) // (1024 * 1024))


def lanes_by_memory(available_mib: int, running: int, reserve_mib: int,
                    per_lane_mib: int) -> int:
    """空きから導いた総本数。

    空きは動いている担当の使用量を引いた後の値なので、割った値は**追加できる本数**である。
    `running` を足して総本数にしてから上限と比べる。
    """
    return running + max(0, (available_mib - reserve_mib) // per_lane_mib)


def run_capacity(args: argparse.Namespace) -> int:
    if args.per_lane_mib <= 0:
        raise Usage("--per-lane-mib は 1 以上である")
    mem = read_meminfo(Path(args.meminfo))
    mem_available_mib = mem["MemAvailable"] // 1024
    swap_total_mib = mem.get("SwapTotal", 0) // 1024
    swap_free_mib = mem.get("SwapFree", 0) // 1024

    cgroup_dir = resolve_cgroup_dir(args.cgroup_dir)
    cgroup_available = read_cgroup_available_mib(cgroup_dir)
    oom_kill = read_oom_kill(cgroup_dir)

    if oom_kill == UNKNOWN or args.oom_baseline is None:
        oom_kill_increased = UNKNOWN
    else:
        oom_kill_increased = "yes" if oom_kill > args.oom_baseline else "no"

    # cgroup に上限があるホストでは、VM に空きがあっても cgroup の残りを超えた時点で落ちる。
    budget_mib = mem_available_mib
    if isinstance(cgroup_available, int):
        budget_mib = min(budget_mib, cgroup_available)

    by_memory = lanes_by_memory(budget_mib, args.running, args.reserve_mib,
                                args.per_lane_mib)
    allowed = min(args.max_lanes, by_memory)
    limited_by: list[str] = []
    if by_memory <= args.max_lanes:
        limited_by.append("memory")
    if args.max_lanes <= by_memory:
        limited_by.append(NO_LIMIT)

    if swap_total_mib > 0 and swap_free_mib * 100 < swap_total_mib * args.swap_free_min_pct:
        allowed -= 1
        limited_by.append("swap_low")

    if oom_kill_increased == "yes":
        # 増えた見直しだけは 0 を許す。`running` が 0 か 1 のとき、下限の 1 を当てると
        # 「今の本数 − 1 以下」を満たせない。
        lowered = max(0, min(allowed, args.running - 1))
        if lowered < allowed:
            limited_by.append("oom_kill_increased")
        allowed = lowered
    else:
        if allowed < 1:
            allowed = 1
            limited_by.append("floor")

    emit([
        ("mem_available_mib", mem_available_mib),
        ("swap_total_mib", swap_total_mib),
        ("swap_free_mib", swap_free_mib),
        ("cgroup_available_mib", cgroup_available),
        ("oom_kill", oom_kill),
        ("oom_kill_increased", oom_kill_increased),
        ("running", args.running),
        ("by_memory", by_memory),
        ("allowed", allowed),
        ("limited_by", ",".join(limited_by)),
    ])
    return 0


# --- concurrency ------------------------------------------------------------

def parse_time(value: object, *, what: str) -> _dt.datetime:
    if not isinstance(value, str):
        # `--input` の JSON は数値も真偽も配列も持てる。素通しすると `value.strip()`
        # が `AttributeError` を出し、`gh pr view` の失敗と同じ終了コード 1 で落ちる。
        raise Usage(f"{what} が文字列ではない: {value!r}")
    try:
        parsed = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise Usage(f"{what} が ISO8601 ではない: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def format_time(value: _dt.datetime) -> str:
    return value.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_pull_requests(numbers: list[int], repo: Optional[str]) -> list[dict]:
    """`gh pr view` で読むだけ。書き込む副コマンドは呼ばない。"""
    records = []
    for number in numbers:
        command = ["gh", "pr", "view", str(number), "--json", GH_JSON_FIELDS]
        if repo:
            command += ["--repo", repo]
        try:
            proc = subprocess.run(command, capture_output=True, text=True)
        except OSError as exc:
            # 起動できないのも `gh pr view` の失敗である。空の配列を返すと
            # `measure` の `min()` が空列で落ちるため、ここで終了コード 1 にする。
            print(f"gh を実行できない: {exc}", file=sys.stderr)
            raise SystemExit(1)
        if proc.returncode != 0:
            print(f"gh pr view {number} が失敗した: {proc.stderr.strip()}", file=sys.stderr)
            raise SystemExit(1)
        records.append(json.loads(proc.stdout))
    return records


def intervals(records: list[dict], now: _dt.datetime) -> list[tuple[_dt.datetime, _dt.datetime]]:
    """区間は `createdAt` から、`mergedAt`・`closedAt`・`--now` の最初に値のあるものまで。"""
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
    """区間を開始と終了の事象列に変換する。

    同じ時刻に閉じる区間と開く区間は重ならない。閉じるほうを先に数えるため、
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
    """区間と走査結果を表示用の集計値に整形する。"""
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    span_seconds = (end - start).total_seconds()
    if span_seconds > 0:
        pct = (Decimal(overlap_seconds) / Decimal(span_seconds) * 100).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP)
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


def measure(spans: list[tuple[_dt.datetime, _dt.datetime]]) -> dict[str, object]:
    """区間を並行度の集計値へ変換する。"""
    events = build_events(spans)
    max_open, overlap_seconds = scan_events(events)
    return summarize_measurement(spans, max_open, overlap_seconds)


def run_concurrency(args: argparse.Namespace) -> int:
    now = parse_time(args.now, what="--now") if args.now else _dt.datetime.now(_dt.timezone.utc)
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

    measured = measure(intervals(records, now))
    emit([("prs", len(records))] + list(measured.items()))
    return 0


# --- 入口 -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parallel-measure.py", description="並列の本数と並行度を測る（#621）")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capacity", help="起動してよい本数を出す")
    cap.add_argument("--running", type=non_negative_int, default=0,
                     help="いま動いている担当（作業ツリー 1 つ分）の数")
    cap.add_argument("--oom-baseline", type=non_negative_int, default=None,
                     help="実行計画に控えた oom_kill の起点")
    cap.add_argument("--reserve-mib", type=non_negative_int, default=DEFAULT_RESERVE_MIB)
    cap.add_argument("--per-lane-mib", type=non_negative_int, default=DEFAULT_PER_LANE_MIB)
    cap.add_argument("--max", dest="max_lanes", type=non_negative_int,
                     default=DEFAULT_MAX_LANES)
    cap.add_argument("--swap-free-min-pct", type=non_negative_int,
                     default=DEFAULT_SWAP_FREE_MIN_PCT)
    cap.add_argument("--meminfo", default=DEFAULT_MEMINFO)
    cap.add_argument("--cgroup-dir", default=None)
    cap.set_defaults(handler=run_capacity)

    con = sub.add_parser("concurrency", help="並行度と最大同時本数を出す")
    con.add_argument("numbers", nargs="*", type=non_negative_int,
                     help="対象の Pull Request の番号")
    con.add_argument("--repo", default=None)
    con.add_argument("--input", default=None,
                     help="gh を呼ばず、番号と時刻の JSON を読む")
    con.add_argument("--now", default=None,
                     help="開いたままの Pull Request の区間の終わり")
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
