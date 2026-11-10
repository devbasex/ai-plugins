"""開発ループが打つコマンドの組み立て: スプリントの状態の作成と `supervise.py new sprint`、マニフェストのステージのプラン。"""

from __future__ import annotations

import sys
from pathlib import Path

from sprint_loop_record import SPRINT_STATE, SUPERVISE, resume_command, selected_issues


def approved_issues(rec: dict) -> set[int]:
    return {w["number"] for w in rec.get("waiting", []) if w.get("answer") == "approved"}


def design_issues(rec: dict, issues) -> list[int]:
    """設計のステップ（設計承認ゲート）を置く課題: 設計を承認していない課題すべて（#2913 の決定 1）。選定の「新しい承認を生む作業」の
    分類と `new_sprint_argv` の `--design` が同じこれを読む（I2）。"""
    approved = approved_issues(rec)
    return [n for n in issues if n not in approved]


HOLD_ONLY_REFUSAL = "答え待ちの停止の範囲で始めたループの 1 回に設計のステップを置かない"


def sprint_refusal(rec: dict, lap: dict) -> tuple[int, str] | None:
    """`loop sprint` が組まずに断る理由 (終了コード, 1 行)。組んでよければ None。答え待ちの停止の範囲で始めたループの 1 回
    （`hold_only`）は、設計を承認していない課題を含めば断る（#2913 の I1。呼び出しの誤りとして 2）。"""
    if lap.get("issues_kept") is not None:
        return 1, "設計承認ゲートの後のループの 1 回は組み直さない（loop start で残した課題の続きを組む）"
    if lap.get("hold_only") and (left := design_issues(rec, selected_issues(lap))):
        return 2, f"{HOLD_ONLY_REFUSAL}（設計を承認していない課題: {' '.join(f'#{n}' for n in left)}）"
    return None


def sprint_state_init_argv(root: str, rec: dict, lap: dict, issues: list[int]) -> list[str]:
    """sprint-state.py init のコマンド。ループの 1 回のディレクトリに mvv.md があればスプリント MVV を渡す。"""
    init = [sys.executable, str(SPRINT_STATE), "init", lap["state_path"], "--name", lap["sprint"], "--pace", "auto", "--root", root]
    init += [x for n in issues for x in ("--issue", str(n))]
    if lap["versions"]["dev"]:
        init += ["--dev", lap["versions"]["dev"], "--prod", lap["versions"]["prod"]]
    init += ["--goal", resume_command(rec)]
    mvv = Path(lap["dir"]) / "mvv.md"
    if mvv.is_file():
        init += ["--mvv", str(mvv), "--milestone", lap["milestone"]]
    return init


def new_sprint_argv(root: str, rec: dict, lap: dict, extra: list[str]) -> list[str]:
    """supervise.py new sprint --pace auto のコマンド。設計を承認済みの課題は --design から外して --designed へ入れ、
    設計の結果のステージ（supervise.py design-results）で設計の結果を読んで実装のプランへ渡す。
    開発版が無ければ --version を渡さず、キューが流す直前に版を決める（#2164）。"""
    issues = selected_issues(lap)
    design = design_issues(rec, issues)
    dev = lap["versions"]["dev"]
    new = [sys.executable, str(SUPERVISE), "new", "sprint", "--pace", "auto", "--state", lap["state_path"], "--name", lap["sprint"]]
    new += ["--worktree", root, "--issue", *map(str, issues), "--mode", lap["mode"]]
    if dev:
        new += ["--version", dev]
    new += ["--out", lap["out"]]
    if design:
        new += ["--design", *map(str, design)]
    if designed := [n for n in issues if n not in design]:
        new += ["--designed", *map(str, designed)]
    if rec.get("outside_plan"):  # 例外として始めたループは、内側の起動の判定でも同じ理由で通す（#2733）
        new += ["--outside-plan", rec["outside_plan"]]
    new += list(extra)
    return new


def stage_plan(index: list[dict], stage: str) -> str | None:
    """マニフェストのステージの先頭のプラン。ステージが無ければ None。"""
    return next((p for e in index if e.get("name") == stage for p in e.get("plans", [])), None)
