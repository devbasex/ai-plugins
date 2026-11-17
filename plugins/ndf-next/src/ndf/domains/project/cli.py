"""副命令 `ndf project`: プロジェクトの宣言（`.ndf/project.json`）の判定・測定・書き出し・ブランチ戦略・スキーマ（#3569。旧い
`project-decl.py` の移し先）。

    ndf project check   [--force] [--root <ディレクトリ>]                              # 宣言が新しいか
    ndf project measure --out <ファイル> [--budget <秒>] [--root <ディレクトリ>]          # リポジトリと CI を測る
    ndf project write   --measure <ファイル> [--answers <ファイル>] [--dry-run] [--root <ディレクトリ>]  # 宣言を書き出す
    ndf project branch-strategy <init|measure|write|migrate|remove> [--kind <種類>] [--answers <ファイル>] [--result <ファイル>]
                        [--digest <ハッシュ>] [--dry-run] [--root <ディレクトリ>]          # ブランチ戦略の宣言を作る・測る・書く・移す・消す
    ndf project schema  [--out <ファイル>]                                                # 宣言の JSON Schema を出す

`check` の終了コード: 0 = 新しい / 3 = 無い・古い（`--force` なら新しくても 3。種類 `not_applicable`）/ 2 = 壊れている
（種類 `invalid`）/ 1 = 判定できない（git の外。種類 `unknown`）。LLM も GitHub も呼ばず、ファイルを書かない。
`measure` は `--out` だけを書き、`gh` へは読む要求だけを渡す。0 = 書けた（項目が不明でも 0）/ 1 = `--out` を書けない。
`write` は 0 = 書けた・書くものが無い / 1 = 書けない / 2 = 既存の宣言が壊れている。
結果は 1 行の結果 JSON（形は contracts/schemas/io/project.result.schema.json）。その前に人が読む行を出す。

`branch-strategy` だけはセクション `branch_strategy` の作成の結果（形は contracts/schemas/io/branch-strategy.result.schema.json。
10 系の `project-decl.py branch-strategy` と同じ形）を 1 行の JSON で出す。終了コードは ok 0・ask 3・stopped 1・読めない 2。
書く動詞（init・write・migrate・remove）は `--digest` を渡すと示した差分のハッシュが合うときだけ書き、渡さなければ差分と
ハッシュを示すだけにする（init は `--dry-run` が無ければ書く）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ndf import cli, contracts
from ndf.adapters import git
from ndf.contracts import exit_codes, limits, result
from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.domains.project import check, measure_ci, measure_repo, secret, strategy_measure, strategy_migrate, write
from ndf.domains.project import strategy_write as bw
from ndf.domains.project.items import STATUSES, obj, seq, unknown
from ndf.domains.records import jsonfile
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


def _gh_repo(repo: git.Git, remote: str) -> Optional[str]:
    try:
        return git.repo_of_url(repo.remote_url(remote))
    except git.GitError:  # noqa: NDF401 外部コマンド: リモート名か URL を決められなければリポジトリは不明
        return None


class MeasurementContext(NamedTuple):
    """`gh` と CI の項目を測る文脈。リモートの GitHub のリポジトリ・リモートの既定ブランチ・リポジトリの中の課題の置き場。"""

    gh_repo: Optional[str]
    head: Optional[str]
    repo_issues: Optional[dict[str, Any]]


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


def _ci_items(tree: measure_repo.Tree, step: _Steps, ctx: MeasurementContext) -> list[str]:
    """`gh` と CI から測る項目を入れ、指示ファイルと CI の注記を返す。"""
    items = step.items
    ci_part = step("ci", lambda: measure_ci.measure(tree, ctx.gh_repo, ctx.head, step.deadline, os.environ))
    notes = _instruction_notes(items)
    if ci_part:
        items["ci"], items["test_duration"] = ci_part["ci"], ci_part["test_duration"]
        notes += ci_part["notes"]
    elif "test_duration" not in items:
        items["test_duration"] = items["ci"] if "ci" in items else unknown("時間切れ")
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
    decl = check.load(main)
    head = check.branch_state(repo, remote=tree.remote)["head"]
    ctx = MeasurementContext(gh_repo=_gh_repo(repo, tree.remote), head=head, repo_issues=repo_issues)
    notes = _ci_items(tree, step, ctx)
    if tree.skipped:
        notes.append(f"秘密の名前のファイルを開かなかった: {'・'.join(tree.skipped[:10])}")
    return {
        "analyzer": check.ANALYZER,
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "items": {k: step.items[k] for k in write.ITEM_KEYS if k in step.items},
        "inputs": check.inputs(repo, tree.files),
        "branch_state": check.branch_state(repo, check.declared_branches(decl), tree.remote),
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
    existing = check.load(main)
    try:
        measured, answers = _read_input(args.measure), _read_input(args.answers)
    except jsonfile.JsonFileError as exc:
        raise write.WriteError(f"測定の結果か答えを読めない: {exc}") from None
    return write.Inputs(repo.cwd, main, existing, measured, answers)


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
    unknown = sorted({write.p_number(r["key"], "P?") for r in rows if r["kind"] in ("unknown", "secret")})
    names = "・".join(f.name for f in planned.files)
    verb = "作成した" if any(f.old is None for f in planned.files) else "更新した"
    summary = f"{verb}（{names}。不明 {'・'.join(unknown) if unknown else '無し'}）" if planned.files else "書くものが無い"
    metrics = {"written": len(planned.files), "files": [str(f.path) for f in planned.files], "unknown_items": unknown, "dry_run": dry_run}
    return result.result(TOOL, result.OK, summary + write.summary_note(rows), items=rows, metrics=metrics)


# 作成の結果の status → 終了コードの区分（ask は前提が無い・正常な否定の結果）
BS_EXIT = {"ok": "ok", "ask": "precondition", "stopped": "failed"}
BS_LISTS = ("evidence", "questions", "diff", "failed", "warnings")


def _bs_emit(out: dict[str, Any]) -> int:
    """作成の結果を 1 行の JSON で出し、終了コードを返す。"""
    out.setdefault("kind", None)
    out.setdefault("values", {})
    for k in BS_LISTS:
        out.setdefault(k, [])
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return exit_codes.code_of(BS_EXIT[out["status"]])


def _bs_commit(root: Path, plan: bw.WritePlan, digest: Optional[str], base: dict[str, Any], write_now: bool = False) -> int:
    """`digest` を渡すか `write_now` が真なら書き、どちらも無ければ差分とハッシュを示すだけにする。"""
    if plan.failed:
        return _bs_emit({**base, "status": "stopped", "failed": plan.failed})
    if digest is None and not write_now:
        return _bs_emit({**base, "status": "ok", "diff": plan.diff, "digest": plan.digest})
    try:
        done = bw.commit_plan(root, plan, digest)
    except bw.WriteError as exc:
        return _bs_emit({**base, "status": "stopped", "failed": [{"rule": "write", "pointer": "", "reason": exc.reason}]})
    return _bs_emit({**base, "status": "ok", "diff": done.diff, "digest": done.digest})


def _bs_api(root: Path) -> strategy_measure.Api:
    return strategy_measure.default_api(root, time.monotonic() + limits.seconds(strategy_measure.MEASURE_LIMIT))


def _bs_init(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """新規のリポジトリだけに、種類の既定の行へ測った名前を入れたセクションを書く。"""
    api = _bs_api(root)
    state, seen = strategy_measure.classify_repo(root, api, presets)
    base: dict[str, Any] = {"kind": args.kind, "evidence": seen}
    if state != "new":
        reason = f"既存のリポジトリです。`{legacy.COMMAND['11']} measure` で測ってください"
        return _bs_emit({**base, "status": "stopped", "failed": [{"rule": "existing", "pointer": "", "reason": reason}]})
    if not args.kind:
        question = {"pointer": "/kind", "why": "no_kind", "candidates": list(bw.kinds()), "evidence": []}
        return _bs_emit({**base, "status": "ask", "questions": [question]})
    facts = strategy_measure.init_facts(root, api)
    section, questions = bw.init_values(args.kind, facts, presets)
    base = {"kind": args.kind, "values": section, "evidence": bw.evidence_of(section, facts)}
    if questions:
        return _bs_emit({**base, "status": "ask", "questions": questions})
    return _bs_commit(root, bw.plan_write(root, section), args.digest, base, write_now=not args.dry_run)


def _bs_measure(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """既存のリポジトリを測った結果を出す。書かない。"""
    return _bs_emit(strategy_measure.measure_strategy(root, presets=presets, prefer=args.kind))


def _declared_section(root: Path) -> Optional[dict[str, Any]]:
    """読めるセクションがあれば、それを確定した値として持つ測った結果の形。無い・読めなければ None（測り直す）。"""
    try:
        section = bs.load(root).section
    except bs.StrategyError:  # noqa: NDF401 ファイル: 読めないセクションは土台にせず、測り直す
        return None
    return {"status": "ok", "kind": section.get("kind"), "values": section, "evidence": [], "questions": []}


def _bs_write(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """測った結果に答えを入れて書く。`--result` が無ければ今のセクションを土台にし（答えたキーだけを変え、利用者が決めた
    merge.actor・verify・reviews を種類の既定へ戻さない）、セクションも無ければ測り直す。"""
    prior = (
        jsonfile.read_object(Path(args.result))
        if args.result
        else _declared_section(root) or strategy_measure.measure_strategy(root, presets=presets, prefer=args.kind)
    )
    answers = jsonfile.read_object(Path(args.answers)) if args.answers else {}
    res = bw.apply_answers(prior, answers)
    if res["status"] == "ok" and "approval" not in res["values"]:
        # 問いを返した結果は確定した値だけを持つ。答えを足した後に種類の既定から全体を組む
        res = strategy_measure.complete_answers(prior, answers, presets)
    if res["status"] == "ask":
        return _bs_emit(res)
    base = {"kind": res["values"].get("kind"), "values": res["values"], "evidence": seq(res.get("evidence"))}
    return _bs_commit(root, bw.plan_write(root, res["values"]), args.digest, base)


def _bs_migrate(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """旧い 4 か所の宣言をセクションへ移す。`--answers` は問いへの答え（セクションの pointer → 値）で、移した値を上書きする。"""
    answers = jsonfile.read_object(Path(args.answers)) if args.answers else {}
    plan, questions = strategy_migrate.plan_migration(root, legacy.load_moves(), answers)
    if questions:
        return _bs_emit({"status": "ask", "questions": questions})
    return _bs_commit(root, plan, args.digest, {})


def _bs_remove(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """セクションを消す。"""
    return _bs_commit(root, bw.plan_remove(root), args.digest, {})


Verb = Callable[[argparse.Namespace, Path, dict[str, Any]], int]


def _bs_verbs() -> dict[str, Verb]:
    """動詞 → 本体。動詞の選択肢は argparse（choices）が閉じる。"""
    return {"init": _bs_init, "measure": _bs_measure, "write": _bs_write, "migrate": _bs_migrate, "remove": _bs_remove}


def _branch_strategy(args: argparse.Namespace) -> int:
    """動詞（`init`・`measure`・`write`・`migrate`・`remove`）を受けて組み、作成の結果を 1 行の JSON で出す。"""
    try:
        repo, _ = check.repo_dirs(Path(args.root).resolve())
        return _bs_verbs()[args.verb](args, repo.cwd, bw.load_presets())
    except check.CheckError as exc:
        sys.stderr.write(f"{exc}\n")
        return exit_codes.code_of(bs.UNREADABLE)
    except (bs.StrategyError, bw.WriteError) as exc:
        sys.stderr.write(f"{exc.reason}\n")
        return exit_codes.code_of(exc.exit_id)
    except (contracts.ContractError, jsonfile.JsonFileError) as exc:
        sys.stderr.write(f"{exc}\n")
        return exit_codes.code_of(bs.UNREADABLE)


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
    b = sub.add_parser("branch-strategy", help="ブランチ戦略の宣言（branch_strategy）を作る・測る・書く・移す・消す")
    b.add_argument("verb", choices=tuple(_bs_verbs()), help="init・measure・write・migrate・remove")
    b.add_argument("--kind", choices=bw.kinds(), help="代表のリポジトリの種類（init は必須。measure・write は候補の先頭にする）")
    b.add_argument("--answers", help="問いへの答え（セクションの JSON Pointer → 値）")
    b.add_argument("--result", help="write の土台にする measure の結果（省けば今のセクション、無ければ測り直す）")
    b.add_argument("--digest", help="示した差分のハッシュ。合うときだけ書く")
    b.add_argument("--dry-run", action="store_true", help="init で書かずに差分とハッシュだけを示す")
    b.add_argument("--root", default=".", help="リポジトリの作業場所")
    b.set_defaults(handler=_branch_strategy)
    s = sub.add_parser("schema", help="宣言の JSON Schema を出す")
    s.add_argument("--out", help="書く先。省けば標準出力")
    s.set_defaults(handler=_schema)
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)
