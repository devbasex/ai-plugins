"""副命令 `ndf release-steps` のエントリポイント。引数と結果 JSON だけを持ち、処理は段階ごとのモジュールが持つ。

| 副命令 | 打つもの | 段階のモジュール |
| --- | --- | --- |
| `assemble` | workflow | `assemble`（ブランチの組み立てと PR） |
| `materials` | 会話のセッション・workflow | `assemble.collect`（ブランチを作らずに承認資料を組む） |
| `present` | 会話のセッション | `trail.present`（承認資料の提示。承認ゲート） |
| `answer` | 会話のセッション | `trail.answer`（答えを証跡に書き、届いたかを確かめる） |
| `approval-check` | workflow | `trail.check`（証跡の判定を commit status へ） |
| `merge` | workflow | `land`（証跡の後のマージと、ベースブランチへ戻す PR） |
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
        TOOL, result.GATE, f"#{args.pr}（{head[:12]}）の承認資料を提示した。答えを待つ", routes=routes, outputs={"head": head}
    )


def cmd_answer(args: argparse.Namespace) -> dict:
    judgment = trail.answer(_hub(args), args.pr, args.answer, args.by, args.presented)
    return result.result(TOOL, result.OK, f"#{args.pr} の答えが証跡に届いた: {judgment.detail}", outputs={"state": judgment.state})


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
    done = land.Lander(_hub(args), args.base, args.production, args.budget).land(args.pr)
    outputs: dict[str, Any] = {"sync_pr": done.sync_pr} if done.sync_pr is not None else {}
    if done.merged and done.kind is None:
        return result.result(TOOL, result.OK, done.detail, outputs=outputs)
    return result.stopped(TOOL, done.kind if done.kind else "unknown", done.detail, outputs=outputs)


def cmd_publish(args: argparse.Namespace) -> dict:
    made = tagging.publish(Path(args.root), _hub(args), args.revision)
    created = sum(1 for m in made if m.result == tagging.CREATED)
    return result.result(TOOL, result.OK, f"タグと GitHub Release を {created} 件作った", [m.item() for m in made])


def cmd_sync_base(args: argparse.Namespace) -> dict:
    plan = verify.sync_base(Path(args.main_dir), args.base)
    if plan.action == sync_decide.STOP:
        detail = f"メインディレクトリの変更が取り込む差分と重なり、origin と中身が違う: {', '.join(plan.paths)}"
        return result.stopped(TOOL, "precondition", detail, items=[{"kind": "path", "path": p} for p in plan.paths])
    return result.result(TOOL, result.OK, f"{args.base} を origin へそろえた（{plan.action}）", outputs={"action": plan.action})


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
    p.add_argument("--production", required=True, help="本番ブランチ（宣言 worktree.json の production_branch）")


def _target_args(p: argparse.ArgumentParser) -> None:
    _branches(p)
    p.add_argument("--channel", choices=version_decide.CHANNELS, required=True)
    p.add_argument("--level", choices=version_decide.LEVELS, default="patch")
    p.add_argument("--plugin", help="ブランチの名前に版を使うプラグイン（省けば登録の順の先頭）")


def _pr(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, required=True)


def _material(p: argparse.ArgumentParser) -> None:
    p.add_argument("--material", help="承認資料のファイル（省けば PR の本文）")


def _answer_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--answer", choices=approval_decide.ANSWERS, required=True)
    p.add_argument("--presented", required=True, help="提示した先端の SHA（present の outputs.head）")
    p.add_argument("--by", required=True, help="答えた人")


def _materials_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", help="承認資料を書くファイル（省けば標準出力）")


def _production(p: argparse.ArgumentParser) -> None:
    p.add_argument("--production", required=True)


def _budget(p: argparse.ArgumentParser) -> None:
    p.add_argument("--budget", type=float, required=True, help="必須のチェックを待つ秒")


def _revision(p: argparse.ArgumentParser) -> None:
    p.add_argument("--revision", required=True)


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


# 副命令の名前 → 引数を足す関数（並びは足す順。どの副命令にも先に `_common` を足す）
ARGS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], ...]] = {
    "assemble": (_target_args,),
    "materials": (_target_args, _materials_out),
    "present": (_pr, _material),
    "answer": (_pr, _answer_args),
    "approval-check": (_pr, _production),
    "merge": (_pr, _branches, _budget),
    "publish": (_revision,),
    "sync-base": (_main_dir, _base),
    "config-snapshot": (_snapshot_out,),
    "config-check": (_before,),
    "cleanup": (_branch,),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ndf release-steps", description="リリース（段階ごとのモジュール）")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, adders in ARGS.items():
        p = sub.add_parser(name)
        _common(p)
        for add in adders:
            add(p)
    return parser


# 副命令の名前 → ハンドラー。名前はハンドラーの名前（`cmd_<名前>` の `_` を `-` にしたもの）から作り、写さない
HANDLERS = {
    f.__name__.removeprefix("cmd_").replace("_", "-"): f
    for f in (
        cmd_assemble,
        cmd_materials,
        cmd_present,
        cmd_answer,
        cmd_approval_check,
        cmd_merge,
        cmd_publish,
        cmd_sync_base,
        cmd_config_snapshot,
        cmd_config_check,
        cmd_cleanup,
    )
}

# 止まった理由の種類（例外 → 種類）
_ERRORS: tuple[tuple[type[Exception], str], ...] = (
    (assemble.NothingToRelease, "not_applicable"),
    (assemble.AssembleError, "precondition"),
    (trail.TrailError, "refused"),
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
