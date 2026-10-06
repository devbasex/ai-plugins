"""配分テーブル（項目 1 件あたりの所要の見積り）と、その材料の履歴（#933）。

**配分テーブルは保存しない**（#933 決定 7）。改修計画のたびに履歴から集計する。集計した
値を別に持つと、履歴と表が食い違う。履歴は実行ごとに 1 行の JSONL で、リポジトリ
ごとに 1 ファイルに分ける。テストの所要はリポジトリで大きく違い、混ぜると見積りが
外れる。

履歴の行は 2 種類ある（#1385 決定 3）。`report` が最終ゲートを通った実行ごとに書く実行の行（`kind: run`）と、`init` が
着手前のテストの後に成否を問わず書く着手前のテストの行（`kind: init_test`）である。配分テーブルは実行の行だけを集計し、
着手前のテストの行は CI に任せる戦略の着手前の上限（範囲テストの所要）にだけ使う。`kind` を持たない旧い行は実行の行として読む。

**根（`base`）は必ず引数で受ける。** ここで `run_metrics.metrics_dir()` を呼ぶと、
テストが利用者の `~/.local/state` を読み書きする。根を決めるのは呼ぶ側である。
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
from typing import Any, Optional

from .items import DEFERRED, REVERTED
from .phases import phase_seconds
from .rounds import whole_records

HISTORY_NAME = "cross-refactoring-allocation.jsonl"

# `.resolve()` を通す。Kiro CLI は `.kiro/skills/<名前>` を symlink にするため、
# 解かずに `parents[]` を数えると Skill のディレクトリへ届かない。
DEFAULTS_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "allocation-defaults.json"

# 種類ごとに遡る行の数。**その種類を含む行**で数えるため、珍しい手法も 10 回分まで
# 遡れる（設計の「データ構造: 履歴と配分テーブル」）。
WINDOW = 10

# 履歴の行の形の版。形を変えたら上げる。2 で `kind` と着手前のテストの行を足した（#1385）。
ROW_SCHEMA = 2

RUN_KIND = "run"
INIT_TEST_KIND = "init_test"

_STRUCTURE_PREFIX = "structure/"


def history_path(base: pathlib.Path, repo: str) -> pathlib.Path:
    """リポジトリ（`<owner>/<repo>`）の履歴のパス。`/` はディレクトリ名に使えないため `--` へ替える。"""
    return pathlib.Path(base) / str(repo).replace("/", "--") / HISTORY_NAME


def load_defaults(path: pathlib.Path = DEFAULTS_PATH) -> dict[str, float]:
    """初期値（分）を `{"test", "structure", "verify", "fix"}` で返す。

    ファイルは出所の URL と式も持つが、計算に要るのは値だけである。
    """
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {name: float(entry["minutes"]) for name, entry in data["values"].items()}


def read_history(path: pathlib.Path) -> list[dict[str, Any]]:
    """履歴の行を古い順に返す。**読めない行は飛ばす。**

    1 行の破損で見積りの全体を失うより、残りの行で集計するほうがよい。ファイルが
    無い・全行が読めないときは空を返し、呼ぶ側が初期値へ落とす。
    """
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def run_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """実行の行だけ。`kind` を持たない旧い行も実行の行として読む（I9）。"""
    return [row for row in rows if row.get("kind", RUN_KIND) == RUN_KIND]


def _num(value: Any) -> float:
    """数として読めない値は 0 とみなす。手で直された行でも集計を止めない。"""
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _per_unit(samples: list[tuple[float, float]]) -> Optional[float]:
    """`(件数, 秒)` の並びのうち**件数が正のもの**の直近 `WINDOW` 個から、1 件あたりの分を出す。

    件数 0 の行は数えない。0 件の行を窓に入れると、珍しい種類の古い標本が押し出される。
    """
    usable = [(count, seconds) for count, seconds in samples if count > 0][-WINDOW:]
    total_count = sum(count for count, _ in usable)
    if total_count <= 0:
        return None
    return sum(seconds for _, seconds in usable) / total_count / 60


def _kind_samples(rows: list[dict[str, Any]], kind: str) -> list[tuple[float, float]]:
    samples = []
    for row in rows:
        entry = (row.get("kinds") or {}).get(kind) if isinstance(row.get("kinds"), dict) else None
        if isinstance(entry, dict):
            samples.append((_num(entry.get("count")), _num(entry.get("seconds"))))
    return samples


def _structure_samples(rows: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """行ごとに `structure/*` を合算した標本。手法の標本が無いときの代わりに使う。"""
    samples = []
    for row in rows:
        kinds = row.get("kinds") if isinstance(row.get("kinds"), dict) else {}
        count = seconds = 0.0
        for name, entry in kinds.items():
            if str(name).startswith(_STRUCTURE_PREFIX) and isinstance(entry, dict):
                count += _num(entry.get("count"))
                seconds += _num(entry.get("seconds"))
        samples.append((count, seconds))
    return samples


def _section_samples(rows: list[dict[str, Any]], section: str, count_key: str) -> list[tuple[float, float]]:
    samples = []
    for row in rows:
        entry = row.get(section)
        if isinstance(entry, dict):
            samples.append((_num(entry.get(count_key)), _num(entry.get("seconds"))))
    return samples


def build_table(rows: list[dict[str, Any]], defaults: dict[str, float]) -> dict[str, Any]:
    """履歴の行から配分テーブル（分）を集計する。値が出ない種類は初期値で埋める。

    `source` は行が 1 行でもあれば `history` である。個々の種類が初期値へ落ちても
    `history` のまま残す。**何を材料にしたか**を表す目印であり、種類ごとの出所は
    改修計画の報告が値から読めばよい。
    """

    def pick(value: Optional[float], name: str) -> float:
        return float(defaults[name]) if value is None else value

    rows = run_rows(rows)

    kinds: dict[str, float] = {}
    names = sorted(
        {
            str(name)
            for row in rows
            if isinstance(row.get("kinds"), dict)
            for name in row["kinds"]
            if str(name).startswith(_STRUCTURE_PREFIX)
        }
    )
    for name in names:
        value = _per_unit(_kind_samples(rows, name))
        if value is not None:
            kinds[name] = value
    return {
        "source": "history" if rows else "defaults",
        "test": pick(_per_unit(_kind_samples(rows, "test")), "test"),
        "verify": pick(_per_unit(_section_samples(rows, "verify", "items")), "verify"),
        "fix": pick(_per_unit(_section_samples(rows, "fix", "launches")), "fix"),
        "structure": pick(_per_unit(_structure_samples(rows)), "structure"),
        "kinds": kinds,
    }


def lookup(table: dict[str, Any], kind: str) -> float:
    """種類の 1 件あたりの分。標本の無い手法は `structure/*` をまとめた値へ落とす（#933 決定 6）。"""
    if kind in ("test", "verify", "fix"):
        return float(table[kind])
    return float((table.get("kinds") or {}).get(kind, table["structure"]))


def _parse_time(value: Any) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _ended_at(state: dict[str, Any]) -> Optional[str]:
    """実行の終わりの時刻。無ければ手順の終わりの最も遅いもので代える。"""
    if state.get("ended_at"):
        return str(state["ended_at"])
    ends = [
        (parsed, str(span["ended_at"]))
        for span in (state.get("phases") or {}).values()
        if isinstance(span, dict) and (parsed := _parse_time(span.get("ended_at"))) is not None
    ]
    if not ends:
        return None
    # 時差の有無が混ざると比べられないため、UTC へそろえてから比べる。
    return max(ends, key=lambda pair: pair[0].astimezone(_dt.timezone.utc))[1]


def _run_id(state: dict[str, Any]) -> str:
    """実行の名前。`init` の開始を UTC で持つため、同じ Pull Request の実行どうしで重ならない。

    `statefile.now()` は時差を持たない現地時刻を書く。`astimezone` は時差の無い値を
    現地時刻として読むため、そのまま UTC へ直せる。
    """
    started = _parse_time(state.get("started_at"))
    stamp = started.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") if started else "unknown"
    return f"rf{state.get('id')}-{stamp}"


def _kind_seconds(state: dict[str, Any]) -> dict[str, dict[str, float]]:
    """種類ごとの `{"count", "seconds"}`。取り消し・見送りの項目と、コミットの無い欄は数えない。"""
    kinds: dict[str, dict[str, float]] = {}

    def add(kind: str, seconds: Any) -> None:
        bucket = kinds.setdefault(kind, {"count": 0, "seconds": 0})
        bucket["count"] += 1
        bucket["seconds"] += _num(seconds)

    for item in state.get("items") or []:
        if item.get("status") in (REVERTED, DEFERRED):
            continue
        commits = item.get("commits") or {}
        seconds = item.get("seconds") or {}
        if commits.get("test"):
            add("test", seconds.get("test"))
        if commits.get("implement") and item.get("kind"):
            add(str(item["kind"]), seconds.get("implement"))
    return kinds


def _seconds_from_start(state: dict[str, Any], at: Any) -> Optional[int]:
    """`started_at` から `at`（実行の終わり）までの秒。どちらかを読めなければ `None`。

    履歴の 1 行の `elapsed_seconds` に使う。`commands.report` の所要（最終ゲートの終わりまで）とは終わりの取り方が違う。
    """
    started, ended = _parse_time(state.get("started_at")), _parse_time(at)
    if started and ended:
        return int((ended.astimezone(_dt.timezone.utc) - started.astimezone(_dt.timezone.utc)).total_seconds())
    return None


# バッファの区分（`plan.reserve` のキー。#1743 の F4）。報告と実行の行が同じ順で読む。
RESERVE_KEYS = ("danger_whole_test", "final_whole_test", "fix", "final_fix")


def danger_seconds(state: dict[str, Any]) -> Optional[float]:
    """危険フラグの全体テストを走らせた秒の和（全巡）。走らせていなければ `None`。"""
    ran = [_num(r.get("seconds")) for r in whole_records(state) if r.get("ran")]
    return round(sum(ran), 1) if ran else None


def reserve_usage(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    """バッファの区分ごとの「取った・使った・残った」秒（決定 8）。`plan.reserve` が無ければ空。

    使った秒の出所: 危険フラグの全体テストは検証の中の全体テストの秒、最終ゲートの全体テストは最終ゲートのテストの判定
    （手元の全体テストか CI の待ち。使い回したら 0）、修正は `fix_stats.seconds`、最終ゲート修正は `final-fix` の手順の秒。
    """
    reserve = (state.get("plan") or {}).get("reserve") or {}
    if not reserve:
        return {}
    checks = (state.get("final_gate") or {}).get("checks") or []
    used = {
        "danger_whole_test": danger_seconds(state) or 0.0,
        "final_whole_test": sum(_num(c.get("seconds")) for c in checks if c.get("mode") in ("test", "ci")),
        "fix": _num((state.get("fix_stats") or {}).get("seconds")),
        "final_fix": _num(phase_seconds(state).get("final-fix")),
    }
    usage = {}
    for key in RESERVE_KEYS:
        reserved = round(_num(reserve.get(key)) * 60)
        spent = round(used[key])
        usage[key] = {"reserved_seconds": reserved, "used_seconds": spent, "unused_seconds": max(0, reserved - spent)}
    return usage


def build_row(state: dict[str, Any]) -> dict[str, Any]:
    """状態（版 2）から履歴の 1 行を作る。

    項目の所要は**進行側がコミットの時刻から測った `items[].seconds`** を使う
    （#933 決定 8）。担当の申告は使わない。テストはコミットがある項目だけ、実装も
    コミットがある項目だけを数える。取り消し・見送りの項目は数えない。取り消しは
    コミットの欄を残したまま状態だけを変えるため、欄だけを見ると所要の無い件数で
    1 件あたりが下がる。
    """
    kinds = _kind_seconds(state)
    at = _ended_at(state)
    elapsed = _seconds_from_start(state, at)
    verify = state.get("verify_stats") or {}
    fix = state.get("fix_stats") or {}
    baseline = state.get("baseline_test") or {}
    return {
        "schema": ROW_SCHEMA,
        "kind": RUN_KIND,
        "run": _run_id(state),
        "at": at,
        # 状態は Pull Request の番号を `current_pr` に持つ。`pr` があればそちらを使う。
        "pr": state.get("pr", state.get("current_pr")),
        "implementer": state.get("implementer"),
        "budget_minutes": state.get("budget_minutes"),
        "elapsed_seconds": elapsed,
        "phases": phase_seconds(state),
        "kinds": kinds,
        "verify": {"items": verify.get("items", 0), "seconds": verify.get("seconds", 0)},
        "fix": {"launches": fix.get("launches", 0), "seconds": fix.get("seconds", 0)},
        "whole_test": {
            # 全体テストを走らせたときだけ。範囲テストの秒を全体テストの所要として読ませない（#1385 決定 6）。
            "init": baseline.get("seconds") if baseline.get("mode") != "scope" else None,
            "danger": danger_seconds(state),
            "final": (state.get("final_gate") or {}).get("whole_test_seconds"),
        },
        # バッファの使われ方と止まっていた秒（#1743 の F4・F5）。`build_table` は読まない（I11）
        "reserve": reserve_usage(state),
        "paused_seconds": (state.get("pause") or {}).get("seconds"),
    }


def init_test_row(baseline: dict[str, Any], strategy: str, budget_minutes: Optional[int], pr: Optional[int]) -> dict[str, Any]:
    """着手前のテストの行（`kind: init_test`）。打ち切りなら `seconds` は上限の秒（下限の実測）で `timed_out` が真。"""
    mode = baseline.get("mode")
    locations = baseline.get("locations") if mode == "scope" else None
    return {
        "schema": ROW_SCHEMA,
        "kind": INIT_TEST_KIND,
        "at": baseline.get("checked_at"),
        "pr": pr,
        "budget_minutes": budget_minutes,
        "strategy": strategy,
        "mode": mode,
        "locations": sorted({str(p) for p in locations}) if locations is not None else None,
        "seconds": baseline.get("seconds"),
        "timed_out": bool(baseline.get("timed_out")),
    }


def scope_seconds(rows: list[dict[str, Any]], strategy: str, locations: list[str]) -> Optional[float]:
    """同じ戦略・同じテストの置き場所の集合の着手前のテストの行のうち、直近 `WINDOW` 行の秒の最大（I7・決定 4）。
    一致する行が無ければ `None`。`schema` が 2 未満の行と `kind` の無い行は使わない（I9）。"""
    want = sorted({str(p) for p in locations or []})
    if not want:
        return None
    values: list[float] = []
    for row in rows:
        if _num(row.get("schema")) < 2 or row.get("kind") != INIT_TEST_KIND:
            continue
        if row.get("strategy") != strategy or row.get("mode") != "scope":
            continue
        locs = row.get("locations")
        if not isinstance(locs, list) or sorted({str(p) for p in locs}) != want:
            continue
        value = _num(row.get("seconds"))
        if value > 0:
            values.append(value)
    values = values[-WINDOW:]
    return max(values) if values else None


def append_row(path: pathlib.Path, row: dict[str, Any]) -> None:
    """履歴へ 1 行追記する。親のディレクトリが無ければ作る。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
