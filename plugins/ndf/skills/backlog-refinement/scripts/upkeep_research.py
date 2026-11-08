"""upkeep_research.py: ニーズとシーズの調査の項目と、待ち時間の調査（#2315 の決定 7・9・15）。

    upkeep.py research plan [--since <前の見直しの at>]
    upkeep.py research run --until <完了のファイル> [--queue <queue の一覧>] [--max N] [--max-items N] [--lap N] [--loop L]
    upkeep.py research run --run <持ち主の run> --since <前の見直しの at> [--max-items N]   # 見直し・大整理の中で

plan: 材料の宣言（`.ndf/project.json` の `materials`）から調査の項目を並べ、記録の無い項目と `--since` より前に調べた
  古い項目を `todo` に、宣言の無い側面を `skipped_aspects`（材料なし（未宣言））に返す。宣言が無ければすべての側面を飛ばす。
run: 見直しの持ち主になり、完了のファイルが出るまで、並列の上限に空きがあるときだけ 1 項目ずつ `claude -p` で調べる。
  出典と調べた日時がそろった結果だけを `.ndf/backlog/research/<側面>--<対象>.json` に書く（I7）。Issue は書かない。
  利用の記録は `aggregate_only: true` の宣言の行のコマンドだけをスクリプトが打ち、`mask` と `secret_redact` を通した
  出力だけを渡す（I8。調査の supervisor は読み先へ自分でつながない）。`--run` を渡すと見直し・大整理の持ち主の実行のまま
  調べ（宣言を取り直さず解放しない）、`--until` が無ければ待たずに `--since` より古い項目と足りない項目を調べる。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
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
    "seeds.environment": "法令・業界標準・ブラウザや OS・デプロイ先の基盤の変化で、このプロダクトに影響するもの",
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
WEB_ASPECTS = ("needs.market", "seeds.library", "seeds.alternatives", "seeds.environment")
FAIL_LIMIT = 3


class GraceOver(Exception):
    """待っていたプランが終わってから research.grace 秒を過ぎた（調査中の呼び出しを止める）。"""


def _old_slug(s: str) -> str:
    """前の版のファイル名の部分。英数字以外を _ にするため、日本語の名前どうしが同じ名前になる。"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "_"


def _item_slug(s: str) -> str:
    """名前が違えば必ず違うファイル名の部分。英数字の名前は前の版のまま、それ以外は元の名前の短いハッシュを足す。"""
    slug = _old_slug(s)
    return slug if slug == s else f"{slug}-{hashlib.sha256(s.encode('utf-8')).hexdigest()[:10]}"


def item_path(root, item_id: str) -> Path:
    aspect, _, target = item_id.partition(":")
    return D.backlog_dir(root) / "research" / f"{aspect}--{_item_slug(target)}.item.json"


def read_research_item(root, item_id: str) -> dict | None:
    """項目の記録。前の版のファイル名の記録は、記録の id がその項目のときだけ読む（同じ名前に上書きされた別の項目を読まない）。"""
    rec = jsonio.read(item_path(root, item_id), missing=None, broken=None, want=dict)
    if rec is None:
        aspect, _, target = item_id.partition(":")
        old = D.backlog_dir(root) / "research" / f"{aspect}--{_old_slug(target)}.item.json"
        rec = jsonio.read(old, missing=None, broken=None, want=dict)
    return rec if rec is not None and rec.get("id", item_id) == item_id else None


def _usage_rows(needs: dict) -> tuple[list[dict], list[dict]]:
    """needs.usage の行を (使える行, 飛ばした側面) に分ける。集計だけを返す行に限って読む。"""
    usage, skipped = [], []
    for row in needs.get("usage") or []:
        scope = row.get("scope") or {}
        if scope.get("aggregate_only") is True and row.get("command"):
            usage.append(row)
        else:
            skipped.append(
                {"aspect": "needs.usage", "reason": f"{row.get('name')}: aggregate_only が true でない行は読まない（個票を読まない）"}
            )
    return usage, skipped


def _undeclared(items: list[dict], skipped: list[dict]) -> list[dict]:
    """項目にも飛ばした側面にも現れない側面を、宣言の無い側面として返す。"""
    have = {i["aspect"] for i in items} | {s["aspect"] for s in skipped}
    return [{"aspect": a, "reason": UNDECLARED} for a in ASPECTS if a not in have]


def research_items(mat: dict | None) -> tuple[list[dict], list[dict]]:
    """(調査の項目, 飛ばした側面)。項目は {id, aspect, target, decl}。"""
    if mat is None:
        return [], [{"aspect": a, "reason": UNDECLARED} for a in ASPECTS]
    items = []
    needs, seeds = mat.get("needs") or {}, mat.get("seeds") or {}

    def add(aspect, target, decl):
        items.append({"id": f"{aspect}:{target}", "aspect": aspect, "target": target, "decl": decl})

    usage, skipped = _usage_rows(needs)
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
    skipped.extend(_undeclared(items, skipped))
    return items, skipped


def plan(root, since: str | None) -> dict:
    items, skipped = research_items(D.materials(root))
    cut = clock.epoch_seconds(since)
    todo, fresh = [], []
    for it in items:
        rec = read_research_item(root, it["id"])
        checked = clock.epoch_seconds((rec or {}).get("checked_at"))
        if checked is None or (cut is not None and checked < cut):
            todo.append(it["id"])
        else:
            fresh.append(it["id"])
    return {
        "items": [i["id"] for i in items],
        "todo": todo,
        "fresh": fresh,
        "skipped_aspects": skipped,
        "by_id": {i["id"]: i for i in items},
    }


def consulted(root, run: str | None) -> dict:
    """どの材料で調べたか（報告と深さの記録の `research`）。

    `items` は材料の宣言から出た項目（`research plan` の `items` と同じ）、`researched` はその実行（`run`）が調べた項目と
    出典の数、`sources` はその出典の数の和。実行が分からなければ `researched` と `sources` は None。"""
    items, _ = research_items(D.materials(root))
    out = {"items": [i["id"] for i in items], "researched": None, "sources": None}
    if run:
        done = []
        for it in items:
            rec = read_research_item(root, it["id"]) or {}
            if (rec.get("by") or {}).get("run") == run:
                done.append({"id": it["id"], "checked_at": rec.get("checked_at"), "sources": len(rec.get("sources") or [])})
        out["researched"], out["sources"] = done, sum(x["sources"] for x in done)
    return out


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
        parts.append("# スクリプトが集めた材料（利用の記録は集計値・マスク済み）\n" + extra)
    parts.append(RESULT_CONTRACT)
    return "\n\n".join(parts) + "\n"


def tools_of(aspect: str) -> str:
    """Web を読む項目にはファイルを読む Tool を、手元を読む項目には Web の Tool を渡さない（読んだ中身を外へ出させない）。"""
    return "WebFetch,WebSearch" if aspect in WEB_ASPECTS else "Read,Grep,Glob"


def hotspots(root, limit: int = 30) -> str:
    """変更の多いファイル（直近 500 コミット）。git はスクリプトが打ち、結果だけを渡す。"""
    p = subprocess.run(
        ["git", "-C", str(root), "log", "-n", "500", "--format=", "--name-only"], capture_output=True, text=True, timeout=120
    )
    counts: dict = {}
    for f in p.stdout.splitlines():
        if f.strip():
            counts[f] = counts.get(f, 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return "\n".join(f"{n}\t{f}" for f, n in top)


def call_llm(root, prompt: str, timeout: int, tick, tools: str) -> dict:
    """claude -p を 1 回呼ぶ（読むだけの Tool）。{ok, data, cost, seconds}。テストは差し替える。"""
    # 依存（procs・locks）は upkeep.py の main が持ち主を取る前に揃える。ここで起動し直すと自分の宣言に阻まれる
    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")  # supervise_lib の親
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import supervise_lib  # noqa: F401  lib/ を sys.path へ足す
    from supervise_lib.claude import call_claude

    res = call_claude(SYSTEM, prompt, tools, str(root), timeout, tick=tick, caller={"source": TOOL, "kind": "research"})
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
    ok_src = (
        isinstance(srcs, list)
        and srcs
        and all(isinstance(s, dict) and s.get("kind") in ("url", "path", "command") and s.get("value") for s in srcs)
    )
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


def log_call(root, item_id: str, ok: bool, usage: dict, run, loop, lap) -> None:
    """呼び出し 1 回の費用を記録する。結果を捨てた呼び出しも支払った費用は予算に数える。"""
    row = {"id": item_id, "at": clock.now_iso("utc"), "ok": bool(ok), **usage, "lap": lap, "by": {"run": run, "loop": loop}}
    jsonio.append_jsonl(D.backlog_dir(root) / "research" / "calls.jsonl", row)


def research_one(root, it: dict, cfg: dict, product, run: str, loop, lap, tick) -> tuple[dict | None, dict]:
    """1 項目を調べる。(書いた記録か None, 使用量)。上限を超えた・出典の無い結果は書かない。"""
    lim = cfg["research"]
    extra = masked_usage(it["decl"], root, lim["item_seconds"]) if it["aspect"] == "needs.usage" else ""
    extra = "# 変更の多いファイル（回数・パス）\n" + hotspots(root) if it["aspect"] == "method.hotspot" else extra
    got = call_llm(root, prompt_of(it, product, extra), int(lim["item_seconds"]), tick, tools_of(it["aspect"]))
    usage = {"cost": got["cost"], "seconds": got["seconds"]}
    accepted = got["ok"] and got["cost"] <= lim["item_usd"] and got["seconds"] <= lim["item_seconds"] and valid_record(got["data"])
    log_call(root, it["id"], accepted, usage, run, loop, lap)
    if not accepted:
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
    aspect, _, target = it["id"].partition(":")
    jsonio.write_atomic(
        D.backlog_dir(root) / "research" / f"{aspect}--{_item_slug(target)}.item.json", rec, indent=1
    )  # item_path と同じ置き場
    return rec, usage


def _ticker(root, run_id, until: str | None, cfg: dict):
    """呼び出しの間に呼ぶ関数。持ち主の印を `owner.beat` 秒ごとに書き、待っていたプランの完了から `research.grace` 秒を過ぎたら GraceOver を投げる。"""
    beat = {"at": time.time(), "until_seen": None}

    def tick(*_):
        if time.time() - beat["at"] >= cfg["owner"]["beat"]:
            O.touch(root, run_id)
            beat["at"] = time.time()
        # 調査中も待っていたプランの完了を見て、research.grace 秒を過ぎた呼び出しを止める（持ち主を手放し、後の見直しへ渡す）
        if until and Path(until).exists():
            seen = beat["until_seen"] = beat["until_seen"] or time.time()
            if time.time() - seen >= float(cfg["research"].get("grace", 300)):
                raise GraceOver

    return tick


def _wait_turn(root, run_id, item_id: str, until: str | None, listing: str | None, max_: int, tick, sleep) -> str | None:
    """並列の空きを待ち、次の項目を始めてよければ None、止めるなら止まる理由を返す。"""
    while not (until and Path(until).exists()) and not free_slot(listing, max_):
        tick()
        sleep(POLL)
    if until and Path(until).exists():  # 待っていたプランが終わったら新しい項目を始めない（I13）
        return "待っていたプランが終わった"
    if O.touch(root, run_id, item_id):
        return "持ち主でなくなった"
    return None


def run_research(
    root,
    until: str | None,
    listing: str | None,
    max_: int,
    max_items: int,
    loop=None,
    lap=None,
    pid=None,
    sleep=time.sleep,
    owner: str | None = None,
    since: str | None = None,
) -> dict:
    """待ち時間の調査（E3・E4）。戻り値は metrics。

    `owner` を渡すと、見直し・大整理の持ち主の実行のまま調べる（宣言を取り直さず、終えても解放しない）。
    `until` が無ければ完了のファイルを待たない（見直しの中で足りない項目と古い項目を調べるとき）。"""
    cfg = D.settings(root)
    if owner:
        run_id, taken, cur = (None, None, O.review_owner_status(root)["owner"]) if O.touch(root, owner) else (owner, None, None)
    else:
        run_id, taken, cur = O.claim_review(root, "research", loop, pid)
    if run_id is None:
        return {"reason": "not_owner", "owner": cur, "items": []}
    pl = plan(root, since)
    mat = D.materials(root) or {}
    done, cost, secs, fails, why = [], 0.0, 0.0, 0, "項目が無い"
    tick = _ticker(root, run_id, until, cfg)
    try:
        for item_id in pl["todo"]:
            if len(done) >= max_items:
                why = f"1 回の上限 {max_items} 項目に当たった"
                break
            if stop := _wait_turn(root, run_id, item_id, until, listing, max_, tick, sleep):
                why = stop
                break
            try:
                rec, usage = research_one(root, pl["by_id"][item_id], cfg, mat.get("product"), run_id, loop, lap, tick)
            except GraceOver:
                why = "待っていたプランが終わり、猶予を過ぎた"
                break
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
        if not owner:
            O.release_review(root, run_id)
    return {
        "run": run_id,
        "taken_from": taken,
        "items": done,
        "cost": round(cost, 6),
        "seconds": round(secs, 3),
        "lap": lap,
        "stopped": why,
    }


def cmd_plan(a):
    root = git_root(a.root)
    got = plan(root, a.since)
    got.pop("by_id")
    emit(
        result(
            TOOL,
            "ok",
            f"調べる項目 {len(got['todo'])} 件・新しい項目 {len(got['fresh'])} 件・飛ばした側面 {len(got['skipped_aspects'])} 件",
            [],
            got,
        )
    )


def cmd_run(a):
    root = git_root(a.root)
    m = run_research(root, a.until, a.queue, a.max, a.max_items, a.loop, a.lap, a.pid, owner=a.run, since=a.since)
    if m.get("reason") == "not_owner":
        emit(result(TOOL, "gate", "見直しの持ち主が別にいるため調べない", [], m, next="何もしないで終える"), EXIT_GATE)
    emit(result(TOOL, "ok", f"調べた項目 {len(m['items'])} 件（{m['stopped']}）・{m['cost']} ドル・{m['seconds']} 秒", [], m))


def add_research_parsers(sub, common) -> None:
    rp = sub.add_parser("research").add_subparsers(dest="research_cmd", required=True)
    p = rp.add_parser("plan", parents=[common])
    p.add_argument("--since", default=None, help="前の見直しの at（これより前に調べた項目を古いとみなす）")
    p.set_defaults(func=cmd_plan)
    r = rp.add_parser("run", parents=[common])
    r.add_argument("--until", default=None, help="待っているプランの完了のファイル（出たら新しい項目を始めない）。無ければ待たない")
    r.add_argument("--run", default=None, help="見直し・大整理の持ち主の実行（owner claim の run）。宣言を取り直さず解放もしない")
    r.add_argument("--since", default=None, help="前の見直しの at（これより前に調べた項目を古いとみなして調べ直す）")
    r.add_argument("--queue", default=None, help="queue の一覧（<done>.plans.json）。走っている本数を数える")
    r.add_argument("--max", type=int, default=6, help="並列の上限（調査 1 本を含めて数える）")
    r.add_argument("--max-items", type=int, default=3)
    r.add_argument("--lap", type=int, default=None)
    r.add_argument("--loop", default=None)
    r.add_argument("--pid", type=int, default=None)
    r.set_defaults(func=cmd_run)
