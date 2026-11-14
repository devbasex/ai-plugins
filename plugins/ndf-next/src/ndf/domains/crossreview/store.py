"""状態ファイルと修正の結果ファイル（#2770）。

PR を巻き直す（閉じて新しい番号で出し直す）と、状態ファイルは元の番号のまま残る。番号の規則は 1 つにする:

- 状態ファイル・修正の結果ファイルの名前と、修正担当へ渡す指示の番号は、元の番号（`origin_pr`）にそろえる
- 状態ファイルは今の番号（`current_pr`）を持つ。今の番号を渡されたら、`current_pr` の当たる状態ファイルを引く
- 修正の結果は、元の番号と今の番号のどちらの名前・中の `pr` でも受ける（巻き直しの前に起こした修正担当が
  今の番号で書いても読み飛ばさない）

状態ファイルが壊れていれば `StateBroken` を投げる（既定の置き場や空の状態へ倒さない）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NamedTuple, Optional

STATE_NAME = "cross-review-pr{pr}-state.json"
STATE_GLOB = "cross-review-pr*-state.json"
FIX_RESULT_NAME = "fix-pr{pr}-result.json"
# 状態ファイルの鍵
ORIGIN_KEY = "pr"
CURRENT_KEY = "current_pr"
REPO_KEY = "target_repo"


class StateBroken(Exception):
    """状態ファイルか結果ファイルが JSON の辞書として読めない。"""


class FixResultMismatch(Exception):
    """修正の結果ファイルの `pr` が、元の番号とも今の番号とも合わない。"""


class StateRef(NamedTuple):
    """元の PR 番号・今の PR 番号・状態ファイルのパス（まだ無ければ元の番号の名前のパス）。"""

    origin_pr: int
    current_pr: int
    path: Path

    @property
    def numbers(self) -> frozenset[int]:
        return frozenset((self.origin_pr, self.current_pr))


def read_object(path: Path) -> dict[str, Any]:
    """JSON の辞書を読む。無い・壊れている・辞書でなければ `StateBroken`。"""
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateBroken(f"{path} を読めない: {exc}") from None
    if not isinstance(found, dict):
        raise StateBroken(f"{path} が JSON の辞書でない")
    return found


def write_object(path: Path, value: dict[str, Any]) -> None:
    """一時の名前へ書いてから置き換える（読む側に書きかけを見せない。打ち直しても同じ中身）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _ref_of(path: Path, data: dict[str, Any], fallback: int) -> StateRef:
    origin = data.get(ORIGIN_KEY, fallback)  # noqa: NDF502 ファイル: 古い状態ファイルは番号の鍵を持たない
    current = data.get(CURRENT_KEY, origin)  # noqa: NDF502 ファイル: 巻き直していなければ今の番号は元の番号
    if type(origin) is not int or type(current) is not int:
        raise StateBroken(f"{path} の {ORIGIN_KEY}・{CURRENT_KEY} が整数でない")
    return StateRef(origin, current, path)


def state_pr(state_dir: Path, pr: int) -> StateRef:
    """渡された番号の状態ファイルを引く。巻き直しの後の番号なら、`current_pr` の当たる元の番号の状態ファイル。"""
    own = state_dir / STATE_NAME.format(pr=pr)
    if own.is_file():
        return _ref_of(own, read_object(own), pr)
    for path in sorted(state_dir.glob(STATE_GLOB)):
        ref = _ref_of(path, read_object(path), pr)
        if ref.current_pr == pr:
            return ref
    return StateRef(pr, pr, own)


def record_current(ref: StateRef, current_pr: int) -> StateRef:
    """巻き直しの後の今の番号を状態ファイルへ書く。"""
    data = read_object(ref.path) if ref.path.is_file() else {ORIGIN_KEY: ref.origin_pr}
    data[CURRENT_KEY] = current_pr
    write_object(ref.path, data)
    return StateRef(ref.origin_pr, current_pr, ref.path)


def state_repo(ref: StateRef) -> Optional[str]:
    """状態ファイルが持つ対象のリポジトリ。状態ファイルが無ければ None。"""
    if not ref.path.is_file():
        return None
    value = read_object(ref.path).get(REPO_KEY)
    return value if isinstance(value, str) and value else None


def fix_result_path(state_dir: Path, ref: StateRef) -> Path:
    """修正担当へ渡す結果ファイルのパス（元の番号の名前）。"""
    return state_dir / FIX_RESULT_NAME.format(pr=ref.origin_pr)


def pending_fix_result(state_dir: Path, ref: StateRef) -> Optional[Path]:
    """残っている修正の結果ファイル。元の番号の名前を先に、無ければ今の番号の名前を探す。"""
    for pr in dict.fromkeys((ref.origin_pr, ref.current_pr)):
        path = state_dir / FIX_RESULT_NAME.format(pr=pr)
        if path.is_file():
            return path
    return None


def read_fix_result(path: Path, ref: StateRef) -> dict[str, Any]:
    """修正の結果を読む。中の `pr` は元の番号と今の番号のどちらでも受け、ほかの番号なら `FixResultMismatch`。"""
    found = read_object(path)
    file_pr = found.get(ORIGIN_KEY)
    if file_pr not in ref.numbers:
        raise FixResultMismatch(f"{path} の pr {file_pr!r} は元の番号 {ref.origin_pr} とも今の番号 {ref.current_pr} とも合わない")
    return found
