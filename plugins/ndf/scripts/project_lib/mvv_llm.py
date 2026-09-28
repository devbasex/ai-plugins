"""mvv_llm.py: プロジェクト MVV の LLM の呼び出し（候補の生成・照合）と照合の記録（`project-mvv.py`・#1366）。

LLM は `supervise_lib/claude.py` の `call_claude` を Tool なしで 1 回呼ぶ（NDF_SUPERVISE_CLAUDE で差し替えられる）。
呼び出しごとに使用量の帳簿へ 1 行を足す。照合は本文を書き換えず、判定と箇所を `project-mvv.jsonl` へ残すだけである（I14）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import clock
import project_mvv as pm
import project_mvv_signals as pms

TOOL = "project-mvv"
SCRIPTS = Path(__file__).resolve().parents[1]

VET_VERDICTS = ("follow", "suspect", "unknown")

VET_SYSTEM = """あなたは MVV の照合者である。Tool は無い。
渡された本文（プロジェクト MVV の候補・改訂案、またはスプリント MVV）が、NDF の共通原則と（あれば）承認済みのプロジェクト MVV に
従うかを判定する。
- 共通原則の必ず人の承認が要る操作（C1〜）のどれかを承認なしで行えると書く、または優先順位を変える・入れ替えるなら suspect。
  locations の item に C の番号か priority を書く
- スプリント MVV がプロジェクト MVV のレッドライン（P の番号）を緩める・Value を打ち消すなら suspect。item にその番号を書く
- 原則に反する（人を害する・人の発展を妨げる）なら suspect
- 反する所が無ければ follow。材料から決められなければ unknown（迷ったら unknown）
- 本文を書き換えない。判定と箇所を返すだけである
出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|suspect|unknown", "locations": [{"item": "C4|priority|Value 3|P1", "reason": "..."}]}"""


def call_llm_json(system: str, prompt: str, root: Path, kind: str) -> tuple[dict | None, str, dict]:
    """最小構成の claude -p を 1 回呼ぶ（Tool なし）。(JSON, 生の文, 使用量)。"""
    import supervise_lib  # noqa: F401  lib/ を sys.path へ足す
    import usage_ledger
    from supervise_lib.claude import call_claude

    res = call_claude(system, prompt, None, str(root), 900)
    usage = {"cost_usd": res.get("cost"), "seconds": res.get("seconds")}
    usage_ledger.append_safely(
        str(root),
        usage_ledger.UsageRecord(
            source=TOOL,
            kind=kind,
            usage=res.get("usage") or {},
            model_usage=res.get("model_usage"),
            cost_usd=res.get("cost"),
            turns=res.get("turns"),
            seconds=res.get("seconds"),
            session_id=res.get("session"),
        ),
    )
    text = str(res.get("text") or "")
    if not res.get("ok"):
        return None, text[-500:], usage
    return pm.json_object(text), text[-500:], usage


def vet_prompt(kind: str, body: str, mvv: pm.ProjectMvv) -> str:
    what = {"candidate": "プロジェクト MVV の候補", "revision": "プロジェクト MVV の改訂案", "sprint": "スプリント MVV"}[kind]
    parts = [pm.principles()]
    if kind in ("sprint", "revision"):
        parts.append(pm.project_part(mvv))
    parts += [pm.contract(), f"# 照合する本文（{what}）", body.strip()]
    return "\n\n".join(parts) + "\n"


def vet_body(root: Path, body: str, kind: str) -> tuple[dict, dict]:
    """(照合の記録, 使用量)。記録は project-mvv.jsonl へ足す。本文は書き換えない（I14）。"""
    mvv = pm.load_mvv(root)
    rec = {
        "at": clock.now_iso("utc"),
        "repo": pm.mvv_repo_key(root),
        "kind": kind,
        "sha256": pm.sha256_text(body),
        "project_sha256": mvv.sha256 if mvv.approved else "",
        "project_mvv": pm.record(mvv),
        "verdict": "unreadable",
        "locations": [],
    }
    usage: dict = {}
    if kind in ("candidate", "revision"):
        probs = pm.shape_problems(body)
        if probs:
            rec.update(verdict="suspect", locations=probs, machine=True)
    if kind == "revision" and not mvv.approved and rec["verdict"] == "unreadable":
        rec["locations"] = [
            {"item": "project_mvv", "reason": f"改訂には承認済みのプロジェクト MVV が要る（今は {pm.STATUS_LABEL[mvv.status]}）"}
        ]
    elif rec["verdict"] == "unreadable":
        data, raw, usage = call_llm_json(VET_SYSTEM, vet_prompt(kind, body, mvv), root, "mvv-vet")
        if data is None or data.get("verdict") not in VET_VERDICTS:
            rec.update(raw=raw)
        else:
            locs = [x for x in data.get("locations") or [] if isinstance(x, dict)]
            rec.update(verdict=data["verdict"], locations=locs)
        rec.update(cost_usd=usage.get("cost_usd"), seconds=usage.get("seconds"))
    pms.append_jsonl(pm.vet_log_path(), rec)
    return rec, usage


def escape_events(root: Path) -> list[dict]:
    """流出不具合の記録（check-trigger.py の `kind: escape`）。読めなければ空。"""
    try:
        spec = importlib.util.spec_from_file_location("ndf_check_trigger", SCRIPTS / "check-trigger.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return [e for e in mod.read_events(root) if e.get("kind") == "escape"]
    except Exception:  # noqa: BLE001  集計の材料が欠けても止めない
        return []
