"""棚卸しのテストが共有する、判定の計画（plan.json）を書いて `ndf issues apply` を打つ手順。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def apply_plan(run_issues: Callable[..., dict[str, Any]], tmp_path: Path, plan: dict, run_id: str | None = None) -> dict[str, Any]:
    """plan を tmp_path/plan.json へ書き、candidates の後に apply を打つ。run_id を渡すと `--run-id` を付ける。"""
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    # apply は記録のリポジトリ（candidates.json の metrics.repo）と書き込み先を照らす（#3854）
    run_issues("candidates", "--repo", "acme/widgets")
    extra = ["--run-id", run_id] if run_id is not None else []
    return run_issues("apply", "--repo", "acme/widgets", "--plan", str(path), *extra)
