"""pace: fast / auto を使ってよい条件（宣言と承認の記録）の判定。スプリントの進行表を作る `progress.py new` が読む（#3092）。

読む宣言の節が進め方の名前（fast / auto）である以外は同じ条件で、ほかの節の値は使わない:
`.ndf/pace.json` の `<進め方>.enabled`・`verify`・`modes`、開発版のチャネル、fast ではスプリントブランチを残さない取り込み方、
スプリント MVV かプロジェクト MVV の承認の記録（`--state`）。
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import branch_flow
import delivery
import project_mvv
from pace import PaceError, PaceMissing, read_pace
from sprint_mvv import MVV_PACES
from supervise_lib.decl import SUPERVISE_DECL, WORKTREE_DECL, DeclError, decl_roots, delivery_decl, read_decl


def _section(roots: list[Path], name: str) -> dict | str:
    for r in roots:
        try:
            return read_pace(r)[name]
        except PaceMissing:
            continue
        except PaceError as e:
            return str(e)
    return "進め方の宣言（.ndf/pace.json）が無い"


def _flow_refusal(wt: dict) -> str | None:
    """pace: fast はスプリントブランチを作らないため、スプリントブランチを本番に届くまで使う取り込み方と組めない。"""
    try:
        row = branch_flow.of_decl(wt).resolve(None, None).row
    except branch_flow.FlowError as e:
        return str(e)
    if row.keeps_sprint_branch:
        return f"取り込み方 {row.name}（.ndf/worktree.json の branch_flow.promotion）はスプリントブランチを使うため fast と組めない"
    return None


def mvv_refusal(state_path: str | None, root=None, pace: str = "fast") -> str | None:
    """MVV の承認の照合（`lib/project_mvv.approval_refusal`）。外れた理由を返す。"""
    if not state_path:
        return f"--pace {pace} には --state（スプリントの状態）が要る"
    try:
        state = json.loads(Path(state_path).read_text())
    except (OSError, ValueError) as e:
        return f"スプリントの状態を読めない: {e}"
    if not isinstance(state, dict):
        return "スプリントの状態を読めない: オブジェクトでない"
    return project_mvv.approval_refusal(state, root or Path.cwd())


def pace_refusal(worktree: str, mode: str, pace: str, state: str | None, repo: str | None = None) -> str | None:
    """進め方 `pace` を使ってよい条件を確かめ、外れた理由を返す（満たせば None。normal は常に None）。"""
    if pace not in MVV_PACES:
        return None
    roots = decl_roots(worktree, repo)
    sec = _section(roots, pace)
    if isinstance(sec, str):
        return sec
    if not sec["enabled"]:
        return f".ndf/pace.json の {pace}.enabled が true でない"
    if not sec["verify"]:
        return f".ndf/pace.json の {pace}.verify（導入の確認のコマンド）が無い"
    if mode not in sec["modes"]:
        return f"モード {mode} は {pace} に入れられない（入れられるモード: {' / '.join(sec['modes'])}）"
    try:
        wt = read_decl(roots, WORKTREE_DECL)
        sv = read_decl(roots, SUPERVISE_DECL)
    except DeclError as e:
        return str(e)
    if pace == "fast" and (why := _flow_refusal(wt)):
        return why
    a = Namespace(worktree=worktree, repo=repo, base=wt.get("base_branch") or None, production_branch=None, release=sv.get("release"))
    channel = delivery.dev_channel(delivery_decl(a))
    if not channel.ok:
        return channel.reason
    mroot = next((r for r in roots if project_mvv.load_mvv(r).status != "none"), roots[0])
    return mvv_refusal(state, mroot, pace)
