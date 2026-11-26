"""工程の呼び出しと結果の型、プランの実行の記録の読み取り（`ndf.progress.invoke` が使う）。"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import result
from ndf.core import durable, route
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner

# 変更要求の指摘の本文を作業へ渡す環境変数
NOTES_ENV = "NDF_STAGE_NOTES"


@dataclass(frozen=True)
class StageRun:
    """ステップ 1 つの呼び出し。`name` は呼ぶプラン・Skill の名前（script は argv の先頭）。"""

    kind: str
    name: str
    inputs: dict[str, Any]
    outputs: tuple[str, ...]
    range: str
    step: str
    args: dict[str, Any] = field(default_factory=dict)
    # 変更要求の指摘の本文（承認ゲートの request_changes の回答）。作業へは環境変数 NDF_STAGE_NOTES か質問の末尾で渡す
    notes: Optional[str] = None
    # 行の打ち直し（conductor の retry）の回数と、方針による自動の打ち直しか。プランはどちらでも新しい回を始める（`plan_attempt`）
    retries: int = 0
    again: bool = False
    # 同じ工程の承認ゲートの最新の承認の回答が見た対象（事象の `seen`。`gate.Target.as_dict` の形）。無ければ None（#4409）
    approved: Optional[dict[str, Any]] = None
    # 工程が承認ゲートを持つか（light の設計のように持たない工程のマージは、承認をデプロイへ先送りする。#4409）
    gated: bool = False
    # 以下は工程を単位ごとに流すための欄（#4369）。既定のままの呼び出しは、欄が無いときと同じに動く
    # 工程の定義の `per_unit`
    per_unit: bool = False
    # 出所の表（`invoke.FILL_SOURCES`）と工程の入力より先に置く差し込みの値
    overrides: Mapping[str, route.Value] = field(default_factory=dict)
    # `route.templates()` の `name` の代わりに描画するテンプレート
    template: Optional[route.Template] = None
    # プランの子の環境へ足す変数
    env_extra: Mapping[str, str] = field(default_factory=dict)
    # 前の回の開始点が無いときの `--from`
    start: Optional[str] = None

    def plain(self) -> StageRun:
        """単位に分けない呼び出し。入力の `branch` は除く（残すと差し込みの `branch` になり、作業場所の今のブランチを使わない）。"""
        return replace(self, per_unit=False, inputs={k: v for k, v in self.inputs.items() if k != "branch"})

    def env(self, extra: Mapping[str, str]) -> dict[str, str]:
        out = {**os.environ, **extra}
        if self.notes is not None:
            out[NOTES_ENV] = self.notes
        return out


@dataclass(frozen=True)
class StageResult:
    """作業の結果。`report` は結果 JSON、`outputs` は工程の定義が書くと定義したキーだけの値。"""

    report: dict[str, Any]
    outputs: dict[str, Any]
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return str(self.report["status"])

    @property
    def cause(self) -> Optional[dict[str, Any]]:
        held = self.report["cause"] if "cause" in self.report else None
        return held if isinstance(held, dict) else None


class UnitsStopped(Exception):
    """工程を単位ごとに流せずに止める。`kind` は停止理由の語、`detail` は理由の文、`items` は結果 JSON の項目。"""

    def __init__(self, kind: str, detail: str, items: Sequence[Mapping[str, Any]] = ()) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.items = [dict(i) for i in items]


# --- プランの実行の記録 ---------------------------------------------------------------------


def read_report(stdout: str) -> Optional[dict[str, Any]]:
    """標準出力の最後の空でない行の結果 JSON（新しいバージョンなら代わりの `stopped`。`result.received`）。読めないか形が違えば None。"""
    return result.last_line(stdout)


def previous(work_dir: Path) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """保存先の前の回の記録（`run.json`）と、その回の結果のバックアップ（終わっていなければ None。新しいバージョンなら `InternalError`）。"""
    record = plan_files.read_json(work_dir / plan_runner.RUN_FILE)
    if record is None:
        return None, None
    return record, plan_runner.stored_result(work_dir, durable.run_name(record["key"]))


def from_of(command: Any) -> Optional[str]:
    """結果のコマンド（`next`・`routes` の値）の `--from` の値。無ければ None。"""
    argv = command["argv"] if isinstance(command, dict) and isinstance(command.get("argv"), list) else []
    return str(argv[argv.index("--from") + 1]) if "--from" in argv[:-1] else None
