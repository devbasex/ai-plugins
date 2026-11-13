"""CD のある場合（`lib/release_case.py` の cd-no-review / cd-review。#2773）の本番のリリースプランの部品と、リリースと本番チャネルへの
マージのプランが共有する MVV 判定の部品（release_templates から分けた）。`release_templates` を import しない。"""

from __future__ import annotations

import shlex
from pathlib import Path

import release_case
from gate_answer import RELEASE_GATE
from supervise_lib.decl import DeclError
from supervise_lib.paths import MVV_PY, STEPS_PY
from step_result import EXIT_GATE
from supervise_lib.verify_steps import handoff_step, reject_step

MVV_NOTE = "{state_dir}/work/mvv-note.md"  # mvv-gate.py が書く判定の記録（PR のコメントか承認資料の末尾）
RULE_RELEASE_PROD_CD = (
    "CD のある場合（#2773）: release は本番の PR を作って承認を待つ間 10 で止まり、承認資料を組んで本番の PR へ置いたところで"
    "リリース承認ゲートに入る。承認の後は、レビューを必須にしないリポジトリ（cd-no-review）では approve-pr から、レビューが必須の"
    "リポジトリ（cd-review）では MVV 判定のプランでも承認ゲートで終え、本番の PR のレビューの承認を得てから release から続ける。run のステップが落ちたら、直せるもの"
    "（版数の書き漏れ・文書の形）は fix。外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。"
)


def release_cd_case(a, repo: str | None) -> str | None:
    """CD のある場合なら場合の名前（`cd-no-review` / `cd-review`）、CD が無ければ None。宣言を読めなければ組まずに DeclError。"""
    try:
        case = release_case.case_at(repo or a.worktree, strict=True)
    except release_case.CaseUnreadable as e:
        raise DeclError(f"リリースの場合を決められない: {e}") from e
    return case.case if case.has_cd else None


def cd_release_wait(a, case: str | None, on_exit: dict) -> dict:
    """CD のある場合の本番の release が承認を待つ 10 の行き先（承認資料の組み立て facts）。MVV 判定のプランで場合 1 なら承認ゲートに
    数えず（on_exit）、後の mvv の判定に任せる（承認の後にマージまで済めば結果は完了。済まなければ mvv が承認ゲートを返す）。
    場合 2 は MVV 判定でレビューの承認を代えられないため、MVV 判定のプランでも承認ゲート（gate_next）にする。"""
    if getattr(a, "mvv", None) and case == release_case.CD_NO_REVIEW:
        return {"on_exit": {**on_exit, str(EXIT_GATE): "facts"}}
    return {"gate_next": "facts"}


def cd_prod_steps(a, v: str, prs: str, repo: str | None, approval: str, approval_steps: list[dict], case: str) -> list[dict]:
    """CD のある場合の本番の、release が承認ゲート（10）で止まった後のステップ（#2773 の決定 6）: 承認資料を組む `approval_steps`
    （facts → explain → others → 助言の MVV 判定。release_templates の `_dev_approval_steps` を本番の PR の先端で組んだもの）→
    本番の PR へ置き（approval-post）→ リリース承認ゲート → 証跡（approve-pr）→ release（マージ）。MVV 判定のプランは approval-post の
    後に MVV で判定し、従えば approve-pr を --by mvv で打つ。それ以外は approval-post で終え、利用者の承認の後に
    run <計画> --from approve-pr で続ける。

    レビューが必須の場合（`cd-review`）は証跡を書かない（#2773 の I8・受け入れ条件 18）。approve-pr を置かず、承認の後は
    PR のレビューの承認を照合する release から続ける。MVV 判定はレビューの承認を代えられないため、MVV 判定のプランでも判定の
    記録を置いたところで承認ゲートとして終え（release へ戻すと release の 10 から MVV 判定までを上限まで繰り返す）、レビューの
    承認の後に run <計画> --from release で続ける。"""
    mvv = getattr(a, "mvv", None)
    after_gate = "end" if case == release_case.CD_REVIEW else "approve-pr"
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
            after_gate,
        )
        steps += [handoff_step(state, RELEASE_GATE, "判定のコメント"), reject_step(state, RELEASE_GATE, "mvv", root=repo or ".")]
    if after_gate == "approve-pr":
        steps.append(_approve_pr_step(a, repo, v, "mvv" if mvv else "user"))
    return steps


def _prod_pr_cmd(a, v: str) -> str:
    """本番の PR（版上げのブランチ → 本番チャネル）の番号を引くシェルの式。"""
    return f'$(gh pr list --head release/v{v} --base {a.production_branch} --state open --json number --jq ".[0].number")'


def _deploy_pr_step(step_id: str, repo: str | None, subcommand: str, **extra) -> dict:
    """本番の PR に対して release-steps.py の `subcommand` を打つデプロイのステップ（repo があればその下で打つ）。`extra` は遷移。"""
    return {
        "id": step_id,
        "type": "run",
        "stage": "デプロイ",
        "timeout": 300,
        **({"cwd": repo} if repo else {}),
        "cmd": f"sh -c '{STEPS_PY} {subcommand}'",
        **extra,
    }


def _approval_post_step(a, repo: str | None, v: str, approval: str, after: str) -> dict:
    """承認資料を本番の PR へ承認資料のコメントで置く（#2773 の E4）。この後にリリース承認ゲートで止まる。"""
    sub = f"approval-post --pr {_prod_pr_cmd(a, v)} --approval {shlex.quote(material_path(repo, approval))}"
    return _deploy_pr_step("approval-post", repo, sub, on_fail="judge", next=after)


def _approve_pr_step(a, repo: str | None, v: str, by: str) -> dict:
    """リリース承認ゲートの承認を本番の PR へ承認の証跡で書き、release（マージ）へ進む（#2773 の E5）。承認したコミットは
    同じプランの facts の出力から読み、利用者の承認は --answer-from の記録で確かめる。先端が動いていれば 10 で終える。"""
    state = getattr(a, "state", None)
    answer = f" --answer-from {shlex.quote(str(Path(state).resolve()))}" if state else ""
    sub = f"approve --pr {_prod_pr_cmd(a, v)} --facts-from {{state_dir}}{answer} --by {by}"
    return _deploy_pr_step("approve-pr", repo, sub, next="release", gate_next="end")


def mvv_gate_steps(mvv_cmd: str, note_cmd: str, next_id: str) -> list[dict]:
    """リリース承認ゲートの MVV 判定（mvv）と、判定の記録のコメント（note。落ちたら handoff）の 2 ステップ。note の後は `next_id` へ進む。"""
    return [
        {"id": "mvv", "type": "run", "timeout": 900, "cmd": mvv_cmd, "next": "note", "gate_next": "end"},
        {"id": "note", "type": "run", "timeout": 300, "cmd": note_cmd, "on_fail": "handoff", "next": next_id},
    ]


def material_path(repo, approval):
    """承認資料の置き場。元のリポジトリが分かればその下。"""
    return f"{repo}/{approval}" if repo else approval
