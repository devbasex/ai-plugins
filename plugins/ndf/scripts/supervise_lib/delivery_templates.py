"""リリースプラン（導入の確認・本番チャネルへのマージ・リリース承認ゲート）の雛形（#1336 の F5・#1454・#1457）。`engine` を import しない。

スプリントの経路（sprint_routes）が使う。リリースの雛形（release_templates）のリリース承認ゲートの MVV 判定の部品を共有する。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import release_case
from gate_answer import RELEASE_GATE
from supervise_lib.decl import PRODUCTION_SOURCES, DeclError, with_decls
from supervise_lib.paths import MERGED_PY, MVV_PY, STEPS_PY
from supervise_lib.plan import QUEUE_PRS
from supervise_lib.release_cd import MVV_NOTE, mvv_gate_steps
from supervise_lib.release_verify import judge_record_step
from supervise_lib.verify_steps import CD_FAILED_STOP, CD_RESULT_FILE, MARGIN_FLOOR, cd_result_step, handoff_step, reject_step


VERIFY_MATERIAL = "{state_dir}/work/approval-verify.md"  # verify-facts が書く承認資料（#1457）
# リリースの承認資料。normal は promote のリリース承認ゲートの停止が、fast / auto は prepare が書き、promote-approved が照合に読む（#2104）
PROMOTE_MATERIAL = "{state_dir}/work/approval-promote.md"
# 経路 merge で本番チャネルへ入れるリリースプランのフェーズ名。旧い値は legacy-values.json が読み替える（#2748）
PROMOTE_PHASE = "リリース（本番チャネルへのマージ）"


def facts_step(repo: str, cmd: str, next_id: str, step_id: str = "facts") -> dict:
    """導入の確認を走らせて承認資料を書くステップ（落ちたらプランが止まる）。gate-2・本番チャネルへのマージ・導入の確認のプランが使う。"""
    # timeout は導入の確認の上限（雛形の verify-install と同じ）
    return {"id": step_id, "type": "run", "stage": "デプロイ", "timeout": 1500, "cwd": repo, "cmd": cmd, "next": next_id}


def verify_facts_cmd(a, repo: str, verify: str) -> str:
    q = shlex.quote
    return f"{STEPS_PY} verify-facts --root {q(repo)} --base {q(a.base)} --verify {q(verify)} --out {VERIFY_MATERIAL}"


def release_plan(a, repo: str, phase: str, rule: str, steps: list, condition: dict | None) -> dict:
    """リリースプラン（導入の確認・本番チャネルへのマージ・リリース承認ゲート）に共通の枠。上限は書き出しが遷移から求める。condition があれば「実行の条件」を足す。"""
    plan = {
        "フェーズ": phase,
        "課題": a.issue,
        "モード": a.mode,
        "作業場所": repo,
        "リポジトリ": repo,
        "規則": rule,
        "steps": steps,
    }
    if condition:
        plan["実行の条件"] = condition
    return with_decls(plan, a)


def plan_verify(a, repo: str, verify: str, condition: dict | None = None) -> dict:
    """経路 merge だけのスプリント（#1457）の「導入の確認」のプラン: verify（導入の確認。落ちたら止まる）の 1 ステップ。"""
    return release_plan(
        a, repo, "デプロイ（導入の確認）", RULE_VERIFY, [facts_step(repo, verify_facts_cmd(a, repo, verify), "end", "verify")], condition
    )


def record_step(repo: str, how: str) -> dict:
    """リリースの Pull Request・直接のマージの経路のリリース記録のステップ。`how` は `record` の経路の引数。CD の結果が失敗なら 5 で止まる。"""
    return {
        "id": "record",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 300,
        "cwd": repo,
        "cmd": f"{STEPS_PY} record {how} --prs {QUEUE_PRS} --cd-result {CD_RESULT_FILE}",
        "pr_from": "release_pr_url",
        "on_exit": dict(CD_FAILED_STOP),
        "on_fail": "judge-record",
        "next": "end",
    }


def plan_cd_result(a, repo: str, production: str, condition: dict | None = None) -> dict:
    """ベースブランチが本番チャネルの CD の形（本番チャネルへ入れるリリースプランが無い）のプラン: cd-result → record --merged（#2623 の決定 9）。"""
    steps = [
        cd_result_step(repo, production, None, QUEUE_PRS),
        record_step(repo, f"--merged --base {shlex.quote(production)}"),
        judge_record_step(),
    ]
    return release_plan(a, repo, "リリース（CD の結果）", RULE_CD_RESULT, steps, condition)


@dataclass(frozen=True)
class PromoteParams:
    """本番チャネルへ入れるリリースプランに固有の入力。mvv はスプリントの状態（fast / auto）、production は本番チャネル、verify は導入の確認のコマンド。"""

    mvv: str | None = None
    production: str | None = None
    verify: str | None = None


def plan_promote(a, repo: str, ci_wait: int, params: PromoteParams = PromoteParams(), condition: dict | None = None) -> dict:
    """本番チャネルへ入れるリリースプラン（#1336 の F5）: ベースブランチ（a.base）から本番チャネル（a.production_branch）への Pull Request を
    `merged-steps.py promote` が作り（あれば使い）、リリース承認ゲートの後にマージする。後片付けはしない（head はベースブランチ）。
    マージの後に cd-result が CD の結果を読み、record（`release-steps.py record --promote`）がリリースの Pull Request へリリース記録を書き、
    その PR を計画の Pull Request にする（まとめの close が `{queue_record}` で探す。#837・#3126）。record が落ちたら judge-record へ進み、
    CD の結果が失敗なら record が書いた後に止まる（#2623）。

    - normal（`mvv` 無し）: promote（承認の引数無し。リリースの承認資料 PROMOTE_MATERIAL へ先端を載せてリリース承認ゲートで終える）→
      promote-approved（`--from` でだけ入る。PROMOTE_MATERIAL の承認したコミットのときだけマージする。#2104）
    - fast / auto（`mvv` にスプリントの状態）: verify（導入の確認。落ちたら止まる。#1457）→ prepare（PR と承認資料）→ mvv（リリース承認ゲートの MVV 判定）→ note（判定のコメント）→
      promote（`--gate-approved mvv`）。promote は mvv と同じ導入の確認の承認資料（VERIFY_MATERIAL）を `--expect-head-from` に受け、
      確かめたコミットのときだけマージする（#1529）。note か promote が落ちたら handoff が承認ゲートへ落とす
    """
    mvv, verify = params.mvv, params.verify
    head, base = a.base, params.production or a.production_branch
    if not base:
        raise DeclError(f"本番チャネルへのマージに要る本番チャネルが無い（{PRODUCTION_SOURCES}）")
    promote = f"{MERGED_PY} promote --head {shlex.quote(head)} --base {shlex.quote(base)}"
    wait = f" --timeout {ci_wait}"
    if release_case.case_at(repo or getattr(a, "worktree", None)).has_cd:
        wait += " --no-admin"  # CD のある場合は管理者の迂回なしでマージする（#2773 の I7）
    timeout = ci_wait + max(MARGIN_FLOOR, ci_wait // 10)
    approved = {
        "id": "promote-approved",
        "type": "run",
        "stage": "デプロイ",
        "timeout": timeout,
        "cmd": promote + wait + f" --approved-from {PROMOTE_MATERIAL} --gate-approved user",
        "next": "cd-result",
    }
    record = [
        cd_result_step(repo, base, head),
        record_step(repo, f"--promote --head {shlex.quote(head)} --base {shlex.quote(base)}"),
        judge_record_step(),
    ]
    if not mvv:
        steps = _promote_steps_normal(promote, wait, timeout, approved, record)
    else:
        steps = _promote_steps_mvv(a, repo, head, base, promote, wait, timeout, verify, mvv, approved, record)
    return release_plan(a, repo, PROMOTE_PHASE, RULE_PROMOTE, steps, condition)


def _promote_steps_normal(promote: str, wait: str, timeout: int, approved: dict, record: list) -> list:
    """normal の本番チャネルへのマージの steps: promote（リリース承認ゲートで終える）→ promote-approved → record。"""
    return [
        {
            "id": "promote",
            "type": "run",
            "stage": "デプロイ",
            "timeout": timeout,
            "cmd": promote + wait + f" --out {PROMOTE_MATERIAL}",
            "gate_next": "end",
            "next": "cd-result",
        },
        approved,
        *record,
    ]


def _promote_steps_mvv(
    a, repo: str, head: str, base: str, promote: str, wait: str, timeout: int, verify: str | None, mvv: str, approved: dict, record: list
) -> list:
    """fast / auto の本番チャネルへのマージの steps: verify → prepare → mvv → note → promote（`--gate-approved mvv`）→ record。"""
    if not verify:
        raise DeclError("fast / auto の本番チャネルへ入れるリリースプランに導入の確認のコマンド（<節>.verify）が無い")
    state = str(Path(mvv).resolve())
    pr_of = f'$(gh pr list --head {shlex.quote(head)} --base {shlex.quote(base)} --state open --json number --jq ".[0].number")'
    return [
        facts_step(repo, verify_facts_cmd(a, repo, verify), "prepare", "verify"),
        {
            "id": "prepare",
            "type": "run",
            "stage": "デプロイ",
            "timeout": 300,
            "cmd": f"{promote} --prepare --out {PROMOTE_MATERIAL}",
            "next": "mvv",
        },
        *mvv_gate_steps(
            f"sh -c '{MVV_PY} check --sprint {shlex.quote(state)} --gate release --material {PROMOTE_MATERIAL} {VERIFY_MATERIAL} --pr {pr_of} "
            f"--mode {a.mode} --root {shlex.quote(repo)} --note {MVV_NOTE}'",
            f"sh -c 'gh pr comment {pr_of} --body-file {MVV_NOTE}'",
            "promote",
        ),
        {
            "id": "promote",
            "type": "run",
            "stage": "デプロイ",
            "timeout": timeout,
            "cmd": promote + wait + f" --expect-head-from {VERIFY_MATERIAL} --gate-approved mvv",
            "on_fail": "handoff",
            "gate_next": "end",
            "next": "cd-result",
        },
        approved,
        *record,
        handoff_step(state, RELEASE_GATE, "リリースの Pull Request のマージ"),
        reject_step(state, RELEASE_GATE, "verify", root=repo),
    ]


GATE_2_MATERIAL = "{state_dir}/work/approval-deploy.md"  # deploy-facts が書く承認資料


def plan_gate_2(a, repo: str, mvv: str, verify: str, prs: str | None, condition: dict | None = None) -> dict:
    """手動反映の本番系（#1454）のリリース承認ゲートのプラン: facts（導入の確認を走らせ承認資料を書く。落ちたら止まる）→
    mvv（リリース承認ゲートの MVV 判定）→ note（判定の記録を PR へ。PR が無ければ承認資料の末尾へ）。note が落ちたら handoff。
    `prs` が None（fast の単独の queue。前のステージの PR を集められない）なら、mvv は判定を打たずに承認ゲート（10）を返す。
    本番のデプロイそのものは、このプランの後の手で行う「本番」のステージで担い手が起こす。"""
    state = str(Path(mvv).resolve())
    q = shlex.quote
    facts = facts_step(repo, f"{STEPS_PY} deploy-facts --root {q(repo)} --verify {q(verify)} --out {GATE_2_MATERIAL}", "mvv")
    if prs is None:
        why = q("前のステージの Pull Request を集められないため、リリース承認ゲートは MVV 判定で通さず、利用者の承認を求める")
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
        steps = [facts, *mvv_gate_steps(mvv_cmd, note_cmd, "end"), handoff_step(state, RELEASE_GATE, "判定のコメント")]
    steps.append(reject_step(state, RELEASE_GATE, "facts", root=repo))
    return release_plan(a, repo, "リリース（リリース承認ゲート）", RULE_GATE_2, steps, condition)


RULE_GATE_2 = "本番のデプロイの前のリリース承認ゲート。導入の確認（facts）が落ちたら直さずに止める。本番のデプロイ（trigger）はこのプランでは打たない。"

RULE_VERIFY = "開発版のチャネルへの反映の後の導入の確認。落ちたら直さずに止める。"

RULE_CD_RESULT = "本番チャネルへのマージの後に CD の結果を読み、リリース記録へ残す。結果が失敗なら記録を書いて止まる。"

RULE_PROMOTE = (
    "リリースの Pull Request のマージは本番系への反映で、リリース承認ゲートに当たる。承認の無いマージは merged-steps.py が止める。"
    "CI の失敗は直さずに止める（リリースの Pull Request にはコミットを足さない）。"
)
