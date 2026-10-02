"""単発の雛形とスプリントの雛形が共有する手順の定義（#1485）。

進捗記録（`記録`）・触るファイル（`触るファイル`）・PR 本文の材料（`pr` のステップの `materials`）・検査の記録
（`record` と `abort` のステップ）・取り込んだ課題を飛ばす `実行の条件` を、ここ 1 か所で組む。`templates.py` と
`sprint_waves.py` はこの関数を呼び、手順の文字列を自分で組み直さない。"""

from __future__ import annotations

import shlex
from pathlib import Path

from supervise_lib.paths import CHECK_PY, HERE

RECORD_SH = HERE / "projects-sync.sh"  # 課題の本文の「進行」へ工程を記録するスクリプト
REQUIREMENTS = "issues/issue-{n}-requirements.md"  # 要求の写しの置き場（NDF の規約。決定 11）
DESIGN_RESULTS = "design-results.json"  # 設計の結果のステージが計画の置き場へ書くファイル


def requirements_path(n: int) -> str:
    """課題 n の要求の写し（作業ツリーの根からの相対パス）。"""
    return REQUIREMENTS.format(n=n)


def with_record(plan: dict) -> dict:
    """プランへ `記録`（工程が変わるたびに課題ごとに打つスクリプト）を持たせる。"""
    plan["記録"] = str(RECORD_SH)
    return plan


def with_touched(plan: dict, files) -> dict:
    """触るファイルがあればプランの `触るファイル` へ書く（queue が重なるプランを 1 本ずつ流す）。"""
    if files:
        plan["触るファイル"] = list(files)
    return plan


def pr_step(base: str, title: str, summary: str, changes: str, next: str, materials: dict | None = None, **extra) -> dict:
    """`pr` のステップ。`materials` があれば `pr` のハンドラーが本文の材料（手動確認・集めた実装の PR・
    設計の結果・Closes）を集める。"""
    step = {
        "id": "pr",
        "type": "pr",
        "stage": extra.pop("stage", "Pull Request"),
        "base": base,
        "title": title,
        **extra,
        "summary": summary,
        "changes": changes,
        "next": next,
    }
    if materials:
        step["materials"] = materials
    return step


def sprint_materials(collect: str, design_results: str, closes: list[int]) -> dict:
    """スプリント PR の材料。`collect` へマージした実装の PR・設計の結果・スプリントの課題の Closes・手動確認。"""
    return {"manual": True, "collect": collect, "design_results": design_results, "closes": list(closes)}


def record_steps(name: str, *, target_pr: str | None = None, advance: bool = False, review: bool = False, pr: bool = False) -> list[dict]:
    """検査の記録の `record` と `abort`（と、差分の検査なら `abort-before-pr`）のステップ。

    - `target_pr`: PR を指す検査（`--target-pr`）。`advance` なら、マージした PR のマージのコミットへ
      `check-done/*` を進める（スプリントの検査。決定 8）
    - `pr`: 差分の検査（`--pr {pr}`）。落ちたら検査の PR を閉じる"""
    record = f"{CHECK_PY} record --id {shlex.quote(name)} --state {{state_dir}} --root ."
    if review:
        record += " --review"
    if target_pr is not None:
        record += f" --target-pr {target_pr}" + (" --advance-done" if advance else "")
        return [
            {"id": "record", "type": "run", "cmd": record, "on_fail": "abort", "next": "end"},
            {"id": "abort", "type": "run", "cmd": f"{record} --failed", "next": "end"},
        ]
    steps = [
        {"id": "record", "type": "run", "cmd": f"{record} --pr {{pr}}" if pr else record, "on_fail": "abort", "next": "end"},
        {"id": "abort", "type": "run", "cmd": f"{record} --failed" + (" --pr {pr}" if pr else ""), "next": "end"},
    ]
    if pr:
        steps.append({"id": "abort-before-pr", "type": "run", "cmd": f"{record} --failed", "next": "end"})
    return steps


def _skip_condition(text: str) -> dict:
    """実装プランを飛ばす `実行の条件`（理由を出し、終了コード 3 で結果は「完了」）。"""
    return {"cmd": f"echo {shlex.quote(text)}; exit 3", "skip_code": 3}


def absorbed_condition(issue: int, host: int) -> dict:
    """設計で他の課題へ取り込んだ課題の実装プランを飛ばす `実行の条件`。"""
    return _skip_condition(f"#{issue} は設計で #{host} へ取り込んだ（#{host} の実装プランが実装する）")


def closed_condition(issue: int) -> dict:
    """設計で「閉じる」とした課題の実装プランを飛ばす `実行の条件`（実装の PR を持たずにスプリント PR で閉じる）。"""
    return _skip_condition(f"#{issue} は設計で閉じるとした（実装の PR を持たずにスプリント PR で閉じる）")


def sprint_out(a) -> Path:
    """スプリントの計画の置き場（`--out`、省けば `sprint-<名前>`）。"""
    return Path(a.out or f"sprint-{a.name}")
