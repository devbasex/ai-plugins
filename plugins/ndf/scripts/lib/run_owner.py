"""レビュー worktree（`pr<n>`・`rf<n>`）の持ち主の記録と寿命（#2229）。

記録は worktree の外（隣）の `<pr<n>|rf<n>>.owner.json` に置き（決定 3）、`run`（実行の識別子）・`seen_at`（最後に
動いた時刻）・`finished_at`（終わった時刻）を持つ。終わっていなくても、最後に動いた時刻から寿命の窓（`WINDOW`）を
過ぎた実行は終わったとみなす（決定 4）。片付け（`run_state.py`）と cross-review・cross-refactoring の開始が使う。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import uuid
from pathlib import Path
from typing import Any, Sequence

# 寿命の窓: 記録が終わっていなくても、最後に動いた時刻からこの秒数を過ぎた実行を終わったとみなす（決定 4）。
WINDOW = 24 * 3600
# レビュー worktree の中の NDF の状態（未追跡の判定から外し、外す前に消す）。
NDF_STATE_DIRS = (".cross_review", ".cross_refactoring")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def utc_iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def owner_path(state: Path | str) -> Path:
    """レビュー worktree（`pr<n>`・`rf<n>`）の持ち主の記録の置き場。worktree の外（隣）に置く（決定 3）。"""
    p = Path(state)
    return p.with_name(f"{p.name}.owner.json")


def read_owner(state: Path | str) -> tuple[dict[str, Any] | None, str | None]:
    """(記録, 読めない理由)。記録が無ければ (None, None)。"""
    path = owner_path(state)
    if not path.exists():
        return None, None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"持ち主の記録を読めない ({path}: {e})"
    if not isinstance(record, dict) or not record.get("run"):
        return None, f"持ち主の記録の形が違う ({path})"
    return record, None


def _write_owner(state: Path | str, record: dict[str, Any]) -> None:
    path = owner_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def claim_owner(state: Path | str, *, tool: str, pr: int, run: str | None = None) -> str:
    """持ち主の記録を新しく書き（以前の実行の記録は上書きして引き継ぐ）、実行の識別子を返す。"""
    run = run or str(uuid.uuid4())
    now = _now_iso()
    record = {"version": 1, "kind": "review-worktree", "tool": tool, "pr": int(pr), "run": run}
    _write_owner(state, {**record, "started_at": now, "seen_at": now, "finished_at": None})
    return run


def _update(state: Path | str, run: str | None, key: str) -> tuple[bool, str | None]:
    record, err = read_owner(state)
    if err:
        return False, err
    if record is None:
        return False, f"持ち主の記録が無い ({owner_path(state)})"
    if not run or record.get("run") != run:
        return False, f"持ち主の記録は別の実行 {record.get('run')} のもの（渡した実行 {run or '無し'}）"
    record[key] = _now_iso()
    _write_owner(state, record)
    return True, None


def touch_owner(state: Path | str, run: str | None) -> tuple[bool, str | None]:
    """記録の `run` と一致するときだけ、最後に動いた時刻を書く。"""
    return _update(state, run, "seen_at")


def finish_owner(state: Path | str, run: str | None) -> tuple[bool, str | None]:
    """記録の `run` と一致するときだけ、終わった時刻を書く（E2）。"""
    return _update(state, run, "finished_at")


def adopt_owner(state: Path | str, *, tool: str, pr: int, run: str | None) -> str:
    """開始が持ち主の記録を書き、実行の識別子を返す。同じ実行の続きなら記録を引き継いで動いた時刻だけを書く。

    cross-review（`pr<n>`）と cross-refactoring（`rf<n>`）の init が共有する。"""
    record, _ = read_owner(state)
    if run and record is not None and record.get("run") == run and touch_owner(state, run)[0]:
        return run
    return claim_owner(state, tool=tool, pr=pr, run=run)


def mark_owner(state: Path | str | None, run: str | None, what: str) -> str | None:
    """記録へ動いた時刻（`touch`）か終わり（`finish`）を書く。書けなければ理由を返す（別の実行が引き継いだときなど。I12）。

    置き場か実行の識別子が無い（呼び出し元が置き場を持つ）ときは何もしない。drive.py の両方が使う。"""
    if not state or not run:
        return None
    done, why = (finish_owner if what == "finish" else touch_owner)(state, run)
    return None if done else why


def run_args(init_args: Sequence[str]) -> list[str]:
    """init へ足す `--run <実行の識別子>`。識別子は今の耐久ワークフローの ID（打ち直しでも同じ。#2229）。

    呼び出し元が `--run` を渡した・耐久ワークフローの外のときは空を返す。"""
    if "--run" in init_args:
        return []
    import durable  # 耐久の記録を要する。drive.py の中で呼ぶときだけ読む

    try:
        return ["--run", durable.current_id()]
    except durable.DurableError:
        return []


def _last_seen(state: Path, record: dict[str, Any] | None) -> float:
    if record is not None:
        got = _epoch(record.get("seen_at")) or _epoch(record.get("started_at"))
        if got is not None:
            return got
    return mtime(state)


def mtime(path: Path) -> float:
    """状態の最終更新の時刻。ディレクトリは NDF の状態の置き場の時刻も見る。"""
    times = []
    for p in (path, *(path / d for d in NDF_STATE_DIRS)):
        with contextlib.suppress(OSError):
            times.append(p.stat().st_mtime)
    return max(times) if times else 0.0


def last_seen_iso(state: Path, record: dict[str, Any] | None) -> str:
    return utc_iso(_last_seen(state, record))


def alive(state: Path, record: dict[str, Any] | None, now: float) -> bool:
    """記録（無ければ状態の最終更新）から、持ち主の実行が生きているかを決める（決定 4）。"""
    if record is not None and record.get("finished_at"):
        return False
    return now - _last_seen(state, record) < WINDOW


def alive_reason(state: Path, record: dict[str, Any] | None) -> str:
    who = f"持ち主の実行 {record.get('run')}" if record else "持ち主の記録が無く、最終更新が寿命の窓の中の実行"
    return f"{who} が動いている（最後に動いた時刻 {last_seen_iso(state, record)}）"
