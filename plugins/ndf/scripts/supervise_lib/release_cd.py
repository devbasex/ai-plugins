"""CD のある場合（`lib/release_case.py` の cd-no-review / cd-review。#2773）の本番のリリースプランの部品と、リリースと本番チャネルへの
マージのプランが共有する MVV 判定の部品（release_templates から分けた）。`release_templates` を import しない。"""

from __future__ import annotations

import shlex
from pathlib import Path

import release_case
from gate_answer import RELEASE_GATE
from supervise_lib.decl import DeclError
from supervise_lib.paths import MVV_PY, STEPS_PY
from supervise_lib.verify_steps import handoff_step, reject_step

MVV_NOTE = "{state_dir}/work/mvv-note.md"  # mvv-gate.py が書く判定の記録（PR のコメントか承認資料の末尾）
RULE_RELEASE_PROD_CD = (
    "CD のある場合（#2773）: release は本番の PR を作って承認を待つ間 10 で止まり、承認資料を組んで本番の PR へ置いたところで"
    "リリース承認ゲートに入る。承認の後は approve-pr から続ける。run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
    "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。"
)


def reviewed(a, repo: str | None) -> bool:
    """CD のある場合（`cd-no-review` / `cd-review`）か。宣言を読めなければ組まずに DeclError。"""
    try:
        case = release_case.case_at(repo or a.worktree, strict=True)
    except release_case.CaseUnreadable as e:
        raise DeclError(f"リリースの場合を決められない: {e}") from e
    return case.has_cd


def cd_prod_steps(a, v: str, prs: str, repo: str | None, approval: str, approval_steps: list[dict]) -> list[dict]:
    """CD のある場合の本番の、release が承認ゲート（10）で止まった後のステップ（#2773 の決定 6）: 承認資料を組む `approval_steps`
    （facts → explain → others → 助言の MVV 判定。release_templates の `_dev_approval_steps` を本番の PR の先端で組んだもの）→
    本番の PR へ置き（approval-post）→ リリース承認ゲート → 証跡（approve-pr）→ release（マージ）。MVV 判定のプランは approval-post の
    後に MVV で判定し、従えば approve-pr を --by mvv で打つ。それ以外は approval-post で終え、利用者の承認の後に
    run <計画> --from approve-pr で続ける。"""
    mvv = getattr(a, "mvv", None)
    steps = approval_steps
    for s in steps:
        if s.get("next") == "end":  # others（助言の MVV 判定があれば mvv-note）の後は承認資料を置く
            s["next"] = "approval-post"
        if s["id"] == "facts":
            s.pop("gate_as_ok", None)
    steps.append(_approval_post_step(a, repo, v, approval, "mvv" if mvv else "end"))
    if mvv:
        material, state = material_path(repo, approval), str(Path(a.mvv).resolve())
        steps += mvv_gate_steps(
            f"{MVV_PY} check --sprint {shlex.quote(state)} --gate release --material {shlex.quote(material)} --pr {prs} --mode {a.mode}"
            + (f" --root {shlex.quote(repo)}" if repo else "")
            + f" --note {MVV_NOTE}",
            f"sh -c 'for p in {prs}; do gh pr comment \"$p\" --body-file {MVV_NOTE} || exit 1; done'",
            "approve-pr",
        )
        steps += [handoff_step(state, RELEASE_GATE, "判定のコメント"), reject_step(state, RELEASE_GATE, "mvv", root=repo or ".")]
    steps.append(_approve_pr_step(a, repo, v, "mvv" if mvv else "user"))
    return steps


def _prod_pr_cmd(a, v: str) -> str:
    """本番の PR（版上げのブランチ → 本番チャネル）の番号を引くシェルの式。"""
    return f'$(gh pr list --head release/v{v} --base {a.production_branch} --state open --json number --jq ".[0].number")'


def _approval_post_step(a, repo: str | None, v: str, approval: str, after: str) -> dict:
    """承認資料を本番の PR へ承認資料のコメントで置く（#2773 の E4）。この後にリリース承認ゲートで止まる。"""
    cmd = f"sh -c '{STEPS_PY} approval-post --pr {_prod_pr_cmd(a, v)} --approval {shlex.quote(material_path(repo, approval))}'"
    return {
        "id": "approval-post",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 300,
        **({"cwd": repo} if repo else {}),
        "cmd": cmd,
        "on_fail": "judge",
        "next": after,
    }


def _approve_pr_step(a, repo: str | None, v: str, by: str) -> dict:
    """リリース承認ゲートの承認を本番の PR へ承認の証跡で書き、release（マージ）へ進む（#2773 の E5）。承認したコミットは
    同じプランの facts の出力から読み、利用者の承認は --answer-from の記録で確かめる。先端が動いていれば 10 で終える。"""
    state = getattr(a, "state", None)
    answer = f" --answer-from {shlex.quote(str(Path(state).resolve()))}" if state else ""
    cmd = f"sh -c '{STEPS_PY} approve --pr {_prod_pr_cmd(a, v)} --facts-from {{state_dir}}{answer} --by {by}'"
    return {
        "id": "approve-pr",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 300,
        **({"cwd": repo} if repo else {}),
        "cmd": cmd,
        "next": "release",
        "gate_next": "end",
    }


def mvv_gate_steps(mvv_cmd: str, note_cmd: str, next_id: str) -> list[dict]:
    """リリース承認ゲートの MVV 判定（mvv）と、判定の記録のコメント（note。落ちたら handoff）の 2 ステップ。note の後は `next_id` へ進む。"""
    return [
        {"id": "mvv", "type": "run", "timeout": 900, "cmd": mvv_cmd, "next": "note", "gate_next": "end"},
        {"id": "note", "type": "run", "timeout": 300, "cmd": note_cmd, "on_fail": "handoff", "next": next_id},
    ]


def material_path(repo, approval):
    """承認資料の置き場。元のリポジトリが分かればその下。"""
    return f"{repo}/{approval}" if repo else approval
