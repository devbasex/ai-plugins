#!/usr/bin/env python3
"""会話の記録を conductor / supervisor / worker の層の単位で読む（#550）。

**読むだけの部品である。** ローカルの会話の記録（`~/.claude/projects/`）を開き、記録 1 件に
つき 1 つの `AgentRecord` を返す。ネットワークを開かず、送信先を持たない（AC34）。

値の取り方は `issues/issue-550-657-design-contracts.md` の「`AgentRecord` の値」が正本で
ある。**出力に載せないもの**もそこが決めている（`description` の `: ` より後ろ・ファイルの
パス・プロンプト・応答とツールの本文・`requestId`・`cwd`・`gitBranch`）。

コマンド:

    python3 transcript_agents.py list --session <ID> [--layer 層] [--format md|json]

`interrupted` と `wait-reset` は中断と再開の実装（設計の決定 1 の 3 本目）が足す。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------- 語彙（契約の文書の「語彙」の表） ----------

LAYERS = ("conductor", "supervisor", "worker")
POSTS = ("設計", "実装", "検査", "取り込み", "仕上げ")          # 持ち場（supervisor）
TASKS = ("調査", "修正", "検証", "集計")                        # 作業の種類（worker）
OTHER = "その他"
NO_ROLE = "-"

ENDINGS = ("completed", "in_progress", "rate_limit", "api_error")

SYNTHETIC_MODEL = "<synthetic>"

# 記録 1 件が持つ 11 項目（契約の文書の出力表の並び）
ROW_KEYS = (
    "layer", "role", "depth", "model", "fixed", "peak", "work",
    "responses", "duration_seconds", "ending", "interruptions",
)


@dataclass
class AgentRecord:
    """記録 1 件。列の意味は要求の文書の「用語」の表が持つ。"""

    layer: str
    role: str
    depth: int
    agent_id: str | None = None
    parent_agent_id: str | None = None
    model: str | None = None
    fixed: int | None = None
    peak: int | None = None
    work: int | None = None
    responses: int = 0
    started_at: str | None = None
    ended_at: str | None = None
    duration_seconds: int = 0
    ending: str = "completed"
    interruptions: int = 0
    resets_at: str | None = None
    rate_limit_type: str | None = None
    session: str | None = None
    tool_use_ids: list[str] = field(default_factory=list, repr=False)

    def as_row(self) -> dict:
        """契約の 11 項目だけを持つ辞書を返す。"""
        return {k: getattr(self, k) for k in ROW_KEYS}

    def as_json(self) -> dict:
        """`list` の JSON の 1 件。`agent_id` はここにだけ出る。"""
        out = self.as_row()
        out.update({
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "session": self.session,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "resets_at": self.resets_at,
            "rate_limit_type": self.rate_limit_type,
        })
        return out


# ---------- 記録の場所 ----------

def config_root(root: pathlib.Path | str | None = None) -> pathlib.Path:
    """会話の記録の親（`~/.claude` か `CLAUDE_CONFIG_DIR`）を返す。"""
    if root is not None:
        return pathlib.Path(root)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".claude"


def session_paths(
    session: str, root: pathlib.Path | str | None = None,
) -> tuple[pathlib.Path | None, list[pathlib.Path]]:
    """conductor の記録と、その配下の記録のパスを返す。"""
    projects = config_root(root) / "projects"
    conductor: pathlib.Path | None = None
    subs: list[pathlib.Path] = []
    if not projects.is_dir():
        return None, subs
    for project in sorted(projects.iterdir()):
        if not project.is_dir():
            continue
        candidate = project / f"{session}.jsonl"
        if candidate.is_file() and conductor is None:
            conductor = candidate
        subagents = project / session / "subagents"
        if subagents.is_dir():
            subs.extend(sorted(p for p in subagents.glob("agent-*.jsonl")))
    return conductor, subs


# ---------- 層と持ち場 ----------

def head_word(description: str | None) -> str:
    """`description` の最初の `: ` より前を返す。"""
    if not description:
        return ""
    head, sep, _ = description.partition(": ")
    return head.strip() if sep else description.strip()


def layer_of(depth: int, description: str | None) -> str:
    """層は深さで決め、深さ 1 だけ `description` の先頭語で分ける（決定 14）。"""
    if depth <= 0:
        return "conductor"
    if depth >= 2:
        return "worker"
    return "worker" if head_word(description) in TASKS else "supervisor"


def role_of(description: str | None, layer: str) -> str:
    """持ち場（supervisor）または作業の種類（worker）を返す。"""
    if layer == "conductor":
        return NO_ROLE
    vocabulary = POSTS if layer == "supervisor" else TASKS
    head = head_word(description)
    return head if head in vocabulary else OTHER


# ---------- 記録 1 件を読む ----------

def _iter_lines(path: pathlib.Path) -> tuple[list[dict], int]:
    """読めた行と、飛ばした行の数を返す。"""
    rows: list[dict] = []
    skipped = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if isinstance(row, dict):
                    rows.append(row)
                else:
                    skipped += 1
    except OSError:
        return rows, skipped + 1
    return rows, skipped


def _message(row: dict) -> dict:
    msg = row.get("message")
    return msg if isinstance(msg, dict) else {}


def _is_synthetic(row: dict) -> bool:
    return _message(row).get("model") == SYNTHETIC_MODEL


def _input_total(row: dict) -> int | None:
    usage = _message(row).get("usage")
    if not isinstance(usage, dict):
        return None
    total = 0
    for key in (
        "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total += int(value)
    return total


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _error_status(row: dict) -> str | None:
    """API の失敗を表す合成の応答の種別（`429` など）を返す。"""
    status = row.get("apiErrorStatus")
    if status is not None:
        return str(status)
    error = row.get("error")
    return str(error) if error else None


def _ending_of(rows: list[dict]) -> str:
    """表の上から順に判定し、最初に当たった値を返す（契約の終わり方の表）。"""
    last_assistant = -1
    for i, row in enumerate(rows):
        if row.get("type") == "assistant":
            last_assistant = i
    if any(row.get("type") == "user" for row in rows[last_assistant + 1:]):
        return "in_progress"
    if last_assistant < 0:
        return "completed"
    last = rows[last_assistant]
    if not _is_synthetic(last):
        return "completed"
    return "rate_limit" if _error_status(last) == "429" else "api_error"


def _tool_use_ids(rows: list[dict]) -> list[str]:
    """その記録が起動したサブエージェントの `tool_use` の id を集める。"""
    ids: list[str] = []
    for row in rows:
        if row.get("type") != "assistant":
            continue
        content = _message(row).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Agent":
                block_id = block.get("id")
                if isinstance(block_id, str) and block_id:
                    ids.append(block_id)
    return ids


def read_file(path: pathlib.Path, meta: dict | None = None) -> tuple[AgentRecord, int]:
    """記録 1 件を読む。返すのは `AgentRecord` と飛ばした行の数である。"""
    meta = meta or {}
    rows, skipped = _iter_lines(path)

    depth = meta.get("spawnDepth")
    depth = int(depth) if isinstance(depth, int) else 0
    description = meta.get("description")
    layer = layer_of(depth, description)

    agent_id = None
    if path.name.startswith("agent-"):
        agent_id = path.name[len("agent-"):-len(".jsonl")]

    record = AgentRecord(
        layer=layer,
        role=role_of(description, layer),
        depth=depth,
        agent_id=agent_id,
        ending=_ending_of(rows),
        tool_use_ids=_tool_use_ids(rows),
    )

    # 固定費・最大充填・応答数・モデルは、合成でない応答だけで数える（AC24）
    seen: set[str] = set()
    models: Counter = Counter()
    for row in rows:
        if row.get("type") != "assistant" or _is_synthetic(row):
            continue
        total = _input_total(row)
        if total is not None:
            if record.fixed is None:
                record.fixed = total
            record.peak = total if record.peak is None else max(record.peak, total)
        message_id = _message(row).get("id")
        if isinstance(message_id, str) and message_id and message_id not in seen:
            seen.add(message_id)
            model = _message(row).get("model")
            if isinstance(model, str) and model:
                models[model] += 1
    record.responses = len(seen)
    if record.fixed is not None and record.peak is not None:
        record.work = record.peak - record.fixed
    if models:
        record.model = models.most_common(1)[0][0]

    # 上限の中断から続けた回数（429 の合成の応答のうち、後ろに合成でない応答が続くもの）
    pending = 0
    for row in rows:
        if row.get("type") != "assistant":
            continue
        if _is_synthetic(row):
            if _error_status(row) == "429":
                pending += 1
            continue
        record.interruptions += pending
        pending = 0

    if record.ending == "rate_limit":
        for row in reversed(rows):
            if row.get("type") != "assistant" or not _is_synthetic(row):
                continue
            quota = row.get("quotaLimits")
            if isinstance(quota, dict):
                resets = quota.get("resetsAt")
                if isinstance(resets, (int, float)) and not isinstance(resets, bool):
                    record.resets_at = datetime.fromtimestamp(
                        int(resets), tz=timezone.utc,
                    ).isoformat()
                limit_type = quota.get("rateLimitType")
                if isinstance(limit_type, str):
                    record.rate_limit_type = limit_type
            break

    times = [t for t in (_parse_time(row.get("timestamp")) for row in rows) if t]
    if times:
        record.started_at = times[0].isoformat()
        record.ended_at = times[-1].isoformat()
        record.duration_seconds = int((times[-1] - times[0]).total_seconds())
    return record, skipped


def read_meta(path: pathlib.Path) -> dict:
    meta_path = path.with_suffix(".meta.json")
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_session(
    session: str,
    root: pathlib.Path | str | None = None,
    counter: dict | None = None,
) -> list[AgentRecord]:
    """1 つのセッションの 3 層すべてを読む。`counter` を渡すと飛ばした件数を積む。"""
    conductor_path, sub_paths = session_paths(session, root)
    records: list[AgentRecord] = []
    subs: list[tuple[AgentRecord, dict]] = []
    skipped = 0
    by_tool_use: dict[str, AgentRecord] = {}

    if conductor_path is not None:
        record, n = read_file(conductor_path, {"spawnDepth": 0})
        record.session = session
        skipped += n
        records.append(record)
    for path in sub_paths:
        meta = read_meta(path)
        record, n = read_file(path, meta)
        record.session = session
        skipped += n
        records.append(record)
        subs.append((record, meta))
    for record in records:
        for tool_use_id in record.tool_use_ids:
            by_tool_use[tool_use_id] = record

    # 起動元は `toolUseId` でたどる。conductor が起動したものは null のままにする
    for record, meta in subs:
        tool_use_id = meta.get("toolUseId")
        launcher = by_tool_use.get(tool_use_id) if isinstance(tool_use_id, str) else None
        if launcher is not None and launcher.layer != "conductor":
            record.parent_agent_id = launcher.agent_id

    if counter is not None:
        counter["skipped"] = counter.get("skipped", 0) + skipped
    return records


def read_sessions(
    sessions: list[str],
    root: pathlib.Path | str | None = None,
    counter: dict | None = None,
) -> list[AgentRecord]:
    out: list[AgentRecord] = []
    for session in sessions:
        out.extend(read_session(session, root=root, counter=counter))
    return out


# ---------- 出力 ----------

def _minutes(seconds: int) -> str:
    return f"{seconds / 60:.1f}"


def _cell(value) -> str:
    return "-" if value is None else str(value)


LIST_HEADER = (
    "| 層 | 持ち場 | 深さ | モデル | 固定費 | 最大充填 | 実作業 | 応答数 "
    "| 所要（分） | 終わり方 | 中断 |"
)
LIST_RULE = "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |"


def format_list(records: list[AgentRecord], with_agent_id: bool = True) -> str:
    """記録 1 件につき 1 行の表を返す。

    **`agent_id` はこの一覧にだけ出す。** `skill-stats` の集計は識別子を持たない
    （振り返りのコメントへ貼る表に載せないため。契約の文書の「出力に含めないもの」）。
    """
    header = LIST_HEADER + (" agent_id |" if with_agent_id else "")
    rule = LIST_RULE + (" --- |" if with_agent_id else "")
    lines = [header, rule]
    for r in records:
        row = (
            f"| {r.layer} | {r.role} | {r.depth} | {_cell(r.model)} | "
            f"{_cell(r.fixed)} | {_cell(r.peak)} | {_cell(r.work)} | {r.responses} | "
            f"{_minutes(r.duration_seconds)} | {r.ending} | {r.interruptions} |"
        )
        lines.append(row + (f" {_cell(r.agent_id)} |" if with_agent_id else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="会話の記録を conductor / supervisor / worker の層で読む",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="そのセッションの 3 層すべての記録を出す")
    listing.add_argument("--session", action="append", default=[], required=True,
                         help="セッション ID（繰り返して複数を渡せる）")
    listing.add_argument("--layer", choices=LAYERS, default=None,
                         help="1 つの層に絞る")
    listing.add_argument("--format", choices=["md", "json"], default="md")
    args = parser.parse_args(argv)

    counter: dict = {}
    records = read_sessions(args.session, counter=counter)
    if args.layer:
        records = [r for r in records if r.layer == args.layer]

    skipped = counter.get("skipped", 0)
    if skipped:
        print(f"[transcript-agents] 読めない行を飛ばした: {skipped} 件", file=sys.stderr)
    if not records:
        print(
            "[transcript-agents] 記録が見つからない: "
            f"{' '.join(args.session)}", file=sys.stderr,
        )

    if args.format == "json":
        print(json.dumps(
            {"agents": [r.as_json() for r in records], "skipped": skipped},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(format_list(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
