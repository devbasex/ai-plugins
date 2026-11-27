"""実験の記録の行を読むだけの判定（#2944）。ファイルの読み書きと時計は `model_choice` が持つ。

割り付け（`model_choice.launch_choice`）・出現率の計算（`experiments.refresh`）・`ndf models report` が、同じ規則で候補・起動できない
組・実際のモデル・費用を計算できないモデルを決めるために共有する。`model_choice` は同じ名前で公開し直す。
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import facts

# 同梱の表
TABLE = "launch-experiments"
# 出現率の項目の確率の合計の許容差
_SUM_TOLERANCE = 1e-6
# Codex の起動の費用を換算する単価の表
PRICES = "data/codex-prices.json"


def codex_prices() -> dict[str, Any]:
    """同梱の Codex の単価表（`default_model` と、モデル → 100 万トークンあたりの USD の `models`）。"""
    return contracts.load_json(PRICES)


def records_rule() -> dict[str, Any]:
    """表 launch-experiments の records の行（起動できない組の条件と記録を残す期間）。"""
    return facts.rows(TABLE, kind="records")[0]


def row_time(row: Mapping[str, Any]) -> datetime:
    return datetime.fromisoformat(row["at"])


def arm_text(arm: Mapping[str, str]) -> str:
    return json.dumps(dict(arm), sort_keys=True)


def assigns(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """割り付け ID → 割り付けの行。"""
    return {r["assign_id"]: r for r in rows if r["kind"] == "assign"}


@dataclass(frozen=True)
class Where:
    """割り付けの行と出現率の項目を照らすキー（リポジトリ・工程・ランタイム・互換性のキー）。B2 の出現率の計算も使う。"""

    repo: str
    stage: str
    runtime: str
    key: str

    def of(self, row: Optional[Mapping[str, Any]]) -> bool:
        """`row`（割り付けの行か出現率の項目）が同じキーを持つか。"""
        return row is not None and all(row[k] == v for k, v in vars(self).items())

    def at(self, row: Mapping[str, Any]) -> bool:
        """`row`（割り付けの行か停止の行）が同じリポジトリ・工程・ランタイムか（互換性のキーは見ない）。

        単位の ID は呼び出し元が付けるので、別のリポジトリ・工程・ランタイムが同じ名前を使っても混ぜない。
        """
        return (row["repo"], row["stage"], row["runtime"]) == (self.repo, self.stage, self.runtime)

    def fields(self) -> dict[str, str]:
        """停止の行に書く範囲の欄。"""
        return {"repo": self.repo, "stage": self.stage, "runtime": self.runtime}


def candidates(
    arms: Sequence[Mapping[str, str]],
    rows: Sequence[Mapping[str, Any]],
    entry: Optional[Mapping[str, Any]],
    now: datetime,
    where: Where,
    *,
    refused: Optional[Mapping[int, datetime]] = None,
) -> list[int]:
    """打ち切った組（出現率の項目の期限の前）と起動できない組を除いた添字。基準の組（添字 0）は除かない。

    起動できない組は、直近 `refusal_days` 日の `launch_error` の行のうち、割り付け ID で引いた割り付けの行がリポジトリ・工程・
    ランタイム・互換性のキーで今と同じで、組が同じものが `refusals` 件以上ある組。割り付けの行を引けない拒否は数えない。
    `refused` は呼び手が同じ引数で求めた `refused_until` の結果で、渡されれば記録を走査し直さない（戻る日時も表示する呼び手が使う）。
    """
    refused = refused_until(arms, rows, now, where) if refused is None else refused
    cutoffs = entry["cutoffs"] if entry is not None else []
    cut = {c["arm"] for c in cutoffs if now < datetime.fromisoformat(c["until"])}
    return [i for i in range(len(arms)) if i == 0 or (i not in cut and i not in refused)]


def refused_until(arms: Sequence[Mapping[str, str]], rows: Sequence[Mapping[str, Any]], now: datetime, where: Where) -> dict[int, datetime]:
    """起動できない組 → 候補へ戻る日時。基準の組（添字 0）は含めない。

    起動できない組は、直近 `refusal_days` 日の `launch_error` の行のうち、割り付け ID で引いた割り付けの行がリポジトリ・工程・
    ランタイム・互換性のキーで今と同じで、組が同じものが `refusals` 件以上ある組。割り付けの行を引けない拒否は数えない。
    戻る日時は、新しいほうから `refusals` 件目の行の日時に `refusal_days` を足した日時で、それを過ぎると件数がしきい値を下回る。
    """
    rule, found = records_rule(), assigns(rows)
    days = timedelta(days=rule["refusal_days"])
    times: dict[str, list[datetime]] = defaultdict(list)
    for r in rows:
        if r["kind"] == "launch_error" and row_time(r) >= now - days and where.of(found.get(r["assign_id"])):
            times[arm_text(r["arm"])].append(row_time(r))
    out: dict[int, datetime] = {}
    for i, arm in enumerate(arms):
        got = sorted(times[arm_text(arm)], reverse=True)
        if i != 0 and len(got) >= rule["refusals"]:
            out[i] = got[rule["refusals"] - 1] + days
    return out


def has_observed(rows: Sequence[Mapping[str, Any]], assign_id: str) -> bool:
    """割り付け ID に、実際のモデルの行（`observed`）が既にあるか。"""
    return any(r["kind"] == "observed" and r["assign_id"] == assign_id for r in rows)


def observed_models(rows: Sequence[Mapping[str, Any]], where: Where, arms: Sequence[Mapping[str, str]], start: datetime) -> dict[str, str]:
    """まとまり → `start` 以降の同じキーの最も新しい `observed` の行の実際のモデル。割り付け（`entry_fits`）と出現率の計算
    （`experiments.refresh`）が同じ観測でモデルを決めるために共有する。
    """
    return {g: model for g, (_, model) in observed_latest(rows, where, arms, start).items()}


def observed_latest(
    rows: Sequence[Mapping[str, Any]], where: Where, arms: Sequence[Mapping[str, str]], start: datetime
) -> dict[str, tuple[datetime, str]]:
    """まとまり → `start` 以降の同じキーの最も新しい `observed` の行の日時と実際のモデル。

    最も新しい行は記録の順でなく行の日時で選ぶ。並行する起動の観測は、ロックの待ちで日時と逆の順に記録されることがある。
    日時も返すので、広い範囲で 1 回だけ求めれば、それより後に始まる範囲の観測は日時で絞るだけで得られる（`ndf models report`）。
    """
    found = assigns(rows)
    latest: dict[str, tuple[datetime, str]] = {}
    for r in rows:
        if r["kind"] == "observed" and row_time(r) >= start and where.of(found.get(r["assign_id"])):
            group = model_group(r["arm"], arms)
            if group not in latest or row_time(r) >= latest[group][0]:
                latest[group] = (row_time(r), r["model"])
    return latest


def unpriced_models(rows: Sequence[Mapping[str, Any]], where: Where, start: datetime) -> list[str]:
    """`start` 以降に割り付けた、`where` と同じキーの単位の `unpriced` の行のモデル（名前の順・重ねない）。

    割り付けの段階を `uniform` にする条件（`model_choice._has_unpriced`）と `ndf models report` の表示が、同じ条件でモデルを決めるために共有する。
    """
    found = assigns(rows)
    models = {
        r["model"]
        for r in rows
        if r["kind"] == "unpriced" and where.of(a := found.get(r["assign_id"])) and a is not None and row_time(a) >= start
    }
    return sorted(models - set(codex_prices()["models"]))  # 行を書いた後に単価表へ載ったモデルは数えない


def model_group(arm: Mapping[str, str], arms: Sequence[Mapping[str, str]]) -> str:
    """実際のモデルのまとまり: モデルを指定しない組すべては `default`、指定する組は添字。"""
    return "default" if "model" not in arm else str(list(arms).index(arm)) if arm in arms else "?"


def probs_fit(entry: Mapping[str, Any], arms: int) -> bool:
    """項目の添字と確率が `arms` 組の選択肢に合うか。候補と打ち切りの添字が範囲の中にあり、`adaptive` の確率は組の数だけあって、
    候補が 0 より大きく、ほかが 0 で、合計が 1。読み手（割り付け）と書き手（出現率の計算）の両方が確かめる。"""
    indexes = list(entry["candidates"]) + [c["arm"] for c in entry["cutoffs"]]
    if any(i >= arms for i in indexes):
        return False
    if entry["phase"] != "adaptive":
        return True
    probs = entry["probs"]
    cands = set(entry["candidates"])
    shape = len(probs) == arms and all((p > 0) == (i in cands) for i, p in enumerate(probs))
    return shape and abs(sum(probs) - 1) <= _SUM_TOLERANCE


def entry_fits(
    entry: Mapping[str, Any], observed: Mapping[str, str], arms: Sequence[Mapping[str, str]], allocation: Mapping[str, Any]
) -> bool:
    """項目を今の割り付けに使えるか: 割り付けの値が今の設定と同じで、添字と確率が選択肢に合い（`probs_fit`）、実際のモデルが
    期間の中の同じキーの最も新しい `observed` の行（呼び手が `observed_models` で求めた `observed`）と、まとまりごとに食い違わない。

    観測は呼び手が 1 回だけ求め、出現率の計算は同じ結果を単位の絞り込みにも使う。
    """
    if entry["allocation"] != dict(allocation) or not probs_fit(entry, len(arms)):
        return False
    return all(observed[g] == m for g, m in entry["models"].items() if g in observed)
