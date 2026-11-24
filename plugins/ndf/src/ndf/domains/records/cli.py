"""`ndf records` のサブコマンド（記録: 確定仕様化・引継ぎ・用語集・使用量・MVV・手順 0 の確かめ・工程の終わり）。

    ndf records glossary render [--check]          # 用語集の文書をコンテキストごとのファイルと索引に作る
    ndf records glossary meanings                  # 意味の説明に残った廃止した語
    ndf records spec finalize --spec <F>... [--remove <設計>...] [--source-of <語>=<F>...] [-m <件名>]
    ndf records handoff check <文書> [--trim --history <退避先>]
    ndf records usage stop                         # SubagentStop の hook の入力（標準入力）を使用量ログへ 1 行書く
    ndf records usage report --since <日付> --until <日付>   # JST の日付の期間の合計と閉じた課題の数
    ndf records mvv signals --base <ブランチ>       # ベースブランチの MVV のバージョンで改訂の兆候を数える
    ndf records mvv answer [--version <バージョン>]         # 承認ゲートの回答（標準入力の JSON）を覆しの記録へ写す
    ndf records mvv gate --gate design|release     # 承認ゲートの直前の MVV 判定（工程の入力は環境変数 NDF_STAGE_INPUT）
    ndf records preflight                          # 手順 0 の 3 つの確かめ（worktree の設定・設定・MVV）
    ndf records cleanup-stage <PR番号>...           # クリーンアップを終えた PR が閉じる課題に工程の記録を付ける
    ndf records close-check <課題> [--verification <JSON>] [--require]   # スプリントで課題を閉じてよいか
    ndf records gws-check                          # gws の認証を確かめる
    ndf records reading prompt|add|check --file <JSON>   # 読み手役への指示・確認の記録・質問を出す前に足りないもの

どのサブコマンドも今の位置（`--cwd`。既定はカレントディレクトリ）のリポジトリを対象にする。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ndf import cli, contracts
from ndf.adapters import github as github_mod
from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.domains.records import cleanup_stage, close_decide, glossary, gws, handoff, mvv, mvv_gate, preflight, reading, spec, usage
from ndf.progress import invoke, model
from ndf.progress.invoke import INPUT_ENV
from ndf.domains.records import glossary_decide as gd
from ndf.domains.records import spec_decide as sd

TOOL = cli.tool_of(__name__)
# リリース後テストの記録のスキーマ（contracts/schemas/release-verification.schema.json）
VERIFICATION = "release-verification"


def _root(args: argparse.Namespace) -> Path:
    where = args.cwd if args.cwd is not None else Path.cwd()
    return Path(Git(where).run("rev-parse", "--show-toplevel").strip())


def _hub(root: Path) -> github_mod.GitHub:
    return github_mod.GitHub(Git(root).remote_repo(), github_mod.client())


def _stdin_json() -> Any:
    try:
        return json.loads(sys.stdin.read() or "null")
    except json.JSONDecodeError:  # noqa: NDF401 利用者の入力: 読めない入力は無いものとして呼び手が止める
        return None


def _glossary_meanings(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    found = glossary.check_meanings(root)
    if found:
        return result.stopped(TOOL, "check_failed", f"意味の説明に廃止した語が {len(found)} 件ある", items=found)
    return result.result(TOOL, result.OK, "意味の説明に廃止した語は無い")


def _glossary_render(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        differ = glossary.render(root, args.check)
    except gd.RenderError as exc:
        items = [{"context": cid, "lines": n} for cid, n in sorted(exc.over.items())]
        return result.stopped(TOOL, "check_failed", str(exc), items=items)
    if args.check and differ:
        return result.stopped(TOOL, "check_failed", f"用語集の文書が SSOT と違う: {len(differ)} 件", items=differ)
    verb = "今と違う" if args.check else "書いた"
    return result.result(TOOL, result.OK, f"用語集の文書: {verb}ファイル {len(differ)} 件", differ)


def _spec(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        overrides = sd.parse_overrides(args.source_of)
        paths = spec.finalize(root, args.spec, args.remove, overrides)
    except sd.SourceError as exc:
        items = [{"term": t, "reason": "missing"} for t in exc.missing]
        items += [{"term": t, "reason": "ambiguous", "files": f} for t, f in exc.ambiguous.items()]
        return result.stopped(TOOL, "precondition", str(exc), items=items)
    except gd.RenderError as exc:
        return result.stopped(TOOL, "check_failed", str(exc))
    except (ValueError, OSError) as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    items = [{"path": p} for p in paths]
    if args.message is None:
        return result.result(TOOL, result.OK, f"確定仕様化: コミットに入れるパス {len(paths)} 件", items)
    sha = spec.commit(root, paths, args.message)
    return result.result(TOOL, result.OK, f"確定仕様化をコミットした: {len(paths)} 件", items, {"commit": sha})


def _handoff(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    moved: list[str] = []
    # 相対パスは対象のリポジトリ（--cwd）の根から解く。呼び出し元の同名の文書を読み書きしない
    path = root / args.path.expanduser()
    try:
        if args.trim:
            if args.history is None:
                return result.stopped(TOOL, "invalid", "--trim には --history が要る")
            moved = handoff.trim_file(path, root / args.history.expanduser())
        report = handoff.check_file(path, root, handoff.body_limits())
    except OSError as exc:
        return result.stopped(TOOL, "precondition", f"引継ぎ文書を読み書きできない: {exc}")
    metrics = {"lines": report.lines, "bytes": report.bytes}
    items = [{"violation": v} for v in report.violations] + [{"missing_path": p} for p in report.missing_paths]
    items += [{"moved": h} for h in moved]
    if not report.passed:
        detail = f"引継ぎ文書が通らない: 違反 {len(report.violations)} 件・無いパス {len(report.missing_paths)} 件"
        return result.stopped(TOOL, "check_failed", detail, items=items, metrics=metrics)
    return result.result(TOOL, result.OK, "引継ぎ文書は通った", items, metrics)


def _usage_stop(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    payload = _stdin_json()
    warnings: list[dict[str, Any]] = []
    row = usage.record_subagent(payload if isinstance(payload, dict) else {}, warnings=warnings)
    # hook を落とさない（書けなかった理由は標準エラーへ出した）
    summary = "使用量の使用量ログへ書いた" if row else "使用量の使用量ログへ書かなかった"
    return result.result(TOOL, result.OK, summary, ([row] if row else []) + warnings)


def _usage_report(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        path = usage.usage_log_path()
    except LookupError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    hub = _hub(root)
    got = usage.report(path, args.since, args.until, lambda q: usage.closed_count(hub, q))
    return result.result(TOOL, result.OK, f"使用量: {got['since']}..{got['until']}", [got])


def _mvv_signals(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    try:
        s = mvv.signals(root, args.base, warnings=warnings)
    except (mvv.MvvError, OSError) as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    item = {k: v for k, v in dataclasses.asdict(s).items() if k != "revision_due"}
    summary = "MVV の改訂を提案する兆候がある" if s.revision_due else "MVV の改訂の兆候は閾値に届かない"
    return result.result(TOOL, result.OK, summary, [item, *warnings], {"revision_due": s.revision_due})


def _mvv_answer(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        answer = _stdin_json()
        if not isinstance(answer, dict):
            return result.stopped(TOOL, "invalid", "承認ゲートの回答（標準入力の JSON object）が無い")
        rows = mvv.record_answer(root, answer, args.version)
    except (mvv.MvvError, OSError) as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    kinds = "・".join(r["kind"] for r in rows)
    return result.result(TOOL, result.OK, f"覆しの記録: {kinds}" if rows else "覆しではないので書かなかった", rows)


def _mvv_gate(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    inputs = invoke.stage_inputs(os.environ)
    if inputs is None:
        return result.stopped(TOOL, "invalid", f"工程の入力（環境変数 {INPUT_ENV} の JSON object）が無い")
    warnings: list[dict[str, Any]] = []
    out = mvv_gate.gate(root, args.gate, inputs, _hub, warnings=warnings)
    summary = "MVV 判定で承認ゲートを省く" if out["skip"] else "承認ゲートを人へ問う: " + " / ".join(out["why"])
    return result.result(TOOL, result.OK, summary, warnings, outputs={model.MVV_KEY: out})


def _cleanup_stage(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    items = cleanup_stage.record_cleanup_stage(_hub(root), args.prs)
    failed = [i for i in items if i["result"] == cleanup_stage.FAILED]
    recorded = sum(1 for i in items if i["result"] == cleanup_stage.RECORDED)
    if failed:
        detail = f"クリーンアップの工程の記録が {len(failed)} 件失敗した（クリーンアップの本体は戻さない）"
        gaps = [{"kind": "stage_record", "name": cleanup_stage.item_name(i), "reason": i["reason"]} for i in failed]
        return result.stopped(TOOL, "incomplete", detail, items=items, gaps=gaps)
    return result.result(TOOL, result.OK, f"クリーンアップの工程を {recorded} 件の課題に記録した", items)


def _verification_rows(path: Path) -> tuple[Any, str | None]:
    """リリース後テストの記録を読んで形を確かめ、(issues, 止める理由) を返す。"""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"リリース後テストの記録を読めない: {exc}"
    errs = contracts.schema_errors(record, contracts.schema_path(VERIFICATION))
    if errs:
        return None, "リリース後テストの記録の形が違う: " + "; ".join(errs)
    return record["issues"], None


def _close_check(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    rows = None
    if args.verification is not None:
        rows, err = _verification_rows(args.verification)
        if err is not None:
            return result.stopped(TOOL, "precondition", err)
    decided, why = close_decide.verdict(args.issue, rows, args.require)
    item = {"issue": args.issue, "closable": decided == close_decide.CLOSE, "reason": why}
    if decided == close_decide.KEEP:
        return result.stopped(TOOL, "refused", f"#{args.issue} を閉じない: {why}", items=[item])
    if decided == close_decide.ASK:
        # 閉じる操作は gh issue close で、ndf のコマンドを持たない
        routes = {"approved": None, "rejected": None}
        return result.result(TOOL, result.GATE, f"#{args.issue} を閉じるかは人が決める: {why}", [item], routes=routes)
    return result.result(TOOL, result.OK, f"#{args.issue} を閉じてよい: {why}", [item])


def _gws_check(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        status = gws.auth_status()
    except gws.GwsError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    if not gws.authenticated(status):
        return result.stopped(TOOL, "precondition", "gws は未認証（クライアントの設定だけがあり、トークンが無い）")
    return result.result(TOOL, result.OK, "gws は認証済み")


def _reading(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        # 相対パスは対象のリポジトリ（--cwd）の根から解く（引継ぎ文書と同じ）
        entry = json.loads((root / args.file.expanduser()).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return result.stopped(TOOL, "precondition", f"入力の JSON を読めない: {exc}")
    try:
        return _READING[args.action](entry, root)
    except reading.ReadingInvalid as exc:
        return result.stopped(TOOL, "invalid", f"入力の形が違う: {exc}", items=[{"error": str(exc)}])
    except OSError as exc:
        return result.stopped(TOOL, "precondition", f"読み手役が確認した記録を読み書きできない: {exc}")
    except github_mod.GitHubError as exc:
        return result.stopped(TOOL, "unknown", f"承認する PR の今の head を GitHub から読めない: {exc}")


def _reading_prompt(entry: Any, root: Path) -> dict[str, Any]:
    return result.result(TOOL, result.OK, "読み手役への指示を作った", metrics={"prompt": reading.prompt(entry)})


def _reading_add(entry: Any, root: Path) -> dict[str, Any]:
    return result.result(TOOL, result.OK, "読み手役が確認した記録を 1 行足した", [reading.add(entry, root)])


def _reading_check(entry: Any, root: Path) -> dict[str, Any]:
    missing = reading.check(entry, root, _hub)
    if missing:
        detail = "読み手役が確認した記録が足りない: " + "・".join(missing)
        return result.stopped(TOOL, "precondition", detail, items=[{"missing": word} for word in missing])
    return result.result(TOOL, result.OK, "読み手役が確認した記録は、質問と承認する PR に合う")


_READING: dict[str, Callable[[Any, Path], dict[str, Any]]] = {"prompt": _reading_prompt, "add": _reading_add, "check": _reading_check}


def _preflight(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    return preflight.check(root)


def _add_spec(sub: Any) -> None:
    s = sub.add_parser("spec").add_subparsers(dest="action", required=True).add_parser("finalize")
    s.add_argument("--spec", action="append", default=[], required=True, help="溶かした先の確定仕様（複数可）")
    s.add_argument("--remove", action="append", default=[], help="消す設計（複数可）")
    s.add_argument("--source-of", action="append", default=[], help="<語>=<ファイル> で語の SSOT を明示する")
    s.add_argument("-m", "--message", default=None, help="コミットの件名。無ければコミットせずパスだけを出す")
    sub.choices["spec"].set_defaults(handler=_spec)


def _add_handoff(sub: Any) -> None:
    h = sub.add_parser("handoff").add_subparsers(dest="action", required=True).add_parser("check")
    h.add_argument("path", type=Path)
    h.add_argument("--trim", action="store_true", help="テンプレートに無いセクションを退避先へ移してから確かめる")
    h.add_argument("--history", type=Path, default=None, help="退避先（--trim のとき要る）")
    sub.choices["handoff"].set_defaults(handler=_handoff)


def _add_usage(sub: Any) -> None:
    u = sub.add_parser("usage").add_subparsers(dest="action", required=True)
    u.add_parser("stop").set_defaults(handler=_usage_stop)
    r = u.add_parser("report")
    r.add_argument("--since", type=date.fromisoformat, required=True)
    r.add_argument("--until", type=date.fromisoformat, required=True)
    r.set_defaults(handler=_usage_report)


def _add_mvv(sub: Any) -> None:
    m = sub.add_parser("mvv").add_subparsers(dest="action", required=True)
    signals = m.add_parser("signals")
    signals.add_argument("--base", required=True, help="ベースブランチ")
    signals.set_defaults(handler=_mvv_signals)
    answer = m.add_parser("answer")
    answer.add_argument(
        "--version", type=int, default=None, help="回答したときの MVV のバージョン（無ければ worktree の設定の現行のバージョン）"
    )
    answer.set_defaults(handler=_mvv_answer)
    gate = m.add_parser("gate")
    gate.add_argument("--gate", required=True, choices=(mvv_gate.DESIGN, mvv_gate.RELEASE), help="承認ゲートの区分")
    gate.set_defaults(handler=_mvv_gate)


def _add_reading(sub: Any) -> None:
    r = sub.add_parser("reading").add_subparsers(dest="action", required=True)
    for action in _READING:
        a = r.add_parser(action)
        a.add_argument("--file", type=Path, required=True, help="入力の JSON のファイル")
        a.set_defaults(handler=_reading)


def _add_close_check(sub: Any) -> None:
    k = sub.add_parser("close-check")
    k.add_argument("issue", type=int)
    k.add_argument("--verification", type=Path, default=None, help="リリース後テストの記録（release-verification のスキーマの JSON）")
    k.add_argument("--require", action="store_true", help="記録が無ければ閉じない")
    k.set_defaults(handler=_close_check)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}")
    p.add_argument("--cwd", type=Path, default=None, help="対象のリポジトリの中の位置（既定はカレントディレクトリ）")
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("glossary").add_subparsers(dest="action", required=True)
    render = g.add_parser("render")
    render.add_argument("--check", action="store_true", help="書かずに、今と違うファイルを出す")
    render.set_defaults(handler=_glossary_render)
    g.add_parser("meanings").set_defaults(handler=_glossary_meanings)
    _add_spec(sub)
    _add_handoff(sub)
    _add_usage(sub)
    _add_mvv(sub)
    c = sub.add_parser("cleanup-stage")
    c.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    c.set_defaults(handler=_cleanup_stage)
    _add_close_check(sub)
    _add_reading(sub)
    sub.add_parser("preflight").set_defaults(handler=_preflight)
    sub.add_parser("gws-check").set_defaults(handler=_gws_check)
    return p


def _run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        root = _root(args)
        handler: Callable[[argparse.Namespace, Path], dict[str, Any]] = args.handler
        return handler(args, root)
    except GitError as exc:
        return result.stopped(TOOL, "precondition", f"リポジトリを読めない: {exc}")
    except glossary.GlossaryError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    except github_mod.GitHubError as exc:
        return result.stopped(TOOL, "transient", f"GitHub を読めない: {exc}")


def main(argv: list[str]) -> int:
    return result.emit(_run(_parser().parse_args(argv)))
