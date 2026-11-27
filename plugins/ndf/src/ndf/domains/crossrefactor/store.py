"""殻: cross-refactoring の 1 回の実行の状態ファイル。

状態ファイルは作業場所の根（`<保存先>/rf<PR>/state.json`）に 1 つ置き、作業場所の worktree の外に持つ（差分に入らない）。
書くときは同じディレクトリの一時ファイルへ書いてから置き換え、途中で止まっても半端な JSON を残さない。

| キー | 中身 |
| --- | --- |
| `pr`・`branch`・`base`・`args` | 対象の PR・head のブランチ・差分の起点・結果を変える引数 |
| `target_scope` | リファクタリング計画の対象範囲（`--scope`）。最終ゲートの直しはこの中だけを変えてよい |
| `touched` | 課題が触る関数（`symbols_decide.Touched.as_json`） |
| `phases` | 工程ごとの記録（起点 `base_sha` と開始の時刻。`phase_decide.start_phase`） |
| `items` | 項目（`items_decide`）。項目ごとに起点 `start`・検証の記録 `history`・見送りの理由 `reason` を持つ |
| `skipped` | 範囲外で見送った提案 |
| `final` | 最終ゲートの記録（`status`・`rounds`・`head`・`means`。必須のチェックの経路は `checks`、手元の経路は `tests`（`passed`・`skipped`）と `digest`、落ちれば `base_failed`・`head_failed`・`outside_failures`） |
| `done` | 完了の記録（`head`・`args`・結果 JSON。手元の経路は `tests`、テストを打った完了なら `digest`・`path_digest`） |
| `onto`・`rebasing` | 手元の経路の載せ直しの起点と、載せ直しの途中の記録（`{head, tip}`。#4566） |
| `local_passed`・`local_base_failed` | 手元のテスト全体の合格の記録と、基準側の不合格の記録（`{sha, digest, path_digest, run}`） |
| `pushing` | 手元の経路で push を打つ直前に書く、送る SHA と合格の組 |
| `fixing` | 手元の経路で、最終ゲート修正のために修正担当を起動する直前に書く、直す前の SHA・取得した先頭・落ちた suite（`{before, tip, labels}`）。範囲の検査を終えたら消す。残っていれば再開の頭で範囲を検査する |
| `verify_trees` | 手元の最終ゲートの検証用の作業場所のパス（作る前に書き、取り除いた後に消す。取り除けずに残したものは `release` の後も残す） |
| `gate_logs` | NDF が書いた手元の最終ゲートのログの記録（`{name, tmp, st_dev, st_ino}`。`release` の後も残す） |
| `repo` | 前のバージョンが `NDF_WORKTREE_BASE` の直下に作った場所を使い続けるときの、git の共通ディレクトリの実パス |
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"


class StateBroken(Exception):
    """状態ファイルを読めない（JSON でない・オブジェクトでない）。"""


def path_of(root: Path) -> Path:
    return root / STATE_FILE


def load(path: Path) -> dict[str, Any]:
    """状態を読む。ファイルが無ければ空の状態。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise StateBroken(f"状態ファイルが JSON でない: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateBroken(f"状態ファイルがオブジェクトでない: {path}")
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    """状態を書く（一時ファイルから置き換える）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
