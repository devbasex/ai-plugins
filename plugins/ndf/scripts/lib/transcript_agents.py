#!/usr/bin/env python3
"""会話の記録を conductor / supervisor / worker の層の単位で読む（#550）。

**読むだけの部品である。** ローカルの会話の記録（`~/.claude/projects/`）を開き、記録 1 件に
つき 1 つの `AgentRecord` を返す。ネットワークを開かず、送信先を持たない（AC34）。

値の取り方は `docs/specifications/ndf-context-window-metrics.md` の「`AgentRecord` の値」が正本で
ある。**出力に載せないもの**もそこが決めている（`description` の `: ` より後ろ・ファイルの
パス・プロンプト・応答とツールの本文・`requestId`・`cwd`・`gitBranch`）。

コマンド:

    python3 transcript_agents.py list --session <ID> [--layer 層] [--format md|json]
    python3 transcript_agents.py interrupted --session <ID> [--layer 層] [--depth N]
                                             [--agent <id>...] [--parent <id>]
                                             [--now <ISO 8601>] [--format md|json]
    python3 transcript_agents.py wait-reset --session <ID> [--layer 層] [--depth N]
                                            [--margin 秒] [--max-sleep 秒]

**上限の中断は通知ではなく記録で見分ける**（#657 の決定 18）。通知の本文は人が読む文言で
書式を約束していないが、記録の合成の応答は `apiErrorStatus` と `quotaLimits.resetsAt` を値
として持つ。`wait-reset` が眠る長さも**記録の解除時刻から取る**。固定の間隔で待たない。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------- 語彙（契約の文書の「語彙」の表） ----------

LAYERS = ("conductor", "supervisor", "worker")
POSTS = ("設計", "実装", "検査", "取り込み", "仕上げ")          # フェーズ（supervisor）
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


# ---------- 層とフェーズ ----------

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
    """フェーズ（supervisor）または作業の種類（worker）を返す。"""
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


def _token_stats(rows: list[dict]) -> tuple[int | None, int | None, int | None]:
    """合成でない応答から固定費・最大充填・実作業を返す。"""
    fixed = None
    peak = None
    for row in rows:
        if row.get("type") != "assistant" or _is_synthetic(row):
            continue
        total = _input_total(row)
        if total is not None:
            if fixed is None:
                fixed = total
            peak = total if peak is None else max(peak, total)
    work = peak - fixed if fixed is not None and peak is not None else None
    return fixed, peak, work


def _model_stats(rows: list[dict]) -> tuple[int, str | None]:
    """合成でない応答から応答数と最頻出モデルを返す。"""
    seen: set[str] = set()
    models: Counter = Counter()
    for row in rows:
        if row.get("type") != "assistant" or _is_synthetic(row):
            continue
        message_id = _message(row).get("id")
        if isinstance(message_id, str) and message_id and message_id not in seen:
            seen.add(message_id)
            model = _message(row).get("model")
            if isinstance(model, str) and model:
                models[model] += 1
    model = models.most_common(1)[0][0] if models else None
    return len(seen), model


def _aggregate_token_metrics(rows: list[dict], record: AgentRecord) -> None:
    """固定費・最大充填・応答数・モデルを合成でない応答だけで数える（AC24）。

    `record` の `fixed` / `peak` / `work` / `responses` / `model` を埋める。
    """
    record.fixed, record.peak, record.work = _token_stats(rows)
    record.responses, record.model = _model_stats(rows)


def _count_interruptions(rows: list[dict]) -> int:
    """上限の中断から続けた回数（429 の合成の応答のうち、後ろに合成でない応答が続くもの）。"""
    interruptions = 0
    pending = 0
    for row in rows:
        if row.get("type") != "assistant":
            continue
        if _is_synthetic(row):
            if _error_status(row) == "429":
                pending += 1
            continue
        interruptions += pending
        pending = 0
    return interruptions


def _calculate_duration(rows: list[dict]) -> tuple[str | None, str | None, int]:
    """タイムスタンプ走査で開始/終了時刻と所要時間（秒）を返す。"""
    times = [t for t in (_parse_time(row.get("timestamp")) for row in rows) if t]
    if not times:
        return None, None, 0
    return times[0].isoformat(), times[-1].isoformat(), int(
        (times[-1] - times[0]).total_seconds()
    )


def _fill_rate_limit(rows: list[dict], record: AgentRecord) -> None:
    """上限の中断のとき、最後の合成の応答から解除時刻と上限の種類を取る。

    見るのは**最後の合成の応答 1 件だけ**である。1 つの記録が 2 度中断していても、
    次に待つのは最後の解除時刻だからである。
    """
    if record.ending != "rate_limit":
        return
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

    _aggregate_token_metrics(rows, record)
    record.interruptions = _count_interruptions(rows)

    _fill_rate_limit(rows, record)

    record.started_at, record.ended_at, record.duration_seconds = (
        _calculate_duration(rows)
    )
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
    _link_parent_agents(records, subs)

    if counter is not None:
        counter["skipped"] = counter.get("skipped", 0) + skipped
    return records


def _link_parent_agents(
    records: list[AgentRecord], subs: list[tuple[AgentRecord, dict]],
) -> None:
    by_tool_use: dict[str, AgentRecord] = {}
    for record in records:
        for tool_use_id in record.tool_use_ids:
            by_tool_use[tool_use_id] = record

    # 起動元は `toolUseId` でたどる。conductor が起動したものは null のままにする
    for record, meta in subs:
        tool_use_id = meta.get("toolUseId")
        launcher = by_tool_use.get(tool_use_id) if isinstance(tool_use_id, str) else None
        if launcher is not None and launcher.layer != "conductor":
            record.parent_agent_id = launcher.agent_id


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
    "| 層 | フェーズ | 深さ | モデル | 固定費 | 最大充填 | 実作業 | 応答数 "
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


# ---------- 中断と再開（#657） ----------

def parse_now(value: str | None = None) -> datetime:
    """`--now` の値を読む。省いたときは現在時刻（UTC）を返す。"""
    if not value:
        return datetime.now(timezone.utc)
    parsed = _parse_time(value)
    if parsed is None:
        raise ValueError(f"時刻として読めない: {value}")
    return parsed


def resets_passed(record: AgentRecord, now: datetime | None = None) -> bool:
    """上限が解けたかを返す。

    **解除時刻を持たない上限の中断は真にする。** 待つ先が無いまま止まるのを避けるため
    である。記録から取れないだけで、上限そのものは解けているかもしれない。
    """
    reset = _parse_time(record.resets_at)
    if reset is None:
        return True
    return reset <= (now or datetime.now(timezone.utc))


def interrupted(
    records: list[AgentRecord],
    layer: str | None = None,
    depth: int | None = None,
    agents: list[str] | None = None,
    parent: str | None = None,
) -> list[AgentRecord]:
    """上限の中断（`ending` が `rate_limit`）だけを、指定の絞り込みで返す。

    `server_error`（500 / 529）と `authentication_failed` は `ending` が `api_error` に
    なるため、ここには現れない（AC40）。続けさせた直後の記録は `in_progress` であり、
    再び現れない（契約の終わり方の表）。
    """
    picked = set(agents or ())
    # 記録の属性名 → 期待値。指定の無い（None の）軸は絞り込まない
    wanted = {attr: value for attr, value in (("layer", layer), ("depth", depth),
                                              ("parent_agent_id", parent))
              if value is not None}
    out: list[AgentRecord] = []
    for record in records:
        if record.ending != "rate_limit":
            continue
        if any(getattr(record, attr) != value for attr, value in wanted.items()):
            continue
        if picked and record.agent_id not in picked:
            continue
        out.append(record)
    return out


INTERRUPTED_HEADER = (
    "| 層 | フェーズ | 深さ | 終わり方 | 上限の種類 | 解除時刻 | 解除済み "
    "| 起動元 | agent_id |"
)
INTERRUPTED_RULE = "| --- | --- | ---: | --- | --- | --- | --- | --- | --- |"


def format_interrupted(
    records: list[AgentRecord], now: datetime | None = None,
) -> str:
    """中断した記録の一覧を返す。**解除時刻と起動元が読める**（AC47）。"""
    lines = [INTERRUPTED_HEADER, INTERRUPTED_RULE]
    for r in records:
        passed = "済" if resets_passed(r, now) else "まだ"
        lines.append(
            f"| {r.layer} | {r.role} | {r.depth} | {r.ending} | "
            f"{_cell(r.rate_limit_type)} | {_cell(r.resets_at)} | {passed} | "
            f"{_cell(r.parent_agent_id)} | {_cell(r.agent_id)} |"
        )
    return "\n".join(lines)


def _sleep(seconds: float) -> None:
    """眠る。テストはこの関数を差し替えて秒数だけを見る。"""
    time.sleep(seconds)


def wait_reset(
    sessions: list[str],
    root: pathlib.Path | str | None = None,
    layer: str | None = None,
    depth: int | None = None,
    margin: int = 60,
    max_sleep: int | None = None,
    now: datetime | None = None,
    sleeper=None,
) -> tuple[int, int, int]:
    """解除まで眠る。返すのは（眠った秒数、起きた時点の中断の件数、終了コード）である。

    **眠るのは、まだ来ていない解除時刻のうち最も早いもの + `margin` までである。**
    固定の間隔で待たない（AC41）。終了コードは 0 = 解除時刻を過ぎた、
    3 = `max_sleep` で区切った（まだ解除前）。
    """
    sleeper = sleeper or _sleep
    now = now or datetime.now(timezone.utc)

    def pick() -> list[AgentRecord]:
        return interrupted(
            read_sessions(sessions, root=root), layer=layer, depth=depth,
        )

    futures = [
        t for t in (_parse_time(r.resets_at) for r in pick()) if t and t > now
    ]
    slept = 0
    cut = False
    if futures:
        # **切り上げる。** 端数を捨てると解除時刻より早く起きてしまい、`--margin 0` では
        # 解除の前に再開してよいと読める答えを返す（AC44）。
        seconds = math.ceil((min(futures) - now).total_seconds()) + int(margin)
        slept = max(seconds, 0)
        if max_sleep is not None and slept > max_sleep:
            slept = int(max_sleep)
            cut = True
        sleeper(slept)
    return slept, len(pick()), 3 if cut else 0


def _now_argument(value: str) -> datetime:
    try:
        return parse_now(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _non_negative_int(value: str) -> int:
    """秒数の引数を読む。**負の値は引数の誤り（終了コード 2）にする。**

    負の `--max-sleep` は `time.sleep` が拒み、負の `--margin` は解除の前に待ちを
    終わらせる。どちらも待ちの保証（AC44）を壊すため、眠る前に弾く。
    """
    try:
        seconds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"整数として読めない: {value}") from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError(f"負の秒数は受け付けない: {value}")
    return seconds


def _add_session_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", action="append", default=[], required=True,
                        help="セッション ID（繰り返して複数を渡せる）")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="会話の記録を conductor / supervisor / worker の層で読む",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="そのセッションの 3 層すべての記録を出す")
    _add_session_argument(listing)
    listing.add_argument("--layer", choices=LAYERS, default=None,
                         help="1 つの層に絞る")
    listing.add_argument("--format", choices=["md", "json"], default="md")

    stuck = sub.add_parser("interrupted", help="上限の中断だけを出す")
    _add_session_argument(stuck)
    stuck.add_argument("--layer", choices=LAYERS, default=None, help="1 つの層に絞る")
    stuck.add_argument("--depth", type=int, default=None,
                       help="深さで絞る。1 は conductor の直下")
    stuck.add_argument("--agent", action="append", default=[],
                       help="agent_id で絞る（繰り返して複数を渡せる）")
    stuck.add_argument("--parent", default=None, help="起動元の agent_id で絞る")
    stuck.add_argument("--now", type=_now_argument, default=None,
                       help="解除済みの判定に使う時刻（試験用。既定は現在時刻）")
    stuck.add_argument("--format", choices=["md", "json"], default="md")

    waiting = sub.add_parser("wait-reset", help="解除時刻まで眠る")
    _add_session_argument(waiting)
    waiting.add_argument("--layer", choices=LAYERS, default=None, help="1 つの層に絞る")
    waiting.add_argument("--depth", type=int, default=None, help="深さで絞る")
    waiting.add_argument("--margin", type=_non_negative_int, default=60,
                         help="解除時刻の後に置く余白の秒数（既定 60）")
    waiting.add_argument("--max-sleep", type=_non_negative_int, default=None,
                         help="1 度に眠る上限の秒数。区切ったときは終了コード 3")
    return parser


def _report_skipped(counter: dict, sessions: list[str], found: bool) -> int:
    skipped = counter.get("skipped", 0)
    if skipped:
        print(f"[transcript-agents] 読めない行を飛ばした: {skipped} 件", file=sys.stderr)
    if not found:
        print(
            "[transcript-agents] 記録が見つからない: "
            f"{' '.join(sessions)}", file=sys.stderr,
        )
    return skipped


def _run_list(args) -> int:
    counter: dict = {}
    records = read_sessions(args.session, counter=counter)
    if args.layer:
        records = [r for r in records if r.layer == args.layer]
    skipped = _report_skipped(counter, args.session, bool(records))

    if args.format == "json":
        print(json.dumps(
            {"agents": [r.as_json() for r in records], "skipped": skipped},
            ensure_ascii=False, indent=2,
        ))
    else:
        print(format_list(records))
    return 0


def _run_interrupted(args) -> int:
    counter: dict = {}
    all_records = read_sessions(args.session, counter=counter)
    records = interrupted(
        all_records, layer=args.layer, depth=args.depth,
        agents=args.agent, parent=args.parent,
    )
    skipped = _report_skipped(counter, args.session, bool(all_records))
    now = args.now or datetime.now(timezone.utc)

    if args.format == "json":
        rows = []
        for record in records:
            row = record.as_json()
            row["resets_passed"] = resets_passed(record, now)
            rows.append(row)
        print(json.dumps(
            {"agents": rows, "skipped": skipped}, ensure_ascii=False, indent=2,
        ))
    else:
        print(format_interrupted(records, now))
    return 0


def _run_wait_reset(args) -> int:
    slept, remaining, code = wait_reset(
        args.session, layer=args.layer, depth=args.depth,
        margin=args.margin, max_sleep=args.max_sleep,
    )
    print(
        f"[transcript-agents] 眠った: {slept} 秒 / 中断した記録: {remaining} 件"
    )
    return code


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "interrupted":
        return _run_interrupted(args)
    if args.command == "wait-reset":
        return _run_wait_reset(args)
    return _run_list(args)


if __name__ == "__main__":
    sys.exit(main())
