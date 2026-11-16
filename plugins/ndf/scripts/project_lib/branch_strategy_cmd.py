"""branch_strategy_cmd.py: 解析の `project-decl.py branch-strategy` の本体（#3672 の決定 4）。

ブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）を作る・測る・書く・移す・消す唯一の入口で、結果は
作成の結果（`status`・`kind`・`values`・`evidence`・`questions`・`diff`・`digest`・`failed`）の 1 行の JSON にする。
終了コードは ok 0・ask 3・stopped 1・読めない 2。使う側は `deps.require("jsonschema", "locks")` を先に呼ぶ。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import branch_strategy as bs
import branch_strategy_legacy as legacy
import jsonio
import proc
import repo
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE

from project_lib import branch_strategy_measure as bsm
from project_lib import branch_strategy_migrate as bmig
from project_lib import branch_strategy_write as bw

BS_EXIT = {"ok": 0, "ask": EXIT_PRECONDITION, "stopped": 1}


def _bs_emit(out: dict) -> int:
    out.setdefault("kind", None)
    for k in ("values", "evidence", "questions", "diff", "failed", "warnings"):
        out.setdefault(k, {} if k == "values" else [])
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return BS_EXIT[out["status"]]


def _bs_facts(root: Path) -> dict:
    """`init` が既定の行へ入れる測った名前（既定ブランチ・プラグイン・CD と開発版のワークフロー・merge queue）。"""
    import gh_graphql

    s = bsm.Samples()
    bsm.local_facts(root, s)
    head = proc.git_out(root, "symbolic-ref", "--short", "HEAD")
    production = repo.default_branch(root) or (head or "").strip() or None
    facts = {"production": production}
    plugin = bsm.single_plugin(s)
    if plugin:
        name, runtimes = plugin
        facts.update(plugin=name, runtimes=runtimes)
    cd = bsm.cd_workflows(s)
    facts["workflow"] = cd[0] if cd else None
    slug = repo.owner_repo(root)
    facts["merge_queue"] = gh_graphql.merge_queue_enabled(slug, production) if slug and production else None
    return facts


def _bs_commit(root: Path, plan, digest: str | None, base: dict, write: bool = False) -> int:
    """`digest` を渡すか `write` が真なら書き、どちらも無ければ差分とハッシュを示すだけにする。"""
    if plan.failed:
        return _bs_emit({**base, "status": "stopped", "failed": plan.failed})
    if digest is None and not write:
        return _bs_emit({**base, "status": "ok", "diff": plan.diff, "digest": plan.digest})
    try:
        done = bw.commit_plan(root, plan, digest)
    except bw.WriteError as e:
        return _bs_emit({**base, "status": "stopped", "failed": [{"rule": "write", "pointer": "", "reason": e.reason}]})
    return _bs_emit({**base, "status": "ok", "diff": done.diff, "digest": done.digest})


def run_branch_strategy(a, root: Path) -> int:
    """動詞（`init`・`measure`・`write`・`migrate`・`remove`）を受けて組み、作成の結果を 1 行の JSON で出す。"""
    presets = bw.load_presets()
    try:
        if a.verb == "init":
            state, seen = bsm.classify_repo(root, presets=presets)
            base = {"kind": a.kind, "evidence": seen}
            if state != "new":
                return _bs_emit(
                    {
                        **base,
                        "status": "stopped",
                        "failed": [
                            {
                                "rule": "existing",
                                "pointer": "",
                                "reason": "既存のリポジトリです。`branch-strategy measure` で測ってください",
                            }
                        ],
                    }
                )
            if not a.kind:
                return _bs_emit(
                    {
                        **base,
                        "status": "ask",
                        "questions": [{"pointer": "/kind", "why": "no_kind", "candidates": list(bw.KINDS), "evidence": []}],
                    }
                )
            facts = _bs_facts(root)
            section, questions = bw.init_values(a.kind, facts, presets)
            base = {"kind": a.kind, "values": section, "evidence": bw.evidence_of(section, facts)}
            if questions:
                return _bs_emit({**base, "status": "ask", "questions": questions})
            return _bs_commit(root, bw.plan_write(root, section), a.digest, base, write=not a.dry_run)
        if a.verb == "measure":
            return _bs_emit(bsm.measure_strategy(root, prefer=a.kind))
        if a.verb == "write":
            prior = jsonio.read(a.result, want=dict) if a.result else bsm.measure_strategy(root, prefer=a.kind)
            answers = jsonio.read(a.answers, want=dict) if a.answers else {}
            res = bw.apply_answers(prior, answers)
            if res["status"] == "ask":
                return _bs_emit(res)
            base = {k: res.get(k) for k in ("kind", "values", "evidence")}
            base["kind"] = res["values"].get("kind")
            return _bs_commit(root, bw.plan_write(root, res["values"]), a.digest, base)
        if a.verb == "migrate":
            plan, questions = bmig.plan_migration(root, legacy.load_moves())
            if questions:
                return _bs_emit({"status": "ask", "questions": questions})
            return _bs_commit(root, plan, a.digest, {})
        return _bs_commit(root, bw.plan_remove(root), a.digest, {})
    except (bs.StrategyError, bw.WriteError, legacy.LegacyTableError, jsonio.JsonReadError) as e:
        sys.stderr.write(f"{getattr(e, 'reason', e)}\n")
        return getattr(e, "exit_code", EXIT_UNREADABLE)
