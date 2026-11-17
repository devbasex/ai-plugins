"""cross-refactoring の配分の表を読む口と、履歴へ実行の行を書く口（#3643。判断は `allocation_decide`・`allocation_history_decide`）。

- 初期値は Skill の `data/allocation-defaults.json`、宣言は表 ndf-files の estimate（`allocation_minutes`）
- 履歴は `<記録の根>/<owner>--<repo>/cross-refactoring-allocation.jsonl`（今の plugins/ndf が書く所と同じ）。記録の根は
  `NDF_METRICS_DIR` → `<旧い NDF の状態の根>/metrics`。
  読めない行は飛ばし、ファイルが無い・リモートが GitHub でないときは初期値（と宣言）で表を作る
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.adapters import git
from ndf.contracts import facts
from ndf.domains.crossrefactor import allocation_decide, allocation_history_decide

DEFAULTS_PATH = identity.ROOT / "skills" / "cross-refactoring" / "data" / "allocation-defaults.json"
HISTORY_NAME = "cross-refactoring-allocation.jsonl"
METRICS_ENV = "NDF_METRICS_DIR"  # 記録の根を差し替える（今の plugins/ndf の run_metrics.metrics_dir と同じ）


def history_path(repo: str, environ: Optional[Mapping[str, str]] = None) -> Path:
    """リポジトリ（`<owner>/<repo>`）の履歴のパス。`/` は `--` へ替える。"""
    env = os.environ if environ is None else environ
    explicit = env.get(METRICS_ENV)
    base = Path(explicit) if explicit else identity.legacy_state_root(env) / "metrics"
    return base / repo.replace("/", "--") / HISTORY_NAME


def _object(path: Path) -> dict[str, Any]:
    """JSON の object を読む。読めない・object でなければ `AllocationError`。"""
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise allocation_decide.AllocationError(f"{path} を読めない: {exc}") from exc
    if not isinstance(got, dict):
        raise allocation_decide.AllocationError(f"{path} が object でない")
    return got


def _declared(root: Path) -> Optional[dict[str, Any]]:
    place = facts.value("ndf-files", "estimate", "place")
    path = root / facts.value("places", place, "path") / facts.value("ndf-files", "estimate", "path")
    return _object(path) if path.is_file() else None


def _rows(path: Path) -> list[dict[str, Any]]:
    """履歴の行（古い順）。ファイルが無ければ空、読めない行は飛ばす（1 行の破損で集計を失わない）。"""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:  # noqa: S112 ファイル: 読めない行は飛ばし、残りの行で集計する
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _repo(work: git.Git) -> Optional[str]:
    try:
        return work.remote_repo()
    except git.GitError:  # noqa: NDF401 外部コマンド: リモートが GitHub でなければ履歴を読み書きせず初期値で表を作る
        return None


def table(work: git.Git, root: Path) -> dict[str, Any]:
    """配分の表（分）。初期値に `root` の宣言を重ね、履歴の実行の行があればその実測で置き換える。

    初期値か宣言が読めない・形が違えば `allocation_decide.AllocationError`（黙って捨てない）。
    """
    values = allocation_decide.decide(_object(DEFAULTS_PATH), _declared(root))
    repo = _repo(work)
    return allocation_history_decide.build_table(_rows(history_path(repo)) if repo is not None else [], values)


def record_run(work: git.Git, state: Mapping[str, Any], at: str) -> Optional[Path]:
    """最終ゲートを通った実行の行を履歴へ足し、書いたパスを返す。リモートが GitHub でなければ書かずに None。"""
    repo = _repo(work)
    if repo is None:
        return None
    path = history_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(allocation_history_decide.run_row(state, at), ensure_ascii=False) + "\n")
    return path
