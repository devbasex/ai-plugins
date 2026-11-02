"""upkeep_research.py: ニーズとシーズの調査の項目と、待ち時間の調査（#2315 の決定 7・9・15）。

    upkeep.py research plan [--since <前の見直しの at>]
    upkeep.py research run --until <完了のファイル> [--queue <queue の一覧>] [--max N] [--max-items N] [--lap N] [--loop L]

plan: 材料の宣言（`.ndf/project.json` の `materials`）から調査の項目を並べ、記録の無い項目と `--since` より前に調べた
  古い項目を `todo` に、宣言の無い側面を `skipped_aspects`（材料なし（未宣言））に返す。宣言が無ければすべての側面を飛ばす。
run: 見直しの持ち主になり、完了のファイルが出るまで、並列の上限に空きがあるときだけ 1 項目ずつ `claude -p` で調べる。
  出典と調べた日時がそろった結果だけを `.ndf/backlog/research/<側面>--<対象>.json` に書く（I7）。Issue は書かない。
  利用の記録は `aggregate_only: true` の宣言の行のコマンドだけをスクリプトが打ち、`mask` と `secret_redact` を通した
  出力だけを渡す（I8。調査の supervisor は読み先へ自分でつながない）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import clock
import jsonio
import secret_redact
from step_result import EXIT_GATE, EXIT_UNREADABLE, StepError, emit, git_root, result
import upkeep_depth as D
import upkeep_owner as O

TOOL = "backlog-refinement"
UNDECLARED = "材料なし（未宣言）"
ASPECTS = (
    "needs.usage",
    "needs.market",
    "seeds.library",
    "seeds.alternatives",
    "seeds.environment",
    "method.unused",
    "method.hotspot",
    "method.failures",
)
# 項目ごとに調べる観点。seeds.library と seeds.alternatives は 1 つの宣言の行から 2 つの項目になる（受け入れ条件 14）
VIEWPOINTS = {
    "needs.usage": "宣言した利用の記録の集計値から、利用者が求めている変更（よく使われる・離脱の多い・失敗の多い機能）",
    "needs.market": "比べる相手の製品の公開の情報（変更履歴・料金・機能の一覧）から、このプロダクトに無く求められている機能",
    "seeds.library": "使っているものの変化（新しい版の機能・非推奨・保守の停止・脆弱性・ライセンスの変更）",
    "seeds.alternatives": "競合（置き換え先の候補）と比べた観点（保守の状況・性能・ライセンス・移行の費用）。自前の実装を置き換えられるか",
    "seeds.environment": "法令・業界標準・ブラウザや OS・配布先の基盤の変化で、このプロダクトに影響するもの",
    "method.unused": "宣言した利用の記録から、利用率の低い機能（廃止の候補）",
    "method.hotspot": "git の履歴で変更の多いファイルのうち複雑なもの（品質の課題を壊れやすい場所から選ぶ）",
    "method.failures": "開発ループの記録（止まった理由・人へ戻した承認ゲート・同じ失敗）を束ねた共通の原因",
}
RESULT_CONTRACT = """出力は次の JSON の 1 つだけ。前後に文を書かない。出典の無い見つけたことは書かない。
{"sources": [{"kind": "url|path|command", "value": "..."}], "findings": [{"title": "...", "summary": "...", "kind": "needs|seeds|prune|quality"}]}"""
SYSTEM = (
    "あなたは NDF の調査の supervisor である。渡した 1 つの調査の項目だけを調べる。外部へ書き込まない・Issue を作らない・"
    "人へ問わない。秘密と個人情報を書かない。"
)
POLL = 30
FAIL_LIMIT = 3


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "_"


def item_path(root, item_id: str) -> Path:
    aspect, _, target = item_id.partition(":")
    return D.backlog_dir(root) / "research" / f"{aspect}--{_slug(target)}.json"


def items_of(mat: dict | None) -> tuple[list[dict], list[dict]]:
    """(調査の項目, 飛ばした側面)。項目は {id, aspect, target, decl}。"""
    if mat is None:
        return [], [{"aspect": a, "reason": UNDECLARED} for a in ASPECTS]
    items, skipped = [], []
    needs, seeds = mat.get("needs") or {}, mat.get("seeds") or {}

    def add(aspect, target, decl):
        items.append({"id": f"{aspect}:{target}", "aspect": aspect, "target": target, "decl": decl})

    usage = []
    for row in needs.get("usage") or []:
        scope = row.get("scope") or {}
        if scope.get("aggregate_only") is True and row.get("command"):
            usage.append(row)
        else:
            skipped.append({"aspect": "needs.usage", "reason": f"{row.get('name')}: aggregate_only が true でない行は読まない（個票を読まない）"})
    for row in usage:
        add("needs.usage", row.get("name") or row["command"], row)
    for row in needs.get("market") or []:
        add("needs.market", row.get("name") or row.get("url") or "?", row)
    for row in seeds.get("libraries") or []:
        add("seeds.library", row.get("name") or "?", row)
        add("seeds.alternatives", row.get("name") or "?", row)
    for row in seeds.get("environment") or []:
        add("seeds.environment", row.get("name") or row.get("url") or "?", row)
    if usage:
        add("method.unused", "usage", {"usage": [u.get("name") for u in usage]})
    add("method.hotspot", "git", {})
    add("method.failures", "loop", {"failures": mat.get("failures")})
    have = {i["aspect"] for i in items} | {s["aspect"] for s in skipped}
    for a in ASPECTS:
        if a not in have:
            skipped.append({"aspect": a, "reason": UNDECLARED})
    return items, skipped


def _epoch(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def plan(root, since: str | None) -> dict:
    items, skipped = items_of(D.materials(root))
    cut = _epoch(since)
    todo, fresh = [], []
    for it in items:
        rec = jsonio.read(item_path(root, it["id"]), missing=None, broken=None, want=dict)
        checked = _epoch((rec or {}).get("checked_at"))
        if checked is None or (cut is not None and checked < cut):
            todo.append(it["id"])
        else:
            fresh.append(it["id"])
    return {"items": [i["id"] for i in items], "todo": todo, "fresh": fresh, "skipped_aspects": skipped, "by_id": {i["id"]: i for i in items}}


def masked_usage(row: dict, root, timeout: float) -> str:
    """宣言した利用の記録のコマンドを打ち、`mask` と `secret_redact` を通した出力を返す（I8）。"""
    scope = row.get("scope") or {}
    if scope.get("aggregate_only") is not True:
        raise StepError(f"{row.get('name')}: aggregate_only が true でない行は読まない", EXIT_UNREADABLE)
    env_days = {"NDF_USAGE_DAYS": str(scope.get("days") or "")}
    p = subprocess.run(
        row["command"], shell=True, cwd=str(root), capture_output=True, text=True, timeout=timeout, env={**os.environ, **env_days}
    )
    out = p.stdout
    for pat in scope.get("mask") or []:
        out = re.sub(pat, "***", out)
    return secret_redact.redact_output(out, max_lines=400, max_chars=20000)


def prompt_of(it: dict, product: dict | None, extra: str = "") -> str:
    decl = {k: v for k, v in (it.get("decl") or {}).items() if k != "command"}
    parts = [
        f"# 調査の項目: {it['id']}",
        f"観点: {VIEWPOINTS[it['aspect']]}",
        f"宣言: {json.dumps(decl, ensure_ascii=False)}",
    ]
    if product:
        parts.append(f"プロダクト: {json.dumps(product, ensure_ascii=False)}")
    if extra:
        parts.append("# 宣言のコマンドの出力（集計値・マスク済み）\n" + extra)
    parts.append(RESULT_CONTRACT)
    return "\n\n".join(parts) + "\n"


def call_llm(root, prompt: str, timeout: int, tick) -> dict:
    """claude -p を 1 回呼ぶ（読むだけの Tool）。{ok, data, cost, seconds}。テストは差し替える。"""
    import deps

    deps.require("procs", "locks")
    import supervise_lib  # noqa: F401  lib/ を sys.path へ足す
    from supervise_lib.claude import call_claude

    res = call_claude(SYSTEM, prompt, "Read,Grep,Glob,WebFetch,WebSearch,Bash(git log:*)", str(root), timeout, tick=tick, caller={"source": TOOL, "kind": "research"})
    data = None
    m = re.search(r"\{.*\}", str(res.get("text") or ""), re.S)
    if m:
        try:
            data = json.loads(m.group(0))
        except ValueError:
            data = None
    return {"ok": bool(res.get("ok")), "data": data, "cost": float(res.get("cost") or 0), "seconds": float(res.get("seconds") or 0)}


def valid_record(data) -> bool:
    """出典 1 つ以上と見つけたことの配列を持つか（I7）。"""
    if not isinstance(data, dict):
        return False
    srcs = data.get("sources")
    ok_src = isinstance(srcs, list) and srcs and all(isinstance(s, dict) and s.get("kind") in ("url", "path", "command") and s.get("value") for s in srcs)
    return bool(ok_src) and isinstance(data.get("findings"), list)


def running_plans(listing: str | None) -> int:
    """queue の一覧で、状態ディレクトリがあり報告の無いプランの数（走っている本数）。"""
    if not listing:
        return 0
    got = jsonio.read(listing, missing={}, broken={}, want=dict)
    n = 0
    for p in got.get("plans") or []:
        st = Path(p).parent / (Path(p).stem + "-state")
        if st.exists() and not (st / "report.md").exists():
            n += 1
    return n


def free_slot(listing: str | None, max_: int) -> bool:
    """調査は並列の上限に数える（受け入れ条件 6a）。走っている本数 + 調査 1 本が上限以下のときだけ空きがある。"""
    return running_plans(listing) + 1 <= max_


def research_one(root, it: dict, cfg: dict, product, run: str, loop, lap, tick) -> tuple[dict | None, dict]:
    """1 項目を調べる。(書いた記録か None, 使用量)。上限を超えた・出典の無い結果は書かない。"""
    lim = cfg["research"]
    extra = masked_usage(it["decl"], root, lim["item_seconds"]) if it["aspect"] == "needs.usage" else ""
    got = call_llm(root, prompt_of(it, product, extra), int(lim["item_seconds"]), tick)
    usage = {"cost": got["cost"], "seconds": got["seconds"]}
    if not got["ok"] or got["cost"] > lim["item_usd"] or got["seconds"] > lim["item_seconds"] or not valid_record(got["data"]):
        return None, usage
    rec = {
        "id": it["id"],
        "checked_at": clock.now_iso("utc"),
        "sources": got["data"]["sources"],
        "findings": [f for f in got["data"]["findings"] if isinstance(f, dict)],
        **usage,
        "lap": lap,
        "by": {"run": run, "loop": loop},
    }
    jsonio.write_atomic(item_path(root, it["id"]), rec, indent=1)
    return rec, usage


def run(root, until: str, listing: str | None, max_: int, max_items: int, loop=None, lap=None, pid=None, sleep=time.sleep) -> dict:
    """待ち時間の調査（E3・E4）。戻り値は metrics。"""
    cfg = D.settings(root)
    run_id, taken, cur = O.claim(root, "research", loop, pid)
    if run_id is None:
        return {"reason": "not_owner", "owner": cur, "items": []}
    pl = plan(root, None)
    mat = D.materials(root) or {}
    done, cost, secs, fails, why = [], 0.0, 0.0, 0, "項目が無い"
    beat = {"at": time.time()}

    def tick(*_):
        if time.time() - beat["at"] >= cfg["owner"]["beat"]:
            O.touch(root, run_id)
            beat["at"] = time.time()

    try:
        for item_id in pl["todo"]:
            if len(done) >= max_items:
                why = f"1 回の上限 {max_items} 項目に当たった"
                break
            while not Path(until).exists() and not free_slot(listing, max_):
                tick()
                sleep(POLL)
            if Path(until).exists():  # 待っていたプランが終わったら新しい項目を始めない（I13）
                why = "待っていたプランが終わった"
                break
            if O.touch(root, run_id, item_id):
                why = "持ち主でなくなった"
                break
            try:
                rec, usage = research_one(root, pl["by_id"][item_id], cfg, mat.get("product"), run_id, loop, lap, tick)
            except (StepError, OSError, subprocess.SubprocessError):
                rec, usage = None, {"cost": 0.0, "seconds": 0.0}
            cost += usage["cost"]
            secs += usage["seconds"]
            if rec is None:
                fails += 1
                if fails >= FAIL_LIMIT:
                    why = f"項目の失敗が {FAIL_LIMIT} 回続いた"
                    break
                continue
            fails = 0
            done.append(item_id)
        else:
            why = "調べる項目が尽きた" if pl["todo"] else why
    finally:
        O.release(root, run_id)
    return {"run": run_id, "taken_from": taken, "items": done, "cost": round(cost, 6), "seconds": round(secs, 3), "lap": lap, "stopped": why}


def cmd_plan(a):
    root = git_root(a.root)
    got = plan(root, a.since)
    got.pop("by_id")
    emit(result(TOOL, "ok", f"調べる項目 {len(got['todo'])} 件・新しい項目 {len(got['fresh'])} 件・飛ばした側面 {len(got['skipped_aspects'])} 件", [], got))


def cmd_run(a):
    root = git_root(a.root)
    m = run(root, a.until, a.queue, a.max, a.max_items, a.loop, a.lap, a.pid)
    if m.get("reason") == "not_owner":
        emit(result(TOOL, "gate", "見直しの持ち主が別にいるため調べない", [], m, next="何もしないで終える"), EXIT_GATE)
    emit(result(TOOL, "ok", f"調べた項目 {len(m['items'])} 件（{m['stopped']}）・{m['cost']} ドル・{m['seconds']} 秒", [], m))


def research_command(script: Path, root: str, until: str, listing: str | None, lap: int | None, loop: str | None) -> str:
    """`loop sprint` が返す、待ちと並べて背景で打つ 1 行。"""
    args = ["python3", str(script), "research", "run", "--root", root, "--until", until]
    if listing:
        args += ["--queue", listing]
    if lap is not None:
        args += ["--lap", str(lap)]
    if loop:
        args += ["--loop", loop]
    return shlex.join(args)


def add_parsers(sub, common) -> None:
    rp = sub.add_parser("research").add_subparsers(dest="research_cmd", required=True)
    p = rp.add_parser("plan", parents=[common])
    p.add_argument("--since", default=None, help="前の見直しの at（これより前に調べた項目を古いとみなす）")
    p.set_defaults(func=cmd_plan)
    r = rp.add_parser("run", parents=[common])
    r.add_argument("--until", required=True, help="待っているプランの完了のファイル（出たら新しい項目を始めない）")
    r.add_argument("--queue", default=None, help="queue の一覧（<done>.plans.json）。走っている本数を数える")
    r.add_argument("--max", type=int, default=6, help="並列の上限（調査 1 本を含めて数える）")
    r.add_argument("--max-items", type=int, default=3)
    r.add_argument("--lap", type=int, default=None)
    r.add_argument("--loop", default=None)
    r.add_argument("--pid", type=int, default=None)
    r.set_defaults(func=cmd_run)
