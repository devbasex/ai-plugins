"""配布のプラン（導入の確認・昇格・承認ゲート 2）の雛形（#1336 の F5・#1454・#1457）。`engine` を import しない。

スプリントの経路（sprint_routes）が使う。リリースの雛形（release_templates）の関門 2 の MVV 判定の部品を共有する。
"""

from __future__ import annotations

import shlex
from pathlib import Path

from supervise_lib.decl import WORKTREE_DECL, DeclError, with_decls
from supervise_lib.paths import MERGED_PY, MVV_PY, STEPS_PY
from supervise_lib.plan import QUEUE_PRS
from supervise_lib.release_templates import MVV_NOTE, judge_record_step, mvv_gate_steps
from supervise_lib.verify_steps import MARGIN_FLOOR, handoff_step


VERIFY_MATERIAL = "{state_dir}/work/approval-verify.md"  # verify-facts が書く承認資料（#1457）


def facts_step(repo: str, cmd: str, next_id: str, step_id: str = "facts") -> dict:
    """導入の確認を走らせて承認資料を書くステップ（落ちたらプランが止まる）。gate-2・昇格・導入の確認のプランが使う。"""
    # timeout は導入の確認の上限（雛形の verify-install と同じ）
    return {"id": step_id, "type": "run", "stage": "配布", "timeout": 1500, "cwd": repo, "cmd": cmd, "next": next_id}


def verify_facts_cmd(a, repo: str, verify: str) -> str:
    q = shlex.quote
    return f"{STEPS_PY} verify-facts --root {q(repo)} --base {q(a.base)} --verify {q(verify)} --out {VERIFY_MATERIAL}"


def release_plan(a, repo: str, phase: str, rule: str, steps: list, condition: dict | None) -> dict:
    """配布のプラン（導入の確認・昇格・承認ゲート 2）に共通の枠。上限 12 で、condition があれば「実行の条件」を足す。"""
    plan = {
        "フェーズ": phase,
        "課題": a.issue,
        "モード": a.mode,
        "作業場所": repo,
        "リポジトリ": repo,
        "規則": rule,
        "上限": 12,
        "steps": steps,
    }
    if condition:
        plan["実行の条件"] = condition
    return with_decls(plan, a)


def plan_verify(a, repo: str, verify: str, condition: dict | None = None) -> dict:
    """経路 merge だけのスプリント（#1457）の「導入の確認」のプラン: verify（導入の確認。落ちたら止まる）の 1 ステップ。"""
    return release_plan(
        a, repo, "配布（導入の確認）", RULE_VERIFY, [facts_step(repo, verify_facts_cmd(a, repo, verify), "end", "verify")], condition
    )


def plan_promote(
    a,
    repo: str,
    ci_wait: int,
    mvv: str | None = None,
    condition: dict | None = None,
    production: str | None = None,
    verify: str | None = None,
) -> dict:
    """昇格のプラン（#1336 の F5）: ベースブランチ（a.base）から本番チャネル（a.production_branch）への Pull Request を
    `merged-steps.py promote` が作り（あれば使い）、承認ゲート 2 の後にマージする。後片付けはしない（head はベースブランチ）。
    マージの後に record（`release-steps.py record --promote`）が昇格の PR へリリース記録を書き、その PR を計画の
    Pull Request にする（まとめの close が `{queue_pr:promote}` で読む。#837）。record が落ちたら judge-record へ進む。

    - normal（`mvv` 無し）: promote（承認の引数無し。承認ゲート 2 で終える）→ promote-approved（`--from` でだけ入る）
    - fast / auto（`mvv` にスプリントの状態）: verify（導入の確認。落ちたら止まる。#1457）→ prepare（PR と承認資料）→ mvv（承認ゲート 2 の MVV 判定）→ note（判定のコメント）→
      promote（`--gate-approved mvv`）。note か promote が落ちたら handoff が承認ゲートへ落とす
    """
    head, base = a.base, production or a.production_branch
    if not base:
        raise DeclError(f"昇格に要る本番チャネルが無い（--production-branch か .ndf/{WORKTREE_DECL} の production_branch）")
    promote = f"{MERGED_PY} promote --head {shlex.quote(head)} --base {shlex.quote(base)}"
    wait = f" --timeout {ci_wait}"
    timeout = ci_wait + max(MARGIN_FLOOR, ci_wait // 10)
    approved = {
        "id": "promote-approved",
        "type": "run",
        "stage": "配布",
        "timeout": timeout,
        "cmd": promote + wait + " --gate-approved user",
        "next": "record",
    }
    record = [
        {
            "id": "record",
            "type": "run",
            "stage": "配布",
            "timeout": 300,
            "cwd": repo,
            "cmd": f"{STEPS_PY} record --promote --head {shlex.quote(head)} --base {shlex.quote(base)} --prs {QUEUE_PRS}",
            "pr_from": "release_pr_url",
            "on_fail": "judge-record",
            "next": "end",
        },
        judge_record_step(),
    ]
    if not mvv:
        steps = [
            {
                "id": "promote",
                "type": "run",
                "stage": "配布",
                "timeout": timeout,
                "cmd": promote + wait,
                "gate_next": "end",
                "next": "record",
            },
            approved,
            *record,
        ]
    else:
        if not verify:
            raise DeclError("fast / auto の昇格のプランに導入の確認のコマンド（<節>.verify）が無い")
        state = str(Path(mvv).resolve())
        material = "{state_dir}/work/approval-promote.md"
        pr_of = f'$(gh pr list --head {shlex.quote(head)} --base {shlex.quote(base)} --state open --json number --jq ".[0].number")'
        steps = [
            facts_step(repo, verify_facts_cmd(a, repo, verify), "prepare", "verify"),
            {
                "id": "prepare",
                "type": "run",
                "stage": "配布",
                "timeout": 300,
                "cmd": f"{promote} --prepare --out {material}",
                "next": "mvv",
            },
            *mvv_gate_steps(
                f"sh -c '{MVV_PY} check --sprint {shlex.quote(state)} --gate release --material {material} {VERIFY_MATERIAL} --pr {pr_of} "
                f"--mode {a.mode} --root {shlex.quote(repo)} --note {MVV_NOTE}'",
                f"sh -c 'gh pr comment {pr_of} --body-file {MVV_NOTE}'",
                "promote",
            ),
            {
                "id": "promote",
                "type": "run",
                "stage": "配布",
                "timeout": timeout,
                "cmd": promote + wait + " --gate-approved mvv",
                "on_fail": "handoff",
                "gate_next": "end",
                "next": "record",
            },
            approved,
            *record,
            handoff_step(state, "関門 2", "昇格のマージ"),
        ]
    return release_plan(a, repo, "配布（昇格）", RULE_PROMOTE, steps, condition)


GATE_2_MATERIAL = "{state_dir}/work/approval-deploy.md"  # deploy-facts が書く承認資料


def plan_gate_2(a, repo: str, mvv: str, verify: str, prs: str | None, condition: dict | None = None) -> dict:
    """手動反映の本番系（#1454）の承認ゲート 2 のプラン: facts（導入の確認を走らせ承認資料を書く。落ちたら止まる）→
    mvv（承認ゲート 2 の MVV 判定）→ note（判定の記録を PR へ。PR が無ければ承認資料の末尾へ）。note が落ちたら handoff。
    `prs` が None（fast の単独の queue。前のステージの PR を集められない）なら、mvv は判定を打たずに承認ゲート（10）を返す。
    本番のデプロイそのものは、このプランの後の手で行う「本番」のステージで担い手が起こす。"""
    state = str(Path(mvv).resolve())
    q = shlex.quote
    facts = facts_step(repo, f"{STEPS_PY} deploy-facts --root {q(repo)} --verify {q(verify)} --out {GATE_2_MATERIAL}", "mvv")
    if prs is None:
        why = q("前のステージの Pull Request を集められないため、承認ゲート 2 は MVV 判定で通さず、利用者の承認を求める")
        steps = [
            facts,
            {
                "id": "mvv",
                "type": "run",
                "timeout": 60,
                "cmd": f"sh -c 'echo \"$1\"; exit 10' mvv {why}",
                "gate_next": "end",
                "next": "end",
            },
        ]
    else:
        mvv_cmd = (
            f"{MVV_PY} check --sprint {q(state)} --gate release --material {GATE_2_MATERIAL} --pr {prs} --mode {a.mode} "
            f"--root {q(repo)} --note {MVV_NOTE}"
        )
        note_cmd = (
            f"sh -c 'set -- {prs}; [ $# -gt 0 ] || {{ cat {MVV_NOTE} >> {GATE_2_MATERIAL}; exit 0; }}; "
            f'for p; do gh pr comment "$p" --body-file {MVV_NOTE} || exit 1; done\''
        )
        steps = [facts, *mvv_gate_steps(mvv_cmd, note_cmd, "end"), handoff_step(state, "関門 2", "判定のコメント")]
    return release_plan(a, repo, "配布（承認ゲート 2）", RULE_GATE_2, steps, condition)


RULE_GATE_2 = (
    "本番のデプロイの前の承認ゲート 2。導入の確認（facts）が落ちたら直さずに止める。本番のデプロイ（trigger）はこのプランでは打たない。"
)

RULE_VERIFY = "開発版のチャネルへの反映の後の導入の確認。落ちたら直さずに止める。"

RULE_PROMOTE = (
    "昇格の Pull Request のマージは本番系への反映で、承認ゲート 2 に当たる。承認の無いマージは merged-steps.py が止める。"
    "CI の失敗は直さずに止める（昇格の Pull Request にはコミットを足さない）。"
)
