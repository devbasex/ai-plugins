#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""実行の要約（収束ループ共通層、#662）。

cross-review / cross-refactoring の 1 回の実行が、何分かかりどう終わったかを、
**作業ツリーの外**（利用者の状態ディレクトリ）へ残し、束ねて出す。

入口は 2 つある。

| 入口 | 呼ぶ側 | 何をするか |
| --- | --- | --- |
| `after_save(path, state, kind, extra)` | 状態を保存した直後の `state.py` / `statefile.save` | 要約を書き直す。**例外を外へ出さない** |
| `run_metrics.py aggregate` | 利用者 | 要約を束ねて Markdown の表で出す |

**保存のたびに書き直す**（設計の決定 5）。終了のときだけ書くと、報告まで届かずに
止まった実行が要約を持たない。途中で止まった実行も `final: null` と `last_saved_at` を持つ。

**プロンプト・本文・`detail` を入れない**（決定 7）。`detail` は err.log の抜粋で、
認証の失敗の行に鍵の一部が出うる。

キーの形は `issues/issue-662-598-537-619-584-583-design-contracts.md` の「実行の要約」にある。

    run_metrics.py aggregate [--since <日付>] [--until <日付>] [--repo <owner>/<repo>]
                             [--kind <種類>] [--version <版>]
                             [--by total|round-count|reason] [--dir <置き場所>]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import functools
import json
import os
import pathlib
import re
import sys
from typing import Any, Callable, Mapping, Optional

_LIB = pathlib.Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import clock  # noqa: E402
import monitor_outcome  # noqa: E402

SCHEMA = 1
KINDS = ("cross-review", "cross-refactoring")
_ID_PREFIX = {"cross-review": "pr", "cross-refactoring": "rf"}

# 種類ごとの鍵（cross-review の `measure`、cross-refactoring の `phases`）を返す関数。
Extra = Callable[[pathlib.Path, dict, list], dict]


# ---------------- 置き場所 ----------------

def metrics_dir(env: Optional[Mapping[str, str]] = None) -> pathlib.Path:
    """要約の置き場所。**`NDF_METRICS=0` は見ない**（集計は止めた後も読める）。

    `NDF_METRICS_DIR` → `$XDG_STATE_HOME/ndf/metrics` → `$HOME/.local/state/ndf/metrics`。
    空の値は無いものとして次へ進む。
    """
    env = os.environ if env is None else env
    if env.get("NDF_METRICS_DIR"):
        return pathlib.Path(env["NDF_METRICS_DIR"])
    if env.get("XDG_STATE_HOME"):
        return pathlib.Path(env["XDG_STATE_HOME"]) / "ndf" / "metrics"
    home = env.get("HOME") or str(pathlib.Path.home())
    return pathlib.Path(home) / ".local" / "state" / "ndf" / "metrics"


def _disabled(env: Mapping[str, str]) -> bool:
    return env.get("NDF_METRICS", "") == "0"


# ---------------- 時刻 ----------------

def _iso_seconds(value: Any) -> Optional[str]:
    """読めた時刻を、元のタイムゾーンのまま秒までで書き直す。読めなければ `None`。"""
    parsed = clock.parse(value)
    return None if parsed is None else parsed.isoformat(timespec="seconds")


def _whole_seconds(start: Any, end: Any) -> Optional[int]:
    """2 つの時刻の差を整数の秒で。どちらかが読めなければ `None`。"""
    seconds = clock.seconds_between(start, end)
    return None if seconds is None else int(round(seconds))


# ---------------- 要約の組み立て ----------------

@functools.lru_cache(maxsize=1)
def _ndf_version() -> Optional[str]:
    """プラグインの版。**読めなければ `null`**（配布の形によって置き場所が無い）。"""
    try:
        data = json.loads(
            (_LIB.parents[1] / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, str) else None


def run_id(state: dict, kind: str) -> Optional[int]:
    """cross-review は `pr_history[0].pr`（状態ファイルの鍵）、cross-refactoring は `id`。"""
    if kind == "cross-review":
        for entry in state.get("pr_history") or []:
            if isinstance(entry, dict) and isinstance(entry.get("pr"), int):
                return entry["pr"]
        value = state.get("current_pr")
    else:
        value = state.get("id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def summary_path(state: dict, kind: str, base: Optional[pathlib.Path] = None) -> pathlib.Path:
    """`<置き場所>/<owner>--<repo>/<kind>-<pr|rf><id>-<開始時刻の UTC>.json`。

    **開始時刻を名前に入れる。** 同じ番号で回し直した実行を別のファイルにし、同じ実行の
    保存では同じファイルを上書きする。
    """
    started = clock.parse(state.get("started_at"))
    if started is None:
        raise ValueError("状態ファイルに started_at がありません")
    ident = run_id(state, kind)
    if ident is None:
        raise ValueError("状態ファイルから実行の番号を決められません")
    repo = str(state.get("repo") or "unknown")
    stamp = started.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = metrics_dir() if base is None else base
    return base / repo.replace("/", "--") / f"{kind}-{_ID_PREFIX[kind]}{ident}-{stamp}.json"


def ended_at(state: dict) -> Optional[str]:
    """実行の終了。**終わっていない実行（`final` が `null`）は持たない。**

    途中の値を置くと、止まった実行が所要の分布へ混ざる。
    """
    return _iso_seconds(state.get("ended_at")) if state.get("final") is not None else None


def round_spans(state: dict) -> list[dict]:
    """ラウンドの開始と終了。終了はそのラウンドの値、次のラウンドの開始、全体の終了の順。"""
    return _round_rows(state, ended_at(state))


def _round_rows(state: dict, run_end: Optional[str]) -> list[dict]:
    rounds = [r for r in state.get("rounds") or [] if isinstance(r, dict)]
    out: list[dict] = []
    for index, entry in enumerate(rounds):
        end = _iso_seconds(entry.get("ended_at"))
        if end is None and index + 1 < len(rounds):
            end = _iso_seconds(rounds[index + 1].get("started_at"))
        if end is None and index + 1 == len(rounds):
            end = run_end
        row: dict[str, Any] = {"round": entry.get("round", index + 1)}
        if "kind" in entry:
            row["kind"] = entry.get("kind")
        row["started_at"] = _iso_seconds(entry.get("started_at"))
        row["ended_at"] = end
        out.append(row)
    return out


_REVIEW_STEM = re.compile(r"-pr(\d+)$")


def _launches(state_path: pathlib.Path, state: dict, kind: str,
              started_at: Optional[str], run_end: Optional[str]) -> list[dict]:
    """監視の記録のうち、この実行の起動だけを `detail` を除いて返す。

    **一時ディレクトリは回し直しで使い回され、記録は追記だけで消えない。** 開始より前
    （終わった実行は終了より後も）の行を数えると、前の実行の起動が混ざる。cross-review は
    stem が状態ファイルの番号で決まるため、番号の違う行も外す。
    """
    start, end = clock.parse(started_at), clock.parse(run_end)
    ident = run_id(state, kind)
    rows: list[dict] = []
    for row in monitor_outcome.read_journal(state_path.parent):
        began = clock.parse(row.get("started_at"))
        if start is not None and (began is None or began < start):
            continue
        if end is not None and began is not None and began > end:
            continue
        if kind == "cross-review":
            m = _REVIEW_STEM.search(str(row.get("stem") or ""))
            if m is None or int(m.group(1)) != ident:
                continue
        rows.append({k: v for k, v in row.items() if k != "detail"})
    return rows


def build_summary(state_path: pathlib.Path, state: dict, kind: str,
                  extra: Optional[Extra] = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"要約の種類として知らない値です: {kind!r}")
    final = state.get("final")
    started_at = _iso_seconds(state.get("started_at"))
    end = ended_at(state)
    launches = _launches(pathlib.Path(state_path), state, kind, started_at, end)
    summary: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": kind,
        "repo": state.get("repo"),
        "id": run_id(state, kind),
        "ndf_version": _ndf_version(),
        "host": state.get("host"),
        "started_at": started_at,
        "ended_at": end,
        "last_saved_at": clock.now_iso(),
        "final": final,
        "wall_clock_seconds": _whole_seconds(started_at, end),
        "rounds": _round_rows(state, end),
        "launches": launches,
    }
    if extra is not None:
        summary.update(extra(pathlib.Path(state_path), state, launches))
    return summary


# ---------------- 書き出し ----------------

def write_summary(state_path: pathlib.Path, state: dict, kind: str,
                  extra: Optional[Extra] = None,
                  env: Optional[Mapping[str, str]] = None,
                  ) -> tuple[Optional[pathlib.Path], Optional[str]]:
    """要約を原子的に書く。書いたパスか、書かなかった理由を返す。"""
    env = os.environ if env is None else env
    if _disabled(env):
        return None, "NDF_METRICS=0"
    try:
        path = summary_path(state, kind, base=metrics_dir(env))
    except ValueError as exc:
        return None, str(exc)
    summary = build_summary(state_path, state, kind, extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path.resolve(), None


def after_save(state_path: pathlib.Path, state: dict, kind: str,
               extra: Optional[Extra] = None) -> None:
    """状態の保存の後に呼ぶ。**失敗しても収束ループを止めない**（AC16）。"""
    try:
        write_summary(state_path, state, kind, extra)
    except Exception as exc:  # noqa: BLE001
        print(f"⚠ 計測の要約を書けません: {exc}", file=sys.stderr)


def report_line(state_path: pathlib.Path, state: dict, kind: str,
                extra: Optional[Extra] = None) -> str:
    """`report` の最後の行。**要約を書き直してから**そのパスか理由を返す。"""
    try:
        path, reason = write_summary(state_path, state, kind, extra)
    except Exception as exc:  # noqa: BLE001
        path, reason = None, str(exc)
    if path is not None:
        return f"計測の要約: {path}"
    return f"計測の要約: 書いていません（{reason}）"


# ---------------- 集計 ----------------

def _load_summaries(base: pathlib.Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    broken = 0
    if not base.is_dir():
        return rows, broken
    for path in sorted(base.glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            broken += 1
            continue
        if not isinstance(data, dict) or data.get("kind") not in KINDS:
            broken += 1
            continue
        rows.append(data)
    return rows, broken


def _bound(value: Optional[str], *, upper: bool) -> Optional[_dt.datetime]:
    """日付だけのときは地方時の 0 時。`--until` はその日を含める。"""
    if not value:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        day = _dt.datetime.fromisoformat(value).astimezone()
        return day + _dt.timedelta(days=1) if upper else day
    parsed = clock.parse(value)
    if parsed is None:
        raise SystemExit(f"日付として読めません: {value}")
    return parsed


def _within_time_bound(started: Optional[_dt.datetime],
                       since: Optional[_dt.datetime],
                       until: Optional[_dt.datetime],
                       until_exclusive: bool) -> bool:
    if since and (started is None or started < since):
        return False
    if until and (started is None or (started >= until if until_exclusive else started > until)):
        return False
    return True


def _select(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    since, until = _bound(args.since, upper=False), _bound(args.until, upper=True)
    until_exclusive = bool(args.until and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.until))
    # (args の属性名, 行のキー)。指定の無い（偽の）軸は絞り込まない
    equality_filters = (("repo", "repo"), ("kind", "kind"), ("version", "ndf_version"))
    out = []
    for row in rows:
        started = clock.parse(row.get("started_at"))
        if not _within_time_bound(started, since, until, until_exclusive):
            continue
        if any(getattr(args, attr) and row.get(key) != getattr(args, attr)
               for attr, key in equality_filters):
            continue
        out.append(row)
    return out


def _quantile(sorted_values: list[float], q: float) -> float:
    """線形補間の分位点（`statistics.quantiles` の `inclusive` と同じ）。"""
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    low = int(pos)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (pos - low)


def _wall_minutes(row: dict) -> Optional[float]:
    seconds = row.get("wall_clock_seconds")
    if row.get("final") is None or not isinstance(seconds, (int, float)):
        return None
    return seconds / 60


def _one_decimal(value: float) -> str:
    return f"{value:.1f}"


def _table(header: list[str], rows: list[list[str]]) -> str:
    align = ["---"] + ["---:" for _ in header[1:]]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _finished_rows(rows: list[dict]) -> list[list[str]]:
    out: list[list[str]] = []
    for kind in KINDS:
        minutes = sorted(m for r in rows if r.get("kind") == kind and (m := _wall_minutes(r)) is not None)
        if minutes:
            out.append([kind, str(len(minutes)), _one_decimal(_quantile(minutes, 0.5)),
                        _one_decimal(_quantile(minutes, 0.75)), _one_decimal(_quantile(minutes, 0.9)),
                        _one_decimal(minutes[-1]), _one_decimal(sum(minutes))])
    return out


def _unfinished_rows(rows: list[dict]) -> list[list[str]]:
    out: list[list[str]] = []
    for kind in KINDS:
        pending = [r for r in rows if r.get("kind") == kind and r.get("final") is None]
        if pending:
            out.append([f"終わっていない（{kind}）", str(len(pending)),
                        "—", "—", "—", "—", "—"])
    return out


def _by_total(rows: list[dict]) -> str:
    return _table(["種類", "件数", "中央値（分）", "p75（分）", "p90（分）", "最大（分）", "合計（分）"],
                  _finished_rows(rows) + _unfinished_rows(rows))


def _round_count_bucket(count: int) -> Optional[str]:
    """ラウンド数の表示区分。1 / 2 / 3 以上のどれでもなければ `None`（対象外）。"""
    if count >= 3:
        return "3 以上"
    if count in (1, 2):
        return str(count)
    return None


def _by_round_count(rows: list[dict]) -> str:
    buckets: dict[str, list[float]] = {"1": [], "2": [], "3 以上": []}
    for row in rows:
        minutes = _wall_minutes(row)
        if row.get("kind") != "cross-review" or minutes is None:
            continue
        key = _round_count_bucket(len(row.get("rounds") or []))
        if key is not None:
            buckets[key].append(minutes)
    table = [[k, str(len(v)), _one_decimal(_quantile(sorted(v), 0.5))] for k, v in buckets.items() if v]
    return _table(["ラウンド数", "件数", "中央値（分）"], table)


def _by_reason(rows: list[dict]) -> str:
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        for launch in row.get("launches") or []:
            if not isinstance(launch, dict):
                continue
            key = (str(row.get("kind")), str(launch.get("agent")), str(launch.get("reason")))
            counts[key] = counts.get(key, 0) + 1
    table = [[*key, str(n)] for key, n in sorted(counts.items())]
    return _table(["種類", "担当", "理由", "起動回数"], table)


_BY = {"total": _by_total, "round-count": _by_round_count, "reason": _by_reason}


def aggregate_table(base: pathlib.Path, args: argparse.Namespace) -> str:
    rows, broken = _load_summaries(base)
    text = _BY[args.by](_select(rows, args))
    if broken:
        text += f"\n\n読めない要約: {broken} 件（飛ばしました）"
    return text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="実行の要約を束ねて出す（#662）")
    sub = p.add_subparsers(dest="cmd", required=True)
    ag = sub.add_parser("aggregate", help="要約を種類・ラウンド数・理由で束ねて Markdown の表で出す")
    ag.add_argument("--since", help="開始時刻の下限（日付だけならその日の 0 時から）")
    ag.add_argument("--until", help="開始時刻の上限（日付だけならその日を含む）")
    ag.add_argument("--repo", help="owner/repo")
    ag.add_argument("--kind", choices=KINDS)
    ag.add_argument("--version", help="NDF の版（要約の ndf_version と一致するもの）")
    ag.add_argument("--by", choices=tuple(_BY), default="total")
    ag.add_argument("--dir", help="要約の置き場所（既定は NDF_METRICS_DIR などから決める）")
    return p


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    base = pathlib.Path(args.dir) if args.dir else metrics_dir()
    print(aggregate_table(base, args))


if __name__ == "__main__":
    main()
