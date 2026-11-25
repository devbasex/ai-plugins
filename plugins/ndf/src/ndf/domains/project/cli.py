"""サブコマンド `ndf project`: プロジェクトの設定（`.ndf/project.json`）の判定・測定・書き出し・ブランチ戦略・スキーマ（#3569。旧い
`project-decl.py` の移し先）。

    ndf project check   [--force] [--root <ディレクトリ>]                              # 設定が新しいか
    ndf project measure --out <ファイル> [--budget <秒>] [--root <ディレクトリ>]          # リポジトリと CI を測る
    ndf project write   --measure <ファイル> [--answers <ファイル>] [--dry-run] [--root <ディレクトリ>]  # 設定を書き出す
    ndf project branch-strategy <init|measure|write|migrate|remove> [--kind <種類>] [--answers <ファイル>] [--result <ファイル>]
                        [--digest <ハッシュ>] [--dry-run] [--root <ディレクトリ>]          # ブランチ戦略の設定を作る・測る・書く・移す・消す
    ndf project schema  [--out <ファイル>]                                                # 設定の JSON Schema を出す

`check` の終了コード: 0 = 新しい / 3 = 無い・古い（`--force` なら新しくても 3。種類 `not_applicable`）/ 2 = 壊れている
（種類 `invalid`）/ 1 = 判定できない（git の外。種類 `unknown`）。LLM も GitHub も呼ばず、ファイルを書かない。
`measure` は `--out` だけを書き、`gh` へは読む要求だけを渡す。0 = 書けた（項目が不明でも 0）/ 1 = `--out` を書けない。
`write` は 0 = 書けた・書くものが無い / 1 = 書けない / 2 = 既存の設定が壊れている。
結果は 1 行の結果 JSON（形は contracts/schemas/io/project.result.schema.json）。その前に人が読む行を出す。

`branch-strategy` だけはセクション `branch_strategy` の作成の結果（形は contracts/schemas/io/branch-strategy.result.schema.json。
10 系の `project-decl.py branch-strategy` と同じ形）を 1 行の JSON で出す。終了コードは ok 0・ask 3・stopped 1・読めない 2。
書く動詞（init・write・migrate・remove）は `--digest` を渡すと示した差分のハッシュが合うときだけ書き、渡さなければ差分と
ハッシュを示すだけにする（init は `--dry-run` が無ければ書く）。
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional

from filelock import FileLock, Timeout

from ndf import cli, contracts
from ndf.adapters import git
from ndf.contracts import limits, result
from ndf.domains.project import analysis_record, check, measure_ci, measure_repo, secret, strategy_cli, write
from ndf.domains.project import strategy_write as bw
from ndf.domains.project.items import STATUSES, obj, seq, unknown
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
        return result.emit(result.result(TOOL, result.OK, "設定は新しい", items=items, metrics={"inputs": got.inputs}))
    if got.kind == check.BROKEN:
        return result.emit(result.stopped(TOOL, "invalid", "；".join(got.reasons), items=items))
    label = "無い" if got.kind == check.MISSING else "古い"
    return result.emit(result.stopped(TOOL, "not_applicable", "；".join(got.reasons), summary=f"設定が{label}", items=items))


def _gh_repo(repo: git.Git, remote: str) -> Optional[str]:
    try:
        return git.repo_of_url(repo.remote_url(remote))
    except git.GitError:  # noqa: NDF401 外部コマンド: リモート名か URL を決められなければリポジトリは不明
        return None


class MeasurementContext(NamedTuple):
    """`gh` と CI の項目を測る文脈。リモートの GitHub のリポジトリ・リモートの既定ブランチ・リポジトリの中の課題の保存先。"""

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
        items["ci"] = ci_part["ci"]
        notes += ci_part["notes"]
    if "issues" not in items:
        items["issues"] = measure_ci.measure_issues(ctx.repo_issues, ci_part if ci_part else {})  # noqa: NDF501 外部コマンド: CI の測りが時間切れなら空の材料で数える
    return notes


def measure_project(root: Path, budget: float) -> dict[str, Any]:
    """P1〜P10 を測る。git の木から測る項目を先に、残りの時間で `gh` の項目を測る。"""
    start = time.monotonic()
    dirs = check.repo_dirs(root)
    repo, main = dirs.top, dirs.main
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
    budget = args.budget if args.budget is not None else limits.seconds("project.measure")
    try:
        data = secret.mask(measure_project(root, budget))  # 素材の README の冒頭・テストの scripts の値も伏せてから書く
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


def _inputs(args: argparse.Namespace, dirs: check.RepoDirs) -> write.Inputs:
    """書き出しの入力（解決済みの作業場所のメインディレクトリから読む）。設定が壊れていれば `JsonFileError`、測定の結果か回答が
    読めなければ `WriteError`。前の解析の記録は、無い・読めなければ None（書き出しが作り直す）。"""
    existing = check.load(dirs.main)
    try:
        measured, answers = _read_input(args.measure), _read_input(args.answers)
    except jsonfile.JsonFileError as exc:
        raise write.WriteError(f"測定の結果か回答を読めない: {exc}") from None
    return write.Inputs(dirs.top.cwd, dirs.main, existing, measured, answers, analysis_record.load(dirs.main)[0])


def _unsure(folder: str, reason: str) -> write.WriteError:
    return write.WriteError(f"git が {folder}/ の追跡と無視を確かめられない: {reason}")


def _untracked(repo: git.Git, folder: str) -> None:
    """記録のディレクトリの下に git が追跡するファイルが無いことを確かめる（`.gitignore` を作る前）。あれば・分からなければ `WriteError`。"""
    done = repo.attempt("ls-files", "--", folder)
    if done.returncode != 0:  # 索引を読めなければ、出力が空でも追跡の有無は分からない
        raise _unsure(folder, done.stderr.strip())
    if done.stdout.strip():
        raise write.WriteError(f"{folder}/ の下のファイルが git に追跡されている: {'・'.join(done.stdout.split())}")


def _ignored(ignored: write.Ignored, folder: str, record: str) -> None:
    """計画の `git check-ignore` の答えで、記録のパスが無視されることを確かめる。無視の出所（どの除外の設定か）は問わない。
    無視されない（追跡済みを含む）・分からなければ `WriteError`。"""
    if ignored.error is not None:
        raise _unsure(folder, ignored.error)
    if record not in ignored.rules:
        raise write.WriteError(f"{record} が git に無視されない。{folder}/.gitignore の中身か、記録が追跡されていないかを確かめる")


def _put(f: write.File) -> None:
    try:
        signals.replace_text(f.path, f.new)
    except OSError as exc:
        raise write.WriteError(f"設定を書けない: {exc}") from None


def _commit(main: Path, planned: write.Plan) -> None:
    """計画のファイルを順に書く。記録のディレクトリの下へ書くときは、どのファイルを書くよりも前に、記録が git に無視されることを
    確かめる（止めるときは何も書かずに `WriteError`）。`.gitignore` を作るときは、記録のディレクトリの下に追跡されたファイルが
    無いことを確かめる。同じディレクトリの `.gitignore` の `*` はどの除外の設定より優先されるので、追跡されていなければ記録は無視される。
    `.gitignore` が既にあるときは、計画の `git check-ignore` の答えを使う。どちらの回も、計画の `git check-ignore` が答えられなかったら止める。"""
    record = files.relpath("project-analysis")
    folder = str(Path(record).parent)
    ignore = [f for f in planned.files if f.path == files.path(main, "project-analysis-ignore")]
    if any(f.path.parent == main / folder for f in planned.files):
        if planned.ignored.error is not None:  # 記録のパスを git が扱えない（保存先が別の場所へのリンクなど）。作る回も書かない
            raise _unsure(folder, planned.ignored.error)
        if ignore:
            _untracked(git.Git(main), folder)
        else:
            _ignored(planned.ignored, folder, record)
    for f in ignore + [f for f in planned.files if f not in ignore]:
        _put(f)


def _write(args: argparse.Namespace) -> int:
    """書き出す。`--dry-run` でなければ、設定と記録を読む前から最後のファイルを書くまで、ブランチ戦略の書き込みと同じロックを持つ。"""
    try:
        dirs = check.repo_dirs(Path(args.root).resolve())
        if args.dry_run:
            planned = write.plan(_inputs(args, dirs))
        else:
            with FileLock(str(bw.lock_at(dirs.common)), timeout=limits.seconds("branch-strategy.write-lock")):
                planned = write.plan(_inputs(args, dirs))
                _commit(dirs.main, planned)
    except Timeout:  # OSError の子なので、ロックのファイルを作れないときより先に見る
        return result.emit(result.stopped(TOOL, "check_failed", "別の実行が設定を書いています。終わってから打ち直してください"))
    except OSError as exc:  # ロックのファイルを作れない（git の共通ディレクトリに書けない）など
        return result.emit(result.stopped(TOOL, "check_failed", f"設定を書けない: {exc}"))
    except jsonfile.JsonFileError as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"既存の設定が壊れている（作り直さない）: {exc}。直してから打ち直す"))
    except (check.CheckError, write.WriteError) as exc:
        return result.emit(result.stopped(TOOL, "check_failed", str(exc)))
    return result.emit(_report_write(planned, args.dry_run))


def _print_write(planned: write.Plan, rows: list[dict[str, Any]], dry_run: bool) -> None:
    verb = "書く（--dry-run）" if dry_run else "書いた"
    for f in planned.files:
        if f.summary:  # 解析の記録の側のファイルは差分でなく要約の 1 行だけを出す
            print(f"{verb}: {f.path}（{f.summary}）")
            continue
        print(f"{verb}: {f.path}")
        print(write.unified(f.name, f.old, f.new))
    if not planned.files:
        print("書くものが無い（設定は解析と同じ）")
    print("項目ごとの出所:")
    for row in rows:
        if row["kind"] not in ("note", "ignored"):
            print(write.row_line(row))
    for row in rows:
        if row["kind"] == "note":
            print(f"案内: {row['text']}")
        elif row["kind"] == "ignored":
            print(f"無視した回答: {row['key']}（質問でない）")
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


def _schema(args: argparse.Namespace) -> int:
    text = write.dumps(contracts.load_json(check.SCHEMA))
    if not args.out:
        print(text, end="")
        return result.emit(result.result(TOOL, result.OK, "設定の JSON Schema を出した"))
    try:
        signals.replace_text(Path(args.out), text)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"--out を書けない: {exc}"))
    return result.emit(
        result.result(TOOL, result.OK, f"設定の JSON Schema を書いた: {args.out}", items=[{"kind": "schema", "reason": args.out}])
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description=__doc__.splitlines()[0] if __doc__ else None)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="設定が新しいかを判定する（0 = 新しい / 3 = 無い・古い / 2 = 壊れている / 1 = 判定できない）")
    c.add_argument("--force", action="store_true", help="新しくても古いと判定する（手動の再解析）")
    c.add_argument("--root", default=".", help="リポジトリの作業場所")
    c.set_defaults(handler=_check)
    m = sub.add_parser("measure", help="リポジトリと CI を測り、測定の結果を --out へ書く")
    m.add_argument("--out", required=True, help="測定の結果の保存先（リポジトリの外の一時ディレクトリ）")
    m.add_argument("--budget", type=float, help="締め切りの秒（省けば上限の設定の project.measure）")
    m.add_argument("--root", default=".", help="リポジトリの作業場所")
    m.set_defaults(handler=_measure)
    w = sub.add_parser("write", help="測定の結果と回答から設定を書き出す")
    w.add_argument("--measure", required=True, help="ndf project measure の測定の結果")
    w.add_argument("--answers", help="質問の項目への回答（キー → {value} か {unknown}）")
    w.add_argument("--dry-run", action="store_true", help="書かずに差分だけを出す")
    w.add_argument("--root", default=".", help="リポジトリの作業場所")
    w.set_defaults(handler=_write)
    b = sub.add_parser("branch-strategy", help="ブランチ戦略の設定（branch_strategy）を作る・測る・書く・移す・消す")
    b.add_argument("verb", choices=tuple(strategy_cli.verbs()), help="init・measure・write・migrate・remove")
    b.add_argument("--kind", choices=bw.kinds(), help="代表のリポジトリの種類（init は必須。measure・write は候補の先頭にする）")
    b.add_argument("--answers", help="質問への回答（セクションの JSON Pointer → 値）")
    b.add_argument("--result", help="write の基盤にする measure の結果（省けば今のセクション、無ければ測り直す）")
    b.add_argument("--digest", help="示した差分のハッシュ。合うときだけ書く")
    b.add_argument("--dry-run", action="store_true", help="init で書かずに差分とハッシュだけを示す")
    b.add_argument("--root", default=".", help="リポジトリの作業場所")
    b.set_defaults(handler=strategy_cli.handle)
    s = sub.add_parser("schema", help="設定の JSON Schema を出す")
    s.add_argument("--out", help="書く先。省けば標準出力")
    s.set_defaults(handler=_schema)
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)
