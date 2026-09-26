"""CLI の起動 1 回の結果ファイルの読み取りと、手順の記録への書き込み。"""
from __future__ import annotations

import pathlib
from typing import Any

import models as models_lib
from monitor_outcome import LaunchOutcome, read_launch_outcome

from . import info
from .paths import stem_for


def read_result(state: dict[str, Any], runtime: str, phase: str) -> LaunchOutcome:
    """起動 1 回の結末を読む。**失敗しない。**

    結果ファイルの名前の幹をここで 1 度だけ組み、共通層（`read_launch_outcome`）へ
    渡す。取り込みが同じ組み立てを通るため、監視へ渡した名前の雛形
    （`--stem-template`）と食い違う幹で読むことがない。

    **中断も出力もしない。** 結果を読めなかったときに何をするかは、読んだ側
    （取り込み）が終了コードとして決める。ここで `die` すると、未検証のコミットが
    取り消されないまま残る（#728）。
    """
    return read_launch_outcome(state["tmp_dir"], stem_for(runtime, phase, state["id"]))


# 監視が CLI を止めた結末（手順の上限。無進捗の許容も同じ値を渡す）。
STOPPED_REASONS = frozenset({"timeout", "stalled"})


def note_stopped(state: dict[str, Any], runtime: str, phase: str) -> None:
    """監視が手順の上限で CLI を止めていたら、手順の記録に残す（決定 23）。

    **取り込みは止めたかどうかで変えない。** 未コミットの変更は取り込みの前に捨て
    （`discard_impl_leftovers`）、コミット済みの項目は git の時刻による判定へそのまま
    流す。止めたことは `phases.<手順>.stopped` に残り、報告に 1 行出る。
    """
    monitor = read_result(state, runtime, phase).monitor or {}
    reason = str(monitor.get("reason") or "")
    if reason not in STOPPED_REASONS:
        return
    record = state.setdefault("phases", {}).setdefault(phase, {})
    record["stopped"] = {"reason": reason, "timeout": record.get("timeout"),
                         "at": monitor.get("ended_at")}
    info(f"⏰ 監視が {phase} の CLI を上限（{record.get('timeout')} 秒）で止めました。"
         "未コミットの変更は捨て、コミット済みの項目は締め切りで判定します")


def record_observed_model(state: dict[str, Any], runtime: str, phase: str) -> None:
    """実装担当の CLI の出力から、実際に使われたモデル名を拾って記録する。

    取れるのは claude だけである。取れないランタイムは `None` のままにし、
    報告では既定モデルの実行として区別する。
    """
    stem = stem_for(runtime, phase, state["id"])
    stdout_log = pathlib.Path(state["tmp_dir"]) / f"{stem}-stdout.log"
    if not stdout_log.exists():
        return
    observed = models_lib.observed_model(
        runtime, stdout_log.read_text(encoding="utf-8", errors="replace")
    )
    if not observed:
        return
    model = state.setdefault("implementer_model", {"requested": None, "observed": None})
    model["observed"] = observed
    warning = models_lib.mismatch_warning(runtime, model.get("requested"), observed)
    if warning:
        info(warning)
