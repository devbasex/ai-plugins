"""使用量の帳簿の集計（#1953）。帳簿 1 つ × 期間の合計を、呼び出し元・日・プランごとに出す。読むだけで書かない。

**帳簿の間で単位を足さない（I7）。** 集計は帳簿（ランタイム）を 1 つだけ受け、出力の `usage` の鍵はそのランタイムの
記録の名前のまま（入れ子は `cache_creation.ephemeral_5m_input_tokens` のように `.` でつなぐ）。

Claude の帳簿では 3 つの値を並べる（#1953 の決定 5）:

| 値 | 求め方 |
| --- | --- |
| サブエージェントだけ | `kind=subagent` の行 + 会話の記録の `subagents/agent-*.jsonl` のうち `agent_id` が帳簿に無いもの |
| 全体 | `claude -p` の行の `usage` + サブエージェント（上と同じ）+ `claude -p` の行ごとの `modelUsage` の和から `usage` と親を同じくするサブエージェントを引いた残り（0 未満は 0。5 分の書き込みとして数える）+ 会話の記録だけの `claude -p`（`entrypoint=sdk-cli` で、セッション ID が帳簿に無いもの） |
| 会話の記録だけ | 全体のうち、会話の記録から足した分 |

利用者が自分で開いた会話（conductor）の親の会話は数えない。会話の記録も帳簿の行も残さない呼び出しは数えられず、
帳簿の最初の行の時刻を添えてそう出す。リポジトリを選んだときの会話の記録は、記録の `cwd` のリポジトリ（作業ツリーが
もう無ければ、パスにリポジトリ名を含むか）で絞る。

閉じた課題の数は `.ndf/project.json` の `issues.primary` が `github` のときだけ `gh` で数え、ほかは `"unknown"`。

ライブラリなので、標準ライブラリとライブラリだけを import する（I1）。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import usage_capture
import usage_ledger

JST = timezone(timedelta(hours=9))
UNKNOWN = usage_ledger.UNKNOWN
GROUP_BY = ("caller", "day", "plan")
CLAUDE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
MODEL_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "cacheReadInputTokens": "cache_read_input_tokens",
    "cacheCreationInputTokens": "cache_creation_input_tokens",
    "outputTokens": "output_tokens",
}
W5 = "cache_creation.ephemeral_5m_input_tokens"


# --- 足し算 ---


def flat(usage, prefix: str = "") -> dict:
    """使用量の数値を `.` でつないだ鍵の dict にする。`"unknown"` と数値でない値は持たない。"""
    out: dict = {}
    if not isinstance(usage, dict):
        return out
    for k, v in usage.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flat(v, key + "."))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = v
    return out


def add_into(acc: dict, usage) -> None:
    for k, v in flat(usage).items():
        acc[k] = round(acc.get(k, 0) + v, 6)


def bucket() -> dict:
    return {"calls": 0, "unknown_calls": 0, "usage": {}}


def count(b: dict, row: dict) -> None:
    b["calls"] += 1
    if row.get("usage") == UNKNOWN:
        b["unknown_calls"] += 1
    add_into(b["usage"], row.get("usage"))


# --- 期間 ---


def parse_at(value) -> Optional[datetime]:
    t = usage_capture.utc_seconds(value)
    return datetime.fromtimestamp(t, timezone.utc) if t is not None else None


def jst_day(value) -> str:
    t = parse_at(value)
    return t.astimezone(JST).date().isoformat() if t else ""


def in_period(at, since: Optional[str], until: Optional[str]) -> bool:
    """`since`・`until` は JST の日付（両端を含む）。どちらも無ければ真。"""
    day = jst_day(at)
    if not day:
        return not (since or until)
    return (not since or day >= since) and (not until or day <= until)


def group_key(row: dict, by: str) -> str:
    if by == "day":
        return jst_day(row.get("at"))
    if by == "plan":
        return row.get("plan") or "（supervise の外）"
    return f"{row.get('source') or UNKNOWN}:{row.get('kind') or UNKNOWN}"


def summarize(rows: Iterable[dict], by: str = "caller") -> dict:
    """呼び出し元・日・プランごとの合計と、総計。"""
    groups: dict = defaultdict(bucket)
    totals = bucket()
    for r in rows:
        count(groups[group_key(r, by)], r)
        count(totals, r)
    return {"groups": [{"key": k, **v} for k, v in sorted(groups.items())], "totals": totals}


# --- Claude の 3 つの値 ---


def model_usage_sum(row: dict) -> dict:
    out: dict = {}
    for mu in (row.get("model_usage") or {}).values():
        if isinstance(mu, dict):
            for src, dst in MODEL_USAGE_KEYS.items():
                out[dst] = out.get(dst, 0) + (mu.get(src) or 0)
    return out


def residual(row: dict, children: list) -> dict:
    """`claude -p` の行の `modelUsage` の和から、`usage` と親を同じくするサブエージェントを引いた残り（0 未満は 0）。"""
    mu = model_usage_sum(row)
    if not mu:
        return {}
    used = flat(row.get("usage"))
    for c in children:
        for k, v in flat(c).items():
            used[k] = used.get(k, 0) + v
    out = {k: max(0, v - used.get(k, 0)) for k, v in mu.items()}
    if out.get("cache_creation_input_tokens"):
        out[W5] = out["cache_creation_input_tokens"]
    return {k: v for k, v in out.items() if v}


def _first_value(path: Path, key: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 50:
                    break
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and isinstance(d.get(key), str):
                    return d[key]
    except OSError:
        return None
    return None


def repo_matches(cwd: Optional[str], repo: Optional[str]) -> bool:
    """会話の記録の `cwd` が選んだリポジトリのものか。作業ツリーがもう無ければ、パスにリポジトリ名を含むかで見る。"""
    if not repo:
        return True
    if not cwd:
        return False
    if os.path.isdir(cwd):
        return usage_ledger.repo_key(cwd) == repo
    return repo.split("__", 1)[-1] in cwd


def transcripts(projects: Path, since: Optional[str], until: Optional[str], repo: Optional[str]) -> dict:
    """会話の記録の `claude -p` の会話（sdk-cli）とサブエージェント。{"sdk": {session: usage}, "agents": {agent_id: (parent, usage)}}。"""
    out: dict = {"sdk": {}, "agents": {}}
    if not projects.is_dir():
        return out
    lo = datetime.fromisoformat(since).replace(tzinfo=JST).timestamp() if since else 0.0
    for path in projects.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < lo:
                continue
        except OSError:
            continue
        if not in_period(_first_value(path, "timestamp"), since, until) or not repo_matches(_first_value(path, "cwd"), repo):
            continue
        session = path.stem
        if _first_value(path, "entrypoint") == "sdk-cli":
            usage, _ = usage_capture.transcript_usage(path)
            if usage != UNKNOWN:
                out["sdk"][session] = usage
        for agent in (path.parent / session / "subagents").glob("agent-*.jsonl"):
            usage, _ = usage_capture.transcript_usage(agent)
            if usage != UNKNOWN:
                out["agents"][agent.stem.removeprefix("agent-")] = (session, usage)
    return out


def claude_views(rows: list, all_rows: list, since: Optional[str], until: Optional[str], repo: Optional[str], projects: Optional[Path]) -> dict:
    """Claude の帳簿の「サブエージェントだけ」「全体」「会話の記録だけ」と、数えられない範囲の注記。"""
    subs = [r for r in rows if r.get("kind") == "subagent"]
    calls = [r for r in rows if r.get("kind") != "subagent"]
    known_agents = {r.get("agent_id") for r in all_rows if r.get("kind") == "subagent"}
    known_sessions = {r.get("session_id") for r in all_rows if r.get("kind") != "subagent"}
    found = transcripts(projects, since, until, repo) if projects else {"sdk": {}, "agents": {}}
    extra_agents = {a: v for a, v in found["agents"].items() if a not in known_agents}
    sub_only, total, only = bucket(), bucket(), bucket()
    for r in subs:
        count(sub_only, r)
        count(total, r)
    for parent, usage in extra_agents.values():
        for b in (sub_only, total, only):
            count(b, {"usage": usage})
    children = defaultdict(list)
    for r in subs:
        children[r.get("parent_session_id")].append(r.get("usage"))
    for parent, usage in extra_agents.values():
        children[parent].append(usage)
    for r in calls:
        count(total, r)
        add_into(total["usage"], residual(r, children.get(r.get("session_id"), []) if r.get("session_id") else []))
    for session, usage in found["sdk"].items():
        if session not in known_sessions:
            count(total, {"usage": usage})
            count(only, {"usage": usage})
    first = min((r.get("at") for r in all_rows if r.get("at")), default=None)
    note = (
        f"{first} より前の、会話の記録を残さない claude -p は含まない（帳簿の最初の行の時刻）"
        if first
        else "帳簿が無い。会話の記録を残さない claude -p は含まない"
    )
    return {"subagent_only": sub_only, "total": total, "transcript_only": only, "uncountable_note": note}


# --- 閉じた課題の数とスプリントの期間 ---


def issues_on_github(root: Path) -> bool:
    try:
        d = json.loads((Path(root) / ".ndf" / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and (d.get("issues") or {}).get("primary") == "github"


def _gh(root: Path, args: list) -> Optional[str]:
    try:
        p = subprocess.run(["gh", *args], cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def milestone_info(root: Path, slug: str, milestone: str) -> Optional[dict]:
    """マイルストーンを番号か名前で引く（`gh api`。`slug` は `owner/repo`）。引けなければ None。"""
    out = _gh(root, ["api", f"repos/{slug}/milestones?state=all&per_page=100"])
    try:
        items = json.loads(out) if out else []
    except ValueError:
        return None
    for m in items if isinstance(items, list) else []:
        if str(m.get("number")) == str(milestone) or m.get("title") == milestone:
            return m
    return None


def milestone_period(m: dict) -> tuple[str, str]:
    """マイルストーンの `created_at` から `closed_at`（開いていれば今）を JST の日付で。"""
    end = jst_day(m.get("closed_at")) or datetime.now(JST).date().isoformat()
    return jst_day(m.get("created_at")), end


def closed_issues(root: Path, slug: str, since: Optional[str], until: Optional[str], milestone: Optional[dict]):
    """閉じた課題の数。GitHub Issues でないか `gh` が失敗すれば `"unknown"`。"""
    if not issues_on_github(root):
        return UNKNOWN
    if milestone is not None:
        n = milestone.get("closed_issues")
        return n if isinstance(n, int) else UNKNOWN
    lo = since or "2000-01-01"
    hi = until or date.today().isoformat()
    out = _gh(root, ["api", "-X", "GET", "search/issues", "-f", f"q=repo:{slug} is:issue closed:{lo}..{hi}"])
    try:
        n = json.loads(out).get("total_count") if out else None
    except (ValueError, AttributeError):
        n = None
    return n if isinstance(n, int) else UNKNOWN


# --- 入口 ---


def report(
    ledger: str,
    *,
    root: Path,
    since: Optional[str] = None,
    until: Optional[str] = None,
    milestone: Optional[str] = None,
    by: str = "caller",
    repo: Optional[str] = None,
    directory: Optional[Path] = None,
    projects: Optional[Path] = None,
) -> dict:
    """帳簿 1 つの集計（入出力の契約 C3 の JSON）。`repo` が None なら全リポジトリ（課題とマイルストーンは `root` のもの）。"""
    slug = (repo or usage_ledger.repo_key(str(root))).replace("__", "/", 1)
    m = None
    if milestone:
        m = milestone_info(root, slug, milestone)
        if m is None:
            raise ValueError(f"マイルストーンを引けない: {milestone}")
        since, until = milestone_period(m)
    all_rows = usage_ledger.read_usage_ledger(ledger, directory, repo)
    rows = [r for r in all_rows if in_period(r.get("at"), since, until)]
    out = {
        "ledger": ledger,
        "repo": repo.replace("__", "/", 1) if repo else "all",
        "period": {"since": since, "until": until},
        **summarize(rows, by),
        "closed_issues": closed_issues(root, slug, since, until, m) if "/" in slug else UNKNOWN,
    }
    if ledger == "claude":
        out["claude"] = claude_views(rows, all_rows, since, until, repo, projects)
    return out


def step_totals(plan: str, step: str, since: float, directory: Optional[Path] = None) -> dict:
    """supervise の 1 ステップの中で起動した CLI の行を、ランタイムごとに合計する（#1953 の決定 11）。

    supervise 自身の `claude -p`（`source=supervise` の Claude の行）は報告が別に数えるため除く。"""
    out = {}
    for rt in usage_ledger.RUNTIMES:
        b = bucket()
        for r in usage_ledger.read_usage_ledger(rt, directory):
            t = parse_at(r.get("at"))
            if r.get("plan", "") != plan or r.get("step", "") != str(step) or t is None or t.timestamp() < since - 1:
                continue
            if rt == "claude" and r.get("source") == "supervise":
                continue
            count(b, r)
        if b["calls"]:
            out[rt] = b
    return out


def runtimes_text(entries: Iterable[dict]) -> str:
    """報告の「CLI（drive・work の中）」の行の中身。ランタイムごとに回数と使用量（帳簿の間で足さない）。"""
    acc: dict = {}
    for e in entries:
        for rt, b in (e.get("llm_runtimes") or {}).items():
            a = acc.setdefault(rt, bucket())
            a["calls"] += b.get("calls", 0)
            a["unknown_calls"] += b.get("unknown_calls", 0)
            add_into(a["usage"], b.get("usage"))
    parts = []
    for rt, a in sorted(acc.items()):
        u = " / ".join(f"{k} {v:,.6g}" for k, v in sorted(a["usage"].items())) or "使用量なし"
        parts.append(f"{rt} {a['calls']} 回（{u}" + (f"・不明 {a['unknown_calls']} 回" if a["unknown_calls"] else "") + "）")
    return "; ".join(parts) or "無し"


def is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10
