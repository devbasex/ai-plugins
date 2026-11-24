"""サブコマンド `ndf release-steps` のエントリポイント。引数と結果 JSON だけを持ち、処理は段階ごとのモジュールが持つ。

| サブコマンド | 打つもの | 段階のモジュール |
| --- | --- | --- |
| `assemble` | workflow | `assemble`（ブランチの組み立てと PR） |
| `materials` | 会話のセッション・workflow | `assemble.collect`（ブランチを作らずに承認資料を組む） |
| `present` | 会話のセッション | `trail.present`（承認資料の提示。承認ゲート） |
| `answer` | 会話のセッション | `trail.answer`（回答を証跡に書き、届いたかを確かめる） |
| `approval-check` | workflow | `trail.check`（証跡の判定を commit status へ） |
| `merge` | workflow | `land`（証跡の後に、ブランチ戦略の設定の主体と方式で入れ、要ればベースブランチへ戻す PR） |
| `record` | workflow | `record.write`（含めた PR の各々へリリース記録を書く。本番ブランチへのマージの後） |
| `verification-record` | 会話のセッション | `record.write_verification`（リリース後テストの記録を、リリース記録と同じ PR へ書く。リリースの PR なら記録を持つ PR を示して止まる） |
| `records` | 会話のセッション | `record.read`（PR のリリース記録とリリース後テストの記録を読む） |
| `publish` | workflow | `tagging`（タグと GitHub Release） |

`assemble`・`materials`・`publish` は、メインディレクトリのブランチ戦略の設定（リリースの形・バージョンの保存先・名前の書式・
リリース方式）と、メインディレクトリの `.ndf/worktree.json` の `remote`（リモート名。1 回の実行で 1 度だけ求める）で動く。
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
from ndf.core import branch_strategy
from ndf.domains.release import (
    approval_decide,
    assemble,
    cleanup,
    forms_decide,
    land,
    materials_decide,
    names_decide,
    record,
    record_decide,
    route_decide,
    sync_decide,
    tagging,
    trail,
    units,
    user_config_decide,
    verify,
    version_decide,
)
from ndf.domains.release import ndf_settings as decl_mod
from ndf.domains.release import plugins as plugins_mod
from ndf.domains.release import version_store as store_mod

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)


def _main_dir(args: argparse.Namespace) -> Path:
    """メインディレクトリ（作業場所の設定は PR の変更でマージの主体・方式を差し替えられるため読まない）。
    git の外ならメインディレクトリを持たないので `--root`。"""
    root = Path(args.root)
    try:
        return git_mod.Git(root).common_dir().parent
    except git_mod.GitError:  # git の外はメインディレクトリが無いので --root の設定を読む
        return root


def _strategy(args: argparse.Namespace) -> branch_strategy.Strategy:
    """メインディレクトリのブランチ戦略の設定。"""
    return branch_strategy.load(_main_dir(args))


def _remote(args: argparse.Namespace) -> str:
    """メインディレクトリの設定のリモート名（#3615 の決定 9）。"""
    return git_mod.remote_of(_main_dir(args))


def _hub(args: argparse.Namespace, remote: str | None = None) -> GitHub:
    """GitHub のリポジトリ（`--repo` が無ければリモートの URL から。`remote` を渡せば設定を読み直さずにその名前を使う）。"""
    repo = args.repo if args.repo else git_mod.Git(Path(args.root)).remote_repo(remote)
    return GitHub(repo, github_mod.client())


def _target(args: argparse.Namespace, remote: str) -> assemble.Target:
    return assemble.Target(args.channel, args.level, args.base, args.production, remote, args.plugin, tuple(args.units), args.target_pr)


def cmd_assemble(args: argparse.Namespace) -> dict:
    remote = _remote(args)
    return assembled(assemble.assemble(Path(args.root), _hub(args, remote), _target(args, remote), _strategy(args)))


def assembled(built: assemble.Built) -> dict:
    """組み立ての結果。URL は要約に載せる（`outputs` はコントラクトの閉じた語彙で、`url` を持たない）。"""
    outputs = {"pr": built.pr, "branch": built.branch, "product_version": built.version}
    return result.result(
        TOOL, result.OK, f"{built.branch} の PR #{built.pr} を作った: {built.url}", materials_decide.items(built.facts), outputs=outputs
    )


def cmd_materials(args: argparse.Namespace) -> dict:
    remote = _remote(args)
    facts = assemble.collect(Path(args.root), _hub(args, remote), _target(args, remote), _strategy(args))
    text = materials_decide.render(facts)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return result.result(TOOL, result.OK, "承認資料を組んだ", materials_decide.items(facts), presentation_path=args.out)


def cmd_present(args: argparse.Namespace) -> dict:
    writer = trail.trail_writer(_strategy(args), merged_by_human=False)
    if writer != trail.WRITER_ANSWER:
        # 証跡の書き手が回答の手順でなければ問わない（GitHub のレビューか人のマージが承認を持つ。#3671）
        return result.result(
            TOOL, result.OK, f"#{args.pr} の承認の証跡の書き手は {writer} で、承認ゲートで問わない", outputs={"writer": writer}
        )
    hub = _hub(args)
    material = Path(args.material).read_text(encoding="utf-8") if args.material else hub.pull(args.pr).body
    shown = trail.present(hub, args.pr, material)
    head = shown.head
    if not shown.asked:
        detail = f"#{args.pr}（{head[:12]}）には今の HEAD への回答が既にあり、質問を送らない: {shown.judgment.detail}"
        return result.result(TOOL, result.OK, detail, outputs={"head": head, "state": shown.judgment.state, "writer": writer})
    return result.result(
        TOOL,
        result.GATE,
        f"#{args.pr}（{head[:12]}）の承認資料を提示した。回答を待つ",
        routes=present_routes(args.pr, head),
        outputs={"head": head},
    )


def present_routes(pr: int, head: str) -> dict:
    """回答ごとに打つ `answer`。担い手（`by`）は打つ側が埋める（`ask` はキーの名前で、打つ側が `--by <値>` を足す）。"""
    return {
        answer: result.command(TOOL, "answer", "--pr", str(pr), "--answer", answer, "--presented", head, ask=("by",))
        for answer in approval_decide.ANSWERS
    }


def cmd_answer(args: argparse.Namespace) -> dict:
    writer = trail.trail_writer(_strategy(args), merged_by_human=False)
    judgment = trail.answer(_hub(args), args.pr, args.answer, args.by, args.presented, writer)
    return result.result(TOOL, result.OK, f"#{args.pr} の回答が証跡に届いた: {judgment.detail}", outputs={"state": judgment.state})


def cmd_approval_check(args: argparse.Namespace) -> dict:
    hub = _hub(args)
    pull = hub.pull(args.pr)
    if pull.base_ref != args.production:
        return result.stopped(TOOL, "not_applicable", f"#{args.pr} は本番ブランチ {args.production} 宛てでない（{pull.base_ref}）")
    strategy = _strategy(args)
    formats = names_decide.formats_of(strategy.section["release"])
    if strategy.release_strategy == "trunk" and not formats.is_release_branch(pull.head_ref):
        # trunk ではスプリントの PR も本番宛てになる。承認を取るのは版上げの PR（head がリリースブランチの書式）だけで、
        # ほかは証跡を読まず commit status も付けない（10 系の approval-check と同じ。#3672）
        detail = f"#{args.pr} は版上げの PR でない（head {pull.head_ref}。リリースブランチの書式 {formats.branch} に合わない）"
        return result.stopped(TOOL, "not_applicable", detail)
    got = trail.check(hub, args.pr)
    return result.result(
        TOOL, result.OK, f"#{args.pr} の承認の判定: {got.judgment.detail}", outputs={"state": got.judgment.state, "head": got.pull.head_sha}
    )


def cmd_merge(args: argparse.Namespace) -> dict:
    strategy = _strategy(args)
    done = land.Lander(_hub(args), strategy, args.budget).land(args.pr)
    route = route_decide.route_of(strategy)
    # 版上げの PR を作る設定だけが、本番へ入った後にリリース記録（record）を書く。workflow は `production_sha` があり
    # `records` が真のときだけ record を打つ（戻す PR で止まっても、本番へ入っていれば記録を書く）
    outputs: dict[str, Any] = {"records": route.release_pr and route.bump}
    if done.sync_pr is not None:
        outputs["sync_pr"] = done.sync_pr
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
    formats = names_decide.formats_of(_strategy(args).section["release"])
    made, written = record.write(_hub(args), args.pr, args.production, formats, args.production_sha)
    count = sum(1 for w in written if w.result == record.WRITTEN)
    outputs = {"product_version": made.version, "included": list(made.included)}
    detail = f"v{made.version} のリリース記録を {count} 件書いた（含めた PR {len(written)} 本。既にあった {len(written) - count} 本）"
    return result.result(TOOL, result.OK, detail, [w.item() for w in written], outputs=outputs)


def cmd_verification_record(args: argparse.Namespace) -> dict:
    payload = json.loads(Path(args.record).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise record.RecordError(f"{args.record} はオブジェクトの JSON でない")
    try:
        url = record.write_verification(_hub(args), args.pr, payload, lambda: _release_names(args))
    except record.RecordElsewhere as exc:
        return result.stopped(TOOL, "precondition", str(exc), items=exc.items)
    return result.result(TOOL, result.OK, f"#{args.pr} へリリース後テストの記録を書いた: {url}")


def _release_names(args: argparse.Namespace) -> tuple[str | None, names_decide.NameFormats]:
    """ブランチ戦略の設定の本番ブランチと名前の書式（`cmd_record` と同じ読み方）。設定が読めなければ `(None, 既定の書式)`
    （記録を持つ PR を探さずに止まる。#3852）。"""
    try:
        strategy = _strategy(args)
    except branch_strategy.StrategyError:  # 設定が無い・壊れているリポジトリでも、止まる文に理由を載せて返す
        return record._no_names()
    return strategy.production, names_decide.formats_of(strategy.section["release"])


def cmd_records(args: argparse.Namespace) -> dict:
    got = record.read(_hub(args), args.pr)
    outputs = {"release": got.release.as_json() if got.release else None, "verifications": list(got.verifications)}
    version = got.release.version if got.release else "無し"
    detail = f"#{args.pr} のリリース記録（バージョン {version}）とリリース後テストの記録 {len(got.verifications)} 件を読んだ"
    return result.result(TOOL, result.OK, detail, outputs=outputs)


def cmd_publish(args: argparse.Namespace) -> dict:
    strategy = _strategy(args)
    if not tagging.makes_tags(strategy):
        return result.stopped(TOOL, "not_applicable", "設定の release.tag が null で、タグと GitHub Release を作らない")
    made = tagging.publish(Path(args.root), _hub(args), args.revision, strategy, args.since)
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
    p.add_argument("--base", required=True, help="ベースブランチ（設定 project.json の branch_strategy.branches.base）")
    p.add_argument("--production", required=True, help="本番ブランチ（設定 project.json の branch_strategy.branches.production）")


def _target_args(p: argparse.ArgumentParser) -> None:
    _branches(p)
    p.add_argument("--channel", choices=version_decide.CHANNELS, required=True)
    p.add_argument("--level", choices=version_decide.LEVELS, default="patch")
    p.add_argument("--plugin", help="ブランチの名前にバージョンを使う単位（省けば単位の順の先頭）")
    p.add_argument("--units", nargs="+", default=(), help="集めるスプリントブランチ（release_branch の設定で必須）")
    p.add_argument("--pr", dest="target_pr", type=int, help="本番の PR の番号（承認の対象が production_pr の設定で必須）")


def _pr(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, required=True)


def _material(p: argparse.ArgumentParser) -> None:
    p.add_argument("--material", help="承認資料のファイル（省けば PR の本文）")


def _answer_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--answer", choices=approval_decide.ANSWERS, required=True)
    p.add_argument("--presented", required=True, help="提示した HEAD の SHA（present の outputs.head）")
    p.add_argument(
        "--by", required=True, choices=trail.BY_CHOICES, help="回答した担い手（利用者 user / MVV 判定 mvv。迂回は user だけを数える）"
    )


def _materials_out(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", help="承認資料を書くファイル（省けば標準出力）")


def _production(p: argparse.ArgumentParser) -> None:
    p.add_argument("--production", required=True)


def _production_sha(p: argparse.ArgumentParser) -> None:
    p.add_argument("--production-sha", help="本番へ入れたコミット（strict の squash で閉じた PR の記録に要る）")


def _budget(p: argparse.ArgumentParser) -> None:
    p.add_argument("--budget", type=float, required=True, help="必須のチェックを待つ秒")


def _revision(p: argparse.ArgumentParser) -> None:
    p.add_argument("--revision", required=True)
    p.add_argument("--since", help="前の push の先頭。祖先なら --revision までの first-parent のコミットを古い順にすべて公開する")


def _verification(p: argparse.ArgumentParser) -> None:
    p.add_argument("--record", required=True, help="リリース後テストの記録の JSON（スキーマ release-verification）")


def _main_dir_arg(p: argparse.ArgumentParser) -> None:
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
    cmd_merge: (_pr, _budget),
    cmd_record: (_pr, _production, _production_sha),
    cmd_verification_record: (_pr, _verification),
    cmd_records: (_pr,),
    cmd_publish: (_revision,),
    cmd_sync_base: (_main_dir_arg, _base),
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
    (assemble.NoReleasePr, "not_applicable"),
    (forms_decide.FormUnsupported, "precondition"),
    (store_mod.StoreError, "precondition"),
    (units.UnitsError, "precondition"),
    (tagging.TagMoved, "refused"),
    (assemble.AssembleError, "precondition"),
    (assemble.ReleaseOpen, "refused"),
    (trail.TrailError, "refused"),
    (branch_strategy.StrategyError, "precondition"),
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
