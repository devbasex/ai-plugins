"""スプリントのステージごとのプランを作る関数（#1142 の C1）。組み立てと拒否の判定は `sprint` が持つ。"""

from __future__ import annotations

import argparse
import functools
import json
import shlex
import subprocess
import sys
from pathlib import Path

import branch_flow
import legacy_names
from gate_answer import DESIGN_GATE

from supervise_lib.decl import decl_fields, sprint_flow
from supervise_lib.design_steps import detail_design_runs, gate_steps, pr_steps, writing_steps
from supervise_lib.paths import GLOSSARY_PY, MVV_PY, PUSH_DESIGN, SELF, SPEC_COPY_PY, WORKTREE_SETUP
from supervise_lib.procedures import DESIGN_RESULTS, pr_step, rebuild_step, requirements_path, sprint_materials, sprint_out, with_record
from supervise_lib.procedures import design_path, survey_steps
from supervise_lib.release_templates import DEV_SERIES, MVV_NOTE, advise_steps, plan_release, release_names
from supervise_lib.sprint_revise import with_revise_mvv, with_revise_note
from supervise_lib.plan import sprint_state_path
from supervise_lib.review_units import apply_check, apply_design, apply_impl, drop_unchosen, fast_review_row
from supervise_lib.templates import plan_check, plan_check_since, plan_impl
from supervise_lib.verify_steps import handoff_step, merge_steps, reject_step


RULE_DESIGN = (
    "設計の cross-review は上限 3 ラウンドで打ち切る（収束を待たない）。上限に達して未解決の指摘が 0 件なら、収束と同じ"
    "経路（直しの取り込み → 仕様のコピーの照合 → 用語チェック）を通って gate。未解決が残れば解決のフロー（直し → 再レビュー）を"
    "回し、解決できない（review-settled の判定）か駆動そのものが失敗したら gate。"
)
# 解決のフローの「解決できない」の判定の上限: 直しの成功の回数・直しの費用（ドル）・最初の直しから後の秒（#2388 の決定 3）
RESOLVE_LIMITS = "--max-attempts 2 --max-cost 1.5 --max-seconds 3600"


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


def _review_steps(n: int, copy: str, push: str) -> list:
    """設計のプランのレビューのまとまり。全ペースの設計のプランが共有する（#2388 の決定 4）。cross-review が上限到達
    （終了コード 6）で終わったら review-settled が未解決の件数を読み、0 件なら sync-review、残れば解決のフロー
    （resolve-sync → resolve → push-resolve → review の打ち直し）、解決できなければ ready（承認ゲート 1 の判断）へ回す。"""
    run = {"type": "run", "stage": "ドキュメントレビュー", "timeout": 120}
    sync = "git fetch -q && git merge -q --ff-only '@{u}'"
    settled = f"python3 {shlex.quote(str(SELF))} review-settled --state {{state_dir}}/state.json --step review --fix-step resolve {RESOLVE_LIMITS}"
    fix = "cross-review が上限のラウンドで終わり、未解決の指摘が残った。review の報告（結果の items[0].report）が示す未解決の指摘を直してコミットする（push しない）。直せない指摘は理由を PR のスレッドへ返信する。"
    return [
        # --max-rounds を渡さない。設計の分類の既定（3 ラウンド・前のラウンドからの変更だけ）で回る。駆動の失敗は ready へ回す
        {**run, "id": "review", "type": "drive", "drive": "cross-review", "kind": "ドキュメントレビュー", "timeout": 3600, "args": "{pr}"}
        | {"on_exit": {"6": "review-settled"}, "on_fail": "ready", "next": "sync-review"},
        {
            **run,
            "id": "review-settled",
            "cmd": settled,
            "on_exit": {"1": "resolve-sync", "2": "ready"},
            "on_fail": "ready",
            "next": "sync-review",
        },
        {**run, "id": "resolve-sync", "cmd": sync, "on_fail": "ready", "next": "resolve"},
        {"id": "resolve", "type": "work", "kind": "修正", "stage": run["stage"], "inputs": ["review"], "timeout": 1800, "prompt": fix}
        | {"on_fail": "ready", "next": "push-resolve"},
        {**run, "id": "push-resolve", "timeout": 300, "cmd": push, "on_fail": "ready", "next": "review"},
        # レビューの直しは別の場所から PR のブランチへ push される。追いついてから語を見て push する
        {**run, "id": "sync-review", "cmd": sync, "next": "requirements-recheck"},
        # レビューの直しの後にも写しと課題の本文（正）の一致を見る。写しだけを直した直しを送らずに止める
        # （on_fail を置かない。受け入れ条件の変更は課題の本文を直す利用者の判断で、修正担当の手直しで済ませない。#1769）
        {**run, "id": "requirements-recheck", "cmd": f"{SPEC_COPY_PY} check {n} {copy}", "next": "survey-check"},
    ]


def _glossary_steps(glossary_check: str, push: str) -> list:
    """設計のプランの用語チェックのまとまり（glossary-check / fix-glossary / glossary-recheck / push-glossary）。"""
    return [
        # 用語チェック。当たりは 1 回だけ直し、残った当たりは設計承認ゲートの提示へ載せる
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
        {"id": "push-glossary", "type": "run", "stage": "ドキュメントレビュー", "timeout": 300, "cmd": push, "next": "ready"},
    ]


def plan_sprint_design(a, n: int, repo: str) -> dict:
    """設計のフェーズ: 設計文書を書き、設計 PR を出し、cross-review（設計の既定 3 ラウンド）の後に設計承認ゲートで止まる。"""
    branch = f"design/issue-{n}"
    flow = sprint_flow(a)  # 起点と設計 PR の宛先はスプリントの開始時のブランチ運用で決める（#3121）
    target = flow.doc_target(sprint_branch(a))
    glossary_check = f"{GLOSSARY_PY} check --diff origin/{shlex.quote(target)} --root ."
    copy = requirements_path(n)
    doc = design_path(n)  # 設計文書の頭のファイル（#1289 の決定 2）。題はこの H1 から作る
    # 設計を送った後に題を H1 へ合わせ直す（レビューの直しで H1 が変わる。#1289 の決定 9）
    push = f"{PUSH_DESIGN} && python3 {shlex.quote(str(SELF))} sync-title --pr {{pr}} --doc {doc}"
    detail = detail_design_runs(a.mode)  # 書く段と調査の検査の両方が使う 1 つの決定
    plan = {
        "フェーズ": "設計",
        "課題": [n],
        "モード": a.mode,
        "作業場所": f"{repo}/.worktrees/{branch}",
        "branch": branch,
        "起点": f"origin/{target}",
        "リポジトリ": repo,
        "規則": RULE_DESIGN,
        "steps": [
            *writing_steps(a, n, copy, detail),
            rebuild_step(n, doc),
            *with_base(pr_steps(a, n, doc), target),
            *_review_steps(n, copy, push),
            *survey_steps(n, next_id="glossary-check", require_detail=detail),
            *_glossary_steps(glossary_check, push),
            *gate_steps(),
        ],
    }
    return with_record(with_revise_note(apply_design(a, plan), a, n, doc))  # 設計 PR のレビューの単位で組む（#3163）


def with_base(steps: list[dict], target: str) -> list[dict]:
    """Pull Request のステップの宛先を `target`（ブランチ運用の文書の Pull Request の宛先。#3121）にする。"""
    return [{**s, "base": target} if s.get("type") == "pr" else s for s in steps]


def sprint_branch_first(a, waves: list[dict]) -> list[dict]:
    """文書の Pull Request の宛先がスプリントブランチなら、設計 PR を出す前にスプリントブランチを切る（#3121）。
    スプリントブランチのステージを先頭へ移し、ほかのステージの連結（then_of）は変えない。設計の課題が無ければそのまま。"""
    if not a.design or sprint_flow(a).flow.doc_prs != "sprint":
        return waves
    head = [w for w in waves if w["name"] == "スプリントブランチ"]
    rest = [w for w in waves if w["name"] != "スプリントブランチ"]
    return [{k: v for k, v in w.items() if k != "then_of"} for w in head] + rest


def pin_flow(a) -> None:
    """スプリントの目録（`sprint.json`）に開始時に書いたブランチ運用があれば、`a.flow` をそれにする。`new sprint` を同じ
    スプリントで打ち直したときと `new close` は、宣言ではなく開始時の値で組む（#3121 の決定 4）。"""
    try:
        got = json.loads((sprint_out(a) / "sprint.json").read_text(encoding="utf-8")).get(branch_flow.SNAPSHOT_KEY)
    except (OSError, ValueError, AttributeError):
        return
    if isinstance(got, dict) and got:
        a.flow = got


def flow_head(a) -> dict:
    """目録へ書く開始時のブランチ運用（後の工程と new close はこれを読む。#3121 の決定 4）。"""
    return {branch_flow.SNAPSHOT_KEY: a.flow} if getattr(a, "flow", None) else {}


def plan_sprint_branch(a, repo: str) -> dict:
    """スプリントブランチをスプリントの起点から切り、origin へ送る。起点がベースブランチでなければ `--from` で渡す
    （宣言を読み直さず、開始時のスナップショットで切る。#3121 の I3）。"""
    mb = sprint_branch(a)
    wt = f"{repo}/.worktrees/{mb}"
    start = sprint_flow(a).start_branch
    frm = f" --from {shlex.quote(start)}" if start and start != a.base else ""
    cmd = f"bash {shlex.quote(str(WORKTREE_SETUP))} create {shlex.quote(mb)}{frm} && git -C {shlex.quote(wt)} push -q -u origin {shlex.quote(mb)}"
    return with_record(
        {
            "フェーズ": "実装",
            "課題": a.issue,
            "モード": a.mode,
            "作業場所": repo,
            "規則": "",
            "steps": [{"id": "sprint-branch", "type": "run", "stage": "作業場所の用意", "timeout": 600, "cmd": cmd, "next": "end"}],
        }
    )


def design_results_path(a) -> str:
    """設計の結果のステージが書き、スプリント PR の pr のステップが読む `design-results.json` の絶対パス。"""
    return str(sprint_out(a).resolve() / DESIGN_RESULTS)


def design_read(a) -> list[int]:
    """設計の結果を読む課題: 今回設計する課題（--design）と、設計を承認済みの課題（--designed。開発ループの loop answer）。"""
    return list(dict.fromkeys([*a.design, *(getattr(a, "designed", None) or [])]))


def plan_design_results(a, repo: str) -> dict:
    """設計の結果のステージ（設計承認ゲートの直後）: マージした設計 PR から設計の結果を読み、実装のステージのプランへ
    `触るファイル`・取り込んだ課題・`実行の条件` を書く（決定 2）。LLM を呼ばない run のステップ 1 つ。"""
    manifest = sprint_out(a).resolve() / "sprint.json"
    cmd = (
        f"python3 {shlex.quote(str(SELF))} design-results --manifest {shlex.quote(str(manifest))} "
        f"--base {shlex.quote(sprint_flow(a).doc_target(sprint_branch(a)))} --root {shlex.quote(repo)} --design {' '.join(map(str, design_read(a)))}"
    )
    return with_record(
        {
            "フェーズ": "実装",
            "課題": a.issue,
            "モード": a.mode,
            "作業場所": repo,
            "規則": "",
            "steps": [{"id": "design-results", "type": "run", "stage": "計画", "timeout": 600, "cmd": cmd, "next": "end"}],
        }
    )


def plan_sprint_impl(a, n: int, repo: str, issues: list[int] | None = None) -> dict:
    """実装のフェーズ: 課題の作業ツリーをスプリントブランチから切り、課題の PR をスプリントブランチへ集める。
    `issues` を渡すと課題のまとまりのプラン（`n` は代表の課題）で、ブランチと Pull Request は代表の番号で作る（#3081）。"""
    mb = sprint_branch(a)
    branch = f"feat/issue-{n}-{a.name}"
    refs = " ".join(f"#{i}" for i in issues or [n])
    ns = argparse.Namespace(
        **{
            **decl_fields(a),
            "issue": list(issues or [n]),
            "prompt": None,
            "prompt_file": None,
            "tests": a.tests or ["."],
            "mode": a.mode,
            "worktree": f"{repo}/.worktrees/{branch}",
            "base": mb,
            "title": f"{refs} を実装する（スプリント {a.name}）",
            "summary": f"{refs}（スプリント {a.name} のブランチへ集める）",
            "branch": branch,
            "manual": False,  # 手動確認の節はスプリント PR に載せる
        }
    )
    plan = plan_impl(ns)
    plan.update({"起点": f"origin/{mb}", "リポジトリ": repo, "記録しない工程": ["完了判定"]})  # 検査のプランが記録する（#2921）
    return plan


def keep_branch_merge(steps: list[dict], branch: str) -> list[dict]:
    """選んで集める取り込み方の検査の Pull Request のマージ（#3121 の決定 7）。`merge`・`merge-approved` の前に `branch` 宛ての
    開いた Pull Request を数え、1 件でもあればマージせずに一覧を出して止める（終了コード 1）。マージの後に、マージで origin から
    消えた `branch`（`delete_branch_on_merge`）をローカルの先端で push し直す（強制 push はしない）。ほかのステップは変えない。"""
    b = shlex.quote(branch)
    guard = (
        f"n=$(gh pr list --base {b} --state open --json number --jq '[.[].number|tostring]|join(\" \")') && "
        f'{{ [ -z "$n" ] || {{ echo "{branch} 宛ての開いた Pull Request があるためマージしない: $n" >&2; exit 1; }}; }}'
    )
    restore = (
        f"{{ git ls-remote --exit-code --heads origin refs/heads/{b} >/dev/null || git push -q origin refs/heads/{b}:refs/heads/{b}; }}"
    )
    return [{**s, "cmd": f"{guard} && {s['cmd']} && {restore}"} if s.get("id") in ("merge", "merge-approved") else s for s in steps]


def sprint_impl_plans(a, repo: str, pace: str | None = None) -> dict:
    """実装のステージのプラン。まとまりの記録があればまとまり 1 つにつき 1 本（#3081 の I8a）、無ければ課題ごとに 1 本（I9）。
    レビューはレビューの単位の `impl` の行で置く（#3163）。`pace` はプランへ書く進め方。"""
    bundles = getattr(a, "bundles", None)
    if bundles is None:
        plans = {f"impl-{n}": apply_impl(a, plan_sprint_impl(a, n, repo)) for n in a.issue}
    else:
        plans = {f"impl-{b.lead()}": apply_impl(a, plan_sprint_impl(a, b.lead(), repo, b.issues)) for b in bundles}
    for plan in plans.values() if pace else ():
        plan["進め方"] = pace  # 課題の本文の見出し行と通過記録へ進め方を書く（supervise_lib/state.py）
    return plans


def plan_sprint_check(a, repo: str) -> dict:
    """検査のフェーズ: スプリントブランチから起点のブランチへ PR を 1 本出し、構造改善・cross-review・完了判定を 1 回通す。"""
    mb = sprint_branch(a)
    target = sprint_flow(a).check_target()
    ns = argparse.Namespace(
        **decl_fields(a), pr="{pr}", scope=a.scope, issue=a.issue, mode=a.mode, worktree=f"{repo}/.worktrees/{mb}", advance_done=True
    )
    plan = plan_check(ns)
    plan.pop("Pull Request", None)
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
        # 利用者向けの変化・閉じる課題・設計 PR・手動確認は、pr のステップが材料から集める（決定 4）
        pr_step(
            target,
            f"スプリント {a.name}",
            f"スプリント {a.name} の課題を {target} へ取り込む。",
            "",
            plan["steps"][0]["id"],
            sprint_materials(mb, design_results_path(a), a.issue),
            body="template",
        ),
    ] + plan["steps"]
    if sprint_flow(a).row.keeps_sprint_branch:
        plan["steps"] = keep_branch_merge(plan["steps"], mb)
    return apply_check(a, plan)  # スプリントの PR のレビューの単位で、選ばないレビューのステップを外す（#3163）


def plan_sprint_release(a, repo: str, advise: bool = False, channel: str = "dev", version: str | None = None) -> dict:
    """pace: normal のデプロイ。出す PR は検査のプランのスプリントの PR（`{queue_pr:check}`）で、検査の queue の --then でも
    単独で流し直しても埋まる（#1915）。本番（channel が prod）も同じ組み立てで、リリース承認ゲートの後に単独で流す（#1914）。
    `advise` なら承認資料の後に助言の MVV 判定を置く（pace: normal と --state。#1400）。置くのは承認資料を組むプランだけで、
    CD の無い場合は開発版、CD のある場合は本番（#2773 の決定 6。承認資料を本番の PR の先端で組む）である。
    `version` は明示の版（開発版は省けば --version）で、無ければ版を流す時点に決める（開発版は origin から、本番は開発版の
    プランの版の記録から。#2164）。"""
    version = version or (a.version if channel == "dev" else None)
    wt, branch = release_names(version)
    ns = argparse.Namespace(
        **decl_fields(a),
        version=version,
        level=getattr(a, "level", None),
        series=DEV_SERIES if channel == "prod" else None,
        prs=["{queue_pr:check}"],
        prs_from_queue=False,
        channel=channel,
        repo=repo,
        prev_tag=None,
        worktree=f"{repo}/{wt}",
        branch=branch,
        issue=a.issue,
        mode=a.mode,
        # 承認資料を組むプラン（release_templates の _dev_approval_steps を使う種類）だけが読む。CD の無い本番は組まないため読まない
        advise=a.state if advise else None,
        state=getattr(a, "state", None),  # 本番の approve がリリース承認ゲートの利用者の承認の記録を読む（#1426）
        units=[sprint_branch(a)],  # 取り込み方の行が単位を選ぶときのリリースの単位（#3121）
    )
    return plan_release(ns)


def advise_design_steps(a) -> list[dict]:
    """助言の MVV 判定（#1400）の mvv・mvv-note のステップ。判定によらず、想定外の失敗でも設計承認ゲートの judge（gate）へ進む。"""
    note = {
        "timeout": 300,
        "cmd": f"sh -c '[ ! -f {MVV_NOTE} ] || gh pr comment {{pr}} --body-file {MVV_NOTE}'",
        "on_fail": "gate",
        "next": "gate",
    }
    return advise_steps(shlex.quote(sprint_state_path(a)), "design", f"--pr {{pr}} --mode {a.mode} --root .", note, {"stage": "設計"})


def plan_advise_design(a, n: int, repo: str) -> dict:
    """pace: normal と --state の設計: 用語チェックの後の push と設計承認ゲートの judge の間へ助言の MVV 判定を入れる（#1400 の決定 4）。
    判定は設計 PR へコメントし、プランは判定によらず設計承認ゲートで止まる（承認は利用者が行う）。"""
    plan = plan_sprint_design(a, n, repo)
    steps = plan["steps"]
    gate = steps.pop()
    for s in steps:
        if s["id"] == "ready":
            s["next"] = "mvv"
    gate["inputs"] = [*gate["inputs"], "mvv"]
    gate["question"] += "。mvv に助言の MVV 判定（判定・理由・根拠）があれば、設計承認ゲートの提示に載せる（承認は利用者が行う）"
    plan["steps"] = [*steps, *advise_design_steps(a), gate, reject_step(sprint_state_path(a), DESIGN_GATE, "mvv", pr="{pr}")]
    return plan


def plan_mvv_design(a, n: int, repo: str) -> dict:
    """pace: fast と auto の設計: 設計承認ゲートの judge を MVV 判定のステップへ替える。従えばラベルとコメントを付けてマージする。
    ラベルの付与かマージが落ちたら handoff で設計承認ゲートの by: mvv の記録を外し、プランを承認ゲートで終える（#1370 の I8）。"""
    plan = plan_sprint_design(a, n, repo)
    state = shlex.quote(sprint_state_path(a))
    # 用語チェックの当たりが直し切れずに残ったら、mvv の判定へ渡さず設計承認ゲートの judge（gate）へ回す
    push = ""
    for s in plan["steps"]:
        if s["id"] == "push-glossary":
            s["next"] = "mvv"
            push = s["cmd"]  # 送りと題の合わせ直しを push-glossary と同じにする（#1289 の決定 9）
        elif s["id"] == "glossary-recheck":
            s["on_fail"] = "push-glossary-gate"
    plan["steps"] += [
        {"id": "push-glossary-gate", "type": "run", "stage": "ドキュメントレビュー", "timeout": 300, "cmd": push, "next": "ready"},
        {
            "id": "mvv",
            "type": "run",
            "stage": "設計",
            "timeout": 900,
            "cmd": f"{MVV_PY} check --sprint {state} --gate design --pr {{pr}} --mode {a.mode} --root . --note {MVV_NOTE}",
            "next": "approve",
            "gate_next": "end",
        },
        {
            "id": "approve",
            "type": "run",
            # 控えは mvv が従うときだけ書く。承認ゲートを利用者が承認して --from approve で続けたときは無い
            "cmd": f"sh -c 'gh pr edit {{pr}} --add-label design-approved && "
            f"{{ [ ! -f {MVV_NOTE} ] || gh pr comment {{pr}} --body-file {MVV_NOTE}; }} && gh pr ready {{pr}}'",
            "on_fail": "handoff",
            "next": "merge-gate",
        },
        *merge_steps(a, next="end", on_fail="handoff"),
        handoff_step(sprint_state_path(a), DESIGN_GATE, "承認ラベルの付与かマージ"),
        reject_step(sprint_state_path(a), DESIGN_GATE, "mvv", pr="{pr}"),
    ]
    plan["規則"] = (
        "設計の cross-review は上限 3 ラウンドで打ち切る（収束を待たない）。上限に達して未解決の指摘が 0 件なら、収束と同じ経路を"
        "通って設計承認ゲートの判定（mvv のステップ）へ渡す。未解決が残れば解決のフロー（直し → 再レビュー）を回し、解決すれば mvv へ"
        "渡す。解決できない（review-settled の判定）か駆動そのものが失敗したら gate。"
    )
    return with_revise_mvv(plan, a, n)


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
    return apply_impl(a, plan, bundled=False)  # pace: fast は課題のまとまりで実装を組まない（#3163）


def plan_fast_check(a, repo: str, name: str, final: bool = False, review_only: bool = False) -> dict:
    """pace: fast の検査。cross-refactoring を選ばなければ実装レビューだけで組み、選ばないレビューのステップを外す（#3163）。"""
    row = fast_review_row(a)
    checks = row.checks if row.diff != "none" else ()
    review_only = review_only or "cross-refactoring" not in checks
    ns = argparse.Namespace(
        **decl_fields(a),
        id=name,
        worktree=repo,
        issue=a.issue,
        mode=a.mode,
        sprint=getattr(a, "state", None),
        final=final,
        final_sprint=a.name if final else None,  # 最後の検査は範囲をこのスプリントの PR に絞る（#3126）
        review_only=review_only,
    )
    return drop_unchosen(plan_check_since(ns), checks)


def plan_mvv_release(a, repo: str, version: str | None, channel: str, condition: dict | None = None, prs: list[str] | None = None) -> dict:
    """pace: fast と auto のリリース。開発版は承認資料を gate_as_ok で写し、本番は先頭の mvv のステップがリリース承認ゲートを判定する。
    prs を渡せばその Pull Request（`{queue_pr:<名>}` でもよい）を出す版の PR にし、省けば前のステージの PR のすべて。
    `version` は明示の版で、無ければ版を流す時点に決める（#2164）。"""
    wt, branch = release_names(version)
    ns = argparse.Namespace(
        **decl_fields(a),
        version=version,
        level=getattr(a, "level", None),
        series=DEV_SERIES if channel == "prod" else None,
        prs=prs or [],
        prs_from_queue=prs is None,
        channel=channel,
        repo=repo,
        prev_tag=None,
        worktree=f"{repo}/{wt}",
        branch=branch,
        issue=a.issue,
        mode=a.mode,
        mvv=a.state,
        units=[sprint_branch(a)],  # 取り込み方の行が単位を選ぶときのリリースの単位（#3121）
    )
    plan = plan_release(ns)
    if condition:
        plan["実行の条件"] = condition
    return plan
