"""`ndf records` のサブコマンド（記録: 確定仕様化・引継ぎ・用語集・使用量・MVV・手順 0 の確かめ・工程の終わり）。

    ndf records glossary render [--check]          # 用語集の文書をコンテキストごとのファイルと索引に作る
    ndf records glossary meanings                  # 意味の説明に残った廃止した語
    ndf records spec finalize --spec <F>... [--remove <設計>...] [--source-of <語>=<F>...] [-m <件名>]
    ndf records handoff check <文書> [--trim --history <退避先>]
    ndf records usage stop                         # SubagentStop の hook の入力（標準入力）を使用量ログへ 1 行書く
    ndf records usage report --since <日付> --until <日付>   # JST の日付の期間の合計と閉じた課題の数
    ndf records mvv current --base <ブランチ> [--allow-mismatch]   # ベースブランチの承認済みの MVV を読み、本文をファイルに書く
    ndf records mvv signals --base <ブランチ>       # ベースブランチの MVV のバージョンで改訂の兆候を数える
    ndf records mvv answer [--version <バージョン>]         # 承認ゲートの回答（標準入力の JSON）を覆しの記録へ写す
    ndf records mvv gate --gate design|release [--machine <ID>] [--expect-pr <番号>]   # 承認ゲートの直前の MVV 判定（工程の入力は環境変数 NDF_STAGE_INPUT か --machine）
    ndf records mvv cite --gate design|release --base <ブランチ> <ファイル>...   # 承認資料の根拠の行を承認済みの MVV で確かめる
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

import sqlalchemy as sa

from ndf import cli, contracts
from ndf.adapters import github as github_mod
from ndf.adapters import publish
from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.domains.records import cleanup_stage, close_decide, glossary, gws, handoff, mvv, mvv_gate, preflight, reading, spec, usage
from ndf.core import durable
from ndf.progress import command, definition, invoke, model, stage_inputs
from ndf.progress.invoke import INPUT_ENV
from ndf.domains.records import glossary_decide as gd
from ndf.domains.records import spec_decide as sd
from ndf.domains.records.mvv_decide import State

TOOL = cli.tool_of(__name__)
# リリース後テストの記録のスキーマ（contracts/schemas/release-verification.schema.json）
VERIFICATION = "release-verification"


def _root(args: argparse.Namespace) -> Path:
    where = args.cwd if args.cwd is not None else Path.cwd()
    return Path(Git(where).run("rev-parse", "--show-toplevel").strip())


def _hub(root: Path, remote: str | None = None) -> github_mod.GitHub:
    """`root` のリモート（`remote` を省けば解く）の GitHub。解き済みのリモート名を渡すと解き直さない。"""
    return _repo_hub(Git(root).remote_repo(remote))


def _repo_hub(repo: str) -> github_mod.GitHub:
    return github_mod.GitHub(repo, github_mod.client())


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


def _base_mvv(root: Path, base: str, allow_mismatch: bool = False) -> mvv.Snapshot:
    """ベースブランチ `base` を取得し、その MVV を読む（`mvv.current`）。取得できなければ `GitError`。"""
    g = Git(root)
    remote = g.remote()
    # 取得先の参照まで指定して取得する。別のブランチだけを clone した作業場所でも、直前にリモートで承認された MVV を読む
    g.fetch_branch_or_raise(remote, base)
    return mvv.current(root, f"refs/remotes/{remote}/{base}", allow_mismatch=allow_mismatch)


def _mvv_current(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    try:
        snap = _base_mvv(root, args.base, args.allow_mismatch)
    except GitError as exc:
        return result.stopped(TOOL, "precondition", f"MVV を読むベースブランチを取得できない: {exc}")
    except (mvv.MvvError, OSError) as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    out: dict[str, Any] = {"state": snap.state}
    if snap.state == State.APPROVED:
        out |= {"version": snap.version, "sha256": snap.sha256, "path": str(snap.path)}
        out |= {"principles_path": str(snap.principles_path), "items": list(snap.items)}
    warnings = [mvv.mismatch_detail(snap.blobs)] if snap.state == State.MISMATCH else []
    return result.result(TOOL, result.OK, f"{args.base} の MVV: {snap.state}", outputs={"mvv": out}, warnings=warnings)


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


class _InputError(Exception):
    """判定の入力を決められない。`kind` は停止理由の語（`invalid`・`input_missing`・`refused`）。

    `stopped` があれば、その止まった結果をそのまま返す（保存先・DB の例外を `ndf progress` と同じ形に変えたもの）。
    """

    def __init__(self, kind: str, summary: str, stopped: dict[str, Any] | None = None) -> None:
        super().__init__(summary)
        self.kind, self.summary, self.stopped = kind, summary, stopped


def _env_inputs(gate: str, inputs: Any) -> dict[str, Any]:
    """`NDF_STAGE_INPUT` の値を、プランが組むのと同じ規則（工程の入力の規約 `stage-in` と工程の入力のキー）で確かめて返す。

    工程の入力の規則はモードに依らないので、判定のステップを持つ工程の文書から `mode` を読まずに定義を作る。規約の誤りが
    1 件でもあれば `invalid`（`{"context": {...}}` の形もここで落ちる）、無くてキーが足りなければ `input_missing`。
    """
    stage = definition.stage_io(mvv_gate.GATE_STAGES[gate])
    got = definition.stage_input_errors(stage, inputs)
    if got.schema:
        raise _InputError("invalid", f"工程の入力（環境変数 {INPUT_ENV}）の形が合わない: " + "; ".join(got.schema))
    if got.missing:
        raise _InputError("input_missing", f"工程の入力（環境変数 {INPUT_ENV}）にキーが足りない: " + "; ".join(got.missing))
    return dict(inputs)


def _mvv_inputs(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    """判定の入力。`NDF_STAGE_INPUT`（プランの経路）か `--machine`（ステートマシンの ID）のどちらか一方から決める。"""
    raw = os.environ.get(INPUT_ENV)
    if raw is not None and args.machine is not None:
        raise _InputError("invalid", f"判定の入力の出所が 2 つある（環境変数 {INPUT_ENV} と --machine）。どちらか一方にする")
    if args.machine is not None:
        try:
            return stage_inputs.stage_inputs_of(root, args.machine, mvv_gate.GATE_STEPS[args.gate])
        except model.Refused as exc:
            raise _InputError("refused", str(exc)) from exc
        except invoke.StageInputError as exc:
            raise _InputError(exc.kind, str(exc)) from exc
        except sa.exc.SQLAlchemyError as exc:
            # 本文は SQL・バインド値・接続先を含みうるので、ndf progress と同じく種類の名前だけを出す
            raise _InputError("", "", command.stopped_db(TOOL, exc)) from exc
        except durable.StoreStopped as exc:
            raise _InputError("", "", exc.stopped(TOOL)) from exc
    if raw is None:
        raise _InputError("invalid", f"工程の入力（環境変数 {INPUT_ENV} の JSON object か --machine <ステートマシンの ID>）が無い")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _InputError("invalid", f"工程の入力（環境変数 {INPUT_ENV}）を JSON として読めない") from exc
    return _env_inputs(args.gate, parsed)


# --expect-pr の番号が判定する PR と違うときの要約。どちらの PR の番号も載せない（結果は承認資料へ写されうる）
OTHER_PR = "判定する PR が承認する PR と違うので判定しない"


def _mvv_gate(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.expect_pr is not None and args.gate != mvv_gate.RELEASE:
        return result.stopped(TOOL, "invalid", f"--expect-pr は --gate {mvv_gate.RELEASE} のときだけ渡せる")
    try:
        inputs = _mvv_inputs(args, root)
    except _InputError as exc:
        return exc.stopped if exc.stopped is not None else result.stopped(TOOL, exc.kind, exc.summary)
    if args.expect_pr is not None:
        # 判定に使う入力そのものと比べる（gate の中は変えない）。比べるだけなので GitHub へつながない
        try:
            judged: int | None = mvv_gate.release_pr(inputs)
        except mvv_gate.GateError:
            judged = None  # 判定する PR を決められなければ、今どおり gate が停止理由を返す
        if judged is not None and judged != args.expect_pr:
            return result.stopped(TOOL, "not_applicable", OTHER_PR)
    warnings: list[dict[str, Any]] = []
    out = mvv_gate.gate(root, args.gate, inputs, _hub, warnings=warnings)
    summary = "MVV 判定で承認ゲートを省く" if out["skip"] else "承認ゲートを人へ問う: " + " / ".join(out["why"])
    return result.result(TOOL, result.OK, summary, warnings, outputs={model.MVV_KEY: out})


# 根拠の行の誤りの自由記述を伏せられなかったときの注意の頭（後ろに failure_text の定型文と診断の記録の識別子）
CITE_WITHHELD = "根拠の行の誤りの自由記述を伏せた（参照先の公開範囲を照会できない）: "


def _cite_shown(root: Path, errors: list[dict[str, Any]], where: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """根拠の行の誤りの自由記述（`path`・`cited`・`unknown` の要素）を 1 回の伏せる処理に通した誤りと、注意。

    伏せ方は判定するリポジトリの公開範囲を照会せず、公開の書き先と同じにする（結果の受け手はその閲覧者と限らない。#3959）。
    照会に失敗したら各欄を `WITHHELD` にし、注意の原因は `failure_text` の定型文と診断の記録の識別子にする。
    """
    seen = publish.Once(lambda texts: publish.Publisher(_hub(root)).guarded(*texts, assume_public=True)[0])
    shown = iter(seen([t for e in errors for t in (e["path"], e["cited"], *e["unknown"])]))
    items = [{**e, "path": next(shown), "cited": next(shown), "unknown": [next(shown) for _ in e["unknown"]]} for e in errors]
    if seen.failure is None:
        return items, []
    cause = seen.error if seen.error is not None else seen.failure
    return items, [CITE_WITHHELD + mvv_gate.failure_text("cite_lookup_failed", cause, where)]


def _mvv_cite(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    where = {"gate": args.gate}
    # 相対パスは対象のリポジトリ（--cwd）の根から解く（引継ぎ文書と同じ）
    files = [root / f.expanduser() for f in args.files]
    try:
        snap = _base_mvv(root, args.base)
        got = mvv.cite(snap, files, require_basis=args.gate == mvv_gate.DESIGN)
    except (GitError, mvv.MvvError, OSError, UnicodeDecodeError) as exc:
        # 例外の文は認証情報の付いた URL・社内のホスト名・手元のパスを含みうるので、定型文と診断の記録の識別子だけを出す
        return result.stopped(TOOL, "precondition", mvv_gate.failure_text("cite_precondition", exc, where))
    if snap.state != State.APPROVED:
        return result.result(TOOL, result.OK, "承認済みの MVV が無いので確かめていない")
    metrics = got.metrics(snap)
    if not got.errors:
        return result.result(
            TOOL, result.OK, f"根拠の行の誤り 0 件（MVV バージョン {snap.version}・{len(files)} ファイル）", metrics=metrics
        )
    items, warnings = _cite_shown(root, got.errors, where)
    first = items[0]
    summary = f"根拠の行の誤り {len(items)} 件（最初: {first['path']}:{first['line']} {first['cited']}）"
    return result.stopped(TOOL, "refused", summary, items=items, metrics=metrics, warnings=warnings)


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
    except (github_mod.GitHubError, reading.HeadUnknown) as exc:
        return result.stopped(TOOL, "unknown", f"承認する PR の今の head を GitHub から読めない: {exc}")


def _reading_prompt(entry: Any, root: Path) -> dict[str, Any]:
    return result.result(TOOL, result.OK, "読み手役への指示を作った", metrics={"prompt": reading.prompt(entry)})


def _reading_add(entry: Any, root: Path) -> dict[str, Any]:
    return result.result(TOOL, result.OK, "読み手役が確認した記録を 1 行足した", [reading.add(entry, root)])


def _reading_check(entry: Any, root: Path) -> dict[str, Any]:
    missing = reading.check(entry, root, _repo_hub)
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
    current = m.add_parser("current")
    current.add_argument("--base", required=True, help="ベースブランチ")
    current.add_argument("--allow-mismatch", action="store_true", help="本文が承認の記録と合わなくても止まらず、警告を付けて状態だけを返す")
    current.set_defaults(handler=_mvv_current)
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
    gate.add_argument(
        "--machine",
        default=None,
        help="ステートマシンの ID（プランの外で打つとき。環境変数 NDF_STAGE_INPUT の代わりに文脈から入力を組み、両方は渡せない）。"
        "NDF_STAGE_INPUT は工程の入力の規約 stage-in の平らな形で、外れれば invalid、キーが足りなければ input_missing で止まる",
    )
    gate.add_argument(
        "--expect-pr",
        type=int,
        default=None,
        help="承認する PR の番号（--gate release のときだけ）。判定に使う入力の PR と違えば、PR を読まず判定もコメントもせずに"
        " not_applicable で止まる",
    )
    gate.set_defaults(handler=_mvv_gate)
    cite = m.add_parser("cite")
    cite.add_argument(
        "--gate", required=True, choices=(mvv_gate.DESIGN, mvv_gate.RELEASE),
        help="資料を返す承認ゲートの区分（design なら「目指す姿の根拠」のセクションと、決定ごとの根拠の行の有無も確かめる）",
    )  # fmt: skip
    cite.add_argument("--base", required=True, help="ベースブランチ（取得して承認済みの MVV を読む）")
    cite.add_argument("files", nargs="+", type=Path, metavar="ファイル", help="確かめる承認資料")
    cite.set_defaults(handler=_mvv_cite)


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
