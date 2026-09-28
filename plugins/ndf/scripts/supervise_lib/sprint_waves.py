"""スプリントのステージごとのプランを作る関数（#1142 の C1）。組み立てと拒否の判定は `sprint` が持つ。"""

from __future__ import annotations

import argparse
import functools
import shlex
import subprocess
import sys
from pathlib import Path

import legacy_names

from supervise_lib.decl import decl_fields
from supervise_lib.paths import GLOSSARY_PY, MVV_PY, PUSH_DESIGN, SELF, SPEC_COPY_PY, WORKTREE_SETUP
from supervise_lib.release_templates import MVV_NOTE, advise_steps, plan_release
from supervise_lib.templates import plan_check, plan_check_since, plan_impl
from supervise_lib.verify_steps import handoff_step, merge_step


DESIGN_GLOSSARY_NOTE = "{state_dir}/work/glossary-candidates.md"  # 設計のプランが起こした用語集の候補の語
RULE_DESIGN = (
    "設計の cross-review は上限 3 ラウンドで関門 1 へ渡す（収束を待たない）。レビューが ok か、"
    "上限・振動で打ち切られたなら gate。駆動そのものが失敗したら stop。"
)


@functools.cache
def _branch_of(repo: str, name: str) -> str:
    new, *olds = legacy_names.branch_prefixes("sprint/")
    for prefix in (new, *olds):
        refs = (f"refs/heads/{prefix}{name}", f"refs/remotes/origin/{prefix}{name}")
        p = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(refname)", *refs], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            if prefix != new:
                print(f"{prefix}{name} が既にあるため、スプリントブランチに使い続ける（新しく切るときは {new}{name}）", file=sys.stderr)
            return prefix + name
    return new + name


def sprint_branch(a) -> str:
    """スプリントブランチ（`sprint/<名前>`）。`sprint/<名前>` の参照が無く、改名の前の旧名の頭の参照があれば
    そちらを使い続ける（#1407 の I4）。参照はローカルと origin のものだけを見て、fetch しない（決定 6）。"""
    return _branch_of(str(Path(a.worktree).resolve()), a.name)


def plan_sprint_design(a, n: int, repo: str) -> dict:
    """設計のフェーズ: 設計文書を書き、設計 PR を出し、cross-review（設計の既定 3 ラウンド）の後に関門 1 で止まる。"""
    branch = f"design/issue-{n}"
    glossary_check = f"{GLOSSARY_PY} check --diff origin/{shlex.quote(a.base)} --root ."
    copy = f"issues/issue-{n}-requirements.md"
    return {
        "フェーズ": "設計",
        "課題": [n],
        "モード": a.mode,
        "作業場所": f"{repo}/.worktrees/{branch}",
        "branch": branch,
        "起点": f"origin/{a.base}",
        "リポジトリ": repo,
        "規則": RULE_DESIGN,
        "上限": 12,
        "steps": [
            # 設計の工程の入口の検査（#1111 の I5）。用語集が無ければ worktree の中で起こし、設計 PR と一緒に
            # コミットする（承認ゲートを増やさない。語の採否は承認ゲート 1 で利用者が見る）。それ以外の失敗は止まる
            {
                "id": "glossary",
                "type": "run",
                "stage": "設計",
                "timeout": 300,
                "cmd": f"python3 {shlex.quote(str(SELF))} design-glossary --mode {shlex.quote(a.mode)} --root . "
                f"--out {DESIGN_GLOSSARY_NOTE}",
                "next": "requirements-check",
            },
            # 要求が課題の本文にあり、写しが一致すれば要求のステップを飛ばす
            {
                "id": "requirements-check",
                "type": "run",
                "stage": "要求と受け入れ条件",
                "timeout": 120,
                "cmd": f"{SPEC_COPY_PY} check {n} {copy}",
                "on_fail": "requirements",
                "next": "design",
            },
            {
                "id": "requirements",
                "type": "work",
                "full": True,
                "kind": "要求",
                "stage": "要求と受け入れ条件",
                "issues": True,
                "timeout": 3600,
                "next": "design",
                "prompt": f"/ndf:requirements-design #{n}。人へ問わずに進め、決められない点は前提か未決として課題の本文へ"
                f"書く。本文を書いたら `spec-copy.py write {n} {copy}` で写しを作り、コミットする（push しない）。",
            },
            {
                "id": "design",
                "type": "work",
                "full": True,
                "kind": "設計",
                "stage": "設計",
                "issues": True,
                "timeout": 3600,
                "next": "pr",
                "prompt": f"/ndf:design #{n}。設計文書は 1,000 行以下にする（超える主題は設計を 2 本に分けると報告する）。"
                "コミットする（push しない）。",
            },
            # glossary のステップが用語集を起こしたときは、候補の語を PR 本文へ載せる（承認ゲート 1 の材料）
            {
                "id": "pr",
                "type": "pr",
                "stage": "ドキュメントレビュー",
                "base": a.base,
                "title": f"設計: #{n}",
                "summary": f"#{n} の設計（スプリント {a.name}）",
                "append": [DESIGN_GLOSSARY_NOTE],
                "next": "review",
            },
            # --max-rounds を渡さない。設計の分類の既定（3 ラウンド・前のラウンドからの変更だけ）で回る
            {
                "id": "review",
                "type": "drive",
                "drive": "cross-review",
                "kind": "ドキュメントレビュー",
                "stage": "ドキュメントレビュー",
                "timeout": 3600,
                "args": "{pr}",
                "on_fail": "gate",
                "next": "sync-review",
            },
            # レビューの直しは別の場所から PR のブランチへ push される。追いついてから語を見て push する
            {
                "id": "sync-review",
                "type": "run",
                "stage": "ドキュメントレビュー",
                "timeout": 120,
                "cmd": "git fetch -q && git merge -q --ff-only '@{u}'",
                "next": "glossary-check",
            },
            # 用語チェック。当たりは 1 回だけ直し、残った当たりは関門 1 の提示へ載せる
            {
                "id": "glossary-check",
                "type": "run",
                "stage": "ドキュメントレビュー",
                "timeout": 120,
                "cmd": glossary_check,
                "on_fail": "fix-glossary",
                "next": "push-glossary",
            },
            {
                "id": "fix-glossary",
                "type": "work",
                "kind": "修正",
                "stage": "ドキュメントレビュー",
                "inputs": ["glossary-check"],
                "timeout": 1800,
                "next": "glossary-recheck",
                "prompt": "用語チェックの当たりを直す。未登録の語は用語集へ足す（source は空にし、pending_source に"
                "設計文書のパスを書く）か、用語集の語へ言い換える。廃止した語は"
                "用語集の語へ言い換える。用語集を変えたら `glossary.py render` で文書を作り直し、コミットする"
                "（push しない）。利用者が採るかを決めるべき語は直さずに「判断が要る」と報告する。",
            },
            {
                "id": "glossary-recheck",
                "type": "run",
                "stage": "ドキュメントレビュー",
                "timeout": 120,
                "cmd": glossary_check,
                "on_fail": "push-glossary",
                "next": "push-glossary",
            },
            {"id": "push-glossary", "type": "run", "stage": "ドキュメントレビュー", "timeout": 300, "cmd": PUSH_DESIGN, "next": "gate"},
            {
                "id": "gate",
                "type": "judge",
                "inputs": ["review", "glossary-recheck"],
                "question": "関門 1（設計 Pull Request のマージ）へ渡す（gate）か、止める（stop）か。glossary-recheck に"
                "用語チェックの当たりが残っていれば、関門 1 の提示に載せる",
                "choices": ["gate", "stop"],
            },
        ],
    }


def plan_sprint_branch(a, repo: str) -> dict:
    """スプリントブランチを起点のブランチから切り、origin へ送る。"""
    mb = sprint_branch(a)
    wt = f"{repo}/.worktrees/{mb}"
    cmd = (
        f"bash {shlex.quote(str(WORKTREE_SETUP))} create {shlex.quote(mb)} && git -C {shlex.quote(wt)} push -q -u origin {shlex.quote(mb)}"
    )
    return {
        "フェーズ": "実装",
        "課題": a.issue,
        "モード": a.mode,
        "作業場所": repo,
        "規則": "",
        "上限": 3,
        "steps": [{"id": "sprint-branch", "type": "run", "stage": "作業場所の用意", "timeout": 600, "cmd": cmd, "next": "end"}],
    }


def plan_sprint_impl(a, n: int, repo: str) -> dict:
    """実装のフェーズ: 課題の作業ツリーをスプリントブランチから切り、課題の PR をスプリントブランチへ集める。"""
    mb = sprint_branch(a)
    branch = f"feat/issue-{n}-{a.name}"
    ns = argparse.Namespace(
        **{
            **decl_fields(a),
            "issue": [n],
            "prompt": None,
            "prompt_file": None,
            "tests": a.tests or ["."],
            "mode": a.mode,
            "worktree": f"{repo}/.worktrees/{branch}",
            "base": mb,
            "title": f"#{n} を実装する（スプリント {a.name}）",
            "summary": f"#{n}（スプリント {a.name} のブランチへ集める）",
            "branch": branch,
        }
    )
    plan = plan_impl(ns)
    plan.update({"起点": f"origin/{mb}", "リポジトリ": repo})
    return plan


def plan_sprint_check(a, repo: str) -> dict:
    """検査のフェーズ: スプリントブランチから起点のブランチへ PR を 1 本出し、構造改善・cross-review・完了判定を 1 回通す。"""
    mb = sprint_branch(a)
    ns = argparse.Namespace(**decl_fields(a), pr="{pr}", scope=a.scope, issue=a.issue, mode=a.mode, worktree=f"{repo}/.worktrees/{mb}")
    plan = plan_check(ns)
    plan.pop("Pull Request", None)
    related = "関連: " + " ".join(f"#{i}" for i in a.issue)
    plan.update({"branch": mb, "起点": f"origin/{mb}", "リポジトリ": repo})
    plan["steps"] = [
        {
            "id": "collect",
            "type": "run",
            "stage": "実装",
            "timeout": 600,
            "cmd": f"git pull -q --ff-only origin {shlex.quote(mb)}",
            "next": "pr",
        },
        {
            "id": "pr",
            "type": "pr",
            "stage": "Pull Request",
            "base": a.base,
            "title": f"スプリント {a.name}",
            "body": "template",
            "summary": f"スプリント {a.name} の課題を {a.base} へ取り込む。\n\n{related}",
            "changes": f"スプリント {a.name} の課題を {a.base} へ取り込む。",
            "next": "assess",
        },
    ] + plan["steps"]
    return plan


def plan_sprint_release(a, repo: str, advise: bool = False) -> dict:
    """開発版の配布。検査の queue が --then で流し、検査の PR を --prs へ渡す。`advise` なら承認資料の後に
    助言の MVV 判定を置く（pace: normal と --state。#1400）。"""
    ns = argparse.Namespace(
        **decl_fields(a),
        version=a.version,
        prs=[],
        prs_from_queue=True,
        channel="dev",
        repo=repo,
        prev_tag=None,
        worktree=f"{repo}/.worktrees/release/v{a.version}",
        branch=f"release/v{a.version}",
        issue=a.issue,
        mode=a.mode,
        advise=a.state if advise else None,
    )
    return plan_release(ns)


def advise_design_steps(a) -> list[dict]:
    """助言の MVV 判定（#1400）の mvv・mvv-note のステップ。判定によらず、想定外の失敗でも関門 1 の judge（gate）へ進む。"""
    state = shlex.quote(str(Path(a.state).resolve()))
    note = {
        "timeout": 300,
        "cmd": f"sh -c '[ ! -f {MVV_NOTE} ] || gh pr comment {{pr}} --body-file {MVV_NOTE}'",
        "on_fail": "gate",
        "next": "gate",
    }
    return advise_steps(state, "design", f"--pr {{pr}} --mode {a.mode} --root .", note, {"stage": "設計"})


def plan_advise_design(a, n: int, repo: str) -> dict:
    """pace: normal と --state の設計: 用語チェックの後の push と関門 1 の judge の間へ助言の MVV 判定を入れる（#1400 の決定 4）。
    判定は設計 PR へコメントし、プランは判定によらず関門 1 で止まる（承認は利用者が行う）。"""
    plan = plan_sprint_design(a, n, repo)
    steps = plan["steps"]
    gate = steps.pop()
    for s in steps:
        if s["id"] == "push-glossary":
            s["next"] = "mvv"
    gate["inputs"] = [*gate["inputs"], "mvv"]
    gate["question"] += "。mvv に助言の MVV 判定（判定・理由・根拠）があれば、関門 1 の提示に載せる（承認は利用者が行う）"
    plan["steps"] = [*steps, *advise_design_steps(a), gate]
    return plan


def plan_mvv_design(a, n: int, repo: str) -> dict:
    """pace: fast と auto の設計: 関門 1 の judge を MVV 判定のステップへ替える。従えばラベルとコメントを付けてマージする。
    ラベルの付与かマージが落ちたら handoff で関門 1 の by: mvv の記録を外し、プランを関門で終える（#1370 の I8）。"""
    plan = plan_sprint_design(a, n, repo)
    state = shlex.quote(str(Path(a.state).resolve()))
    note = MVV_NOTE
    # 用語チェックの当たりが直し切れずに残ったら、mvv の判定へ渡さず関門 1 の judge（gate）へ回す
    for s in plan["steps"]:
        if s["id"] == "push-glossary":
            s["next"] = "mvv"
        elif s["id"] == "glossary-recheck":
            s["on_fail"] = "push-glossary-gate"
    plan["steps"] += [
        {"id": "push-glossary-gate", "type": "run", "stage": "ドキュメントレビュー", "timeout": 300, "cmd": PUSH_DESIGN, "next": "gate"},
        {
            "id": "mvv",
            "type": "run",
            "stage": "設計",
            "timeout": 900,
            "cmd": f"{MVV_PY} check --sprint {state} --gate design --pr {{pr}} --mode {a.mode} --root . --note {note}",
            "next": "approve",
            "gate_next": "end",
        },
        {
            "id": "approve",
            "type": "run",
            # 控えは mvv が従うときだけ書く。関門を利用者が承認して --from approve で続けたときは無い
            "cmd": f"sh -c 'gh pr edit {{pr}} --add-label design-approved && "
            f"{{ [ ! -f {note} ] || gh pr comment {{pr}} --body-file {note}; }} && gh pr ready {{pr}}'",
            "on_fail": "handoff",
            "next": "merge",
        },
        merge_step(a, next="end", on_fail="handoff"),
        handoff_step(str(Path(a.state).resolve()), "関門 1", "承認ラベルの付与かマージ"),
    ]
    plan["規則"] = (
        "設計の cross-review は上限 3 ラウンドで関門 1 の判定（mvv のステップ）へ渡す（収束を待たない）。駆動そのものが失敗したら gate。"
    )
    return plan


def plan_fast_impl(a, n: int, repo: str) -> dict:
    """pace: fast の実装: 課題の作業ツリーを起点のブランチから切り、Pull Request を起点のブランチへ直接入れる。
    閉じる語は書かない（課題はスプリントの終わりの close のステップが閉じる）。"""
    branch = f"feat/issue-{n}-{a.name}"
    ns = argparse.Namespace(
        **{
            **decl_fields(a),
            "issue": [n],
            "prompt": None,
            "prompt_file": None,
            "tests": a.tests or ["."],
            "mode": a.mode,
            "worktree": f"{repo}/.worktrees/{branch}",
            "title": f"#{n} を実装する（スプリント {a.name}）",
            "summary": f"#{n}（スプリント {a.name}。課題はスプリントの終わりに閉じる）",
            "branch": branch,
        }
    )
    plan = plan_impl(ns)
    plan.update({"起点": f"origin/{a.base}", "リポジトリ": repo, "進め方": "fast"})
    return plan


def plan_fast_check(a, repo: str, name: str, final: bool = False, review_only: bool = False) -> dict:
    ns = argparse.Namespace(
        **decl_fields(a),
        id=name,
        worktree=repo,
        issue=a.issue,
        mode=a.mode,
        sprint=getattr(a, "state", None),
        final=final,
        review_only=review_only,
    )
    return plan_check_since(ns)


def plan_mvv_release(a, repo: str, version: str, channel: str, condition: dict | None = None, prs: list[str] | None = None) -> dict:
    """pace: fast と auto のリリース。開発版は承認資料を gate_as_ok で写し、本番は先頭の mvv のステップが関門 2 を判定する。
    prs を渡せばその Pull Request（`{queue_pr:<名>}` でもよい）を出す版の PR にし、省けば前のステージの PR のすべて。"""
    ns = argparse.Namespace(
        **decl_fields(a),
        version=version,
        prs=prs or [],
        prs_from_queue=prs is None,
        channel=channel,
        repo=repo,
        prev_tag=None,
        worktree=f"{repo}/.worktrees/release/v{version}",
        branch=f"release/v{version}",
        issue=a.issue,
        mode=a.mode,
        mvv=a.state,
    )
    plan = plan_release(ns)
    if condition:
        plan["実行の条件"] = condition
    return plan
