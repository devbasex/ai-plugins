"""副命令 `ndf project`: プロジェクトの宣言（`.ndf/project.json`）の判定・測定・書き出し・ずれ・スキーマ（#3569。旧い
`project-decl.py` の移し先）。

    ndf project check   [--force] [--root <ディレクトリ>]                              # 宣言が新しいか
    ndf project measure --out <ファイル> [--budget <秒>] [--root <ディレクトリ>]          # リポジトリと CI を測る
    ndf project write   --measure <ファイル> [--answers <ファイル>] [--dry-run] [--root <ディレクトリ>]  # 宣言を書き出す
    ndf project drift   --measure <ファイル> [--root <ディレクトリ>]                      # ブランチ運用の宣言と使い方のずれ
    ndf project schema  [--out <ファイル>]                                                # 宣言の JSON Schema を出す

`check` の終了コード: 0 = 新しい / 3 = 無い・古い（`--force` なら新しくても 3。種類 `not_applicable`）/ 2 = 壊れている
（種類 `invalid`）/ 1 = 判定できない（git の外。種類 `unknown`）。LLM も GitHub も呼ばず、ファイルを書かない。
`measure` は `--out` だけを書き、`gh` へは読む要求だけを渡す。0 = 書けた（項目が不明でも 0）/ 1 = `--out` を書けない。
`write` は 0 = 書けた・書くものが無い / 1 = 書けない・答えが取り込み方の表に合わない / 2 = 既存の宣言が壊れている。
`drift` は 0 = 比べた（ずれが無ければ items は空）/ 1 = 測定の結果を読めない / 2 = 宣言が壊れている。
結果は 1 行の結果 JSON（形は contracts/schemas/io/project.result.schema.json）。その前に人が読む行を出す。
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import cli, contracts
from ndf.adapters import git
from ndf.contracts import result
from ndf.domains.project import check, measure_ci, measure_flow, measure_repo, secret, write
from ndf.domains.project.items import QUESTION, STATUSES, obj, seq, unknown
from ndf.domains.records import files, jsonfile
from ndf.domains.relay import signals

TOOL = cli.tool_of(__name__)


def _check(args: argparse.Namespace) -> int:
    try:
        got = check.judge(Path(args.root).resolve(), args.force)
    except check.CheckError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"判定できない: {exc}"))
    items = [{"kind": got.kind, "reason": r} for r in got.reasons]
    if got.kind == check.FRESH:
        return result.emit(result.result(TOOL, result.OK, "宣言は新しい", items=items, metrics={"inputs": got.inputs}))
    if got.kind == check.BROKEN:
        return result.emit(result.stopped(TOOL, "invalid", "；".join(got.reasons), items=items))
    label = "無い" if got.kind == check.MISSING else "古い"
    return result.emit(result.stopped(TOOL, "not_applicable", "；".join(got.reasons), summary=f"宣言が{label}", items=items))


def _gh_repo(repo: git.Git) -> Optional[str]:
    try:
        return git.repo_of_url(repo.remote_url())
    except git.GitError:  # noqa: NDF401 外部コマンド: リモート名か URL を決められなければリポジトリは不明
        return None


def _flow_branches(tree: measure_repo.Tree, worktree: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """ブランチ運用を測るベースブランチと本番チャネル（宣言の値、無ければ既定ブランチ）。"""
    wt = obj(worktree)
    base, production = (v if isinstance(v, str) else None for v in (wt.get("base_branch"), wt.get("production_branch")))
    if base is None or production is None:
        default = measure_repo.default_branch(tree)
        return (default if base is None else base), (default if production is None else production)
    return base, production


class MeasurementContext(NamedTuple):
    """`gh` と CI の項目を測る文脈。リモートの GitHub のリポジトリ・リモートの既定ブランチ・リポジトリの中の課題の置き場・`.ndf/worktree.json`。"""

    gh_repo: Optional[str]
    head: Optional[str]
    repo_issues: Optional[dict[str, Any]]
    worktree: Optional[dict[str, Any]]


class _Steps:
    """項目ごとの測定。締め切りを越えた・失敗した項目を不明にし、1 項目の失敗で測定を止めない。"""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.items: dict[str, Any] = {}

    def __call__(self, key: str, fn: Any) -> Any:
        if time.monotonic() >= self.deadline:
            self.items[key] = unknown("時間切れ")
            return None
        try:
            return fn()
        except measure_repo.TimeUp:  # 途中で締め切りを越えた項目は、読めた分だけで測ったことにしない
            self.items[key] = unknown("時間切れ")
        except Exception as exc:  # noqa: BLE001 ファイル: 1 項目の失敗で測定を止めず、その項目を不明にする
            self.items[key] = unknown(f"測れない: {type(exc).__name__}: {str(exc)[:200]}")
        return None


def _instruction_notes(items: dict[str, Any]) -> list[str]:
    """指示書の項目（`instructions`）の注記。測れなかった項目は空。"""
    return list(seq(obj(obj(items.get("instructions")).get("value")).get("notes")))


def _add_merge_evidence(items: dict[str, Any], merges: Optional[dict[str, int]]) -> None:
    """問いの P6（`branches`）の根拠へ、直近のマージ先の数を足す。"""
    if merges and obj(items.get(write.BRANCHES)).get("status") == QUESTION:
        counts = "・".join(f"{k} {v}" for k, v in sorted(merges.items(), key=lambda kv: -kv[1]))
        items[write.BRANCHES]["evidence"].append({"text": f"直近 100 件のマージ先: {counts}"})


def _ci_items(tree: measure_repo.Tree, step: _Steps, ctx: MeasurementContext) -> list[str]:
    """`gh` と CI から測る項目を入れ、指示ファイルと CI の注記を返す。"""
    items = step.items
    ci_part = step("ci", lambda: measure_ci.measure(tree, ctx.gh_repo, ctx.head, step.deadline, os.environ))
    notes = _instruction_notes(items)
    if ci_part:
        items["ci"], items["test_duration"] = ci_part["ci"], ci_part["test_duration"]
        notes += ci_part["notes"]
        _add_merge_evidence(items, ci_part["merges"])
    elif "test_duration" not in items:
        items["test_duration"] = items["ci"] if "ci" in items else unknown("時間切れ")
    flow = step(write.BRANCH_FLOW, lambda: measure_flow.measure(tree, ctx.gh_repo, *_flow_branches(tree, ctx.worktree)))
    if flow is not None:
        items[write.BRANCH_FLOW] = flow
    if "issues" not in items:
        items["issues"] = measure_ci.measure_issues(ctx.repo_issues, ci_part if ci_part else {})
    return notes


def measure_project(root: Path, budget: float) -> dict[str, Any]:
    """P1〜P10 を測る。git の木から測る項目を先に、残りの時間で `gh` の項目を測る。"""
    start = time.monotonic()
    repo, main = check.repo_dirs(root)
    tree = measure_repo.Tree(repo.cwd, start + budget)
    step = _Steps(start + budget)
    repo_issues = measure_repo.measure(tree, step, step.items)
    _, worktree = check.load(main)
    head = check.branch_state(repo)["head"]
    ctx = MeasurementContext(gh_repo=_gh_repo(repo), head=head, repo_issues=repo_issues, worktree=worktree)
    notes = _ci_items(tree, step, ctx)
    if tree.skipped:
        notes.append(f"秘密の名前のファイルを開かなかった: {'・'.join(tree.skipped[:10])}")
    keys = (*write.ITEM_KEYS, write.BRANCHES, write.BRANCH_FLOW)
    return {
        "analyzer": check.ANALYZER,
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "items": {k: step.items[k] for k in keys if k in step.items},
        "inputs": check.inputs(repo, tree.files),
        "branch_state": check.branch_state(repo, check.declared_branches(worktree)),
        "skipped_files": tree.skipped,
        "notes": notes,
    }


def _measure(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    if out.is_relative_to(root):
        return result.emit(result.stopped(TOOL, "check_failed", f"--out はリポジトリの外（一時ディレクトリ）に置く: {out}"))
    try:
        data = secret.mask(measure_project(root, args.budget))  # 素材の README の冒頭・テストの scripts の値も伏せてから書く
    except (check.CheckError, jsonfile.JsonFileError) as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"測れない: {exc}"))
    try:
        signals.replace_text(out, write.dumps(data))
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "check_failed", f"--out を書けない: {exc}"))
    counts = {s: sum(1 for m in data["items"].values() if m["status"] == s) for s in STATUSES}
    print(f"測定の結果: {out}（{data['elapsed_seconds']} 秒）")
    items = []
    for key, m in data["items"].items():
        print(f"  {write.p_number(key, '')} {key}: {m['status']}{'（' + m['reason'] + '）' if m.get('reason') else ''}")
        items.append({"kind": m["status"], "name": key, **({"reason": m["reason"]} if m.get("reason") else {})})
    metrics = {**counts, "elapsed_seconds": data["elapsed_seconds"], "out": str(out)}
    return result.emit(result.result(TOOL, result.OK, f"測った（{data['elapsed_seconds']} 秒）", items=items, metrics=metrics))


def _read_input(path: Optional[str]) -> dict[str, Any]:
    return jsonfile.read_object(Path(path)) if path else {}


def _inputs(args: argparse.Namespace) -> write.Inputs:
    """書き出しの入力。git の外は `CheckError`、宣言が壊れていれば `JsonFileError`、測定の結果か答えが読めなければ `WriteError`。"""
    repo, main = check.repo_dirs(Path(args.root).resolve())
    existing, worktree = check.load(main)
    try:
        measured, answers = _read_input(args.measure), _read_input(args.answers)
    except jsonfile.JsonFileError as exc:
        raise write.WriteError(f"測定の結果か答えを読めない: {exc}") from None
    return write.Inputs(repo.cwd, main, existing, worktree, measured, answers)


def _write(args: argparse.Namespace) -> int:
    try:
        planned = write.plan(_inputs(args))
    except jsonfile.JsonFileError as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"既存の宣言が壊れている（作り直さない）: {exc}。直してから打ち直す"))
    except (check.CheckError, write.WriteError) as exc:
        return result.emit(result.stopped(TOOL, "check_failed", str(exc)))
    if not args.dry_run:
        try:
            for f in planned.files:
                signals.replace_text(f.path, f.new)
        except OSError as exc:
            return result.emit(result.stopped(TOOL, "check_failed", f"宣言を書けない: {exc}"))
    return result.emit(_report_write(planned, args.dry_run))


def _print_write(planned: write.Plan, rows: list[dict[str, Any]], dry_run: bool) -> None:
    verb = "書く（--dry-run）" if dry_run else "書いた"
    for f in planned.files:
        print(f"{verb}: {f.path}")
        print(write.unified(f.name, f.old, f.new))
    if not planned.files:
        print("書くものが無い（宣言は解析と同じ）")
    print("項目ごとの出所:")
    for row in rows:
        if row["kind"] not in ("note", "ignored"):
            print(write.row_line(row))
    for row in rows:
        if row["kind"] == "note":
            print(f"案内: {row['text']}")
        elif row["kind"] == "ignored":
            print(f"無視した答え: {row['key']}（問いでない）")
    if planned.files and not dry_run:
        print("差分を見て、要らなければコミットしない（承認は求めない）")


def _report_write(planned: write.Plan, dry_run: bool) -> dict[str, Any]:
    rows = secret.mask(planned.rows)  # 表に無い形の秘密も、出力の直前でもう一度伏せる
    _print_write(planned, rows, dry_run)
    unknown = sorted({write.p_number(r["key"], "P6") for r in rows if r["kind"] in ("unknown", "secret")})
    names = "・".join(f.name for f in planned.files)
    verb = "作成した" if any(f.old is None for f in planned.files) else "更新した"
    summary = f"{verb}（{names}。不明 {'・'.join(unknown) if unknown else '無し'}）" if planned.files else "書くものが無い"
    metrics = {"written": len(planned.files), "files": [str(f.path) for f in planned.files], "unknown_items": unknown, "dry_run": dry_run}
    return result.result(TOOL, result.OK, summary + write.summary_note(rows), items=rows, metrics=metrics)


def _drift(args: argparse.Namespace) -> int:
    try:
        _, main = check.repo_dirs(Path(args.root).resolve())
        worktree = check.load(main)[1]
        rows = measure_flow.drift(worktree, obj(_read_input(args.measure).get("items")).get(write.BRANCH_FLOW))
    except check.CheckError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"比べられない: {exc}"))
    except jsonfile.JsonFileError as exc:
        return result.emit(result.stopped(TOOL, "check_failed", f"測定の結果か宣言を読めない: {exc}"))
    except ValueError as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"{files.relpath('worktree')}: {exc}"))
    rows = secret.mask(rows)
    for r in rows:
        print(f"ずれ: {r['name']} 宣言 {r['declared']} / 測った {r['measured']}（{r['confidence']}）— {r['proposal']}")
    summary = f"ブランチ運用の宣言と使い方のずれ {len(rows)} 件" if rows else "ブランチ運用の宣言は使い方と合う"
    declared = worktree is not None and write.BRANCH_FLOW in worktree
    return result.emit(result.result(TOOL, result.OK, summary, items=rows, metrics={"drifted": len(rows), "declared": declared}))


def _schema(args: argparse.Namespace) -> int:
    text = write.dumps(contracts.load_json(check.SCHEMA))
    if not args.out:
        print(text, end="")
        return result.emit(result.result(TOOL, result.OK, "宣言の JSON Schema を出した"))
    try:
        signals.replace_text(Path(args.out), text)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"--out を書けない: {exc}"))
    return result.emit(
        result.result(TOOL, result.OK, f"宣言の JSON Schema を書いた: {args.out}", items=[{"kind": "schema", "reason": args.out}])
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description=__doc__.splitlines()[0] if __doc__ else None)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="宣言が新しいかを判定する（0 = 新しい / 3 = 無い・古い / 2 = 壊れている / 1 = 判定できない）")
    c.add_argument("--force", action="store_true", help="新しくても古いと判定する（手動の再解析）")
    c.add_argument("--root", default=".", help="リポジトリの作業場所")
    c.set_defaults(handler=_check)
    m = sub.add_parser("measure", help="リポジトリと CI を測り、測定の結果を --out へ書く")
    m.add_argument("--out", required=True, help="測定の結果の置き場（リポジトリの外の一時ディレクトリ）")
    m.add_argument("--budget", type=float, default=120.0, help="締め切りの秒（既定 120）")
    m.add_argument("--root", default=".", help="リポジトリの作業場所")
    m.set_defaults(handler=_measure)
    w = sub.add_parser("write", help="測定の結果と答えから宣言を書き出す")
    w.add_argument("--measure", required=True, help="ndf project measure の測定の結果")
    w.add_argument("--answers", help="問いの項目への答え（キー → {value} か {unknown}）")
    w.add_argument("--dry-run", action="store_true", help="書かずに差分だけを出す")
    w.add_argument("--root", default=".", help="リポジトリの作業場所")
    w.set_defaults(handler=_write)
    d = sub.add_parser("drift", help="ブランチ運用の宣言と測った使い方のずれを出す")
    d.add_argument("--measure", required=True, help="ndf project measure の測定の結果")
    d.add_argument("--root", default=".", help="リポジトリの作業場所")
    d.set_defaults(handler=_drift)
    s = sub.add_parser("schema", help="宣言の JSON Schema を出す")
    s.add_argument("--out", help="書く先。省けば標準出力")
    s.set_defaults(handler=_schema)
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)
