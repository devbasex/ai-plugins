"""開発ループが打つコマンドの組み立て: スプリントの状態の作成と `supervise.py new sprint`、周回に版数が要るかの判定。"""

from __future__ import annotations

import sys
from pathlib import Path

import delivery
from sprint_loop_record import SPRINT_STATE, SUPERVISE, resume_command, selected_issues


def sprint_state_init_argv(root: str, rec: dict, lap: dict, issues: list[int]) -> list[str]:
    """sprint-state.py init のコマンド。周回のディレクトリに mvv.md があればスプリント MVV を渡す。"""
    init = [sys.executable, str(SPRINT_STATE), "init", lap["state_path"], "--name", lap["sprint"], "--pace", "auto", "--root", root]
    init += [x for n in issues for x in ("--issue", str(n))]
    if versions := lap.get("versions"):
        init += ["--dev", versions["dev"], "--prod", versions["prod"]]
    init += ["--goal", resume_command(rec)]
    mvv = Path(lap["dir"]) / "mvv.md"
    if mvv.is_file():
        init += ["--mvv", str(mvv), "--milestone", lap["milestone"]]
    return init


def approved_issues(rec: dict) -> set[int]:
    return {w["number"] for w in rec.get("waiting", []) if w.get("answer") == "approved"}


def new_sprint_argv(root: str, rec: dict, lap: dict, extra: list[str]) -> list[str]:
    """supervise.py new sprint --pace auto のコマンド。設計を承認済みの課題は --design から外して --designed へ入れ、
    設計の結果のステージ（supervise.py design-results）で設計の結果を読んで実装のプランへ渡す。"""
    issues = selected_issues(lap)
    approved = approved_issues(rec)
    design = [n for n in issues if n not in approved]
    new = [sys.executable, str(SUPERVISE), "new", "sprint", "--pace", "auto", "--state", lap["state_path"], "--name", lap["sprint"]]
    new += ["--worktree", root, "--issue", *map(str, issues), "--mode", lap["mode"], "--out", lap["out"]]
    if lap.get("versions"):
        new += ["--version", lap["versions"]["dev"]]
    if design:
        new += ["--design", *map(str, design)]
    if designed := [n for n in issues if n in approved]:
        new += ["--designed", *map(str, designed)]
    new += list(extra)
    return new


def version_needed(root: str) -> bool:
    """周回に版数が要るか（リリースの経路が雛形で組むか）。宣言を読めなければ要る側へ倒す（今の止まり方のまま）。"""
    from supervise_lib.release_templates import RELEASE_FORMS

    decl = delivery.load_delivery(root)
    return bool(decl.problems) or delivery.needs_version(delivery.routes(decl, tuple(RELEASE_FORMS)))
