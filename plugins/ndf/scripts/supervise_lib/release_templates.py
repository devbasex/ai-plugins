"""new release の雛形（形 `release.form` ごと。#1142 の C1）。`engine` を import しない。"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from supervise_lib.decl import WORKTREE_DECL, DeclError, with_decls
from supervise_lib.paths import HERE, MERGED_PY, MVV_PY, STEPS_PY, VERIFY_PY
from supervise_lib.plan import QUEUE_PRS


RULE_RELEASE_DEV = ("run のステップが落ちたら、出力を読んで直せるもの（版数の書き漏れ・文書の形）は fix。外部の待ち（CI・ネットワーク）"
                    "の揺れなら同じステップをもう一度（retry）。認証や権限の不足・タグの重複は stop。")
RULE_RELEASE_PROD = ("利用者は関門 2 を承認した。run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
                     "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。")
RULE_RELEASE_PROD_MVV = ("関門 2 は利用者か MVV 判定が承認した（先頭の mvv のステップが 0 を返したときだけ先へ進む）。"
                         "run のステップが落ちたら、直せるもの（版数の書き漏れ・文書の形）は fix。"
                         "外部の待ち（CI・ネットワーク）の揺れなら同じステップをもう一度。タグの重複・権限の不足は stop。")


def plan_release(a) -> dict:
    """配布の計画。形（.ndf/supervise.json の release.form）ごとの雛形へ渡す。知らない形なら DeclError。"""
    form = (a.release or {}).get("form")
    maker = RELEASE_FORMS.get(form)
    if not maker:
        raise DeclError(f"配布の形 {form!r} の雛形が無い（雛形のある形: {', '.join(RELEASE_FORMS)}）。"
                        "その形は /ndf:release で配る")
    return maker(a)


def plan_release_package_plugin(a) -> dict:
    """Claude Code のプラグインを配る形（package-plugin）の計画。宣言の release は
    {"form": "package-plugin", "plugin": <名前>, "runtimes": [<導入を確かめるランタイム>...]}。
    dev: bump → changelog → 説明文 → sync-check → release → verify-install（起点のブランチ）→ approval-facts
    → 提示物の欄。prod: bump → bump-others（前のタグからの差分のある他のプラグインの PATCH。
    release-steps.py changed-plugins）→ changelog → 説明文 → 消費の記録 → sync-check → release → verify-install
    （本番のブランチ）→ 後片付け。sync-check は同期とチェックの宣言があるときだけ置く。
    説明文と提示物の欄は release-steps.py notes が PR 本文の「利用者向けの変化」から組む（LLM を使わない）。"""
    rel = a.release
    plugin, runtimes = rel.get("plugin"), rel.get("runtimes")
    if not isinstance(plugin, str) or not plugin:
        raise DeclError("supervise.json: release.plugin（配るプラグインの名前）が要る")
    if not isinstance(runtimes, list) or not runtimes or not all(isinstance(r, str) for r in runtimes):
        raise DeclError("supervise.json: release.runtimes（導入を確かめるランタイムの並び）が要る")
    v, dev = a.version, a.channel == "dev"
    if not dev and not a.production_branch:
        raise DeclError(f"本番の配布に要る本番のブランチが無い（--production-branch か .ndf/{WORKTREE_DECL} の "
                        "production_branch）")
    rts = ",".join(runtimes)
    base = re.sub(r"-.*$", "", v)  # 開発版の本番承認の提示物は正式版の番号で作る
    # --prs-from-queue なら、queue が先行の計画の Pull Request の番号で QUEUE_PRS を置き換える
    prs = " ".join([*map(str, a.prs), *([QUEUE_PRS] if getattr(a, "prs_from_queue", False) else [])])
    repo = a.repo or (a.worktree.split("/.worktrees/")[0] if "/.worktrees/" in a.worktree else None)
    sync = bool(getattr(a, "sync_checks", None))
    after_notes = "sync" if sync else "release"
    run_ids = ["bump", *([] if dev else ["bump-others"]), "changelog", "notes"] + ([] if dev else ["snapshot"]) + (
        ["sync"] if sync else []) + [
        "release", "verify"] + (["facts", "explain"] if dev else [])
    # 説明文は PR 本文の「利用者向けの変化」から機械で組む（節が無い PR は題名）
    notes = (f"sh -c '{STEPS_PY} notes --version {v} --prs {prs} && git add -A && "
             f"(git diff --cached --quiet || git commit -q -m \"Release: {plugin} v{v}\")'")
    steps = [
        {"id": "bump", "type": "run", "stage": "配布", "cmd": f"{STEPS_PY} bump --plugin {plugin} --to {v} --base {a.base}",
         "on_fail": "judge", "next": "changelog" if dev else "bump-others"},
        *([] if dev else [{"id": "bump-others", "type": "run", "stage": "配布", "cmd": bump_others_cmd(a, plugin),
                           "on_fail": "judge", "next": "changelog"}]),
        {"id": "changelog", "type": "run", "cmd": f"{STEPS_PY} changelog --version {v} --prs {prs}",
         "on_fail": "judge", "next": "notes"},
        {"id": "notes", "type": "run", "stage": "配布", "cmd": notes, "on_fail": "judge",
         "next": after_notes if dev else "snapshot"},
    ]
    if not dev:
        steps.append({"id": "snapshot", "type": "run", "stage": "配布", "timeout": 900,
                      "cmd": f"sh -c '{STEPS_PY} run --root . --stage production --version {v} && git add -A && "
                             f"(git diff --cached --quiet || git commit -q -m \"Release: {plugin} v{v} のトークン消費の記録\")'",
                      "on_fail": "judge", "next": after_notes})
    ref = a.base if dev else a.production_branch
    if sync:
        steps.append({"id": "sync", "type": "run", "preset": "sync-check", "on_fail": "judge", "next": "release"})
    steps += [
        {"id": "release", "type": "run", "stage": "配布", "timeout": 2400 if dev else 3000,
         "cmd": f"{STEPS_PY} release --version {v} --channel {a.channel}", "on_fail": "judge", "next": "verify",
         # 配布の PR（release/v<版> → 起点）と、本番では続く 起点 → 本番 の PR のチェックを調べる
         "probe": {"cmd": f"{MERGED_PY} probe --head {{branch}} --head {{base}} --act"}},
        {"id": "verify", "type": "run", "stage": "配布" if dev else "リリース後テスト", "timeout": 1500, "cwd": repo,
         "cmd": f"sh -c 'git pull -q --ff-only origin {a.base} && {VERIFY_PY} verify-install --ref {ref} "
                f"--expect {v} --runtimes {rts}'",
         "on_fail": "judge", "next": "facts" if dev else "cleanup"},
    ]
    if not dev:
        # 後片付け: 配布の PR（head が release/v{v} で始まる。開発版の release/v{v}-dev.N も含む。宛先は起点のブランチ）と
        # ミッションの PR（--prs）のブランチと作業ツリー
        run_ids.append("cleanup")
        steps.append(
            {"id": "cleanup", "type": "run", "stage": "後片付け", "cwd": repo,
             "cmd": f"sh -c '{MERGED_PY} cleanup $(gh pr list --state merged --limit 30 --json number,headRefName "
                    f"--jq \".[] | select(.headRefName | startswith(\\\"release/v{v}\\\")) | .number\") {prs}'",
             "on_fail": "judge", "next": "end"})
    approval = f"issues/approval-{plugin}-v{base}.md"
    mvv = getattr(a, "mvv", None)
    if dev:
        prev = f" --prev-tag {a.prev_tag}" if a.prev_tag else ""
        facts = {"id": "facts", "type": "run", "cwd": repo,
                 "cmd": f"{STEPS_PY} approval-facts --version {base} --prs {prs}{prev}",
                 "presentation_to": approval, "on_fail": "judge", "gate_next": "explain", "next": "explain"}
        if mvv:
            facts["gate_as_ok"] = True  # 関門 2 は本番の計画の先頭で MVV が判定する
        steps += [
            facts,
            {"id": "explain", "type": "run", "cwd": repo,
             "cmd": f"{STEPS_PY} notes --version {v} --prs {prs} --approval {approval} "
                    f"--verified {rts} --ref {a.base}",
             "on_fail": "judge", "next": "end"},
        ]
    steps += [
        {"id": "judge", "type": "judge", "inputs": run_ids,
         "question": "落ちたステップを直す（fix）か、同じステップをもう一度（retry）か、止める（stop）か。retry なら decision に"
                     "落ちたステップの id を返す",
         "choices": ["fix", *run_ids, "stop"]},
        {"id": "fix", "type": "work", "kind": "修正", "inputs": run_ids,
         "prompt": "落ちたステップの出力を読み、原因を直してコミットする（push しない）。直したら次は落ちたステップからやり直す。",
         "next": after_notes},
    ]
    if mvv and not dev:
        # 関門 2 を MVV で判定する。関門（10）なら報告は 結果: 関門 で止まり、conductor が承認を取ってから
        # run <計画> --from bump で続ける
        material = f"{repo}/{approval}" if repo else approval
        steps.insert(0, {"id": "mvv", "type": "run", "timeout": 900,
                         "cmd": f"{MVV_PY} check --mission {shlex.quote(str(Path(mvv).resolve()))} --gate release "
                                f"--material {shlex.quote(material)} --pr {prs} --mode {a.mode}"
                                + (f" --root {shlex.quote(repo)}" if repo else ""),
                         "next": "bump", "gate_next": "end"})
    rule = RULE_RELEASE_DEV if dev else RULE_RELEASE_PROD_MVV if mvv else RULE_RELEASE_PROD
    plan = {
        "フェーズ": f"配布（{'開発版' if dev else '本番'}）", "課題": a.issue, "モード": a.mode, "作業場所": a.worktree,
        "branch": a.branch or f"release/v{v}", "起点": f"origin/{a.base}",
        "規則": rule, "上限": 20, "steps": steps,
    }
    with_decls(plan, a)
    if repo:
        plan["リポジトリ"] = repo
        plan["記録"] = str(HERE / "projects-sync.sh")
    return plan


# changed-plugins の結果 JSON（最後の行）の items を「名前 版」の行にする
PICK_BUMPS = ('import json,sys; [print(i[\\"name\\"], i[\\"to\\"]) '
              'for i in json.loads(sys.stdin.read().strip().splitlines()[-1])[\\"items\\"]]')


def bump_others_cmd(a, plugin: str) -> str:
    """本番のリリースプランの bump-others（不足 c）。前のタグからの差分のある、宣言の release.plugin 以外の
    プラグインを changed-plugins で列挙し、1 つずつ PATCH を 1 つ上げる。どれかが落ちたら止まる。"""
    since = f" --since {shlex.quote(a.prev_tag)}" if getattr(a, "prev_tag", None) else ""
    return (f'out=$({STEPS_PY} changed-plugins --plugin {plugin}{since}) || {{ printf "%s\\n" "$out"; exit 1; }}; '
            f'printf "%s\\n" "$out"; printf "%s\\n" "$out" | python3 -c "{PICK_BUMPS}" | while read -r n to; do '
            f'{STEPS_PY} bump --plugin "$n" --to "$to" --base {a.base} || exit 1; done')


# 配布の形（release の form-<形>.md）ごとの雛形。無い形は /ndf:release で配る
RELEASE_FORMS = {"package-plugin": plan_release_package_plugin}
