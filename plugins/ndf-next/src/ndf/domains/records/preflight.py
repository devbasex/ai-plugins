"""手順 0 の 3 つの確かめを打ち、1 回の結果 JSON にまとめる口（SF10 #3208）。

- worktree の設定: `ndf worktree check`（オーナーは M8。import せず、エントリポイントを子プロセスで呼ぶ）。設定が無ければ
  `ndf worktree init` を next にする
- 設定: `.ndf/project.json` が読める object である（無ければ既定で動くので通す）。表 ndf-files の project の keys に無いキーは
  warnings に並べ、止めない
- MVV: 設定があれば、本文が承認の記録と同じ（mismatch・unreadable なら止まる。承認し直すのは人なので next は無い）
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import cli, identity
from ndf.contracts import causes, facts, limits, result
from ndf.domains.records import files, jsonfile
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import preflight_decide as pd
from ndf.domains.settings import renames

# 子プロセスの結果 JSON を返す関数（引数の列 → 結果）
Runner = Callable[[list[str], Path], Mapping[str, Any]]
# 結果の tool（サブコマンド `records` の名前）
TOOL = cli.tool_of("ndf.domains.records.cli")


def run_entry(args: list[str], cwd: Path) -> Mapping[str, Any]:
    """自分のエントリポイントを子プロセスで打ち、標準出力の最後の行の結果 JSON を返す。読めなければ stopped/bad_report。"""
    argv = [str(identity.entrypoint(identity.load().root)), *args]
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=limits.seconds("project.git"), check=False)
        lines = done.stdout.strip().splitlines()
        got = json.loads(lines[-1]) if lines else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return result.stopped(TOOL, "bad_report", f"{' '.join(args)} の結果を読めない: {exc}")
    if not isinstance(got, dict):
        return result.stopped(TOOL, "bad_report", f"{' '.join(args)} が結果 JSON を返さなかった")
    return got


def ndf_settings_checked(root: Path) -> pd.Checked:
    """`.ndf/project.json` を確かめる。無ければ通す（既定で動く）。読めない・object でなければ止まる。"""
    path = files.path(root, "project")
    if not path.is_file():
        return pd.Checked(pd.NDF_SETTINGS, result.result(TOOL, result.OK, "プロジェクトの設定が無い（既定で動く）"))
    try:
        raw = jsonfile.read_object(path)
    except jsonfile.JsonFileError as exc:
        return pd.Checked(pd.NDF_SETTINGS, result.stopped(TOOL, "precondition", str(exc)))
    known = set(facts.value("ndf-files", "project", "keys")) | {"version", "$schema"}
    # 知らないキーは NDF が読まないだけなので止めない（利用者の設定の追記を拒まない）
    warnings = [f"{path} の知らないキー（NDF は読まない）: {k}" for k in sorted(set(raw) - known)]
    return pd.Checked(pd.NDF_SETTINGS, result.result(TOOL, result.OK, "プロジェクトの設定を読めた", warnings=warnings))


def _sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # noqa: NDF401 ファイル: 読めない本文は sha256 の無い unreadable の状態にする
        return None


def mvv_state(root: Path) -> str:
    """MVV の状態（absent・unapproved・approved・mismatch・unreadable）。"""
    path = files.path(root, "mvv")
    if not path.is_file():
        return md.status(None, None)
    try:
        decl = jsonfile.read_object(path)
    except jsonfile.JsonFileError:
        return md.State.UNREADABLE
    body = decl["body"] if isinstance(decl.get("body"), str) else facts.value("ndf-files", "mvv-body", "path")
    return md.status(decl, _sha256(path.parent / body))


def mvv_checked(root: Path) -> pd.Checked:
    """MVV の状態を確かめる。旧いキーが残る・本文が承認の記録と違う・読めなければ止まる。旧いキーは本文より先に見る。"""
    state = mvv_state(root)
    if state not in (md.State.UNREADABLE, md.State.ABSENT):
        legacy = renames.legacy_detail("mvv", jsonfile.read_object(files.path(root, "mvv")), root)
        if legacy is not None:
            return pd.Checked(pd.MVV, result.stopped(TOOL, "precondition", legacy))
    if state in (md.State.MISMATCH, md.State.UNREADABLE):
        detail = f"プロジェクト MVV の状態が {state}（本文を承認し直すか、承認した本文へ戻す）"
        return pd.Checked(pd.MVV, result.stopped(TOOL, "precondition", detail))
    return pd.Checked(pd.MVV, result.result(TOOL, result.OK, f"プロジェクト MVV: {state}"))


def check(root: Path, runner: Runner = run_entry) -> dict[str, Any]:
    """3 つの確かめを順に打ち、まとめた結果 JSON を返す。"""
    absent = causes.cause("precondition", "worktree の設定の設定が無い")
    wt = pd.worktree_checked(runner(["worktree", "check"], root), result.command("worktree", "init"), absent)
    s = pd.combine([wt, ndf_settings_checked(root), mvv_checked(root)])
    if s.passed:
        return result.result(TOOL, result.OK, s.summary, s.items)
    extra: dict[str, Any] = {"next": s.next} if s.next else {}
    cause = dict(s.cause) if s.cause else causes.cause("precondition", s.summary)
    return result.result(TOOL, result.STOPPED, s.summary, s.items, cause=cause, **extra)
