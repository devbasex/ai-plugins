"""new release の雛形（形 `release.form` ごと。#1142 の C1）。`engine` を import しない。"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from supervise_lib.decl import WORKTREE_DECL, DeclError, with_decls
from supervise_lib.paths import MERGED_PY, MVV_PY, STEPS_PY, VERIFY_PY
from supervise_lib.plan import QUEUE_PRS
from supervise_lib.verify_steps import MARGIN_FLOOR, handoff_step
from supervise_lib.procedures import with_record


MVV_NOTE = "{state_dir}/work/mvv-note.md"  # mvv-gate.py が書く判定の記録（PR のコメントか承認資料の末尾）
RULE_RELEASE_DEV = (
    "run のステップが落ちたら、出力を読んで直せるもの（版数の書き漏れ・文書の形）は fix。外部の待ち（CI・ネットワーク）"
    "の揺れなら同じステップをもう一度（retry）。認証や権限の不足・タグの重複は stop。"
)
RULE_RELEASE_PROD = (
    "利用者は関門 2 を承認した。run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
    "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。"
)
RULE_RELEASE_PROD_MVV = (
    "関門 2 は利用者か MVV 判定が承認した（先頭の mvv のステップが 0 を返したときだけ先へ進む）。"
    "run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
    "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。"
)


def plan_release(a) -> dict:
    """配布の計画。形（.ndf/supervise.json の release.form）ごとの雛形へ渡す。知らない形なら DeclError。"""
    form = (a.release or {}).get("form")
    maker = RELEASE_FORMS.get(form)
    if not maker:
        raise DeclError(f"配布の形 {form!r} の雛形が無い（雛形のある形: {', '.join(RELEASE_FORMS)}）。その形は /ndf:release で配る")
    return maker(a)


def _validate_release_decl(rel: dict, dev: bool, a) -> tuple[str, list[str]]:
    """宣言の release の plugin・runtimes と、本番の配布の本番のブランチを確かめる。"""
    plugin, runtimes = rel.get("plugin"), rel.get("runtimes")
    if not isinstance(plugin, str) or not plugin:
        raise DeclError("supervise.json: release.plugin（配るプラグインの名前）が要る")
    if not isinstance(runtimes, list) or not runtimes or not all(isinstance(r, str) for r in runtimes):
        raise DeclError("supervise.json: release.runtimes（導入を確かめるランタイムの並び）が要る")
    if not dev and not a.production_branch:
        raise DeclError(f"本番の配布に要る本番のブランチが無い（--production-branch か .ndf/{WORKTREE_DECL} の production_branch）")
    return plugin, runtimes


def _snapshot_step(plugin: str, v: str, after_notes: str) -> dict:
    return {
        "id": "snapshot",
        "type": "run",
        "stage": "配布",
        "timeout": 900,
        "cmd": f"sh -c '{STEPS_PY} run --root . --stage production --version {v} && git add -A && "
        f'(git diff --cached --quiet || git commit -q -m "Release: {plugin} v{v} のトークン消費の記録")\'',
        "on_fail": "judge",
        "next": after_notes,
    }


def _cleanup_step(v: str, prs: str, repo: str | None) -> dict:
    return {
        "id": "cleanup",
        "type": "run",
        "stage": "後片付け",
        "cwd": repo,
        "cmd": f"sh -c '{MERGED_PY} cleanup $(gh pr list --state merged --limit 30 --json number,headRefName "
        f'--jq ".[] | select(.headRefName | startswith(\\"release/v{v}\\")) | .number") {prs}\'',
        "on_fail": "judge",
        "next": "end",
    }


def _dev_approval_steps(a, repo: str | None, approval: str, prs: str, rts: str) -> list[dict]:
    """開発版の approval-facts → 提示物の欄（→ 助言の MVV 判定）のステップ。"""
    v = a.version
    base = re.sub(r"-.*$", "", v)
    advise = getattr(a, "advise", None)  # 助言の MVV 判定を置くスプリントの状態（pace: normal。#1400）
    prev = f" --prev-tag {a.prev_tag}" if a.prev_tag else ""
    facts = {
        "id": "facts",
        "type": "run",
        "cwd": repo,
        "cmd": f"{STEPS_PY} approval-facts --version {base} --prs {prs}{prev}",
        "presentation_to": approval,
        "on_fail": "judge",
        "gate_next": "explain",
        "next": "explain",
    }
    if getattr(a, "mvv", None):
        facts["gate_as_ok"] = True  # 関門 2 は本番の計画の先頭で MVV が判定する
    steps = [
        facts,
        {
            "id": "explain",
            "type": "run",
            "cwd": repo,
            "cmd": f"{STEPS_PY} notes --version {v} --prs {prs} --approval {approval} --verified {rts} --ref {a.base}",
            "on_fail": "judge",
            "next": "mvv" if advise else "end",
        },
    ]
    if advise:
        steps += advise_release_steps(a, repo, approval, prs)
    return steps


def _add_prod_mvv_gate(steps: list[dict], a, repo: str | None, approval: str, prs: str) -> None:
    # 関門 2 を MVV で判定する。関門（10）なら報告は 結果: 関門 で止まり、conductor が承認を取ってから
    # run <計画> --from bump で続ける。従えば判定の記録を --pr の PR のすべてへコメントしてから bump へ進み、
    # コメントが落ちたら handoff が関門 2 の by: mvv の記録を外して関門で終える（#1370 の I8）
    material = _material_path(repo, approval)
    state = str(Path(a.mvv).resolve())
    steps[0:0] = mvv_gate_steps(
        f"{MVV_PY} check --sprint {shlex.quote(state)} --gate release "
        f"--material {shlex.quote(material)} --pr {prs} --mode {a.mode}"
        + (f" --root {shlex.quote(repo)}" if repo else "")
        + f" --note {MVV_NOTE}",
        f"sh -c 'for p in {prs}; do gh pr comment \"$p\" --body-file {MVV_NOTE} || exit 1; done'",
        "bump",
    )
    steps.append(handoff_step(state, "関門 2", "判定のコメント"))


def _prepare_steps(a, plugin: str, v: str, prs: str, dev: bool, after_notes: str) -> list[dict]:
    """bump から説明文まで（本番は bump-others と消費の記録を挟む）のステップ。"""
    # 説明文は PR 本文の「利用者向けの変化」から機械で組む（節が無い PR は題名）
    notes = (
        f"sh -c '{STEPS_PY} notes --version {v} --prs {prs} && git add -A && "
        f'(git diff --cached --quiet || git commit -q -m "Release: {plugin} v{v}")\''
    )
    steps = [
        {
            "id": "bump",
            "type": "run",
            "stage": "配布",
            "cmd": f"{STEPS_PY} bump --plugin {plugin} --to {v} --base {a.base}",
            "on_fail": "judge",
            "next": "changelog" if dev else "bump-others",
        },
        *(
            []
            if dev
            else [
                {
                    "id": "bump-others",
                    "type": "run",
                    "stage": "配布",
                    "cmd": bump_others_cmd(a, plugin),
                    "on_fail": "judge",
                    "next": "changelog",
                }
            ]
        ),
        {"id": "changelog", "type": "run", "cmd": f"{STEPS_PY} changelog --version {v} --prs {prs}", "on_fail": "judge", "next": "notes"},
        {"id": "notes", "type": "run", "stage": "配布", "cmd": notes, "on_fail": "judge", "next": after_notes if dev else "snapshot"},
    ]
    if not dev:
        steps.append(_snapshot_step(plugin, v, after_notes))
    return steps


def _release_verify_steps(a, v: str, dev: bool, repo: str | None, ref: str, rts: str) -> list[dict]:
    """release と verify（導入の確かめ）のステップ。"""
    return [
        {
            "id": "release",
            "type": "run",
            "stage": "配布",
            "timeout": 2400 if dev else 3000,
            "cmd": f"{STEPS_PY} release --version {v} --channel {a.channel}",
            "on_fail": "judge",
            "next": "verify",
            # 配布の PR（release/v<版> → 起点）と、本番では続く 起点 → 本番 の PR のチェックを調べる
            "probe": {"cmd": f"{MERGED_PY} probe --head {{branch}} --head {{base}} --act"},
        },
        {
            "id": "verify",
            "type": "run",
            "stage": "配布" if dev else "リリース後テスト",
            "timeout": 1500,
            "cwd": repo,
            "cmd": f"sh -c 'git pull -q --ff-only origin {a.base} && {VERIFY_PY} verify-install --ref {ref} --expect {v} --runtimes {rts}'",
            "on_fail": "judge",
            "next": "facts" if dev else "cleanup",
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
            "next": after_notes,
        },
    ]


def plan_release_package_plugin(a) -> dict:
    """Claude Code のプラグインを配る形（package-plugin）の計画。宣言の release は
    {"form": "package-plugin", "plugin": <名前>, "runtimes": [<導入を確かめるランタイム>...]}。
    dev: bump → changelog → 説明文 → sync-check → release → verify-install（起点のブランチ）→ approval-facts
    → 提示物の欄。prod: bump → bump-others（前のタグからの差分のある他のプラグインの PATCH。
    release-steps.py changed-plugins）→ changelog → 説明文 → 消費の記録 → sync-check → release → verify-install
    （本番のブランチ）→ 後片付け。sync-check は同期とチェックの宣言があるときだけ置く。
    説明文と提示物の欄は release-steps.py notes が PR 本文の「利用者向けの変化」から組む（LLM を使わない）。"""
    v, dev = a.version, a.channel == "dev"
    plugin, runtimes = _validate_release_decl(a.release, dev, a)
    rts = ",".join(runtimes)
    base = re.sub(r"-.*$", "", v)  # 開発版の本番承認の提示物は正式版の番号で作る
    # --prs-from-queue なら、queue が先行の計画の Pull Request の番号で QUEUE_PRS を置き換える
    prs = " ".join([*map(str, a.prs), *([QUEUE_PRS] if getattr(a, "prs_from_queue", False) else [])])
    repo = a.repo or (a.worktree.split("/.worktrees/")[0] if "/.worktrees/" in a.worktree else None)
    sync = bool(getattr(a, "sync_checks", None))
    after_notes = "sync" if sync else "release"
    run_ids = (
        ["bump", *([] if dev else ["bump-others"]), "changelog", "notes"]
        + ([] if dev else ["snapshot"])
        + (["sync"] if sync else [])
        + ["release", "verify"]
        + (["facts", "explain"] if dev else [])
    )
    steps = _prepare_steps(a, plugin, v, prs, dev, after_notes)
    ref = a.base if dev else a.production_branch
    if sync:
        steps.append({"id": "sync", "type": "run", "preset": "sync-check", "on_fail": "judge", "next": "release"})
    steps += _release_verify_steps(a, v, dev, repo, ref, rts)
    if not dev:
        # 後片付け: 配布の PR（head が release/v{v} で始まる。開発版の release/v{v}-dev.N も含む。宛先は起点のブランチ）と
        # スプリントの PR（--prs）のブランチと作業ツリー
        run_ids.append("cleanup")
        steps.append(_cleanup_step(v, prs, repo))
    approval = f"issues/approval-{plugin}-v{base}.md"
    mvv = getattr(a, "mvv", None)
    if dev:
        steps += _dev_approval_steps(a, repo, approval, prs, rts)
    steps += _judge_fix_steps(run_ids, after_notes)
    if mvv and not dev:
        _add_prod_mvv_gate(steps, a, repo, approval, prs)
    rule = RULE_RELEASE_DEV if dev else RULE_RELEASE_PROD_MVV if mvv else RULE_RELEASE_PROD
    plan = {
        "フェーズ": f"配布（{'開発版' if dev else '本番'}）",
        "課題": a.issue,
        "モード": a.mode,
        "作業場所": a.worktree,
        "branch": a.branch or f"release/v{v}",
        "起点": f"origin/{a.base}",
        "規則": rule,
        "上限": 20,
        "steps": steps,
    }
    with_decls(plan, a)
    if repo:
        plan["リポジトリ"] = repo
        with_record(plan)
    return plan


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
    判定によらず、想定外の失敗でも mvv-note へ進み、プランの結果は facts の関門のままである。"""
    material = _material_path(repo, approval)
    state = shlex.quote(str(Path(a.advise).resolve()))
    args = f"--material {shlex.quote(material)} --pr {prs} --mode {a.mode}" + (f" --root {shlex.quote(repo)}" if repo else "")
    note = {
        "timeout": 120,
        **({"cwd": repo} if repo else {}),
        "cmd": f"sh -c '[ ! -f {MVV_NOTE} ] || {{ printf \"\\n\"; cat {MVV_NOTE}; }} >> {approval}'",
        "next": "end",
    }
    return advise_steps(state, "release", args, note)


# changed-plugins の結果 JSON（最後の行）の items を「名前 版」の行にする
PICK_BUMPS = (
    'import json,sys; [print(i[\\"name\\"], i[\\"to\\"]) for i in json.loads(sys.stdin.read().strip().splitlines()[-1])[\\"items\\"]]'
)


def bump_others_cmd(a, plugin: str) -> str:
    """本番のリリースプランの bump-others（不足 c）。前のタグからの差分のある、宣言の release.plugin 以外の
    プラグインを changed-plugins で列挙し、1 つずつ PATCH を 1 つ上げる。どれかが落ちたら止まる。"""
    since = f" --since {shlex.quote(a.prev_tag)}" if getattr(a, "prev_tag", None) else ""
    return (
        f'out=$({STEPS_PY} changed-plugins --plugin {plugin}{since}) || {{ printf "%s\\n" "$out"; exit 1; }}; '
        f'printf "%s\\n" "$out"; printf "%s\\n" "$out" | python3 -c "{PICK_BUMPS}" | while read -r n to; do '
        f'{STEPS_PY} bump --plugin "$n" --to "$to" --base {a.base} || exit 1; done'
    )


def mvv_gate_steps(mvv_cmd: str, note_cmd: str, next_id: str) -> list[dict]:
    """関門 2 の MVV 判定（mvv）と、判定の記録のコメント（note。落ちたら handoff）の 2 ステップ。note の後は `next_id` へ進む。"""
    return [
        {"id": "mvv", "type": "run", "timeout": 900, "cmd": mvv_cmd, "next": "note", "gate_next": "end"},
        {"id": "note", "type": "run", "timeout": 300, "cmd": note_cmd, "on_fail": "handoff", "next": next_id},
    ]


def _material_path(repo, approval):
    """承認資料の置き場。元のリポジトリが分かればその下。"""
    return f"{repo}/{approval}" if repo else approval


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
        "next": "end",
    }
    if not mvv:
        steps = [
            {"id": "promote", "type": "run", "stage": "配布", "timeout": timeout, "cmd": promote + wait, "gate_next": "end", "next": "end"},
            approved,
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
                "next": "end",
            },
            approved,
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

# 配布の形（release の form-<形>.md）ごとの雛形。無い形は /ndf:release で配る
RELEASE_FORMS = {"package-plugin": plan_release_package_plugin}
