"""new release の雛形（形 `release.form` ごと。#1142 の C1）。`engine` を import しない。"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Callable, NamedTuple

from gate_answer import RELEASE_GATE
from supervise_lib.decl import PRODUCTION_SOURCES, DeclError, with_decls
from supervise_lib.release_flow import REVIEW_RANGE_IDS, bump_others_cmd, promotion_flag, release_branch_step, release_ref, release_units
from supervise_lib.release_flow import release_review_row, review_branch_drop, review_range_steps
from supervise_lib.paths import MERGED_PY, MVV_PY, STEPS_PY, VERIFY_PY
from supervise_lib.plan import QUEUE_PRS
from supervise_lib.verify_steps import (
    CD_FAILED_STOP,
    CD_RESULT_FILE,
    INFRA_WAIT_STOP,
    cd_result_step,
    handoff_step,
    plan_limits,
    reject_step,
    with_margin,
)
from supervise_lib.procedures import with_record
from supervise_lib.release_cd import MVV_NOTE, RULE_RELEASE_PROD_CD, cd_prod_steps, has_release_cd, material_path, mvv_gate_steps
from supervise_lib.queue_fill import RELEASE_BASE, RELEASE_VERSION  # 版の差し込み（キューが流す直前に版の記録で埋める。#2164 の決定 7）


BUMP_STOP = {"2": "stop", "3": "stop"}  # bump が版を読めない（2）・版がもう出ている（3）なら judge を通らずに止める（#2164 の I7）
RULE_RELEASE_DEV = (
    "run のステップが落ちたら、出力を読んで直せるもの（版数の書き漏れ・文書の形）は fix。外部の待ち（CI・ネットワーク）"
    "の揺れなら同じステップをもう一度（retry）。認証や権限の不足・タグの重複は stop。"
)
RULE_RELEASE_PROD = (
    "利用者はリリース承認ゲートを承認した。run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
    "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。"
)
RULE_RELEASE_PROD_MVV = (
    "リリース承認ゲートは利用者か MVV 判定が承認した（先頭の mvv のステップが 0 を返したときだけ先へ進む）。"
    "run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
    "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。"
)


def plan_release(a) -> dict:
    """リリースプラン。形（.ndf/supervise.json の release.form）ごとの雛形へ渡す。知らない形なら DeclError。"""
    form = (a.release or {}).get("form")
    maker = RELEASE_FORMS.get(form)
    if not maker:
        raise DeclError(f"リリースの形 {form!r} の雛形が無い（雛形のある形: {', '.join(RELEASE_FORMS)}）。その形は /ndf:release で配る")
    return maker(a)


DEV_SERIES = "release"  # スプリントの本番のプランが版を読む開発版のプランの名前（#2164）


def release_names(version: str | None) -> tuple[str, str]:
    """リリースプランの (作業場所のリポジトリからの位置, branch)。版が無ければ版の差し込みで書き、キューが流す時点に埋める。"""
    v = version or RELEASE_VERSION
    return f".worktrees/release/v{v}", f"release/v{v}"


def strip_suffix(v: str) -> str:
    """開発版の版数から接尾辞（最初の `-` から後ろ）を外す。"""
    return re.sub(r"-.*$", "", v)


def release_version_of(a) -> tuple[str, str]:
    """(出す版, その接尾辞を外した版)。明示の版が無ければ版の差し込み（#2164）。"""
    v = getattr(a, "version", None)
    return (v, strip_suffix(v)) if v else (RELEASE_VERSION, RELEASE_BASE)


def version_field(a, plugin: str, dev: bool) -> dict:
    """プランの「版」の欄（#2164）。キューの版の差し込みが読む。本番で明示の版も系列も無ければ組めない。"""
    explicit = getattr(a, "version", None) or None
    series = None if dev or explicit else getattr(a, "series", None) or None
    if not dev and not explicit and not series:
        raise DeclError("リリースに要る版が無い（--prod か、版を読む開発版のプランの系列）")
    return {
        "プラグイン": plugin,
        "チャネル": "dev" if dev else "prod",
        "上げ幅": getattr(a, "level", None) or "patch",
        "明示": explicit,
        "系列": series,
    }


def _validate_release_decl(rel: dict, dev: bool, a) -> tuple[str, list[str]]:
    """宣言の release の plugin・runtimes と、リリースの本番のブランチを確かめる。"""
    plugin, runtimes = rel.get("plugin"), rel.get("runtimes")
    if not isinstance(plugin, str) or not plugin:
        raise DeclError("supervise.json: release.plugin（配るプラグインの名前）が要る")
    if not isinstance(runtimes, list) or not runtimes or not all(isinstance(r, str) for r in runtimes):
        raise DeclError("supervise.json: release.runtimes（導入を確かめるランタイムの並び）が要る")
    if not dev and not a.production_branch:
        raise DeclError(f"リリースに要る本番のブランチが無い（{PRODUCTION_SOURCES}）")
    return plugin, runtimes


def _snapshot_step(plugin: str, v: str, after_notes: str) -> dict:
    return {
        "id": "snapshot",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 900,
        "cmd": f"sh -c '{STEPS_PY} run --root . --stage production --version {v} && git add -A && "
        f'(git diff --cached --quiet || git commit -q -m "Release: {plugin} v{v} のトークン消費の記録")\'',
        "on_fail": "judge",
        "next": after_notes,
    }


def _cleanup_step(v: str, prs: str, repo: str | None, review: bool = False) -> dict:
    """本番のリリースの後片付け。`review` なら、その版の衝突の解消のレビューの一時のブランチが残っていれば消す（#3163 の I11）。"""
    drop = review_branch_drop(v) if review else ""
    cmd = (
        f"sh -c '{MERGED_PY} cleanup $(gh pr list --state merged --limit 30 --json number,headRefName "
        f'--jq ".[] | select(.headRefName | startswith(\\"release/v{v}\\")) | .number") {prs}{drop}\''
    )
    return {"id": "cleanup", "type": "run", "stage": "後片付け", "cwd": repo, "cmd": cmd, "on_fail": "judge", "next": "end"}


def _dev_approval_steps(a, repo: str | None, approval: str, prs: str, rts: str, head: str | None = None) -> list[dict]:
    """開発版の approval-facts → 提示物の欄 → 他のプラグインの上げ幅（→ 助言の MVV 判定）のステップ。`head` は承認に出す
    リリースブランチ（#3121）で、範囲の外の検査の PR を `--prs` に渡さない。"""
    v, base = release_version_of(a)
    listed = "" if head else prs
    advise = getattr(a, "advise", None)  # 助言の MVV 判定を置くスプリントの状態（pace: normal。#1400）
    prev = f" --prev-tag {a.prev_tag}" if a.prev_tag else ""
    m = re.fullmatch(r"\{queue_pr:([A-Za-z0-9._-]+)\}", prs.strip())
    check_state = f" --check-state '{{queue_state:{m.group(1)}}}'" if m else ""  # 出す PR を出した検査のプランの状態
    facts = {
        "id": "facts",
        "type": "run",
        "cwd": repo,
        # 前の承認資料を渡し、出し直しでも手で足した節を残す（#1834）
        "cmd": f"{STEPS_PY} approval-facts --version {base} --prs {listed}{prev} --approval {approval}"
        + (f" --head {head}" if head else ""),
        "presentation_to": approval,
        "on_fail": "judge",
        "gate_next": "explain",
        "next": "explain",
    }
    if getattr(a, "mvv", None):
        facts["gate_as_ok"] = True  # リリース承認ゲートは本番の計画の先頭で MVV が判定する
    steps = [
        facts,
        {
            "id": "explain",
            "type": "run",
            "cwd": repo,
            # 検査のプランの状態から承認の材料（レビュー・リファクタリング・全体テスト）を読む（#1488）
            "cmd": f"{STEPS_PY} notes --version {v} --prs {listed} --approval {approval} --verified {rts} --ref {a.base}{check_state}",
            "on_fail": "judge",
            "next": "others",
        },
        {
            # 版を上げる他のプラグインと上げ幅の候補を承認資料へ書く（#1752。本番の bump-others が同じ表を読む）
            "id": "others",
            "type": "run",
            "cwd": repo,
            "cmd": f"{STEPS_PY} changed-plugins --plugin {a.release['plugin']}{prev.replace('--prev-tag', '--since')} "
            f"--prs {listed} --approval {approval}"
            + (f" --head {head}" if head else ""),  # facts と同じリリースブランチの先頭から他のプラグインを拾う（#3121）
            "on_fail": "judge",
            "next": "mvv" if advise else "end",
        },
    ]
    if advise:
        steps += advise_release_steps(a, repo, approval, prs)
    return steps


def _add_prod_mvv_gate(steps: list[dict], a, repo: str | None, approval: str, prs: str) -> None:
    # リリース承認ゲートを MVV で判定する。承認ゲート（10）なら報告は 結果: 関門 で止まり、conductor が承認を取ってから
    # run <計画> --from bump で続ける。従えば判定の記録を --pr の PR のすべてへコメントしてから bump へ進み、
    # コメントが落ちたら handoff がリリース承認ゲートの by: mvv の記録を外して承認ゲートで終える（#1370 の I8）
    material = material_path(repo, approval)
    state = str(Path(a.mvv).resolve())
    steps[0:0] = mvv_gate_steps(
        f"{MVV_PY} check --sprint {shlex.quote(state)} --gate release "
        f"--material {shlex.quote(material)} --pr {prs} --mode {a.mode}"
        + (f" --root {shlex.quote(repo)}" if repo else "")
        + f" --note {MVV_NOTE}",
        f"sh -c 'for p in {prs}; do gh pr comment \"$p\" --body-file {MVV_NOTE} || exit 1; done'",
        "bump",
    )
    steps.append(handoff_step(state, RELEASE_GATE, "判定のコメント"))
    steps.append(reject_step(state, RELEASE_GATE, "mvv", root=repo or "."))


def _prepare_steps(
    a, plugin: str, v: str, prs: str, dev: bool, after_notes: str, repo: str | None, approval: str, on_pr: bool = False
) -> list[dict]:
    """bump から説明文まで（本番は bump-others と消費の記録を挟む）のステップ。`on_pr`（CD のある場合。#2773）の本番の
    bump-others は PR の本文の候補（--prs）で上げる（release_flow.bump_others_cmd）。"""
    # 説明文は PR 本文の「利用者向けの変化」から機械で組む（節が無い PR は題名）
    notes = (
        f"sh -c '{STEPS_PY} notes --version {v} --prs {prs} && git add -A && "
        f'(git diff --cached --quiet || git commit -q -m "Release: {plugin} v{v}")\''
    )
    bump_others = bump_others_cmd(a, plugin, material_path(repo, approval), prs if on_pr else None)
    others = {"id": "bump-others", "type": "run", "stage": "デプロイ", "cmd": bump_others}
    steps = [
        {
            "id": "bump",
            "type": "run",
            "stage": "デプロイ",
            "cmd": f"{STEPS_PY} bump --plugin {plugin} --to {v} --base {release_ref(a, dev)}",
            "on_exit": dict(BUMP_STOP),
            "on_fail": "judge",
            "next": "changelog" if dev else "bump-others",
        },
        *([] if dev else [{**others, "on_fail": "judge", "next": "changelog"}]),
        {"id": "changelog", "type": "run", "cmd": f"{STEPS_PY} changelog --version {v} --prs {prs}", "on_fail": "judge", "next": "notes"},
        {"id": "notes", "type": "run", "stage": "デプロイ", "cmd": notes, "on_fail": "judge", "next": after_notes if dev else "snapshot"},
    ]
    if not dev:
        steps.append(_snapshot_step(plugin, v, after_notes))
    return steps


def _release_step(a, v: str, dev: bool, repo: str | None, approval: str, on_pr: bool = False) -> dict:
    """release のステップ。本番は承認資料を --approval で渡し（承認したコミットはステップの実行時に資料から読む）、
    承認の外の変更で承認ゲート（10）になったら verify へ進まない。MVV 判定のプランは handoff でリリース承認ゲートの by: mvv の
    記録を外して人へ戻し、それ以外は end で終える（#815 の I7）。

    待つ PR ごとの CI の待ちの上限は `ci_wait_timeout`（マージのプランと同じ値）を `--ci-wait` で渡し、ステップの
    `timeout` は 待つ PR の数（開発版 1・本番 2）× その上限 に余裕を足したものと今の値の大きい方にする（基盤待ちの
    75 が実行器の打ち切りより先に出る）。75 は `on_exit` で judge を通らずに止まる（#1645 の決定 6・7）。"""
    ci_wait = int(plan_limits(a)["ci_wait_timeout"])
    step = {
        "id": "release",
        "type": "run",
        "stage": "デプロイ",
        "timeout": max(2400 if dev else 3000, with_margin((1 if dev else 2) * ci_wait)),
        "cmd": f"{STEPS_PY} release --version {v} --ci-wait {ci_wait} --channel {a.channel}",
        "on_exit": dict(INFRA_WAIT_STOP),
        "on_fail": "judge",
        "next": "verify" if dev else "cd-result",
        # リリースの PR（release/v<版> → 起点）と、本番では続く release/v<版> → 本番 の PR のチェックを調べる（#2436 の R1）
        "probe": {"cmd": f"{MERGED_PY} probe --head {{branch}} --head {{base}} --act"},
    }
    if on_pr and not dev:
        # CD のある場合（#2773）: 承認はリリースの PR の上で取る。承認の前は本番の PR を作って 10 で止まり、承認資料を組んで置く
        step["cmd"] += promotion_flag(a)
        step["gate_next"] = "facts"
    elif not dev:
        step["cmd"] += f" --approval {shlex.quote(material_path(repo, approval))}" + promotion_flag(a)
        step["gate_next"] = "handoff" if getattr(a, "mvv", None) else "end"
    return step


def _record_step(v: str, prs: str) -> dict:
    """リリースの後に、本番のリリースの PR へリリース記録を書くステップ（#1273）。落ちたら judge-record へ回し、
    リリース（release）へは戻らない。書けたら本番のリリースの PR を計画の Pull Request にする（まとめの close が読む）。"""
    return {
        "id": "record",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 300,
        "cmd": f"{STEPS_PY} record --version {v} --prs {prs} --cd-result {CD_RESULT_FILE}",
        "pr_from": "release_pr_url",
        "on_exit": dict(CD_FAILED_STOP),
        "on_fail": "judge-record",
        "next": "verify",
    }


def judge_record_step() -> dict:
    """record が落ちたときの判断。選べるのは record のやり直しか停止だけ（fix はデプロイへ戻るため置かない）。"""
    return {
        "id": "judge-record",
        "type": "judge",
        "inputs": ["record"],
        "question": "リリース記録の書き込み（record）が落ちた。リリースは済んでいる。GitHub の揺れなら record をもう一度、"
        "権限の不足・本番のリリースの PR が無いなら止める（stop）",
        "choices": ["record", "stop"],
    }


# 起動指示に足す、利用者の設定ディレクトリで導入しないことの要点。正本は release-verification の Skill の節（#2401 の決定 7）
NO_USER_INSTALL = (
    "導入の確かめは verify-install だけで行い、利用者の設定ディレクトリ（CLAUDE_CONFIG_DIR か ~/.claude、CODEX_HOME か ~/.codex）で"
    "導入の操作（claude plugin install / update / marketplace add / update、codex plugin add / marketplace add）を打たない。"
    "隔離のつもりで環境変数を付けて手で打つことも含む。利用者の設定ディレクトリへの導入が要る条件は、導入せずに記録の表のその行を"
    "「保留（理由: 利用者の設定ディレクトリへの導入が要る）」とし、作業の報告を「判断が要る」で返す"
    "（/ndf:release-verification の「利用者の設定ディレクトリで導入しない」）。"
)


def release_verify_steps(sprint: str, refs: str, record_pr: str, after: str = "retro") -> list[dict]:
    """控えを取る（config-save）→ リリース後テスト → 控えと比べる（config-check）→ `after`。リリース後テストが失敗したら
    config-check-on-fail で比べ、変わっていなくても止まる。3 つの run は on_fail を持たず 0 以外で止まる（#2401 の決定 1・2）。"""
    run = {"type": "run", "timeout": 120}  # 控えは {state_dir}/user-config-before.json（名前はスクリプトが決める）
    check = {**run, "cmd": f"{VERIFY_PY} user-config-check --dir {{state_dir}}"}
    return [
        {"id": "config-save", **run, "cmd": f"{VERIFY_PY} user-config-save --dir {{state_dir}}", "next": "release-verify"},
        release_verify_step(sprint, refs, record_pr),
        {"id": "config-check", **check, "next": after},
        {"id": "config-check-on-fail", **check, "on_exit": {"0": "stop"}},
    ]


def release_verify_step(sprint: str, refs: str, record_pr: str) -> dict:
    """リリース後テスト（/ndf:release-verification）。記録をリリース記録の PR へ置き、まとめの close がそれを読む（#1683）。"""
    return {
        "id": "release-verify",
        "type": "work",
        "full": True,
        "kind": "リリース後テスト",
        "stage": "リリース後テスト",
        "timeout": 3600,
        "prompt": f"/ndf:release-verification スプリント {sprint}（{refs}）。リリース記録は Pull Request #{record_pr} にある。"
        "受け入れ条件ごとに確かめ、スプリントの Pull Request の「手動確認」でリリース後テストへ回した項目と、"
        "マージ前に未検証として残した項目も行として載せて、リリース後テストの記録をその Pull Request へコメントで書く。"
        "課題は閉じない（次の close が閉じる）。"
        "進捗記録（progress-record.sh・projects-sync.sh）は打たない（このステップが ok で終わった後にプランが打つ）。"
        "番号が 0 なら本番へ出ていないため、何もせずに終える。" + NO_USER_INSTALL,
        "on_fail": "config-check-on-fail",
        "next": "config-check",
    }


def _release_verify_steps(
    a, v: str, dev: bool, repo: str | None, ref: str, rts: str, approval: str, prs: str, on_pr: bool = False
) -> list[dict]:
    """release と verify（導入の確かめ）のステップ。本番は間に record（リリース記録）を挟む。"""
    dev_next = ("release-branch" if release_units(a) else "end" if on_pr else "facts") if dev else "cleanup"
    return [
        _release_step(a, v, dev, repo, approval, on_pr),
        *([] if dev else [cd_result_step(repo, a.production_branch, release_names(v)[1]), _record_step(v, prs)]),
        {
            "id": "verify",
            "type": "run",
            # 導入の確認だけを流すため、本番でも「リリース後テスト」と記録しない。受け入れ条件ごとの確認と
            # リリース記録の PR への記録は、まとめの release-verify（release_verify_step）が担う（#2288）
            "stage": "デプロイ",
            "timeout": 1500,
            "cwd": repo,
            "cmd": f"sh -c 'git pull -q --ff-only origin {a.base} && {VERIFY_PY} verify-install --ref {ref} --expect {v} --runtimes {rts}'",
            "on_fail": "judge",
            "next": dev_next,
        },
    ]


def _judge_fix_steps(run_ids: list[str], after_notes: str) -> list[dict]:
    """落ちたステップの判断（judge）と修正（fix）のステップ。"""
    return [
        {
            "id": "judge",
            "type": "judge",
            "inputs": run_ids,
            "question": "落ちたステップを直す（fix）か、同じステップをもう一度（retry）か、止める（stop）か。retry なら decision に"
            "落ちたステップの id を返す",
            "choices": ["fix", *run_ids, "stop"],
        },
        {
            "id": "fix",
            "type": "work",
            "kind": "修正",
            "inputs": run_ids,
            "prompt": "落ちたステップの出力を読み、原因を直してコミットする（push しない）。直したら次は落ちたステップからやり直す。",
            "back_to_failed": True,  # 落ちたのが after_notes より前（bump など）ならそこへ戻る（#1315）
            "next": after_notes,
        },
    ]


class _KindArgs(NamedTuple):
    """リリースの種類ごとの承認の手順が読む値。"""

    a: object
    repo: str | None
    approval: str
    prs: str
    rts: str
    v: str
    base: str
    units: list


def _dev_approvals(c: _KindArgs) -> list[dict]:
    return _dev_approval_steps(c.a, c.repo, c.approval, c.prs, c.rts, release_names(c.base)[1] if c.units else None)


def _cd_prod_approvals(c: _KindArgs) -> list[dict]:
    return cd_prod_steps(
        c.a, c.v, c.prs, c.repo, c.approval, _dev_approval_steps(c.a, c.repo, c.approval, c.prs, c.rts, release_names(c.v)[1])
    )


def _no_approvals(c: _KindArgs) -> list[dict]:
    return []


def _approve_gate(steps: list[dict], c: _KindArgs) -> None:
    steps.insert(0, approve_step(c.a, c.repo, c.approval))


def _mvv_gate(steps: list[dict], c: _KindArgs) -> None:
    _add_prod_mvv_gate(steps, c.a, c.repo, c.approval, c.prs)


def _no_gate(steps: list[dict], c: _KindArgs) -> None:
    return None


class ReleaseKind(NamedTuple):
    """package-plugin のリリースの種類ごとの違い: 規則の文・承認の手順の id・リリースブランチの後・承認の手順・承認ゲート。"""

    rule: str
    approval_ids: tuple[str, ...]
    after_branch: str  # CD のある場合の開発版はリリースブランチ（とそのレビュー）で終える（#2773）
    approval_steps: Callable[[_KindArgs], list[dict]]
    add_gate: Callable[[list[dict], _KindArgs], None]


FACTS_IDS = ("facts", "explain", "others")
RELEASE_KINDS = {
    "dev": ReleaseKind(RULE_RELEASE_DEV, FACTS_IDS, "facts", _dev_approvals, _no_gate),
    "dev-cd": ReleaseKind(RULE_RELEASE_DEV, (), "end", _no_approvals, _no_gate),
    "prod": ReleaseKind(RULE_RELEASE_PROD, (), "facts", _no_approvals, _approve_gate),
    "prod-mvv": ReleaseKind(RULE_RELEASE_PROD_MVV, (), "facts", _no_approvals, _mvv_gate),
    "prod-cd": ReleaseKind(RULE_RELEASE_PROD_CD, (*FACTS_IDS, "approval-post"), "end", _cd_prod_approvals, _no_gate),
}


def _release_kind(dev: bool, on_pr: bool, mvv) -> str:
    """チャネル・CD のある場合か・MVV 判定の有無 → RELEASE_KINDS の鍵。MVV 判定は CD の無い本番だけに効く。"""
    if dev:
        return "dev-cd" if on_pr else "dev"
    if on_pr:
        return "prod-cd"
    return "prod-mvv" if mvv else "prod"


def plan_release_package_plugin(a) -> dict:
    """Claude Code のプラグインを配る形（package-plugin）の計画。宣言の release は
    {"form": "package-plugin", "plugin": <名前>, "runtimes": [<導入を確かめるランタイム>...]}。
    dev: bump → changelog → 説明文 → sync-check → release → verify-install（起点のブランチ）→ approval-facts
    → 提示物の欄 → 他のプラグインの上げ幅の候補（changed-plugins --prs --approval）。prod: bump → bump-others
    （前のタグからの差分のある他のプラグインを、承認資料の表の上げ幅で上げる。changed-plugins --decided）→ changelog → 説明文 → 消費の記録 → sync-check → release → verify-install
    （本番のブランチ）→ 後片付け。本番は release の後に cd-result（CD の結果）と record（本番のリリースの PR へリリース記録）を
    挟み、record が落ちたら judge-record（record か stop）へ回す。sync-check は同期とチェックの宣言があるときだけ置く。
    説明文と提示物の欄は release-steps.py notes が PR 本文の「利用者向けの変化」から組む（LLM を使わない）。"""
    dev = a.channel == "dev"
    plugin, runtimes = _validate_release_decl(a.release, dev, a)
    rts = ",".join(runtimes)
    repo = a.repo or (a.worktree.split("/.worktrees/")[0] if "/.worktrees/" in a.worktree else None)
    on_pr = has_release_cd(a, repo)  # CD のある場合は承認をリリースの PR の上で取る（#2773）
    v, base = release_version_of(a)  # 開発版の本番承認の提示物は正式版の番号で作る
    # --prs-from-queue なら、queue が先行の計画の Pull Request の番号で QUEUE_PRS を置き換える
    prs = " ".join([*map(str, a.prs), *([QUEUE_PRS] if getattr(a, "prs_from_queue", False) else [])])
    sync = bool(getattr(a, "sync_checks", None))
    after_notes = "sync" if sync else "release"
    run_ids = ["bump", *([] if dev else ["bump-others"]), "changelog", "notes", *([] if dev else ["snapshot"]), *(["sync"] if sync else [])]
    units = release_units(a)
    mvv = getattr(a, "mvv", None)
    kind = RELEASE_KINDS[_release_kind(dev, on_pr, mvv)]
    after_branch = kind.after_branch
    reviews = review_range_steps(a, repo, base, after_branch) if dev and units else []  # リリースブランチの PR のレビュー（#3163）
    run_ids += ["release", "verify", *(["release-branch"] if dev and units else []), *(REVIEW_RANGE_IDS if reviews else ())]
    run_ids += kind.approval_ids
    # まとめたリリース（#2436）の本番は、提示で作って承認した資料を渡す
    approval = getattr(a, "approval_path", None) or f"issues/approval-{plugin}-v{base}.md"
    steps = _prepare_steps(a, plugin, v, prs, dev, after_notes, repo, approval, on_pr)
    ref = a.base if dev else a.production_branch
    if sync:
        steps.append({"id": "sync", "type": "run", "preset": "sync-check", "on_fail": "judge", "next": "release"})
    steps += _release_verify_steps(a, v, dev, repo, ref, rts, approval, prs, on_pr)
    if not dev:
        # 後片付け: デプロイの PR（head が release/v{v} で始まる。開発版の release/v{v}-dev.N も含む。宛先は起点のブランチ）と
        # スプリントの PR（--prs）のブランチと作業ツリー
        run_ids.append("cleanup")
        steps.append(_cleanup_step(v, prs, repo, release_review_row(a).diff != "none"))
    steps += [release_branch_step(a, repo, base, units, reviews[0]["id"] if reviews else after_branch), *reviews] if dev and units else []
    ctx = _KindArgs(a, repo, approval, prs, rts, v, base, units)
    steps += kind.approval_steps(ctx)
    steps += _judge_fix_steps(run_ids, after_notes)
    if not dev:
        steps.append(judge_record_step())
    kind.add_gate(steps, ctx)  # CD のある場合の承認ゲートは approval-post の後に置く（release_cd.cd_prod_steps）
    plan = {
        "フェーズ": "開発版のデプロイ" if dev else "リリース",
        "課題": a.issue,
        "モード": a.mode,
        "作業場所": a.worktree,
        "branch": a.branch or f"release/v{v}",
        "起点": f"origin/{a.base}",
        "版": version_field(a, plugin, dev),
        "規則": kind.rule,
        "steps": steps,
    }
    plan.update({"起点のコミット": f"origin/{release_names(v)[1]}"} if units and not dev else {})  # 開発版のプランが組んだリリースブランチ
    with_decls(plan, a)
    if repo:
        plan["リポジトリ"] = repo
        with_record(plan)
    return plan


def approve_step(a, repo: str | None, approval: str) -> dict:
    """本番のプラン（MVV 判定なし）の先頭: リリース承認ゲートの承認の記録を書く（#1426 の決定 7）。提示した SHA は開発版の
    facts の出力（--dev-state か、同じディレクトリの開発版のプランの状態）から読み、利用者の承認の記録は --state から読む。
    証跡が無ければ承認ゲート（10）で終え、bump へ進まない。"""
    dev_state = getattr(a, "dev_state", None)
    facts = shlex.quote(str(Path(dev_state).resolve())) if dev_state else "'{queue_state?:release}'"
    state = getattr(a, "state", None)
    answer = f" --answer-from {shlex.quote(str(Path(state).resolve()))}" if state else ""
    return {
        "id": "approve",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 120,
        **({"cwd": repo} if repo else {}),
        "cmd": f"{STEPS_PY} approve --approval {approval} --facts-from {facts}{answer} --by user",
        "next": "bump",
        "gate_next": "end",
    }


def advise_steps(state: str, gate: str, args: str, note: dict, head: dict | None = None) -> list[dict]:
    """助言の MVV 判定（#1400）の mvv・mvv-note のステップ。判定によらず、想定外の失敗でも mvv-note へ進む。
    `head` は両ステップの type の後（stage）、`note` は mvv-note の timeout から後（載せ先と後続）である。"""
    return [
        {
            "id": "mvv",
            "type": "run",
            **(head or {}),
            "timeout": 900,
            # 前の実行の判定の記録を載せないよう先に消す（MVV なしのときは記録を書かない）
            "cmd": f"rm -f {MVV_NOTE} && {MVV_PY} check --sprint {state} --gate {gate} {args} --note {MVV_NOTE} --advise",
            "on_fail": "mvv-note",
            "next": "mvv-note",
        },
        {"id": "mvv-note", "type": "run", **(head or {}), **note},
    ]


def advise_release_steps(a, repo: str | None, approval: str, prs: str) -> list[dict]:
    """開発版の explain の後の助言の MVV 判定（#1400）。承認資料と出す版の PR を材料にし、判定の記録を承認資料の末尾へ足す。
    判定によらず、想定外の失敗でも mvv-note へ進み、プランの結果は facts の承認ゲートのままである。"""
    material = material_path(repo, approval)
    state = shlex.quote(str(Path(a.advise).resolve()))
    args = f"--material {shlex.quote(material)} --pr {prs} --mode {a.mode}" + (f" --root {shlex.quote(repo)}" if repo else "")
    note = {
        "timeout": 120,
        **({"cwd": repo} if repo else {}),
        "cmd": f"sh -c '[ ! -f {MVV_NOTE} ] || {{ printf \"\\n\"; cat {MVV_NOTE}; }} >> {approval}'",
        "next": "end",
    }
    return advise_steps(state, "release", args, note)


# リリースの形（release の form-<形>.md）ごとの雛形。無い形は /ndf:release で配る
RELEASE_FORMS = {"package-plugin": plan_release_package_plugin}
