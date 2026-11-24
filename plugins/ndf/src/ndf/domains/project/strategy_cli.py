"""サブコマンド `ndf project branch-strategy` の動詞（init・measure・write・migrate・remove）。

セクション `branch_strategy` の作成の結果（形は contracts/schemas/io/branch-strategy.result.schema.json。
10 系の `project-decl.py branch-strategy` と同じ形）を 1 行の JSON で出す。終了コードは ok 0・ask 3・stopped 1・読めない 2。
書く動詞（init・write・migrate・remove）は `--digest` を渡すと示した差分のハッシュが合うときだけ書き、渡さなければ差分と
ハッシュを示すだけにする（init は `--dry-run` が無ければ書く）。引数の組み立ては `cli.py` の `_parser` が持つ。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ndf import contracts
from ndf.contracts import exit_codes, limits
from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.domains.project import check, strategy_measure, strategy_migrate, strategy_rules
from ndf.domains.project import strategy_write as bw
from ndf.domains.project.items import obj, seq
from ndf.domains.records import jsonfile

LISTS = ("evidence", "questions", "diff", "failed", "warnings")


def _emit(out: dict[str, Any]) -> int:
    """作成の結果を 1 行の JSON で出し、終了コードを返す。"""
    out.setdefault("kind", None)
    out.setdefault("values", {})
    for k in LISTS:
        out.setdefault(k, [])
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    # ask は結果のコントラクトの status でない（前提が無い・正常な否定の結果の終了コードで返す）
    status = out["status"]
    return exit_codes.code_of("precondition") if status == "ask" else exit_codes.first_code(status)


def _commit(root: Path, plan: bw.WritePlan, digest: Optional[str], base: dict[str, Any], write_now: bool = False) -> int:
    """`digest` を渡すか `write_now` が真なら書き、どちらも無ければ差分とハッシュを示すだけにする。"""
    if plan.failed:
        return _emit({**base, "status": "stopped", "failed": plan.failed})
    if digest is None and not write_now:
        return _emit({**base, "status": "ok", "diff": plan.diff, "digest": plan.digest})
    try:
        done = bw.commit_plan(root, plan, digest)
    except bw.WriteError as exc:
        return _emit({**base, "status": "stopped", "failed": [{"rule": "write", "pointer": "", "reason": exc.reason}]})
    return _emit({**base, "status": "ok", "diff": done.diff, "digest": done.digest})


def _api(root: Path) -> strategy_rules.Api:
    return strategy_measure.default_api(root, time.monotonic() + limits.seconds(strategy_measure.MEASURE_LIMIT))


def _init(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """新規のリポジトリだけに、種類の既定の行へ測った名前を入れたセクションを書く。"""
    api = _api(root)
    state, seen = strategy_measure.classify_repo(root, api, presets)
    base: dict[str, Any] = {"kind": args.kind, "evidence": seen}
    if state != "new":
        reason = f"既存のリポジトリです。`{legacy.COMMAND['11']} measure` で測ってください"
        return _emit({**base, "status": "stopped", "failed": [{"rule": "existing", "pointer": "", "reason": reason}]})
    if not args.kind:
        question = {"pointer": "/kind", "why": "no_kind", "candidates": list(bw.kinds()), "evidence": []}
        return _emit({**base, "status": "ask", "questions": [question]})
    facts = strategy_measure.init_facts(root, api)
    section, questions = bw.init_values(args.kind, facts, presets)
    base = {"kind": args.kind, "values": section, "evidence": bw.evidence_of(section, facts)}
    if questions:
        return _emit({**base, "status": "ask", "questions": questions})
    return _commit(root, bw.plan_write(root, section), args.digest, base, write_now=not args.dry_run)


def _measure(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """既存のリポジトリを測った結果を出す。書かない。"""
    return _emit(strategy_measure.measure_strategy(root, presets=presets, prefer=args.kind))


def _declared_section(root: Path) -> Optional[dict[str, Any]]:
    """読めるセクションがあれば、それを確定した値として持つ測った結果の形。無い・読めなければ None（測り直す）。"""
    try:
        section = bs.load(root).section
    except bs.StrategyError:  # noqa: NDF401 ファイル: 読めないセクションは基盤にせず、測り直す
        return None
    return {"status": "ok", "kind": section.get("kind"), "values": section, "evidence": [], "questions": []}


def _write(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """測った結果に回答を入れて書く。`--result` が無ければ今のセクションを基盤にし（回答したキーだけを変え、利用者が決めた
    merge.actor・verify・reviews を種類の既定へ戻さない）、セクションも無ければ測り直す。"""
    prior = (
        jsonfile.read_object(Path(args.result))
        if args.result
        else _declared_section(root) or strategy_measure.measure_strategy(root, presets=presets, prefer=args.kind)
    )
    answers = jsonfile.read_object(Path(args.answers)) if args.answers else {}
    res = bw.apply_answers(prior, answers)
    if res["status"] == "ok" and "approval" not in obj(prior.get("values")):
        # 質問を返した結果は確定した値だけを持つ。回答を足した後に種類の既定から全体を組む。
        # 完全なセクションかは回答を入れる前の値で見る（`/approval/via` の回答が `approval` を作っても補完を飛ばさない）
        res = strategy_measure.complete_answers(prior, answers, presets)
    if res["status"] == "ask":
        return _emit(res)
    base = {"kind": res["values"].get("kind"), "values": res["values"], "evidence": seq(res.get("evidence"))}
    return _commit(root, bw.plan_write(root, res["values"]), args.digest, base)


def _migrate(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """旧い 4 か所の設定をセクションへ移す。`--answers` は質問への回答（セクションの pointer → 値）で、移した値を上書きする。"""
    answers = jsonfile.read_object(Path(args.answers)) if args.answers else {}
    plan, questions = strategy_migrate.plan_migration(root, legacy.load_moves(), answers)
    if questions:
        return _emit({"status": "ask", "questions": questions})
    return _commit(root, plan, args.digest, {})


def _remove(args: argparse.Namespace, root: Path, presets: dict[str, Any]) -> int:
    """セクションを消す。"""
    return _commit(root, bw.plan_remove(root), args.digest, {})


Verb = Callable[[argparse.Namespace, Path, dict[str, Any]], int]


def verbs() -> dict[str, Verb]:
    """動詞 → 本体。動詞の選択肢は argparse（choices）が閉じる。"""
    return {"init": _init, "measure": _measure, "write": _write, "migrate": _migrate, "remove": _remove}


def handle(args: argparse.Namespace) -> int:
    """動詞（`init`・`measure`・`write`・`migrate`・`remove`）を受けて組み、作成の結果を 1 行の JSON で出す。"""
    try:
        repo, _ = check.repo_dirs(Path(args.root).resolve())
        return verbs()[args.verb](args, repo.cwd, bw.load_presets())
    except check.CheckError as exc:
        sys.stderr.write(f"{exc}\n")
        return exit_codes.code_of(bs.UNREADABLE)
    except (bs.StrategyError, bw.WriteError) as exc:
        sys.stderr.write(f"{exc.reason}\n")
        return exit_codes.code_of(exc.exit_id)
    except (contracts.ContractError, jsonfile.JsonFileError) as exc:
        sys.stderr.write(f"{exc}\n")
        return exit_codes.code_of(bs.UNREADABLE)
