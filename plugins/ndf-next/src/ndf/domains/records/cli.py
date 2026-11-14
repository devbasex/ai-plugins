"""`ndf records` の副命令（記録: 確定仕様化・引継ぎ・用語集・使用量・MVV・手順 0 の確かめ・工程の終わり）。

    ndf records glossary render [--check]          # 用語集の文書をコンテキストごとのファイルと索引に作る
    ndf records glossary meanings                  # 意味の説明に残った廃止した語
    ndf records spec finalize --spec <F>... [--remove <設計>...] [--source-of <語>=<F>...] [-m <件名>]
    ndf records handoff check <文書> [--trim --history <退避先>]
    ndf records usage stop                         # SubagentStop の hook の入力（標準入力）を帳簿へ 1 行書く
    ndf records usage report --since <日付> --until <日付>   # JST の日付の期間の合計と閉じた課題の数
    ndf records mvv signals --base <ブランチ>       # ベースブランチの MVV の版で改訂の兆候を数える
    ndf records mvv answer [--version <版>]         # 承認ゲートの答え（標準入力の JSON）を覆しの記録へ写す
    ndf records preflight                          # 手順 0 の 3 つの確かめ（worktree の設定・宣言・MVV）
    ndf records cleanup-stage <PR番号>...           # 後片付けを終えた PR が閉じる課題に工程の記録を付ける
    ndf records close-check <課題> [--verification <JSON>] [--require]   # スプリントで課題を閉じてよいか
    ndf records gws-check                          # gws の認証を確かめる

どの副命令も今の位置（`--cwd`。既定はカレントディレクトリ）のリポジトリを対象にする。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ndf import cli
from ndf.adapters import github as github_mod
from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.domains.records import cleanup_stage, close_decide, glossary, gws, handoff, mvv, preflight, spec, usage
from ndf.domains.records import glossary_decide as gd
from ndf.domains.records import spec_decide as sd

TOOL = cli.tool_of(__name__)


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


def _glossary(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.action == "meanings":
        found = glossary.check_meanings(root)
        if found:
            return result.stopped(TOOL, "check_failed", f"意味の説明に廃止した語が {len(found)} 件ある", items=found)
        return result.result(TOOL, result.OK, "意味の説明に廃止した語は無い")
    try:
        differ = glossary.render(root, args.check)
    except gd.RenderError as exc:
        items = [{"context": cid, "lines": n} for cid, n in sorted(exc.over.items())]
        return result.stopped(TOOL, "check_failed", str(exc), items=items)
    if args.check and differ:
        return result.stopped(TOOL, "check_failed", f"用語集の文書が正本と違う: {len(differ)} 件", items=differ)
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
    git = Git(root)
    git.run("add", "-A", "--", *paths)
    git.run("commit", "-q", "-m", args.message)
    return result.result(TOOL, result.OK, f"確定仕様化をコミットした: {len(paths)} 件", items, outputs={"commit": git.rev_parse("HEAD")})


def _handoff(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    moved: list[str] = []
    try:
        if args.trim:
            if args.history is None:
                return result.stopped(TOOL, "invalid", "--trim には --history が要る")
            moved = handoff.trim_file(args.path, args.history)
        report = handoff.check_file(args.path, root, handoff.body_limits())
    except OSError as exc:
        return result.stopped(TOOL, "precondition", f"引継ぎ文書を読み書きできない: {exc}")
    metrics = {"lines": report.lines, "bytes": report.bytes}
    items = [{"violation": v} for v in report.violations] + [{"missing_path": p} for p in report.missing_paths]
    items += [{"moved": h} for h in moved]
    if not report.passed:
        detail = f"引継ぎ文書が通らない: 違反 {len(report.violations)} 件・無いパス {len(report.missing_paths)} 件"
        return result.stopped(TOOL, "check_failed", detail, items=items, metrics=metrics)
    return result.result(TOOL, result.OK, "引継ぎ文書は通った", items, metrics)


_SEARCH = "query($q: String!) { search(query: $q, type: ISSUE) { issueCount } }"


def _closed_count(hub: github_mod.GitHub, condition: str) -> int:
    """閉じた課題（PR を除く）の数。条件は `usage_decide.closed_query` の JST の両端。"""
    data = hub.graphql(_SEARCH, {"q": f"repo:{hub.repo} is:issue {condition}"})
    return int(data["search"]["issueCount"])


def _usage(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.action == "stop":
        payload = _stdin_json()
        row = usage.record_subagent(payload if isinstance(payload, dict) else {})
        # hook を落とさない（書けなかった理由は標準エラーへ出した）
        return result.result(TOOL, result.OK, "使用量の帳簿へ書いた" if row else "使用量の帳簿へ書かなかった", [row] if row else [])
    try:
        path = usage.ledger_path()
    except LookupError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    hub = _hub(root)
    got = usage.report(path, args.since, args.until, lambda q: _closed_count(hub, q))
    return result.result(TOOL, result.OK, f"使用量: {got['since']}..{got['until']}", [got])


def _mvv(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        if args.action == "signals":
            s = mvv.signals(root, args.base)
            item = {"version": s.version, "overrides": s.overrides, "unknown_pass": s.unknown_pass, "unknown": s.unknown}
            summary = "MVV の改訂を提案する兆候がある" if s.revise else "MVV の改訂の兆候は閾値に届かない"
            return result.result(TOOL, result.OK, summary, [item], {"revise": s.revise})
        answer = _stdin_json()
        if not isinstance(answer, dict):
            return result.stopped(TOOL, "invalid", "承認ゲートの答え（標準入力の JSON object）が無い")
        row = mvv.record_answer(root, answer, args.version)
    except (mvv.MvvError, OSError) as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    return result.result(TOOL, result.OK, f"覆しの記録: {row['kind']}" if row else "覆しではないので書かなかった", [row] if row else [])


def _cleanup_stage(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    items = cleanup_stage.record_cleanup_stage(_hub(root), args.prs)
    failed = [i for i in items if i["result"] == cleanup_stage.FAILED]
    recorded = sum(1 for i in items if i["result"] == cleanup_stage.RECORDED)
    if failed:
        detail = f"後片付けの工程の記録が {len(failed)} 件失敗した（後片付けの本体は戻さない）"
        gaps = [{"kind": "stage_record", "name": f"#{i['issue'] if 'issue' in i else i['pr']}", "reason": i["reason"]} for i in failed]
        return result.stopped(TOOL, "incomplete", detail, items=items, gaps=gaps)
    return result.result(TOOL, result.OK, f"後片付けの工程を {recorded} 件の課題に記録した", items)


def _close_check(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    rows = None
    if args.verification is not None:
        try:
            rows = json.loads(args.verification.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return result.stopped(TOOL, "precondition", f"リリース後テストの記録を読めない: {exc}")
        if not isinstance(rows, list):
            return result.stopped(TOOL, "precondition", "リリース後テストの記録が行の配列でない")
    ok, why = close_decide.closable(args.issue, rows, args.require)
    item = {"issue": args.issue, "closable": ok, "reason": why}
    if not ok:
        return result.stopped(TOOL, "refused", f"#{args.issue} を閉じない: {why}", items=[item])
    return result.result(TOOL, result.OK, f"#{args.issue} を閉じてよい: {why}", [item])


def _gws_check(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        status = gws.auth_status()
    except gws.GwsError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    if not gws.authenticated(status):
        return result.stopped(TOOL, "precondition", "gws は未認証（クライアントの設定だけがあり、トークンが無い）")
    return result.result(TOOL, result.OK, "gws は認証済み")


def _preflight(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    return preflight.check(root)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}")
    p.add_argument("--cwd", type=Path, default=None, help="対象のリポジトリの中の位置（既定はカレントディレクトリ）")
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("glossary").add_subparsers(dest="action", required=True)
    g.add_parser("render").add_argument("--check", action="store_true", help="書かずに、今と違うファイルを出す")
    g.add_parser("meanings")
    s = sub.add_parser("spec").add_subparsers(dest="action", required=True).add_parser("finalize")
    s.add_argument("--spec", action="append", default=[], required=True, help="溶かした先の確定仕様（複数可）")
    s.add_argument("--remove", action="append", default=[], help="消す設計（複数可）")
    s.add_argument("--source-of", action="append", default=[], help="<語>=<ファイル> で語の正本を明示する")
    s.add_argument("-m", "--message", default=None, help="コミットの件名。無ければコミットせずパスだけを出す")
    h = sub.add_parser("handoff").add_subparsers(dest="action", required=True).add_parser("check")
    h.add_argument("path", type=Path)
    h.add_argument("--trim", action="store_true", help="雛形に無い節を退避先へ移してから確かめる")
    h.add_argument("--history", type=Path, default=None, help="退避先（--trim のとき要る）")
    u = sub.add_parser("usage").add_subparsers(dest="action", required=True)
    u.add_parser("stop")
    r = u.add_parser("report")
    r.add_argument("--since", type=date.fromisoformat, required=True)
    r.add_argument("--until", type=date.fromisoformat, required=True)
    m = sub.add_parser("mvv").add_subparsers(dest="action", required=True)
    m.add_parser("signals").add_argument("--base", required=True, help="ベースブランチ")
    m.add_parser("answer").add_argument("--version", type=int, default=None, help="答えたときの MVV の版")
    c = sub.add_parser("cleanup-stage")
    c.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    k = sub.add_parser("close-check")
    k.add_argument("issue", type=int)
    k.add_argument("--verification", type=Path, default=None, help="リリース後テストの記録（行の配列の JSON）")
    k.add_argument("--require", action="store_true", help="記録が無ければ閉じない")
    for name, handler in (("glossary", _glossary), ("spec", _spec), ("handoff", _handoff), ("usage", _usage), ("mvv", _mvv),
                          ("cleanup-stage", _cleanup_stage), ("close-check", _close_check)):  # fmt: skip
        sub.choices[name].set_defaults(handler=handler)
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
