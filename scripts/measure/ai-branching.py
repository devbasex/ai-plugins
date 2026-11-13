#!/usr/bin/env python3
"""#1142（#3152 から統合）の洗い出し: 開発のワークフローで AI が分岐と例外の行き先を決めた回を、実際の記録から数える。

3 つの記録を読み、回数を種類ごとに数えて JSON で出す。数は「AI が出力を読んで次の手を決めた跡」の件数で、すべてが
誤りではない。数え方を変えずに、作り直しの前と後を同じコマンドで打つ。

    python3 scripts/measure/ai-branching.py [--since 2026-10-14] [--ndf .ndf] [--transcripts <根>]... [--jobs 6]

- プランの記録（`progress.jsonl`）: `--ndf` の下と `--state`（既定 `~/.local/state/ndf`）の下のすべて。
  `run` の行の `from`（途中からの再開）を、直前の実行の終わり方（承認ゲート / 失敗 / それ以外）で分ける。
  `attention` の行（`wait` が終了コード 20 で AI に読ませる知らせ）を理由ごとに、`judge` のステップを決定ごとに数える。
  実行が「止まった」で終わった回を、最後のステップと終了コードの組で数える
- 並列の計画の台帳（`<ndf>/parallel/*/ledger.jsonl`）: `ask` を `source` ごとに、`resume` の `take`・`settle` の `result` を数える
- 会話の記録（`--transcripts`。既定 `~/.claude/ndf/accounts/*/projects/-work-ai-plugins*`）: `assistant` の行の `tool_use`
  を id で 1 度だけ数え、Bash のコマンドを `RULES` の正規表現で分ける（1 つのコマンドが複数の規則に当たれば、それぞれに数える）。
  Write のうち `"steps"` を含む `.json`（手で組んだプラン）と、tool_result のうち `PHRASES` の文を含むもの（AI が読んだ止まりの文）も数える
"""

import argparse
import collections
import datetime
import glob
import json
import os
import pathlib
import re
from concurrent.futures import ProcessPoolExecutor

# Bash のコマンドの分類（名前, 正規表現）。AI が出力を読んで分岐を決めた跡と、決めた後に打つ手を拾う
# 数えるコマンドの規則（名前と正規表現）。実在のエントリポイントの名前を持つため、コードではなくデータに置く
RULES = [
    (r["name"], r["pattern"]) for r in json.loads((pathlib.Path(__file__).with_name("ai-branching-rules.json")).read_text(encoding="utf-8"))
]
# Bash 以外で数える Tool（AI が問う・続けさせる・止める時点を自分で決めた跡）
TOOLS = ("AskUserQuestion", "SendMessage", "TaskStop")
COMPILED = [(name, re.compile(rx)) for name, rx in RULES]
# tool_result に現れたら「AI が止まりの文を読んで行き先を決めた」と数える文
PHRASES = [
    "CI 以外の理由",
    "が失敗した（exit=",
    "終了コード 3 で止めた",
    "結果: 止まった",
    "結果: 関門",
    "基盤待ち",
    "ベースブランチと衝突",
    "改修の回数",
]


def default_transcripts() -> list[str]:
    return sorted(glob.glob(os.path.expanduser("~/.claude/ndf/accounts/*/projects/-work-ai-plugins*")))


def read_jsonl(path: pathlib.Path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


# ---- プランの記録 ----


def progress_files(ndf: pathlib.Path, state: pathlib.Path) -> list[pathlib.Path]:
    seen = set()
    out = []
    for root in (ndf, state):
        if not root.is_dir():
            continue
        for p in root.rglob("progress.jsonl"):
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(r)
    return sorted(out)


def plan_runs(rows: list[dict]) -> list[list[dict]]:
    """`run` の行で区切った実行ごとの行の並び。"""
    runs: list[list[dict]] = []
    for row in rows:
        if row.get("kind") == "run" or not runs:
            runs.append([])
        runs[-1].append(row)
    return runs


def run_end(run: list[dict]) -> tuple[str, str, str]:
    """実行の終わり方: (種類, 最後のステップ, 終了コード)。種類は 関門 / 失敗 / 完了か不明。"""
    steps = [r for r in run if r.get("kind") == "step"]
    attn = [r for r in run if r.get("kind") == "attention"]
    if not steps:
        return "記録なし", "", ""
    last = steps[-1]
    code = last.get("exit")
    sid = str(last.get("step", ""))
    if (attn and attn[-1].get("reason") == "関門") or (isinstance(code, int) and 10 <= code <= 19):
        return "関門", sid, str(code)
    if last.get("next") == "end" and code not in (0, None) and last.get("type") != "judge":
        return "失敗", sid, str(code)
    if code not in (0, None) and not last.get("next"):
        return "失敗", sid, str(code)
    return "完了か不明", sid, str(code)


def count_progress(files: list[pathlib.Path], since: str) -> dict:
    resume = collections.Counter()
    resume_from = collections.Counter()
    attention = collections.Counter()
    judge = collections.Counter()
    stopped = collections.Counter()
    plans = 0
    for path in files:
        rows = [r for r in read_jsonl(path) if str(r.get("at", "")) >= since]
        if not rows:
            continue
        plans += 1
        runs = plan_runs(rows)
        for i, run in enumerate(runs):
            head = run[0]
            if head.get("kind") == "run" and head.get("from") and i > 0:
                kind, sid, _ = run_end(runs[i - 1])
                same = "同じステップから" if sid == head["from"] else "別のステップから"
                resume[f"{kind}の後・{same}"] += 1
                resume_from[str(head["from"])] += 1
            kind, sid, code = run_end(run)
            if kind == "失敗":
                stopped[f"{sid} exit={code}"] += 1
        for r in rows:
            if r.get("kind") == "attention":
                attention[str(r.get("reason"))] += 1
            if r.get("kind") == "step" and r.get("type") == "judge":
                judge[f"{r.get('step')} → {r.get('decision') or r.get('next')}"] += 1
    return {
        "plans": plans,
        "resume_by_previous_end": dict(resume.most_common()),
        "resume_from_step": dict(resume_from.most_common(20)),
        "attention_by_reason": dict(attention.most_common()),
        "judge_by_decision": dict(judge.most_common()),
        "stopped_by_step_exit": dict(stopped.most_common(25)),
    }


# ---- 並列の計画の台帳 ----


def count_ledgers(ndf: pathlib.Path, since: str) -> dict:
    ask = collections.Counter()
    resume = collections.Counter()
    kinds = collections.Counter()
    for path in sorted((ndf / "parallel").glob("*/ledger.jsonl")):
        for r in read_jsonl(path):
            if str(r.get("at", "")) < since:
                continue
            kinds[str(r.get("kind"))] += 1
            msg = r.get("msg") or {}
            if r.get("kind") == "ask":
                ask[str(msg.get("source"))] += 1
            if r.get("kind") == "resume":
                key = (
                    f"{msg.get('phase')}: take={msg.get('take')}"
                    if msg.get("phase") == "confirm"
                    else f"{msg.get('phase')}: {msg.get('result')}"
                )
                resume[key] += 1
    return {"kinds": dict(kinds.most_common()), "ask_by_source": dict(ask.most_common()), "resume": dict(resume.most_common())}


# ---- 会話の記録 ----


def scan_transcript(args: tuple[str, str]) -> dict:
    path, since = args
    bash: dict[str, list[str]] = {}
    writes: set[str] = set()
    tools: dict[str, str] = {}
    phrases: dict[str, set[str]] = {p: set() for p in PHRASES}
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return {"bash": {}, "writes": [], "tools": {}, "phrases": {}}
    with f:
        for line in f:
            if '"tool_use"' in line and '"assistant"' in line:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("timestamp", ""))[:10] < since:
                    continue
                for c in (row.get("message") or {}).get("content") or []:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    if c.get("name") == "Bash":
                        cmd = str(inp.get("command", ""))
                        hits = [name for name, rx in COMPILED if rx.search(cmd)]
                        if hits:
                            bash[c["id"]] = hits
                    elif c.get("name") in TOOLS:
                        tools[c["id"]] = tool_key(c.get("name"), inp)
                    elif c.get("name") == "Write":
                        fp = str(inp.get("file_path", ""))
                        if fp.endswith(".json") and '"steps"' in str(inp.get("content", "")):
                            writes.add(c["id"])
            elif '"tool_result"' in line and any(p in line for p in PHRASES):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("timestamp", ""))[:10] < since:
                    continue
                for c in (row.get("message") or {}).get("content") or []:
                    if not isinstance(c, dict) or c.get("type") != "tool_result":
                        continue
                    text = json.dumps(c.get("content"), ensure_ascii=False)
                    for p in PHRASES:
                        if p in text:
                            phrases[p].add(str(c.get("tool_use_id")))
    return {"bash": bash, "writes": sorted(writes), "tools": tools, "phrases": {k: sorted(v) for k, v in phrases.items() if v}}


def tool_key(name: str, inp: dict) -> str:
    """AskUserQuestion は問いの `metadata.source` の接頭辞（承認ゲートの問いか、ほかの問い）で分ける。"""
    if name != "AskUserQuestion":
        return name
    src = str(((inp.get("metadata") or {}).get("source")) or "")
    return f"AskUserQuestion（{src.split(':')[0] + ':' + src.split(':')[1] if src.count(':') else src or '印なし'}）"


def count_transcripts(roots: list[str], since: str, jobs: int) -> dict:
    files = [str(p) for r in roots for p in pathlib.Path(r).rglob("*.jsonl")]
    files = [f for f in files if os.path.getmtime(f) >= _epoch(since)]
    bash: dict[str, list[str]] = {}
    writes: set[str] = set()
    tools: dict[str, str] = {}
    phrases: dict[str, set[str]] = collections.defaultdict(set)
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for res in ex.map(scan_transcript, [(f, since) for f in files], chunksize=8):
            bash.update(res["bash"])
            writes.update(res["writes"])
            tools.update(res["tools"])
            for k, v in res["phrases"].items():
                phrases[k].update(v)
    by_rule = collections.Counter(name for hits in bash.values() for name in hits)
    return {
        "files": len(files),
        "bash_by_rule": dict(by_rule.most_common()),
        "hand_written_plans": len(writes),
        "tools": dict(collections.Counter(tools.values()).most_common()),
        "results_with_phrase": {k: len(phrases[k]) for k in PHRASES if phrases.get(k)},
    }


def _epoch(day: str) -> float:
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc).timestamp()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="2026-10-14", help="数え始める日（UTC の日付。両端を含む）")
    ap.add_argument("--ndf", default=".ndf", help="リポジトリの .ndf")
    ap.add_argument("--state", default=os.path.expanduser("~/.local/state/ndf"), help="プランの状態の置き場")
    ap.add_argument("--transcripts", action="append", help="会話の記録の根（複数可）")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--skip-transcripts", action="store_true")
    a = ap.parse_args()
    ndf = pathlib.Path(a.ndf)
    out = {
        "since": a.since,
        "progress": count_progress(progress_files(ndf, pathlib.Path(a.state)), a.since),
        "ledger": count_ledgers(ndf, a.since),
    }
    if not a.skip_transcripts:
        out["transcripts"] = count_transcripts(a.transcripts or default_transcripts(), a.since, a.jobs)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
