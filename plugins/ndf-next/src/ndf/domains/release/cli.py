"""サブコマンド `ndf release-steps` のエントリポイント。引数と結果 JSON だけを持ち、処理は段階ごとのモジュールが持つ。

| サブコマンド | 打つもの | 段階のモジュール |
| --- | --- | --- |
| `assemble` | workflow | `assemble`（ブランチの組み立てと PR） |
| `materials` | 会話のセッション・workflow | `assemble.collect`（ブランチを作らずに承認資料を組む） |
| `present` | 会話のセッション | `trail.present`（承認資料の提示。承認ゲート） |
| `answer` | 会話のセッション | `trail.answer`（回答を証跡に書き、届いたかを確かめる） |
| `approval-check` | workflow | `trail.check`（証跡の判定を commit status へ） |
| `merge` | workflow | `land`（証跡の後のマージと、ベースブランチへ戻す PR） |
| `record` | workflow | `record.write`（含めた PR の各々へリリース記録を書く。本番ブランチへのマージの後） |
| `verification-record` | 会話のセッション | `record.write_verification`（リリース後テストの記録を、リリース記録と同じ PR へ書く） |
| `records` | 会話のセッション | `record.read`（PR のリリース記録とリリース後テストの記録を読む） |
| `publish` | workflow | `tagging`（タグと GitHub Release） |
| `sync-base` | 会話のセッション | `verify.sync_base`（メインディレクトリのベースブランチをそろえる） |
| `config-snapshot`・`config-check` | 会話のセッション | `verify.snapshot`（利用者の設定のスナップショットと比べ） |
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
    record,
    record_decide,
    sync_decide,
    tagging,
    trail,
    user_config_decide,
    verify,
    version_decide,
)
from ndf.domains.release import ndf_settings as decl_mod
from ndf.domains.release import plugins as plugins_mod

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)


def _hub(args: argparse.Namespace) -> GitHub:
    repo = args.repo if args.repo else git_mod.Git(Path(args.root)).remote_repo()
    return GitHub(repo, github_mod.client())


def _target(args: argparse.Namespace) -> assemble.Target:
    return assemble.Target(args.channel, args.level, args.base, args.production, args.plugin)


def cmd_assemble(args: argparse.Namespace) -> dict:
    built = assemble.assemble(Path(args.root), _hub(args), _target(args))
    outputs = {"pr": built.pr, "url": built.url, "branch": built.branch, "version": built.version}
    return result.result(
        TOOL, result.OK, f"{built.branch} の PR #{built.pr} を作った: {built.url}", materials_decide.items(built.facts), outputs=outputs
    )


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
    return result.result(
        TOOL, result.GATE, f"#{args.pr}（{head[:12]}）の承認資料を提示した。回答を待つ", routes=routes, outputs={"head": head}
    )


def cmd_answer(args: argparse.Namespace) -> dict:
    judgment = trail.answer(_hub(args), args.pr, args.answer, args.by, args.presented)
    return result.result(TOOL, result.OK, f"#{args.pr} の回答が証跡に届いた: {judgment.detail}", outputs={"state": judgment.state})


def cmd_approval_check(args: argparse.Namespace) -> dict:
    hub = _hub(args)
    pull = hub.pull(args.pr)
    if pull.base_ref != args.production:
        return result.stopped(TOOL, "not_applicable", f"#{args.pr} は本番ブランチ {args.production} 宛てでない（{pull.base_ref}）")
    got = trail.check(hub, args.pr)
    return result.result(
        TOOL, result.OK, f"#{args.pr} の承認の判定: {got.judgment.detail}", outputs={"state": got.judgment.state, "head": got.pull.head_sha}
    )


def cmd_merge(args: argparse.Namespace) -> dict:
    admin = decl_mod.production_admin(Path(args.root))
    done = land.Lander(_hub(args), args.base, args.production, args.budget, admin=admin).land(args.pr)
    outputs: dict[str, Any] = {"sync_pr": done.sync_pr} if done.sync_pr is not None else {}
    if done.production_sha is not None:
        outputs["production_sha"] = done.production_sha
    outputs["admin"] = done.admin
    if done.admin_record is not None:
        outputs["admin_record"] = done.admin_record
    detail = done.detail
    if done.record_error is not None:  # 本番は出ている。記録だけを打ち直しで書く（#3506 の決定 7）
        detail = f"{detail}。迂回の記録を書けなかった（本番は出ている）: {done.record_error}"
    if done.merged and done.kind is None and done.record_error is None:
        return result.result(TOOL, result.OK, detail, outputs=outputs)
    return result.stopped(TOOL, done.kind if done.kind else "unknown", detail, outputs=outputs)


def cmd_record(args: argparse.Namespace) -> dict:
    made, written = record.write(_hub(args), args.pr, args.production)
    count = sum(1 for w in written if w.result == record.WRITTEN)
    outputs = {"version": made.version, "included": list(made.included)}
    detail = f"v{made.version} のリリース記録を {count} 件書いた（含めた PR {len(written)} 本。既にあった {len(written) - count} 本）"
    return result.result(TOOL, result.OK, detail, [w.item() for w in written], outputs=outputs)


def cmd_verification_record(args: argparse.Namespace) -> dict:
    payload = json.loads(Path(args.record).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise record.RecordError(f"{args.record} はオブジェクトの JSON でない")
    url = record.write_verification(_hub(args), args.pr, payload)
    return result.result(TOOL, result.OK, f"#{args.pr} へリリース後テストの記録を書いた: {url}", outputs={"url": url})


def cmd_records(args: argparse.Namespace) -> dict:
    got = record.read(_hub(args), args.pr)
    outputs = {"release": got.release.as_json() if got.release else None, "verifications": list(got.verifications)}
    version = got.release.version if got.release else "無し"
    detail = f"#{args.pr} のリリース記録（バージョン {version}）とリリース後テストの記録 {len(got.verifications)} 件を読んだ"
    return result.result(TOOL, result.OK, detail, outputs=outputs)


def cmd_publish(args: argparse.Namespace) -> dict:
    made = tagging.publish(Path(args.root), _hub(args), args.revision)
    created = sum(1 for m in made if m.result == tagging.CREATED)
    return result.result(TOOL, result.OK, f"タグと GitHub Release を {created} 件作った", [m.item() for m in made])


def cmd_sync_base(args: argparse.Namespace) -> dict:
    plan = verify.sync_base(Path(args.main_dir), args.base)
    if plan.action == sync_decide.STOP:
        detail = f"メインディレクトリの変更がマージする差分と重なり、origin と中身が違う: {', '.join(plan.paths)}"
        return result.stopped(TOOL, "precondition", detail, items=[{"kind": "path", "path": p} for p in plan.paths])
    return result.result(TOOL, result.OK, f"{args.base} を origin へそろえた（{plan.action}）", outputs={"action": plan.action})


def cmd_config_snapshot(args: argparse.Namespace) -> dict:
    snap = verify.snapshot()
    Path(args.out).write_text(
        json.dumps({"items": snap.items, "unreadable": list(snap.unreadable)}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result.result(TOOL, result.OK, f"利用者の設定のスナップショットを取った（{len(snap.items)} 件）", warnings=list(snap.unreadable))


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
    p.add_argument("--base", required=True, help="ベースブランチ（設定 worktree.json の base_branch）")
    p.add_argument("--production", required=True, help="本番ブランチ（設定 worktree.json の production_branch）")


def _target_args(p: argparse.ArgumentParser) -> None:
    _branches(p)
    p.add_argument("--channel", choices=version_decide.CHANNELS, required=True)
    p.add_argument("--level", choices=version_decide.LEVELS, default="patch")
    p.add_argument("--plugin", help="ブランチの名前にバージョンを使うプラグイン（省けば登録の順の先頭）")


def _pr(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, required=True)


def _material(p: argparse.ArgumentParser) -> None:
    p.add_argument("--material", help="承認資料のファイル（省けば PR の本文）")


def _answer_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--answer", choices=approval_decide.ANSWERS, required=True)
    p.add_argument("--presented", required=True, help="提示した先端の SHA（present の outputs.head）")
    p.add_argument(
        "--by", required=True, choices=trail.BY_CHOICES, help="回答した担い手（利用者 user / MVV 判定 mvv。迂回は user だけを数える）"
    )


def _materials_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", help="承認資料を書くファイル（省けば標準出力）")


def _production(p: argparse.ArgumentParser) -> None:
    p.add_argument("--production", required=True)


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


# サブコマンドのハンドラー → 引数を足す関数（並びは足す順。どのサブコマンドにも先に `_common` を足す）
ARGS: dict[Callable[[argparse.Namespace], dict], tuple[Callable[[argparse.ArgumentParser], None], ...]] = {
    cmd_assemble: (_target_args,),
    cmd_materials: (_target_args, _materials_out),
    cmd_present: (_pr, _material),
    cmd_answer: (_pr, _answer_args),
    cmd_approval_check: (_pr, _production),
    cmd_merge: (_pr, _branches, _budget),
    cmd_record: (_pr, _production),
    cmd_verification_record: (_pr, _verification),
    cmd_records: (_pr,),
    cmd_publish: (_revision,),
    cmd_sync_base: (_main_dir, _base),
    cmd_config_snapshot: (_snapshot_out,),
    cmd_config_check: (_before,),
    cmd_cleanup: (_branch,),
}

# サブコマンドの名前 → ハンドラー。名前はハンドラーの名前（`cmd_<名前>` の `_` を `-` にしたもの）から作り、写さない
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


# 停止理由の種類（例外 → 種類）
_ERRORS: tuple[tuple[type[Exception], str], ...] = (
    (assemble.NothingToRelease, "not_applicable"),
    (assemble.AssembleError, "precondition"),
    (assemble.ReleaseOpen, "refused"),
    (trail.TrailError, "refused"),
    (record.NotReleased, "not_applicable"),
    (record.RecordError, "precondition"),
    (record_decide.UnreadableRecord, "invalid"),
    (decl_mod.NdfSettingsError, "precondition"),
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
