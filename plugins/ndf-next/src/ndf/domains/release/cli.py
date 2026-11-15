"""副命令 `ndf release-steps` のエントリポイント。引数と結果 JSON だけを持ち、処理は段階ごとのモジュールが持つ。

| 副命令 | 打つもの | 段階のモジュール |
| --- | --- | --- |
| `assemble` | workflow | `assemble`（ブランチの組み立てと PR） |
| `materials` | 会話のセッション・workflow | `assemble.collect`（ブランチを作らずに承認資料を組む） |
| `present` | 会話のセッション | `trail.present`（承認資料の提示。承認ゲート） |
| `answer` | 会話のセッション・承認ゲートの待ち | `trail.answer`（答えを証跡に書き、届いたかを確かめる）。`--route github-merge` は `trail.answer_merged`（人のマージを承認の証跡にする） |
| `approval-check` | workflow | `trail.check`（証跡の判定を commit status へ） |
| `merge` | 本番のプラン・workflow | `land`（証跡の後のマージと、ベースブランチへ戻す PR。止まったら次の手を値の項目 `moves` に出す） |
| `guard` | `merge` のプラン | `release_plan.is_production_pr`（本番の PR を直しのステップのあるプランで流さない） |
| `defer` | オーガナイザー・一時レーン | 本番の PR を閉じ、リリースブランチを残す（次の版への回し） |
| `prepare-next` | 本番のプラン | `prepare.prepare_next`（次の版のリリースブランチを本番ブランチから用意する） |
| `record` | workflow | `record.write`（含めた PR の各々へリリース記録を書く。本番ブランチへのマージの後） |
| `verification-record` | 会話のセッション | `record.write_verification`（リリース後テストの記録を、リリース記録と同じ PR へ書く） |
| `records` | 会話のセッション | `record.read`（PR のリリース記録とリリース後テストの記録を読む） |
| `publish` | workflow | `tagging`（タグと GitHub Release） |
| `sync-base` | 会話のセッション | `verify.sync_base`（メインディレクトリのベースブランチをそろえる） |
| `config-snapshot`・`config-check` | 会話のセッション | `verify.snapshot`（利用者の設定の控えと比べ） |
| `cleanup` | 会話のセッション | `cleanup.sweep`（マージしたブランチの worktree を外す） |
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ndf import cli
from ndf.adapters import git as git_mod
from ndf.adapters import github as github_mod
from ndf.adapters.github import GitHub
from ndf.contracts import result
from ndf.domains.release import (
    approval_decide,
    assemble,
    cleanup,
    land,
    materials_decide,
    merge_decide,
    prepare,
    record,
    record_decide,
    release_plan,
    sync_decide,
    tagging,
    trail,
    user_config_decide,
    verify,
    version_decide,
)
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)
# 結果の `outputs` はスプリントの文脈の閉じた語彙（版・PR・ブランチなど）だけを持つ。ほかの値（承認・次の手・SHA など）は
# 種類 `VALUE_KIND` の項目（`name` と `value`）で返す（#3572）
VALUE_KIND = "value"


def _values(**found: Any) -> list[dict[str, Any]]:
    """値の項目の並び（None の値は載せない）。"""
    return [{"kind": VALUE_KIND, "name": k, "value": v} for k, v in found.items() if v is not None]


def _hub(args: argparse.Namespace) -> GitHub:
    repo = args.repo if args.repo else git_mod.Git(Path(args.root)).remote_repo()
    return GitHub(repo, github_mod.client())


def _version_of(ref: str) -> str | None:
    """リリースブランチの名前の版（リリースブランチでなければ None）。"""
    return version_decide.version_of_branch(ref) if ref.startswith(version_decide.BRANCH_PREFIX) else None


def _production_of(args: argparse.Namespace) -> str:
    """`--production` があればそれ、無ければ宣言（`.ndf/worktree.json` の `production_branch`）の本番ブランチ。"""
    return args.production if args.production else decl_mod.production_branch(Path(args.root))


def _target(args: argparse.Namespace) -> assemble.Target:
    return assemble.Target(args.channel, args.level, args.base, _production_of(args), args.plugin, args.source)


def cmd_assemble(args: argparse.Namespace) -> dict:
    built = assemble.assemble(Path(args.root), _hub(args), _target(args))
    outputs = {"pr": built.pr, "branch": built.branch, "version": built.version}
    items = materials_decide.items(built.facts) + _values(url=built.url, reassembled=built.reassembled, renamed_from=built.renamed_from)
    verb = "の承認資料を新しい先端で書き直した" if built.reassembled else "を作った"
    return result.result(TOOL, result.OK, f"{built.branch} の PR #{built.pr} {verb}: {built.url}", items, outputs=outputs)


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


def cmd_answer(args: argparse.Namespace) -> dict:
    if args.route == merge_decide.MERGE_ROUTE:
        try:
            judgment, facts = trail.answer_merged(_hub(args), args.pr, _production_of(args))
        except trail.NotMerged as exc:
            return result.stopped(TOOL, "not_applicable", str(exc))
        version = _version_of(facts.head_ref)
        items = _values(state=judgment.state, by=facts.by, head=facts.head)
        detail = f"#{args.pr} の人のマージを承認の証跡にした: {judgment.detail}"
        return result.result(TOOL, result.OK, detail, items, outputs={"version": version} if version else None)
    missing = [k for k in ("answer", "presented", "by") if getattr(args, k) is None]
    if missing:
        return result.stopped(TOOL, "invalid", f"--route gate には --{'・--'.join(missing)} が要る")
    judgment = trail.answer(_hub(args), args.pr, args.answer, args.by, args.presented)
    return result.result(TOOL, result.OK, f"#{args.pr} の答えが証跡に届いた: {judgment.detail}", _values(state=judgment.state))


def cmd_approval_check(args: argparse.Namespace) -> dict:
    hub = _hub(args)
    pull = hub.pull(args.pr)
    production = _production_of(args)
    if pull.base_ref != production:
        return result.stopped(TOOL, "not_applicable", f"#{args.pr} は本番ブランチ {production} 宛てでない（{pull.base_ref}）")
    got = trail.check(hub, args.pr)
    return result.result(
        TOOL, result.OK, f"#{args.pr} の承認の判定: {got.judgment.detail}", _values(state=got.judgment.state, head=got.pull.head_sha)
    )


def _moves(args: argparse.Namespace, kind: str) -> dict[str, Any]:
    """止まったマージの次の手（打ち直し・差し戻し・次の版への回し）と既定の手（#3572 の決定 3）。"""
    pr = str(args.pr)
    rerun = ["merge", "--pr", pr, "--base", args.base, "--budget", str(args.budget)]
    if args.production:
        rerun += ["--production", args.production]
    moves = {
        merge_decide.RERUN: result.command(TOOL, *rerun),
        merge_decide.REJECT: result.command(TOOL, "answer", "--pr", pr, "--answer", approval_decide.REJECTED, ask=("--presented", "--by")),
        merge_decide.DEFER: result.command(TOOL, "defer", "--pr", pr),
    }
    return {"moves": moves, "suggested": merge_decide.suggested(kind)}


def cmd_merge(args: argparse.Namespace) -> dict:
    admin = decl_mod.production_admin(Path(args.root))
    done = land.Lander(_hub(args), args.base, _production_of(args), args.budget, admin=admin).land(args.pr)
    outputs = {"version": done.version} if done.version is not None else None
    approval = done.approval.as_json() if done.approval is not None else None
    items = [{"kind": "check", "name": c.name, "outcome": c.conclusion} for c in done.failed]
    items += _values(
        sync_pr=done.sync_pr,
        production_sha=done.production_sha,
        admin=done.admin,
        admin_record=done.admin_record,
        approval=approval,
        merge_record=done.merge_record,
    )
    detail = done.detail
    if done.record_error is not None:  # 本番は出ている。記録だけを打ち直しで書く（#3506 の決定 7）
        detail = f"{detail}。記録を書けなかった（本番は出ている）: {done.record_error}"
    if done.merged and done.kind is None and done.record_error is None:
        return result.result(TOOL, result.OK, detail, items, outputs=outputs)
    kind = done.kind if done.kind else "unknown"
    if not done.merged:  # 次の手は打つ側が選ぶ（既定の手を next にすると、打つ側が問わずに本番の PR を閉じる）
        items += _values(**_moves(args, kind))
    return result.stopped(TOOL, kind, detail, items=items, outputs=outputs)


def cmd_guard(args: argparse.Namespace) -> dict:
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
        detail = f"{listed} は本番ブランチ {production} 宛ての本番の PR で、直しのステップのある merge のプランで流さない（release のプランで流す）"
        return result.stopped(TOOL, release_plan.guard_cause(), detail)
    return result.result(TOOL, result.OK, f"{', '.join(f'#{p.number}' for p in opened)} は本番の PR でない")


def cmd_defer(args: argparse.Namespace) -> dict:
    hub = _hub(args)
    production = _production_of(args)
    pull = hub.pull(args.pr)
    if pull.state != "open" or not release_plan.is_production_pr(pull.base_ref, production):
        return result.stopped(TOOL, "precondition", f"#{args.pr} は開いた本番の PR でない（{pull.state}・{pull.base_ref}）")
    hub.rest("PATCH", f"/repos/{hub.repo}/pulls/{args.pr}", {"state": "closed"})
    detail = f"#{args.pr} を閉じ、{pull.head_ref} を残した（直しと後のスプリントを入れてから組み直す）"
    return result.result(TOOL, result.OK, detail, _values(closed=args.pr, branch=pull.head_ref))


def cmd_prepare_next(args: argparse.Namespace) -> dict:
    made = prepare.prepare_next(_hub(args), args.pr, _production_of(args))
    verb = "を用意した" if made.created else "は既にある"
    # 版とブランチは出した版のものではないため outputs（スプリントの文脈）へ書かない
    items = _values(branch=made.branch, version=made.version, created=made.created)
    return result.result(TOOL, result.OK, f"次の版のリリースブランチ {made.branch} {verb}", items)


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
    made = tagging.publish(Path(args.root), _hub(args), args.revision)
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
    repo_git = git_mod.Git(Path(args.root))
    swept = cleanup.sweep(repo_git.worktrees(), args.branch, cleanup.git_remover(repo_git))
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


def _branches(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", required=True, help="ベースブランチ（宣言 worktree.json の base_branch）")
    _production(p)


def _target_args(p: argparse.ArgumentParser) -> None:
    _branches(p)
    p.add_argument("--channel", choices=version_decide.CHANNELS, required=True)
    p.add_argument("--level", choices=version_decide.LEVELS, default="patch")
    p.add_argument("--plugin", help="ブランチの名前に版を使うプラグイン（省けば登録の順の先頭）")
    p.add_argument(
        "--source",
        choices=assemble.SOURCES,
        default=assemble.BASE_SOURCE,
        help="範囲の先端（ベースブランチ base / 常設のリリースブランチ release）",
    )


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


# 副命令のハンドラー → 引数を足す関数（並びは足す順。どの副命令にも先に `_common` を足す）
ARGS: dict[Callable[[argparse.Namespace], dict], tuple[Callable[[argparse.ArgumentParser], None], ...]] = {
    cmd_assemble: (_target_args,),
    cmd_materials: (_target_args, _materials_out),
    cmd_present: (_pr, _material),
    cmd_answer: (_pr, _answer_args),
    cmd_approval_check: (_pr, _production),
    cmd_merge: (_pr, _branches, _budget),
    cmd_guard: (_branch, _production),
    cmd_defer: (_pr, _production),
    cmd_prepare_next: (_pr, _production),
    cmd_record: (_pr, _production),
    cmd_verification_record: (_pr, _verification),
    cmd_records: (_pr,),
    cmd_publish: (_revision,),
    cmd_sync_base: (_main_dir, _base),
    cmd_config_snapshot: (_snapshot_out,),
    cmd_config_check: (_before,),
    cmd_cleanup: (_branch,),
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
    (assemble.AssembleError, "precondition"),
    (assemble.ReleaseOpen, "refused"),
    (prepare.PrepareError, "precondition"),
    (trail.TrailError, "refused"),
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
