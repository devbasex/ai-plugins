"""使用量の使用量ログを書く口（SubagentStop の hook・`claude -p` の呼び手）と、使用量ログを集計する口（N8b）。

使用量ログは 1 本の JSONL（表 ndf-files の usage-log-file）で、書き手（hook かスクリプトか）によらず同じ保存先に書く。
同じ `agent_id` の差を書くときは、使用量ログの錠の中で既存の行を読んでから書く（別々のサブエージェントの hook が
同時に走るため）。形は、その `agent_id` の行だけ確かめる（錠の中で使用量ログ全体を検証しない）。書き込みの失敗は hook を落とさず、標準エラーへ 1 行出して続ける（I5）。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ndf import identity
from ndf.contracts import facts
from ndf.domains.records import jsonl
from ndf.domains.records import usage_decide as ud

if TYPE_CHECKING:
    from ndf.adapters.github import GitHub

# 書き手の区分（使用量ログの行の caller）
SUBAGENT = "subagent"
CLAUDE_CLI = "claude-p"
# 閉じた課題を数える GitHub の検索（GraphQL）
_SEARCH = "query($q: String!) { search(query: $q, type: ISSUE) { issueCount } }"


def usage_log_path(env: Optional[Mapping[str, str]] = None) -> Path:
    """使用量ログのファイル。保存先を決められなければ `LookupError`。"""
    location = facts.value("ndf-files", "usage-log-file", "location")
    try:
        state_dir: Optional[Path] = identity.load().state_dir
    except identity.IdentityError:
        state_dir = None
    where = ud.state_location_dir(
        os.environ if env is None else env,
        facts.value("locations", location, "env"),
        state_dir,
        facts.value("locations", location, "path"),
    )
    return where / facts.value("ndf-files", "usage-log-file", "path")


def _records(path: Path) -> list[dict[str, Any]]:
    # usage を持たない行は JSON として読まない。書きかけ・辞書でない行は応答として数えない
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if '"usage"' in line]
    return jsonl.parse_lines(lines)[0]


def transcript_usage(path: Path, since: Optional[datetime] = None, until: Optional[datetime] = None) -> ud.Usage | str:
    """会話の記録 1 本の使用量。読めなければ `UNKNOWN`（使用量ログの行は unknown になる。hook を落とさない）。"""
    try:
        records = _records(path)
    except (OSError, UnicodeDecodeError):
        return ud.UNKNOWN
    return ud.transcript_total(records, since, until)


def record_subagent(
    payload: Mapping[str, Any], env: Optional[Mapping[str, str]] = None, *, warnings: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """終わったサブエージェント 1 回を使用量ログへ 1 行書き、書いた行を返す（書けなければ None）。

    同じ `agent_id` の 2 回目以降は、前までの行の合計との差を書く。会話の記録を読めなければ usage は unknown。
    使用量ログに JSON として読めない行か、形の違うその `agent_id` の行があれば `warnings` へ足す。新しいバージョンの行があれば書かずに `InternalError`。
    """
    agent = payload.get("agent_id")
    transcript = payload.get("agent_transcript_path")
    if not isinstance(agent, str) or not isinstance(transcript, str):
        print("使用量の使用量ログ: agent_id か agent_transcript_path が無いので書かない", file=sys.stderr)
        return None
    try:
        path = usage_log_path(env)
        with jsonl.lock(path):
            total = transcript_usage(Path(transcript))
            prior, _ = jsonl.read_rows_warned(path, kind="usage", warnings=warnings, wanted=lambda r: r.get("agent_id") == agent)
            usage: Any = ud.stop_delta(prior, agent, total).as_dict() if isinstance(total, ud.Usage) else ud.UNKNOWN
            row = {"at": jsonl.now(), "caller": SUBAGENT, "agent_id": agent, "transcript": transcript, "usage": usage}
            return jsonl.append(path, row, kind="usage")
    except (LookupError, OSError) as exc:
        print(f"使用量の使用量ログへ書けない: {exc}", file=sys.stderr)
        return None


def record_cli(usage: Mapping[str, Any], meta: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> Optional[dict[str, Any]]:
    """`claude -p` の 1 回を、サブエージェントと同じ使用量ログへ 1 行書く。書けなければ None。

    `meta` の欄は `contracts/schemas/internal/usage.schema.json` にあるものだけ（ほかの欄は書かずに `InternalError`）。
    """
    u = ud.usage_of(usage)
    row = {"at": jsonl.now(), "caller": CLAUDE_CLI, **meta, "usage": u.as_dict() if u is not None else ud.UNKNOWN}
    try:
        return jsonl.append_locked(usage_log_path(env), row, kind="usage")
    except (LookupError, OSError) as exc:
        print(f"使用量の使用量ログへ書けない: {exc}", file=sys.stderr)
        return None


def _transcripts(rows: list[dict[str, Any]], since: datetime, until: datetime) -> dict[str, ud.Usage | str]:
    paths: dict[str, str] = {}
    for row in rows:
        if isinstance(row.get("agent_id"), str) and isinstance(row.get("transcript"), str):
            paths[row["agent_id"]] = row["transcript"]
    return {agent: transcript_usage(Path(p), since, until) for agent, p in paths.items()}


def report(path: Path, since: date, until: date, closed: Callable[[str], int]) -> dict[str, Any]:
    """期間（JST の日付）の合計・読めなかった行・閉じた課題の数。`closed` は検索の条件から閉じた課題の数を返す。"""
    a, b = ud.jst_bounds(since, until)
    rows, broken = jsonl.read_rows(path, kind="usage")
    total, unknown = ud.reconcile(rows, _transcripts(rows, a, b), a, b)
    return {
        "since": a.isoformat(),
        "until": b.isoformat(),
        "usage": total.as_dict(),
        "unknown_rows": unknown,
        "broken_rows": len(broken),
        "closed_issues": closed(ud.closed_query(since, until)),
    }


def closed_count(hub: GitHub, condition: str) -> int:
    """閉じた課題（PR を除く）の数。条件は `usage_decide.closed_query` の JST の両端。"""
    data = hub.graphql(_SEARCH, {"q": f"repo:{hub.repo} is:issue {condition}"})
    return int(data["search"]["issueCount"])
