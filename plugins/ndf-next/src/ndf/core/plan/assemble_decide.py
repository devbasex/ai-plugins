"""プランの組み立ての判断（純粋な関数）: 状態の置き場・実行の鍵・差し込み・ステップを打つ作業ディレクトリ。

- 差し込み `{state_dir}` は絶対のパスで埋める。プランを相対のパスで渡しても、作業場所の中のステップが同じ置き場を指す（#2949）
- 実行の鍵はプランの中身・開始点・作業場所の head・回の番号から作る。差し戻しの後に直した head は別の鍵になり、前の回の
  報告を返さずに流れる（#3176）。同じ引数で打ち直せば同じ鍵で、承認ゲートの待ちから続く（B2）
- マージで作業場所が消えたか登録が外れた後のステップは、リポジトリの根で打つ（#2969・#3022・#3186・#1886）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePath
from typing import Any, NamedTuple, Optional

from ndf.core.plan.model import IN_REPO

# 状態の置き場を渡さないときの、プランのファイルの隣の置き場の接尾辞
STATE_SUFFIX = ".state"
# 実行の鍵の先頭の語
KEY_KIND = "plan-run"


def state_dir_for(plan_path: str, state_dir: Optional[str], cwd: str) -> str:
    """差し込み `{state_dir}` に使う置き場の絶対のパス。渡されなければプランのパスから導く。`cwd` は絶対のパス。"""
    base = PurePath(cwd)
    if state_dir is not None:
        return str(base / state_dir)
    plan = base / plan_path
    return str(plan.with_name(plan.stem + STATE_SUFFIX))


def run_key(name: str, digest: str, start: Optional[str], head: Optional[str], round_no: int = 0) -> str:
    """実行の回の鍵。プランの名前・中身・開始点・head・回の番号のどれかが違えば別の鍵になる（B1）。"""
    parts = [KEY_KIND, name, digest, start if start is not None else "", head if head is not None else "", str(round_no)]
    return ":".join(parts)


class RunChoice(NamedTuple):
    """選んだ実行の回。"""

    key: str
    round_no: int


def _same_plan(record: Mapping[str, Any], name: str, digest: str, start: Optional[str]) -> bool:
    return record.get("name") == name and record.get("digest") == digest and record.get("start") == start


def choose_run(record: Optional[Mapping[str, Any]], name: str, digest: str, start: Optional[str], head: Optional[str], finished: bool) -> RunChoice:
    """前の回の記録 `record`（`run.json`）から、今回の実行の鍵を選ぶ。

    同じプラン・開始点の回が終わっていなければ同じ鍵で続ける（承認ゲートの待ちから回復する。B2）。終わっていても、
    head が回の初めか終わりの head と同じなら同じ鍵（終わった結果を返す。B4）。head が違えば次の回の鍵（#3176）。
    プランか開始点が違えば次の回の鍵にする。
    """
    if record is not None and _same_plan(record, name, digest, start):
        if not finished or head is None or head in record["heads"]:
            return RunChoice(record["key"], record["round"])
    round_no = record["round"] + 1 if record is not None else 0
    return RunChoice(run_key(name, digest, start, head, round_no), round_no)


def fill(args: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """引数の中の `{名前}` を値で埋める。値の無い名前はそのまま残す。"""
    out = []
    for arg in args:
        for key, val in values.items():
            arg = arg.replace("{" + key + "}", val)
        out.append(arg)
    return out


def workdir_for(place: str, worktree: Optional[str], repo: Optional[str], worktree_usable: bool) -> Optional[str]:
    """ステップを打つ作業ディレクトリ。作業場所が使えなければリポジトリの根。どちらも無ければ None（呼び手の cwd）。"""
    if place == IN_REPO or worktree is None or not worktree_usable:
        return repo if repo is not None else (worktree if worktree_usable else None)
    return worktree
