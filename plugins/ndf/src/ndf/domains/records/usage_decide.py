"""使用量の使用量ログの数え方（純粋な関数。N8b・#1978・#1979・#2575・#2576）。

- 会話の記録は同じ `message.id` の応答を何行にも分けて書く。応答ごとに最後の行の usage を 1 回だけ数える
- 期間は応答ごとの時刻で絞る（日をまたぐ会話を丸ごと採る・落とすをしない）。日付の期間は JST の両端で読む
- 同じサブエージェント（`agent_id`）が続けて 2 回目以降に止まったときは、前までの行の合計との差を書く
- 集計は、会話の記録を読めるサブエージェントは記録の合計を正とし、使用量ログの行の抜け（停止の hook が最後の応答を
  読む前に書いた分）を補う。読めないものは使用量ログの行の合計にする
- 使用量ログの保存先は書き手によらず 1 つ（`NDF_USAGE_DIR` → 状態の保存先の usage）
- `claude -p` の行の `session_cost_usd` は会話の累計なので、起動 1 回の費用は同じ会話の 1 つ前の行との差で、集計の時に求める（#1959）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

# 数える応答が無い・会話の記録を読めないときの値（判定の「判定できない」と同じ綴り）
from ndf.progress.gate.checks import UNKNOWN

JST = timezone(timedelta(hours=9))


class Usage(NamedTuple):
    """1 つ以上の応答の使用量（token）。項目の名前は会話の記録の message.usage のキー。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def plus(self, other: Usage) -> Usage:
        return Usage(*(a + b for a, b in zip(self, other, strict=True)))

    def minus(self, other: Usage) -> Usage:
        """差（負にならない）。"""
        return Usage(*(max(a - b, 0) for a, b in zip(self, other, strict=True)))

    def as_dict(self) -> dict[str, int]:
        return self._asdict()


def total_of(usages: Iterable[Usage]) -> Usage:
    """使用量の総和（無ければ 0）。"""
    total = Usage()
    for u in usages:
        total = total.plus(u)
    return total


def usage_of(raw: Any) -> Optional[Usage]:
    """usage の辞書を読む。辞書でなければ None。無い項目は 0。"""
    if not isinstance(raw, Mapping):
        return None
    return Usage(*(int(raw[k]) if isinstance(raw.get(k), int) else 0 for k in Usage._fields))


def parse_time(text: Any) -> Optional[datetime]:
    """ISO 8601 の時刻を読む（末尾の Z も受ける）。読めなければ None。"""
    if not isinstance(text, str):
        return None
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:  # noqa: NDF401 ファイル: 読めない時刻は時刻の無い行として扱う
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def responses(records: Iterable[Mapping[str, Any]]) -> dict[str, tuple[Optional[datetime], Usage]]:
    """応答ごとの時刻と usage。同じ `message.id` の行が複数あれば最後の行の値（後の行が上書きする）。"""
    out: dict[str, tuple[Optional[datetime], Usage]] = {}
    for rec in records:
        msg = rec.get("message")
        if not isinstance(msg, Mapping) or not isinstance(msg.get("id"), str):
            continue
        u = usage_of(msg.get("usage"))
        if u is not None:
            out[msg["id"]] = (parse_time(rec.get("timestamp")), u)
    return out


def within(at: Optional[datetime], since: Optional[datetime], until: Optional[datetime]) -> bool:
    """時刻が期間の内か。期間を渡したときに時刻の無いものは外。"""
    if since is None and until is None:
        return True
    if at is None:
        return False
    return (since is None or at >= since) and (until is None or at <= until)


def transcript_total(
    records: Iterable[Mapping[str, Any]], since: Optional[datetime] = None, until: Optional[datetime] = None
) -> Usage | str:
    """会話の記録 1 本の使用量（期間を渡すと応答の時刻が期間内の応答だけ）。usage を持つ応答が 1 つも無ければ `UNKNOWN`。

    応答を読めて期間内に 1 つも無ければ 0（読めた記録の期間外の応答を、使用量ログの行として別の期間へ足さない）。
    """
    found = responses(records).values()
    if not found:
        return UNKNOWN
    return total_of(u for at, u in found if within(at, since, until))


def jst_bounds(since: date, until: date) -> tuple[datetime, datetime]:
    """日付の期間の JST の両端（`<since>T00:00:00+09:00`..`<until>T23:59:59+09:00`）。"""
    return datetime.combine(since, time(0, 0, 0), JST), datetime.combine(until, time(23, 59, 59), JST)


def closed_query(since: date, until: date) -> str:
    """閉じた課題を JST の両端で数える検索の条件（GitHub の `closed:` は時刻と時差を受ける）。"""
    a, b = jst_bounds(since, until)
    return f"closed:{a.isoformat()}..{b.isoformat()}"


def state_location_dir(env: Mapping[str, str], var: str, state_dir: Optional[Path], sub: str) -> Path:
    """状態の保存先の下の保存先（表 locations の行）。環境変数 `var` → 状態の保存先の `sub` の順。

    状態の保存先も無ければ `LookupError`（/tmp へ倒さない）。
    """
    if env.get(var):
        return Path(env[var])
    if state_dir is None:
        raise LookupError(f"使用量の使用量ログの保存先を決められない（{var} も状態の保存先も無い）")
    return state_dir / sub


def _row_usage(row: Mapping[str, Any]) -> Optional[Usage]:
    return usage_of(row.get("usage"))


def stop_delta(prior: Iterable[Mapping[str, Any]], agent_id: str, total: Usage) -> Usage:
    """同じ `agent_id` の前までの行の合計と、今の会話の記録の合計との差（2 回目以降の停止で書く値）。"""
    own = (_row_usage(row) for row in prior if row.get("agent_id") == agent_id)
    return total.minus(total_of(u for u in own if u is not None))


def _counted_row(
    row: Mapping[str, Any], transcripts: Mapping[str, Usage | str], since: Optional[datetime], until: Optional[datetime]
) -> bool:
    """使用量ログの行を数えるか。会話の記録を読めた agent の行は数えず、ほかは時刻が期間内の行を数える。"""
    agent = row.get("agent_id")
    if isinstance(agent, str) and isinstance(transcripts.get(agent), Usage):
        return False
    return within(parse_time(row.get("at")), since, until)


def reconcile(
    rows: Iterable[Mapping[str, Any]],
    transcripts: Mapping[str, Usage | str],
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> tuple[Usage, int]:
    """期間の合計と、usage を読めなかった行の数。

    `transcripts` は `agent_id` → その会話の記録の期間内の合計（読めなければ `UNKNOWN`）。読めた agent は記録の
    合計を 1 回だけ数え、使用量ログの行は数えない。ほかの行は時刻が期間内のものの usage を足す。
    """
    read = total_of(value for agent, value in transcripts.items() if isinstance(value, Usage) and agent)
    counted = [_row_usage(row) for row in rows if _counted_row(row, transcripts, since, until)]
    unknown = sum(1 for u in counted if u is None)
    return read.plus(total_of(u for u in counted if u is not None)), unknown


def _session_cost(row: Mapping[str, Any]) -> Optional[float]:
    value = row.get("session_cost_usd")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _first_per_launch(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """`launch_id` ごとに数える 1 行（値を持つ行のうち最初の行、無ければ最初の行）を、最初に現れた順に並べる。"""
    kept: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        launch = row.get("launch_id")
        if not isinstance(launch, str):
            continue
        if launch not in kept or (_session_cost(kept[launch]) is None and _session_cost(row) is not None):
            kept[launch] = row
    return list(kept.values())


def launch_costs(rows: Iterable[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    """`launch_id` → 起動 1 回の費用（USD）。求められなければ None（#1959 の「起動の費用の求め方」）。

    `session_cost_usd` は同じ会話の累計である。`session_seq` が 0 の行（`session_id` の無い行を含む）はその値、
    ほかは同じ `session_id` の番号が 1 つ前の行との差にする。値が無い、1 つ前の行に値が無い、差が負なら None。
    期間で絞る前の全部の行を受ける（集計期間より前の行も前の行として読む）。
    """
    kept = _first_per_launch(rows)
    totals = {
        (row["session_id"], row["session_seq"]): _session_cost(row)
        for row in kept
        if isinstance(row.get("session_id"), str) and isinstance(row.get("session_seq"), int)
    }
    return {row["launch_id"]: _launch_cost(row, totals) for row in kept}


def _launch_cost(row: Mapping[str, Any], totals: Mapping[tuple[str, int], Optional[float]]) -> Optional[float]:
    value, seq, session = _session_cost(row), row.get("session_seq"), row.get("session_id")
    if value is None or not isinstance(seq, int) or isinstance(seq, bool):
        return None
    if seq == 0:
        return value
    before = totals.get((session, seq - 1)) if isinstance(session, str) else None
    if before is None or value < before:
        return None
    return value - before
