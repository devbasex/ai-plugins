"""答え待ちの停止の範囲にある 1 スプリントのレーンへ答えが届いたとき、起こすかを決める材料（#3000 の決定 3）。

レーンのリポジトリでレーンのスプリントを探し、次に始めるプラン（ステージの並びの先頭から見て、結果が完了でも承認ゲートでもない
プランを持つ最初のステージの、そのプラン）を求め、承認の要らないプラン（`parallel_hold.needs_approval` が偽）があるかを返す。
決められない（スプリントが見つからない・読めない・次に始めるプランが無い）ときは偽を返し、今と同じく起こさない（I5）。
`parallel_answer.owner_next` だけが使う。ファイルを読むだけで書かない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import parallel_hold as ph


def find_sprint(repo: Path, issues: list) -> Optional[Path]:
    """`repo` の `.ndf/sprint-*/` のうち、`state.json` の `issues` が空でなく `issues` の部分集合であるスプリントのディレクトリ。
    複数あれば `sprint.json` の更新時刻が最も新しいもの。無ければ None。"""
    want = {str(i) for i in issues or []}
    hits = []
    for st in Path(repo).glob(".ndf/sprint-*/state.json"):
        try:
            got = json.loads(st.read_text(encoding="utf-8")).get("issues") or []
            spec = st.parent / "sprint.json"
            if got and {str(i) for i in got} <= want and spec.is_file():
                hits.append((spec.stat().st_mtime, st.parent))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return max(hits)[1] if hits else None


def _result(plan: Path) -> str:
    """プランの報告の結果。報告が無ければ未着手（空）。"""
    from supervise_lib.paths import report_result, state_dir_of

    try:
        return report_result((state_dir_of(str(plan)) / "report.md").read_text(encoding="utf-8"))
    except OSError:
        return ""


def next_plans(sprint: Path) -> list[Path]:
    """次に始めるプラン。`sprint.json` の `ステージ` を並びのまま読み、`plans` を持たないステージ（承認ゲート）を飛ばす。
    読めない・次に始めるプランが無ければ空。"""
    from supervise_lib.queue_fill import FINISHED

    try:
        stages = json.loads((Path(sprint) / "sprint.json").read_text(encoding="utf-8")).get("ステージ") or []
    except (OSError, ValueError, AttributeError):
        return []
    for stage in stages:
        plans = [Path(p) if Path(p).is_absolute() else Path(sprint) / p for p in (stage or {}).get("plans") or []]
        todo = [p for p in plans if _result(p) not in FINISHED]
        if todo:
            return todo
    return []


def _free(plan: Path) -> bool:
    try:
        return not ph.needs_approval(json.loads(plan.read_text(encoding="utf-8")), None)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False  # 読めないプランは承認を生みうるとして扱う


def sprint_free(main: Path, name: str, lane: str) -> bool:
    """計画 `name` の 1 スプリントのレーン `lane` の次に始めるプランに、承認の要らないプランが 1 本でもあるか。"""
    import parallel_answer as pa

    got = ph.lane_input(main, name, lane) or {}
    sprint = find_sprint(pa.lane_repo(main, name, lane), got.get("issues") or [])
    return sprint is not None and any(_free(p) for p in next_plans(sprint))
