"""配分テーブル（項目 1 件あたりの所要の見積り）と、その材料の履歴（#933）。

**配分テーブルは保存しない**（#933 決定 7）。計画のたびに履歴から集計する。集計した
値を別に持つと、履歴と表が食い違う。履歴は実行ごとに 1 行の JSONL で、リポジトリ
ごとに 1 ファイルに分ける。テストの所要はリポジトリで大きく違い、混ぜると見積りが
外れる。

**根（`base`）は必ず引数で受ける。** ここで `run_metrics.metrics_dir()` を呼ぶと、
テストが利用者の `~/.local/state` を読み書きする。根を決めるのは呼ぶ側である。
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
from typing import Any, Optional

from .items import DEFERRED, REVERTED

HISTORY_NAME = "cross-refactoring-allocation.jsonl"

# `.resolve()` を通す。Kiro CLI は `.kiro/skills/<名前>` を symlink にするため、
# 解かずに `parents[]` を数えると Skill のディレクトリへ届かない。
DEFAULTS_PATH = pathlib.Path(__file__).resolve().parents[2] / "data" / "allocation-defaults.json"

# 種類ごとに遡る行の数。**その種類を含む行**で数えるため、珍しい手法も 10 回分まで
# 遡れる（設計の「データ構造: 履歴と配分テーブル」）。
WINDOW = 10

# 履歴の行の形の版。形を変えたら上げる。
ROW_SCHEMA = 1

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


def _section_samples(
    rows: list[dict[str, Any]], section: str, count_key: str
) -> list[tuple[float, float]]:
    samples = []
    for row in rows:
        entry = row.get(section)
        if isinstance(entry, dict):
            samples.append((_num(entry.get(count_key)), _num(entry.get("seconds"))))
    return samples


def build_table(rows: list[dict[str, Any]], defaults: dict[str, float]) -> dict[str, Any]:
    """履歴の行から配分テーブル（分）を集計する。値が出ない種類は初期値で埋める。

    `source` は行が 1 行でもあれば `history` である。個々の種類が初期値へ落ちても
    `history` のまま残す。**何を材料にしたか**を表す印であり、種類ごとの出所は
    計画の報告が値から読めばよい。
    """
    def pick(value: Optional[float], name: str) -> float:
        return float(defaults[name]) if value is None else value

    kinds: dict[str, float] = {}
    names = sorted({
        str(name)
        for row in rows if isinstance(row.get("kinds"), dict)
        for name in row["kinds"]
        if str(name).startswith(_STRUCTURE_PREFIX)
    })
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
    """実行の終わりの時刻。無ければフェーズの終わりの最も遅いもので代える。"""
    if state.get("ended_at"):
        return str(state["ended_at"])
    ends = [
        (parsed, str(span["ended_at"]))
        for span in (state.get("phases") or {}).values()
        if isinstance(span, dict)
        and (parsed := _parse_time(span.get("ended_at"))) is not None
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


def build_row(state: dict[str, Any]) -> dict[str, Any]:
    """状態（版 2）から履歴の 1 行を作る。

    項目の所要は**進行側がコミットの時刻から測った `items[].seconds`** を使う
    （#933 決定 8）。担当の申告は使わない。テストはコミットがある項目だけ、実装も
    コミットがある項目だけを数える。取り消し・見送りの項目は数えない。取り消しは
    コミットの欄を残したまま状態だけを変えるため、欄だけを見ると所要の無い件数で
    1 件あたりが下がる。
    """
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

    at = _ended_at(state)
    started, ended = _parse_time(state.get("started_at")), _parse_time(at)
    elapsed = None
    if started and ended:
        elapsed = int((ended.astimezone(_dt.timezone.utc)
                       - started.astimezone(_dt.timezone.utc)).total_seconds())
    verify = state.get("verify_stats") or {}
    fix = state.get("fix_stats") or {}
    whole = state.get("whole_test") or {}
    return {
        "schema": ROW_SCHEMA,
        "run": _run_id(state),
        "at": at,
        # 状態は Pull Request の番号を `current_pr` に持つ。`pr` があればそちらを使う。
        "pr": state.get("pr", state.get("current_pr")),
        "implementer": state.get("implementer"),
        "budget_minutes": state.get("budget_minutes"),
        "elapsed_seconds": elapsed,
        "phases": {
            name: span.get("seconds")
            for name, span in (state.get("phases") or {}).items()
            if isinstance(span, dict)
        },
        "kinds": kinds,
        "verify": {"items": verify.get("items", 0), "seconds": verify.get("seconds", 0)},
        "fix": {"launches": fix.get("launches", 0), "seconds": fix.get("seconds", 0)},
        "whole_test": {
            "init": (state.get("baseline_test") or {}).get("seconds"),
            "danger": whole.get("seconds") if whole.get("ran") else None,
            "final": (state.get("final_gate") or {}).get("whole_test_seconds"),
        },
    }


def append_row(path: pathlib.Path, row: dict[str, Any]) -> None:
    """履歴へ 1 行追記する。親のディレクトリが無ければ作る。"""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
