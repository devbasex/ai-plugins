"""upkeep_state.py: 棚卸しの記録の置き場を決める唯一の部品（#2847）。

    $NDF_UPKEEP_STATE_DIR/<owner>--<name>/       共有の置き場（repo_state_dir）
    ├── candidates.json・apply.json・rank.json    識別子を渡さない実行の棚卸しの回
    ├── ledger.json・estimates.json・rejected.json 回をまたぐ記録（実行ごとに分けない）
    └── runs/<run>/                               実行ごとの置き場（candidates --run で回を始める）
        └── candidates.json・apply.json・rank.json

`round_dir` は実行の識別子から棚卸しの回の置き場を返す。識別子が無いか、その実行の回が始まっていない
（`runs/<run>/candidates.json` が無い）ときは共有の置き場を返す。識別子の形が合わなければ終了コード 2。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from step_result import EXIT_UNREADABLE, StepError

# 棚卸しの回が持つ記録。回をまたぐ記録（ledger・estimates・rejected）は含めない
ROUND_FILES = ("candidates.json", "apply.json", "rank.json")
_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def repo_state_dir(arg, repo: str) -> Path:
    """共有の置き場。`--state-dir` → `$NDF_UPKEEP_STATE_DIR` → 一時ディレクトリの順に採り、リポジトリごとに分ける。"""
    base = arg or os.environ.get("NDF_UPKEEP_STATE_DIR") or str(Path(tempfile.gettempdir()) / "ndf" / "backlog-refinement")
    d = Path(base) / repo.replace("/", "--")
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_run(run) -> str | None:
    """実行の識別子の形を確かめる。None はそのまま返す。合わなければ終了コード 2。"""
    if run is None:
        return None
    if not isinstance(run, str) or not _RUN.fullmatch(run) or ".." in run:
        raise StepError(f"実行の識別子は英数字で始まり英数字・.・_・- だけの 64 文字以内で書く: {run!r}", EXIT_UNREADABLE)
    return run


def round_dir(sd: Path, run, start: bool = False) -> Path:
    """棚卸しの回の置き場。`start` なら `runs/<run>/` を作って返す。そうでなければ回が始まっていれば `runs/<run>/`、無ければ `sd`。"""
    if check_run(run) is None:
        return sd
    d = sd / "runs" / run
    if start:
        d.mkdir(parents=True, exist_ok=True)
        return d
    return d if (d / "candidates.json").is_file() else sd
