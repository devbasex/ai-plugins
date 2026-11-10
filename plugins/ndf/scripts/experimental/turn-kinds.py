#!/usr/bin/env python3
"""turn-kinds.py: 期間を指定し、ターンの型ごとの件数と全 Tool の呼び出しに占める割合を transcript から数える（#2909 の F7）。

    python3 turn-kinds.py --since 2026-10-13 --until 2026-11-09 [--root <transcript の根>] [--rules <規則のファイル>]

- 期間は JST の日付で、両端を含む。`--root` は省くと `transcript_agents.config_root()`（`~/.claude` か `CLAUDE_CONFIG_DIR`）。
  根の下の `projects/` と `ndf/accounts/*/projects/` の `*.jsonl`（subagents を含む）を読む
- 数えるのは `type` が `assistant` の行の `tool_use` だけ（`tool_use` の id で 1 度だけ数える）。分母は全 Tool の呼び出し
- 型と候補の規則は `--rules`（省くと同じディレクトリの `turn-kinds.json`）。型は Bash のコマンドの先頭へ上から当てる
- 会話の本文・会話の ID・コマンドの文字列は出さない（I8）。件数と割合だけを標準出力へ出し、ファイルへ書かない
- 会話を残さない呼び出し（transcript の無い `claude -p` など）は数えられない

結果は `lib/step_result.py` の形の 1 行の JSON。`metrics` は `{tool_calls, kinds: {<型>: {count, share}},
candidates: {<候補>: {count, share}}, files, unreadable_lines}`、`items` は候補ごとの `{kind: candidate, name, result: <割合>}`。
終了コード: 0 = 数えた / 2 = 期間の書き方の誤り・規則を読めない / 3 = 根が無い。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import deps  # noqa: E402

deps.require("mdtable")  # transcript_agents が表の包みを読む
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, emit, result  # noqa: E402
import transcript_agents  # noqa: E402

TOOL = "turn-kinds"
JST = dt.timezone(dt.timedelta(hours=9))
RULES = Path(__file__).resolve().with_name("turn-kinds.json")
# コマンドの先頭から飛ばすもの（コメントの行・cd・変数の代入・包みのコマンド）
PREFIX = re.compile(
    r"^(\s+|#[^\n]*\n|cd\s+\S+\s*(&&|;)\s*|export\s+\S+\s*(&&|;|\n)\s*|[A-Za-z_][A-Za-z0-9_]*=(\"[^\"]*\"|'[^']*'|\S*)\s+|"
    r"(env|nohup|time)\s+|timeout\s+\S+\s+)"
)


def head_of(cmd: str) -> str:
    """コマンドの先頭の 1 つ（飛ばすものを除いた残り）。"""
    prev = None
    s = cmd or ""
    while s != prev:
        prev = s
        s = PREFIX.sub("", s, count=1)
    return s


class Rules:
    def __init__(self, raw: dict):
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError("規則は version 1 のオブジェクトで書く")
        self.kinds = [(k["name"], re.compile(k["pattern"]), k.get("scope", "head")) for k in raw.get("kinds", [])]
        self.candidates = [
            {
                "name": c["name"],
                "kinds": set(c.get("kinds") or []),
                "all": [re.compile(p) for p in c.get("all") or []],
                "repeat": bool(c.get("repeat")),
            }
            for c in raw.get("candidates", [])
        ]

    def kind_of(self, cmd: str) -> str | None:
        head = head_of(cmd)
        for name, rx, scope in self.kinds:
            if rx.search(cmd if scope == "all" else head):
                return name
        return None

    def candidates_of(self, kind: str | None, cmd: str) -> list[dict]:
        out = []
        for c in self.candidates:
            if c["kinds"] and kind not in c["kinds"]:
                continue
            if all(rx.search(cmd) for rx in c["all"]):
                out.append(c)
        return out


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


def window(since: str, until: str) -> tuple[dt.datetime, dt.datetime]:
    """JST の日付の両端を含む期間を、UTC の [始め, 終わり) にする。"""
    start = dt.datetime.combine(_day(since), dt.time(), JST)
    end = dt.datetime.combine(_day(until) + dt.timedelta(days=1), dt.time(), JST)
    if end <= start:
        raise ValueError("--until は --since より後の日付にする")
    return start.astimezone(dt.timezone.utc), end.astimezone(dt.timezone.utc)


def transcript_files(root: Path, start: dt.datetime) -> list[Path]:
    """根の下の transcript。期間の始めより前に最後に書かれたファイルは読まない。"""
    dirs = [root / "projects", *sorted(root.glob("ndf/accounts/*/projects"))]
    files = []
    floor = start.timestamp()
    for d in dirs:
        for f in d.rglob("*.jsonl") if d.is_dir() else []:
            if "tool-results" in f.parts:
                continue
            try:
                if f.stat().st_mtime >= floor:
                    files.append(f)
            except OSError:
                continue
    return sorted(files)


def _line_time(obj: dict) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(obj.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None


def _assistant_line(line: str, start: dt.datetime, end: dt.datetime, counts: dict) -> dict | None:
    """期間の中の assistant の行（tool_use を含みうる行だけを読む）。JSON でない行は counts に数えて None。"""
    if '"tool_use"' not in line or '"assistant"' not in line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        counts["unreadable_lines"] += 1
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    at = _line_time(obj)
    return obj if at is not None and start <= at < end else None


def _new_tool_blocks(obj: dict, seen: set):
    """行の tool_use のブロックのうち、まだ見ていないもの（id で 1 度だけ）。"""
    content = (obj.get("message") or {}).get("content")
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        key = block.get("id") or id(block)
        if key in seen:
            continue
        seen.add(key)
        yield block


def tool_uses(path: Path, start: dt.datetime, end: dt.datetime, seen: set, counts: dict):
    """期間の中の assistant の行の tool_use を順に返す（id で 1 度だけ）。読めない行は counts に数える。"""
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        counts["unreadable_files"] += 1
        return
    with fh:
        for line in fh:
            if (obj := _assistant_line(line, start, end, counts)) is not None:
                yield from _new_tool_blocks(obj, seen)


def count_turn_kinds(files: list[Path], rules: Rules, start: dt.datetime, end: dt.datetime) -> dict:
    kinds: dict[str, int] = {}
    cands: dict[str, int] = {c["name"]: 0 for c in rules.candidates}
    counts = {"tool_calls": 0, "unreadable_lines": 0, "unreadable_files": 0}
    seen: set = set()
    for f in files:
        prev_hits: set[str] = set()  # 同じ会話の直前の Tool の呼び出しが当たった候補
        for block in tool_uses(f, start, end, seen, counts):
            counts["tool_calls"] += 1
            cmd = (block.get("input") or {}).get("command") if block.get("name") == "Bash" else None
            if not isinstance(cmd, str):
                prev_hits = set()
                continue
            kind = rules.kind_of(cmd)
            if kind:
                kinds[kind] = kinds.get(kind, 0) + 1
            hits = {c["name"] for c in rules.candidates_of(kind, cmd)}
            for c in rules.candidates:
                if c["name"] in hits and (not c["repeat"] or c["name"] in prev_hits):
                    cands[c["name"]] += 1
            prev_hits = hits
    return {"kinds": kinds, "candidates": cands, **counts}


def _share(n: int, total: int) -> float:
    return round(n / total, 6) if total else 0.0


def main(argv=None) -> "NoReturn":  # noqa: F821
    ap = argparse.ArgumentParser(prog="turn-kinds.py", description=__doc__.splitlines()[0])
    ap.add_argument("--since", required=True, help="JST の日付（YYYY-MM-DD・含む）")
    ap.add_argument("--until", required=True, help="JST の日付（YYYY-MM-DD・含む）")
    ap.add_argument("--root", help="transcript の根（既定は ~/.claude か CLAUDE_CONFIG_DIR）")
    ap.add_argument("--rules", default=str(RULES), help="型と候補の規則（既定は同じディレクトリの turn-kinds.json）")
    a = ap.parse_args(argv)
    try:
        start, end = window(a.since, a.until)
        rules = Rules(json.loads(Path(a.rules).read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, re.error) as e:
        emit(result(TOOL, "stopped", f"期間か規則を読めない: {e}"), EXIT_UNREADABLE)
    root = transcript_agents.config_root(a.root)
    if not root.is_dir():
        emit(result(TOOL, "stopped", f"transcript の根が無い: {root}"), EXIT_PRECONDITION)
    files = transcript_files(root, start)
    got = count_turn_kinds(files, rules, start, end)
    total = got["tool_calls"]
    metrics = {
        "tool_calls": total,
        "kinds": {k: {"count": n, "share": _share(n, total)} for k, n in sorted(got["kinds"].items(), key=lambda x: -x[1])},
        "candidates": {k: {"count": n, "share": _share(n, total)} for k, n in got["candidates"].items()},
        "files": len(files),
        "unreadable_lines": got["unreadable_lines"],
    }
    items = [{"kind": "candidate", "name": k, "result": f"{v['share']:.4%}"} for k, v in metrics["candidates"].items()]
    emit(result(TOOL, "ok", f"{a.since}〜{a.until}（JST）の Tool の呼び出し {total} 件を数えた", items, metrics))


if __name__ == "__main__":
    main()
