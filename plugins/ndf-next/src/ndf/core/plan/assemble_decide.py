"""プランの組み立ての判断（純粋な関数）: 状態の置き場・実行の鍵・差し込み・ステップを打つ作業ディレクトリ。

- 差し込み `{state_dir}` は絶対のパスで埋める。プランを相対のパスで渡しても、作業場所の中のステップが同じ置き場を指す（#2949）
- 実行の鍵はプランの中身・開始点・作業場所の head・回の番号から作る。差し戻しの後に直した head は別の鍵になり、前の回の
  報告を返さずに流れる（#3176）。同じ引数で打ち直せば同じ鍵で、承認ゲートの待ちから続く（B2）
- 差し込みの値を変えた打ち直しは別の回にする（#3053）。`{owner}` は回をまたいで同じ持ち主の名前（#2640）
- マージで作業場所が消えたか登録が外れた後のステップは、リポジトリの根で打つ（#2969・#3022・#3186・#1886）
- `--fresh` は前の回に戻らず、耐久の記録の置き場に無い番号の回で先頭から流す。状態の置き場を消した後も前の鍵に戻らない（#1592）
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
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


def run_digest(plan_digest: str, sets: Mapping[str, str]) -> str:
    """回を分ける中身の要約。差し込みの値（`--set`）を変えて打ち直すと別の回になり、前の回の報告を返さない（#3053）。"""
    if not sets:
        return plan_digest
    body = json.dumps([plan_digest, sorted(sets.items())], ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def owner_of(name: str, state_dir: str) -> str:
    """差し込み `{owner}`: プランと状態の置き場で決まる持ち主の名前。回・開始点・差し込みの値が変わっても同じ。

    ステップが作業場所の持ち主を記録するときに使えば、打ち直して回が変わっても前の回の続きとして扱える（#2640）。
    """
    return f"{name}-{hashlib.sha256(state_dir.encode('utf-8')).hexdigest()[:12]}"


class RunChoice(NamedTuple):
    """選んだ実行の回。"""

    key: str
    round_no: int


class RunPlan(NamedTuple):
    """回を分けるプランの側の値: プランの名前・中身の要約・開始点。"""

    name: str
    digest: str
    start: Optional[str]

    def key(self, head: Optional[str], round_no: int) -> str:
        """head と回の番号を添えた実行の回の鍵（`run_key`）。"""
        return run_key(self.name, self.digest, self.start, head, round_no)


def _same_plan(record: Mapping[str, Any], plan: RunPlan) -> bool:
    return record.get("name") == plan.name and record.get("digest") == plan.digest and record.get("start") == plan.start


def _never_taken(_key: str) -> bool:
    return False


def choose_run(
    record: Optional[Mapping[str, Any]],
    plan: RunPlan,
    head: Optional[str],
    finished: bool,
    *,
    fresh: bool = False,
    taken: Callable[[str], bool] = _never_taken,
) -> RunChoice:
    """前の回の記録 `record`（`run.json`）から、`plan`（名前・中身・開始点）の今回の実行の鍵を選ぶ。

    同じプラン・開始点の回が終わっていなければ同じ鍵で続ける（承認ゲートの待ちから回復する。B2）。終わっていても、
    head が回の初めか終わりの head と同じなら同じ鍵（終わった結果を返す。B4）。head が違えば次の回の鍵（#3176）。
    プランか開始点が違えば次の回の鍵にする。

    `fresh` なら前の回を続けず、次の番号から数えて `taken`（耐久の記録の置き場に鍵の記録があるか）が偽になる最初の
    番号の鍵を選ぶ。`record` が無くても（状態の置き場を消した後でも）前の回の鍵に戻らない（#1592）。
    """
    if not fresh and record is not None and _same_plan(record, plan):
        if not finished or head is None or head in record["heads"]:
            return RunChoice(record["key"], record["round"])
    first = record["round"] + 1 if record is not None else 0
    if not fresh:
        return RunChoice(plan.key(head, first), first)
    round_no = next(n for n in itertools.count(first) if not taken(plan.key(head, n)))
    return RunChoice(plan.key(head, round_no), round_no)


def fill(args: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """引数の中の `{名前}` を値で埋める。値の無い名前はそのまま残す。"""
    out = []
    for arg in args:
        for key, val in values.items():
            arg = arg.replace("{" + key + "}", val)
        out.append(arg)
    return out


def workdir_for(location: str, worktree: Optional[str], repo: Optional[str], worktree_usable: bool) -> Optional[str]:
    """ステップを打つ作業ディレクトリ。作業場所が使えなければリポジトリの根。どちらも無ければ None（呼び手の cwd）。"""
    if location == IN_REPO or worktree is None or not worktree_usable:
        return repo if repo is not None else (worktree if worktree_usable else None)
    return worktree
