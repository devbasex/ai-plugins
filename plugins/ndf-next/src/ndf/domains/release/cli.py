"""副命令 `ndf release-steps` のエントリポイント。引数と結果 JSON だけを持ち、処理は段階ごとのモジュールが持つ。

| 副命令 | 打つもの | 段階のモジュール |
| --- | --- | --- |
| `assemble` | workflow・スプリントのプラン | `assemble`（`github-flow` は本番の PR の本文の承認資料の節を書く。ほかの行はリリースブランチと PR を作る） |
| `materials` | 会話のセッション・workflow | `assemble.collect`（承認資料を組んで出す。本文を書かない） |
| `present` | 会話のセッション | `trail.present`（承認資料の提示。承認ゲート） |
| `answer` | 会話のセッション・承認ゲートの待ち | `trail.answer`（答えを証跡に書き、届いたかを確かめる）。`--route github-merge` は `trail.answer_merged`（人のマージを承認の証跡にする） |
| `approval-check` | workflow | `trail.check`（証跡の判定を commit status へ） |
| `merge` | 本番のプラン・workflow | `land`（証跡の後に、マージの方式で照合付きで入れる。`github-flow` でない行は同期の PR も。止まったら次の手を値の項目 `moves` に出す） |
| `confirm` | 本番のプラン | `confirm`（CD のタグと GitHub Release を待ち、作った版を記録する。`github-flow` は版上げの PR のマージも待つ） |
| `guard` | `merge`・`check`・`close` のプラン | `release_plan.is_production_pr`（本番の PR をマージせずにデプロイの工程へ渡す。検証用ブランチを main へ入れない） |
| `bump` | workflow（main への push） | `bump`（`github-flow` で版上げの PR を作り・進め・置き換え、照合付きで入れる。ほかの行は何もしない） |
| `pr-check` | workflow（main 宛ての PR） | `pr_check.check`（版数・上げ幅のラベル・検証用ブランチ・レビューの単位の検査） |
| `verify-ref` | workflow（main 宛ての PR の出来事） | `verify_ref.build`（検証用ブランチを組み直し、検証環境へ配る。検証の行が無ければ何もしない） |
| `record` | 本番のプラン・workflow | `record.write`（含めた PR の各々へリリース記録を書く。`confirm` の後） |
| `verification-record` | 会話のセッション | `record.write_verification`（リリース後テストの記録を、リリース記録と同じ PR へ書く） |
| `records` | 会話のセッション | `record.read`（PR のリリース記録とリリース後テストの記録を読む） |
| `publish` | workflow | `tagging`（タグと GitHub Release） |
| `sync-base` | 会話のセッション | `verify.sync_base`（メインディレクトリのベースブランチをそろえる） |
| `config-snapshot`・`config-check` | 会話のセッション | `verify.snapshot`（利用者の設定の控えと比べ） |
| `cleanup` | 本番のプラン・会話のセッション | `cleanup.sweep`（マージしたブランチの worktree を外す。`--pr` なら PR の head のブランチ） |
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ndf import cli
from ndf.adapters import ci
from ndf.adapters import git as git_mod
from ndf.adapters import github as github_mod
from ndf.adapters.github import GitHub
from ndf.contracts import result
from ndf.domains.release import (
    approval_decide,
    assemble,
    bump,
    bump_decide,
    cleanup,
    confirm,
    land,
    materials_decide,
    merge_decide,
    pr_check,
    record,
    record_decide,
    release_plan,
    sync_decide,
    tagging,
    trail,
    user_config_decide,
    verify,
    verify_ref,
    version_decide,
    version_store,
)
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)
# 次の手を差し戻しだけにする止まった理由（同じ head で打ち直しても通らない。#3572 の決定 3）と、キューから外れた止まった理由
QUEUE_REMOVED = "queue_removed"
REJECT_ONLY = frozenset({"refused", QUEUE_REMOVED, "behind"})
# 結果の `outputs` はスプリントの文脈の閉じた語彙（版・PR・ブランチなど）だけを持つ。ほかの値（承認・次の手・SHA など）は
# 種類 `VALUE_KIND` の項目（`name` と `value`）で返す（#3572）
VALUE_KIND = "value"


def _values(**found: Any) -> list[dict[str, Any]]:
    """値の項目の並び（None の値は載せない）。"""
    return [{"kind": VALUE_KIND, "name": k, "value": v} for k, v in found.items() if v is not None]


def _hub(args: argparse.Namespace) -> GitHub:
    repo = args.repo if args.repo else git_mod.Git(Path(args.root)).remote_repo()
    return GitHub(repo, github_mod.client())


def _production_of(args: argparse.Namespace) -> str:
    """`--production` があればそれ、無ければ宣言（`.ndf/worktree.json` の `production_branch`）の本番ブランチ。"""
    return args.production if args.production else decl_mod.production_branch(Path(args.root))


def _target(args: argparse.Namespace) -> assemble.Target:
    return assemble.Target(_production_of(args), args.pr, args.plugin, args.channel, args.level, args.base)


def _store_of(root: Path) -> version_store.VersionStore:
    """取り込み方の行と宣言からバージョンの置き場を選ぶ（`version_store.of`）。"""
    return version_store.of(root, decl_mod.load(root), decl_mod.flow_of(root))


def cmd_assemble(args: argparse.Namespace) -> dict:
    built = assemble.assemble(Path(args.root), _hub(args), _target(args))
    items = materials_decide.items(built.facts) + _values(url=built.url, head=built.facts.head)
    if built.branch is not None:
        outputs = {"pr": built.pr, "branch": built.branch, "version": built.version}
        return result.result(TOOL, result.OK, f"{built.branch} の PR #{built.pr} を作った: {built.url}", items, outputs=outputs)
    detail = f"#{built.pr} の本文の承認資料を {built.facts.head[:12]} で書いた（予定の版 v{built.version}）: {built.url}"
    return result.result(TOOL, result.OK, detail, items, outputs={"pr": built.pr, "version": built.version})


def cmd_materials(args: argparse.Namespace) -> dict:
    facts = assemble.collect(Path(args.root), _hub(args), _target(args))
    text = materials_decide.render(facts)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return result.result(TOOL, result.OK, "承認資料を組んだ", materials_decide.items(facts), presentation_path=args.out)


def cmd_present(args: argparse.Namespace) -> dict:
    hub = _hub(args)
    material = Path(args.material).read_text(encoding="utf-8") if args.material else hub.pull(args.pr).body
    head = trail.present(hub, args.pr, material)
    routes = {
        answer: result.command(TOOL, "answer", "--pr", str(args.pr), "--answer", answer, "--presented", head, ask=("--by",))
        for answer in approval_decide.ANSWERS
    }
    return result.result(TOOL, result.GATE, f"#{args.pr}（{head[:12]}）の承認資料を提示した。答えを待つ", _values(head=head), routes=routes)


def _answer_merged(args: argparse.Namespace) -> dict:
    """経路 `github-merge` の答え。マージの事実から証跡を書き、出した版を outputs に出す。"""
    try:
        judgment, facts = trail.answer_merged(_hub(args), args.pr, _production_of(args))
    except trail.NotMerged as exc:
        return result.stopped(TOOL, "not_applicable", str(exc))
    version = bump.planned_for(Path(args.root), _hub(args), args.pr, _production_of(args))
    items = _values(state=judgment.state, by=facts.by, head=facts.head)
    detail = f"#{args.pr} の人のマージを承認の証跡にした: {judgment.detail}"
    return result.result(TOOL, result.OK, detail, items, outputs={"version": version} if version else None)


def cmd_answer(args: argparse.Namespace) -> dict:
    if args.route == merge_decide.MERGE_ROUTE:
        return _answer_merged(args)
    missing = [k for k in ("answer", "presented", "by") if getattr(args, k) is None]
    if missing:
        return result.stopped(TOOL, "invalid", f"--route gate には --{'・--'.join(missing)} が要る")
    judgment = trail.answer(_hub(args), args.pr, args.answer, args.by, args.presented)
    return result.result(TOOL, result.OK, f"#{args.pr} の答えが証跡に届いた: {judgment.detail}", _values(state=judgment.state))


def cmd_approval_check(args: argparse.Namespace) -> dict:
    hub = _hub(args)
    pull = hub.pull(args.pr)
    production = _production_of(args)
    if not release_plan.is_production_pr(pull.base_ref, production):
        return result.stopped(TOOL, "not_applicable", f"#{args.pr} は本番ブランチ {production} 宛てでない（{pull.base_ref}）")
    got = trail.check(hub, args.pr)
    return result.result(
        TOOL, result.OK, f"#{args.pr} の承認の判定: {got.judgment.detail}", _values(state=got.judgment.state, head=got.pull.head_sha)
    )


def _same_target(args: argparse.Namespace) -> list[str]:
    """元の呼び出しの対象（`--root`・`--repo`・`--production`）。次の手が別のリポジトリ・本番ブランチを対象にしないように引き継ぐ。"""
    found = ["--root", str(args.root)]
    if args.repo:
        found += ["--repo", args.repo]
    if args.production:
        found += ["--production", args.production]
    return found


def _moves(args: argparse.Namespace, kind: str, released: bool) -> dict[str, Any]:
    """止まったマージの次の手（打ち直し・差し戻し）と既定の手（#3572 の決定 3）。

    本番へのマージが済んで記録だけが止まった（`released`）ときは、本番の PR を差し戻してはいけないため、
    マージ済みからの再開（打ち直し）だけを返す。
    """
    pr = str(args.pr)
    target = _same_target(args)
    rerun = result.command(TOOL, "merge", "--pr", pr, "--budget", str(args.budget), *target)
    if released:
        return {"moves": {merge_decide.RERUN: rerun}, "suggested": merge_decide.RERUN}
    answer_args = ("answer", "--pr", pr, "--answer", approval_decide.REJECTED, *target)
    reject = result.command(TOOL, *answer_args, ask=("--presented", "--by"))
    if kind in REJECT_ONLY:
        # 同じ head で打ち直しても通らない（head が動いた・キューから外れた・先端を含まない）。差し戻しだけを出す
        return {"moves": {merge_decide.REJECT: reject}, "suggested": merge_decide.REJECT}
    return {"moves": {merge_decide.RERUN: rerun, merge_decide.REJECT: reject}, "suggested": merge_decide.suggested(kind)}


def _planned(root: Path, hub: GitHub, number: int, production: str) -> str | None:
    """本番の PR の予定の版（置き場を選べなければ None。確定の版は confirm が読む）。"""
    try:
        store = _store_of(root)
    except version_store.VersionStoreError:  # noqa: NDF401 ファイル: 予定の版は表示だけで、置き場の誤りは bump と confirm が止める
        return None
    return bump.planned_for(root, hub, number, production, store)


def cmd_merge(args: argparse.Namespace) -> dict:
    root, hub, production = Path(args.root), _hub(args), _production_of(args)
    flow = decl_mod.flow_of(root)
    base = decl_mod.base_branch(root, production) if flow != decl_mod.GITHUB_FLOW else None
    lander = land.Lander(
        hub, production, args.budget, admin=decl_mod.production_admin(root), flow=flow, method=decl_mod.merge_method_of(root), base=base
    )
    done = lander.land(args.pr)
    return _merge_result(args, done, _planned(root, hub, args.pr, production))


def _check_items(failed: Sequence[ci.Check]) -> list[dict[str, Any]]:
    """落ちたチェックの項目（種類 `check`・名前・結論）。"""
    return [{"kind": "check", "name": c.name, "outcome": c.conclusion} for c in failed]


def _bump_outputs(made: bump.Bumped) -> dict[str, Any] | None:
    """版上げの結果の outputs（版と版上げの PR のうち値のあるもの。どちらも無ければ None）。"""
    outputs = {k: v for k, v in (("version", made.version), ("pr", made.pr)) if v is not None}
    return outputs if outputs else None


def _merge_result(args: argparse.Namespace, done: land.Landed, version: str | None = None) -> dict:
    """マージの結果 `done` と予定の版を結果 JSON（items・outputs・止まった理由・記録の失敗の説明・止まったときの次の手）にする。"""
    outputs = {"version": version} if version is not None else None
    approval = done.approval.as_json() if done.approval is not None else None
    items = _check_items(done.failed)
    if done.kind == QUEUE_REMOVED:
        items.append({"kind": "queue", "name": "removed", "detail": done.detail})
    if done.mismatch is not None:
        items.append({"kind": "method", "name": done.mismatch[0], "detail": done.mismatch[1]})
    items += _values(
        production_sha=done.production_sha,
        admin=done.admin,
        admin_record=done.admin_record,
        approval=approval,
        merge_record=done.merge_record,
        method=done.method,
        sync_pr=done.sync_pr,
    )
    detail = done.detail
    if done.record_error is not None:  # 本番は出ている。記録だけを打ち直しで書く（#3506 の決定 7）
        detail = f"{detail}。記録を書けなかった（本番は出ている）: {done.record_error}"
    if done.merged and done.kind is None and done.record_error is None:
        return result.result(TOOL, result.OK, detail, items, outputs=outputs)
    kind = done.kind if done.kind else "unknown"
    # 次の手は打つ側が選ぶ（既定の手を next にすると、打つ側が問わずに差し戻す）。本番へ出たかは `production_sha` で分ける
    if not done.merged:
        items += _values(**_moves(args, kind, released=done.production_sha is not None))
    return result.stopped(TOOL, kind, detail, items=items, outputs=outputs)


def cmd_guard(args: argparse.Namespace) -> dict:
    verify_ref_name = decl_mod.optional_verify_ref(Path(args.root))
    if verify_ref_name is not None and verify_ref_name in args.branch:
        detail = f"{verify_ref_name}: 検証用ブランチは main へ戻さない（マージしない）"
        return result.stopped(TOOL, "refused", detail)
    try:
        production = _production_of(args)
    except decl_mod.DeclarationError as exc:  # 本番ブランチを宣言しないリポジトリの merge のプランを止めない
        return result.result(TOOL, result.OK, "本番ブランチが決まらないので本番の PR かを確かめない", warnings=[str(exc)])
    found = _hub(args).pulls_by_head(args.branch)
    opened = [p for b in args.branch for p in found[b] if p.state == "open"]
    if not opened:
        return result.result(TOOL, result.OK, f"{', '.join(args.branch)} の開いた PR が無い（本番の PR でない）")
    hits = [p for p in opened if release_plan.is_production_pr(p.base_ref, production)]
    if hits:
        listed = ", ".join(f"#{p.number}" for p in hits)
        detail = f"{listed} は本番ブランチ {production} 宛ての本番の PR で、このプランではマージしない（デプロイの工程で流す）"
        return result.stopped(TOOL, release_plan.guard_cause(), detail)
    return result.result(TOOL, result.OK, f"{', '.join(f'#{p.number}' for p in opened)} は本番の PR でない")


def _bump_stopped(hub: GitHub, made: bump.Bumped) -> dict:
    """止まった版上げの結果。版上げの PR があれば理由をコメントで残す。"""
    landed = made.landed
    kind = landed.kind if landed is not None and landed.kind else "unknown"
    detail = landed.detail if landed is not None else "版上げが止まった"
    if made.pr is not None:
        hub.rest("POST", f"/repos/{hub.repo}/issues/{made.pr}/comments", {"body": f"版上げが止まった（{kind}）: {detail}"})
    items = _check_items(landed.failed if landed else ())
    items += _values(state=made.state, level=made.level, pr_url=f"https://github.com/{hub.repo}/pull/{made.pr}" if made.pr else None)
    return result.stopped(TOOL, kind, detail, items=items, outputs=_bump_outputs(made))


def cmd_bump(args: argparse.Namespace) -> dict:
    root, hub, production = Path(args.root), _hub(args), _production_of(args)
    flow = decl_mod.flow_of(root)
    if flow != decl_mod.GITHUB_FLOW:
        detail = f"取り込み方 {flow} ではリリースブランチがバージョンを上げる（CD のバージョン上げをしない）"
        return result.result(TOOL, result.OK, detail, _values(state=bump_decide.NOT_APPLICABLE))
    store = _store_of(root)
    lander = land.Lander(
        hub, production, args.budget, admin=decl_mod.production_admin(root), flow=flow, method=decl_mod.merge_method_of(root)
    )
    today = datetime.now(timezone.utc).date().isoformat()
    made = bump.Bumper(root, hub, lander, production, today, flow, store).bump()
    if made.state not in bump_decide.bump_states():
        return _bump_stopped(hub, made)
    production_sha = made.landed.production_sha if made.landed else None
    items = _values(state=made.state, level=made.level, production_sha=production_sha, revision=made.revision, store=store.kind)
    detail = f"版上げ: {made.state}（版 {made.version or '無し'}・版上げの PR {f'#{made.pr}' if made.pr else '無し'}）"
    return result.result(TOOL, result.OK, detail, items, outputs=_bump_outputs(made))


def _unit_of(root: Path, store: version_store.VersionStore) -> confirm.UnitOf:
    """タグを確かめる単位の名前（`version_store.unit_at`。publish がタグを作る対象と同じ規則）。"""

    def unit_of(revision: str, version: str) -> str:
        return version_store.unit_at(store, git_mod.Git(root), revision, version).name

    return unit_of


def cmd_confirm(args: argparse.Namespace) -> dict:
    root, hub, production = Path(args.root), _hub(args), _production_of(args)
    flow, store = decl_mod.flow_of(root), _store_of(root)
    watch = confirm.Watch(args.budget, time.monotonic, time.sleep)
    got = confirm.confirm(hub, git_mod.Git(root), args.pr, production, _unit_of(root, store), watch, flow, store)
    outputs = {"version": got.version} if got.version is not None else None
    values = _values(tag=got.tag, bump_pr=got.bump_pr, bump_revision=got.bump_revision)
    if got.kind is None:
        return result.result(TOOL, result.OK, got.detail, values, outputs=outputs)
    # 次の手は CD を打ち直した後の confirm の打ち直しの 1 つ（routes は答えの語彙を持つため、merge と同じく値の項目 moves で返す）
    rerun = result.command(TOOL, "confirm", "--pr", str(args.pr), "--budget", str(args.budget), *_same_target(args))
    items = [{"kind": "cd", "name": got.missing or confirm.MISSING_BUMP, "detail": got.detail}, *values]
    items += _values(moves={merge_decide.RERUN: rerun}, suggested=merge_decide.RERUN)
    return result.stopped(TOOL, got.kind, got.detail, items=items, outputs=outputs)


def cmd_pr_check(args: argparse.Namespace) -> dict:
    root = Path(args.root)
    found = pr_check.check(root, _hub(args), git_mod.Git(root), args.pr)
    if found is None:
        return result.result(TOOL, result.OK, f"ブランチ戦略が {decl_mod.GITHUB_FLOW} でないので #{args.pr} を検査しない")
    if found:
        return result.stopped(TOOL, "check_failed", f"#{args.pr} の検査で {len(found)} 件の破れがある", items=found)
    return result.result(TOOL, result.OK, f"#{args.pr} の検査に破れは無い")


def cmd_verify_ref(args: argparse.Namespace) -> dict:
    root = Path(args.root)
    verify = decl_mod.optional_verify(root)
    if verify is None:
        # 検証環境を使わないリポジトリの workflow を落とさない（結果は ok、状態は値の項目 state の not_applicable）
        return result.result(TOOL, result.OK, "検証の行が無いので検証環境を使わない", _values(state=bump_decide.NOT_APPLICABLE))
    try:
        built = verify_ref.build(root, _hub(args), verify, datetime.now(timezone.utc))
    except verify_ref.VerifyConflict as exc:
        return result.stopped(TOOL, "base_conflict", str(exc), items=[{"kind": "conflict", "name": f"#{exc.number}"}])
    except verify_ref.VerifyDeployFailed as exc:
        code = str(exc.code) if exc.code is not None else "none"
        return result.stopped(TOOL, "deploy_failed", str(exc), items=[{"kind": "deploy", "name": code, "detail": str(exc)}])
    included = [{"number": n, "head": h} for n, h in built.included]
    items = _values(ref=built.ref, head=built.head, included=included, kind=built.kind)
    shown = f"{built.version}。" if built.version else ""
    detail = f"{built.ref} を {built.head[:12]} へ組み直した（{built.kind}。{shown}含めた PR {len(included)} 本）"
    return result.result(TOOL, result.OK, detail, items, outputs={"version": built.version} if built.version else None)


def cmd_record(args: argparse.Namespace) -> dict:
    made, written = record.write(_hub(args), args.pr, _production_of(args))
    count = sum(1 for w in written if w.result == record.WRITTEN)
    outputs = {"version": made.version}
    detail = f"v{made.version} のリリース記録を {count} 件書いた（含めた PR {len(written)} 本。既にあった {len(written) - count} 本）"
    return result.result(TOOL, result.OK, detail, [w.item() for w in written] + _values(included=list(made.included)), outputs=outputs)


def cmd_verification_record(args: argparse.Namespace) -> dict:
    payload = json.loads(Path(args.record).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise record.RecordError(f"{args.record} はオブジェクトの JSON でない")
    url = record.write_verification(_hub(args), args.pr, payload)
    return result.result(TOOL, result.OK, f"#{args.pr} へリリース後テストの記録を書いた: {url}", _values(url=url))


def cmd_records(args: argparse.Namespace) -> dict:
    got = record.read(_hub(args), args.pr)
    items = _values(release=got.release.as_json() if got.release else None, verifications=list(got.verifications))
    version = got.release.version if got.release else "無し"
    detail = f"#{args.pr} のリリース記録（版 {version}）とリリース後テストの記録 {len(got.verifications)} 件を読んだ"
    return result.result(TOOL, result.OK, detail, items)


def cmd_publish(args: argparse.Namespace) -> dict:
    root = Path(args.root)
    made = tagging.publish(root, _hub(args), args.revision, _store_of(root))
    created = sum(1 for m in made if m.result == tagging.CREATED)
    return result.result(TOOL, result.OK, f"タグと GitHub Release を {created} 件作った", [m.item() for m in made])


def cmd_sync_base(args: argparse.Namespace) -> dict:
    plan = verify.sync_base(Path(args.main_dir), args.base)
    if plan.action == sync_decide.STOP:
        detail = f"メインディレクトリの変更が取り込む差分と重なり、origin と中身が違う: {', '.join(plan.paths)}"
        return result.stopped(TOOL, "precondition", detail, items=[{"kind": "path", "path": p} for p in plan.paths])
    return result.result(TOOL, result.OK, f"{args.base} を origin へそろえた（{plan.action}）", _values(action=plan.action))


def cmd_config_snapshot(args: argparse.Namespace) -> dict:
    snap = verify.snapshot()
    Path(args.out).write_text(
        json.dumps({"items": snap.items, "unreadable": list(snap.unreadable)}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result.result(TOOL, result.OK, f"利用者の設定を控えた（{len(snap.items)} 件）", warnings=list(snap.unreadable))


def cmd_config_check(args: argparse.Namespace) -> dict:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))["items"]
    lines = user_config_decide.diff(before, verify.snapshot().items)
    if lines:
        return result.stopped(
            TOOL,
            "check_failed",
            f"利用者の設定ディレクトリが変わった（{len(lines)} 件）",
            items=[{"kind": "config", "change": x} for x in lines],
        )
    return result.result(TOOL, result.OK, "利用者の設定ディレクトリは変わっていない")


def cmd_cleanup(args: argparse.Namespace) -> dict:
    if not args.branch and args.pr is None:
        return result.stopped(TOOL, "invalid", "--branch か --pr のどちらかが要る")
    branches = args.branch if args.branch else [_hub(args).pull(args.pr).head_ref]
    repo_git = git_mod.Git(Path(args.root))
    swept = cleanup.sweep(repo_git.worktrees(), branches, cleanup.git_remover(repo_git))
    items = [{"kind": "worktree", "path": str(p), "result": "removed"} for p in swept.removed]
    items += [{"kind": "worktree", "path": str(p), "result": "kept", "reason": why} for p, why in swept.kept]
    return result.result(
        TOOL,
        result.OK,
        f"worktree を {len(swept.removed)} 件外した（残した {len(swept.kept)} 件）",
        items,
        warnings=[f"{p}: {w}" for p, w in swept.kept],
    )


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default=".", help="リポジトリの根")
    p.add_argument("--repo", help="<所有者>/<リポジトリ>。省けば origin")


def _target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, help="本番の PR（取り込み方 github-flow で要る）")
    _production(p)
    p.add_argument("--plugin", help="予定の版を出す単位（省けば置き場の順の先頭）")
    p.add_argument("--channel", choices=version_decide.CHANNELS, help="リリースのチャネル（github-flow でない行で要る）")
    p.add_argument("--level", choices=version_decide.LEVELS, default=version_decide.LEVELS[2], help="上げ幅（github-flow でない行）")
    p.add_argument("--base", help="ベースブランチ（github-flow でない行で要る。宣言 worktree.json の base_branch）")


def _pr(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, required=True)


def _material(p: argparse.ArgumentParser) -> None:
    p.add_argument("--material", help="承認資料のファイル（省けば PR の本文）")


def _answer_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--route",
        choices=merge_decide.ROUTES,
        default=merge_decide.GATE_ROUTE,
        help="承認の経路（承認ゲート gate / 人の GitHub でのマージ github-merge。github-merge は答えた者と対象をマージから読む）",
    )
    p.add_argument("--answer", choices=approval_decide.ANSWERS, help="答え（gate で要る）")
    p.add_argument("--presented", help="提示した先端の SHA（present の値の項目 head。gate で要る）")
    p.add_argument(
        "--by", choices=trail.BY_CHOICES, help="答えた担い手（利用者 user / MVV 判定 mvv。迂回は user だけを数える。gate で要る）"
    )
    _production(p)


def _materials_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", help="承認資料を書くファイル（省けば標準出力）")


def _production(p: argparse.ArgumentParser) -> None:
    p.add_argument("--production", help="本番ブランチ（省けば宣言 worktree.json の production_branch）")


def _budget(p: argparse.ArgumentParser) -> None:
    p.add_argument("--budget", type=float, required=True, help="必須のチェックを待つ秒")


def _revision(p: argparse.ArgumentParser) -> None:
    p.add_argument("--revision", required=True)


def _verification(p: argparse.ArgumentParser) -> None:
    p.add_argument("--record", required=True, help="リリース後テストの記録の JSON（スキーマ release-verification）")


def _main_dir(p: argparse.ArgumentParser) -> None:
    p.add_argument("--main-dir", required=True)


def _base(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", required=True)


def _snapshot_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", required=True)


def _before(p: argparse.ArgumentParser) -> None:
    p.add_argument("--before", required=True)


def _branch(p: argparse.ArgumentParser) -> None:
    p.add_argument("--branch", action="append", required=True)


def _cleanup_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--branch", action="append", help="worktree を外すブランチ（省けば --pr の head のブランチ）")
    p.add_argument("--pr", type=int, help="head のブランチの worktree を外す PR")


# 副命令のハンドラー → 引数を足す関数（並びは足す順。どの副命令にも先に `_common` を足す）
ARGS: dict[Callable[[argparse.Namespace], dict], tuple[Callable[[argparse.ArgumentParser], None], ...]] = {
    cmd_assemble: (_target_args,),
    cmd_materials: (_target_args, _materials_out),
    cmd_present: (_pr, _material),
    cmd_answer: (_pr, _answer_args),
    cmd_approval_check: (_pr, _production),
    cmd_merge: (_pr, _production, _budget),
    cmd_confirm: (_pr, _production, _budget),
    cmd_guard: (_branch, _production),
    cmd_bump: (_production, _budget),
    cmd_pr_check: (_pr,),
    cmd_verify_ref: (),
    cmd_record: (_pr, _production),
    cmd_verification_record: (_pr, _verification),
    cmd_records: (_pr,),
    cmd_publish: (_revision,),
    cmd_sync_base: (_main_dir, _base),
    cmd_config_snapshot: (_snapshot_out,),
    cmd_config_check: (_before,),
    cmd_cleanup: (_cleanup_args,),
}

# 副命令の名前 → ハンドラー。名前はハンドラーの名前（`cmd_<名前>` の `_` を `-` にしたもの）から作り、写さない
HANDLERS = {f.__name__.removeprefix("cmd_").replace("_", "-"): f for f in ARGS}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ndf release-steps", description="リリース（段階ごとのモジュール）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in HANDLERS.items():
        p = sub.add_parser(name)
        _common(p)
        for add in ARGS[handler]:
            add(p)
    return parser


# 止まった理由の種類（例外 → 種類）
_ERRORS: tuple[tuple[type[Exception], str], ...] = (
    (assemble.NothingToRelease, "not_applicable"),
    (assemble.ReleaseOpen, "refused"),
    (assemble.AssembleError, "precondition"),
    (version_store.VersionStoreError, "precondition"),
    (bump.BumpError, "precondition"),
    (bump.BumpConflict, "base_conflict"),
    (confirm.ConfirmError, "precondition"),
    (bump_decide.LevelError, "invalid"),
    (trail.TrailError, "refused"),
    (verify_ref.VerifyEscape, "refused"),
    (record.NotReleased, "not_applicable"),
    (record.RecordError, "precondition"),
    (record_decide.UnreadableRecord, "invalid"),
    (decl_mod.DeclarationError, "precondition"),
    (decl_mod.StepFailed, "check_failed"),
    (plugins_mod.PluginsError, "precondition"),
    (version_decide.VersionError, "precondition"),
    (git_mod.GitError, "precondition"),
)


def _stopped_of(exc: Exception) -> dict:
    if isinstance(exc, github_mod.GitHubError):
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        return result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)
    kind = next(k for t, k in _ERRORS if isinstance(exc, t))
    return result.stopped(TOOL, kind, str(exc))


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        out = HANDLERS[args.command](args)
    except (github_mod.GitHubError, *(t for t, _k in _ERRORS)) as exc:
        out = _stopped_of(exc)
    return result.emit(out)
