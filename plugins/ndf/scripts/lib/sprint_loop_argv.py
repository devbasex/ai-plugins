"""開発ループが打つコマンドの組み立て: スプリントの状態の作成と `supervise.py new sprint`、マニフェストのステージのプラン。"""

from __future__ import annotations

import sys
from pathlib import Path

from sprint_loop_record import SPRINT_STATE, SUPERVISE, resume_command, selected_issues


def approved_issues(rec: dict) -> set[int]:
    return {w["number"] for w in rec.get("waiting", []) if w.get("answer") == "approved"}


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
    approved = approved_issues(rec)
    design = [n for n in issues if n not in approved]
    dev = lap["versions"]["dev"]
    new = [sys.executable, str(SUPERVISE), "new", "sprint", "--pace", "auto", "--state", lap["state_path"], "--name", lap["sprint"]]
    new += ["--worktree", root, "--issue", *map(str, issues), "--mode", lap["mode"]]
    if dev:
        new += ["--version", dev]
    new += ["--out", lap["out"]]
    if design:
        new += ["--design", *map(str, design)]
    if designed := [n for n in issues if n in approved]:
        new += ["--designed", *map(str, designed)]
    new += list(extra)
    return new


def stage_plan(index: list[dict], stage: str) -> str | None:
    """マニフェストのステージの先頭のプラン。ステージが無ければ None。"""
    return next((p for e in index if e.get("name") == stage for p in e.get("plans", [])), None)
