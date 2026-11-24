"""裏の作業を待つ子のプロンプトのキャッシュを起床させる判定（#4068・#4199 の詳細設計の「判定（`keep_cache`）」）。

純粋な関数で、時刻・値・会話の記録の末尾の文字列を引数で受け、ファイルを開かない。

- `last_turn` は今回の Stop の応答を末尾から読む（`adapters.transcript.last_context` を 1 回だけ呼ぶ）
- `wake_reply` は起床の後の応答（起床の呼び出し）を、`wake_opening` は応答を始めた入力を読む
- `ratios`・`wake_at`・`n_max`・`wake_step`・`cache_hit` は起床の時点・回数の上限・次の手・当たりを決める
- `is_wake_input` は起床の文の入力の行の形だけを知る。形はまだ測っていないので、実装の PR では常に None を返す
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping, NamedTuple, Optional

from ndf.adapters import transcript
from ndf.domains.hooks.tables import TableError

# 公式の寿命の秒（値段ではないので上限の設定に置かない）
TTL_SECONDS = {"1h": 3600, "5m": 300}
# 眠る hook 1 つが 1 回の眠りで世代を確かめる回数の上限（利用者が設定した間隔の値の守り）
MAX_CHECKS = 300
# 起床の文の頭（起きた応答の見分けに使う）
WAKE_HEAD = "【ndf-relay】"
# 起床の文（`k`・`n_max` を埋める）
WAKE_TEXT = WAKE_HEAD + "キャッシュを保つための起床（{k}/{n_max}）。Tool を呼ばずに「待機を続ける」の 1 行だけで応答を終える。"
# 寿命別の書き込みの項目（`usage.cache_creation` の中）
WRITE_KEYS = {"1h": "ephemeral_1h_input_tokens", "5m": "ephemeral_5m_input_tokens"}
_READ, _WRITE = transcript.USAGE_KEYS[1:]


class LastTurn(NamedTuple):
    """今回の応答。`ttl` は応答の寿命別の書き込みで決めた寿命（両方 0 なら None）。"""

    message_id: str
    at: float
    tokens: int
    ttl: Optional[str]
    model: str
    cache_read: int
    cache_write: int
    output: int


class TurnRead(NamedTuple):
    """`last_turn` の結果。`reason` は `unrecorded`・`form`・`newer` か None、`ref` は `form` のときの (message_id, at)。"""

    turn: Optional[LastTurn]
    reason: Optional[str]
    ref: Optional[tuple[str, float]] = None


class Usage(NamedTuple):
    """起床に属する全呼び出しの合計。`priced` が偽なら寿命別の書き込みは None。"""

    calls: int
    cache_read: int
    output: int
    cache_write_1h: Optional[int]
    cache_write_5m: Optional[int]
    priced: bool


class Reply(NamedTuple):
    """`wake_reply` の結果。`kind` は `wake`・`preempted`・`unknown`・`truncated`。"""

    kind: str
    first_read: Optional[int]
    usage: Optional[Usage]


class Opening(NamedTuple):
    """`wake_opening` の結果。`kind` は `wake`・`normal`・`unknown`。`normal` のときだけ `at` と `first_read` を持つ。"""

    kind: str
    at: Optional[float] = None
    first_read: Optional[int] = None


class Ratios(NamedTuple):
    """判定に使う倍率と値（`ratios` が上限の設定から読む）。"""

    w: float
    w_1h: float
    w_5m: float
    r: float
    d: float
    out: float
    wake_margin: float
    interval: float
    hit: float


class Step(NamedTuple):
    """`wake_step` の結果。`kind` は `wake`・`stop`、`reason` は `count`（回数の上限）・`cost`（費用の上限）か None。"""

    kind: str
    reason: Optional[str] = None


def _int_value(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _usage_row(rec: Mapping[str, Any]) -> Mapping[str, Any]:
    msg = rec.get("message")
    got = msg.get("usage") if isinstance(msg, dict) else None
    return got if isinstance(got, dict) else {}


def _amount(usage: Mapping[str, Any], key: str) -> int:
    """`usage` の量の項目。無いか整数でなければ 0（合計に入れない）。"""
    got = usage[key] if key in usage else 0
    return got if _int_value(got) else 0


def _split_writes(usage: Mapping[str, Any]) -> Optional[tuple[int, int]]:
    """寿命別の書き込み（1 時間・5 分）。項目が欠ける・整数でない・負なら None。"""
    split = usage.get("cache_creation")
    if not isinstance(split, dict):
        return None
    got = tuple(split[k] if k in split else None for k in WRITE_KEYS.values())
    return got if all(_int_value(v) and v >= 0 for v in got) else None


def ttl_of(writes: tuple[int, int]) -> Optional[str]:
    """寿命別の書き込みから寿命を決める。1 時間が正なら `1h`、5 分だけ正なら `5m`、両方 0 なら None（未定）。"""
    one_hour, five_min = writes
    if one_hour > 0:
        return "1h"
    return "5m" if five_min > 0 else None


def last_turn(tail: str, last_message: Optional[str], started: float) -> TurnRead:
    """今回の Stop の応答を末尾から読む。状態を読まず、状態の寿命も受けない（補うのは `_settle` だけ）。"""
    got = transcript.last_context(tail, transcript.Reply(last_message))
    at = transcript.timestamp_seconds(got.timestamp)
    ref = (got.message_id, at) if got.message_id is not None and at is not None else None
    matched = got.state == "current" or got.reason == "form"
    if matched and ref is not None and ref[1] > started:
        return TurnRead(None, "newer")
    if got.reason == "form":
        return TurnRead(None, "form", ref)
    if got.state != "current" or ref is None or got.entry is None:
        return TurnRead(None, "unrecorded")
    turn = _turn_of(got.entry, ref, got.tokens)
    return TurnRead(turn, None) if turn is not None else TurnRead(None, "form", ref)


def _turn_of(entry: Mapping[str, Any], ref: tuple[str, float], tokens: Optional[int]) -> Optional[LastTurn]:
    usage = _usage_row(entry)
    model = entry["message"].get("model")
    output = usage.get("output_tokens")
    writes = _split_writes(usage)
    if tokens is None or not _int_value(output) or not isinstance(model, str) or not model or writes is None:
        return None
    return LastTurn(ref[0], ref[1], tokens, ttl_of(writes), model, _amount(usage, _READ), _amount(usage, _WRITE), output)


def is_wake_input(line: Mapping[str, Any]) -> Optional[bool]:
    """入力の行 1 つが起床の文なら真、通常の入力なら偽、決められなければ None。

    起床の文が会話の記録の入力の行にどう載るかは、まだ測っていない（設計の決定 7 の実測 8）。実装の PR では常に None を返し、
    バージョンの一覧（`cache.rewake` の `versions`）も空のままにする。有効化の PR で、測った形でこの関数を直す。
    """
    return None


# ---------------------------------------------------------------- 行の数え方（`wake_reply`・`wake_opening`）


def _is_tool_result(rec: Mapping[str, Any]) -> bool:
    content = rec["message"].get("content") if isinstance(rec.get("message"), dict) else None
    return isinstance(content, list) and bool(content) and all(isinstance(e, dict) and e.get("type") == "tool_result" for e in content)


def _parsed_tail(tail: str) -> list:
    """末尾の行を解釈した列。圧縮の境の直後の最初の `user` の行（要約の行）には目印の `_summary` を立てる。"""
    rows = [transcript.json_line(line) for line in tail.splitlines() if line.strip()]
    after_boundary = False
    for i, rec in enumerate(rows):
        if transcript.is_boundary(rec):
            after_boundary = True
        elif after_boundary and transcript.is_talk(rec) and rec["type"] == "user":
            rows[i] = {**rec, "_summary": True}
            after_boundary = False
    return rows


def _input_kind(rec: Any) -> Optional[str]:
    """入力の行の種類（`wake`・`normal`・`unknown`）。入力に数えない行（会話でない・応答・Tool の結果・要約・付随する行）は None。"""
    if not transcript.is_talk(rec) or rec["type"] != "user" or rec.get("_summary") or _is_tool_result(rec):
        return None
    woke = is_wake_input(rec)
    if woke is not None:
        return "wake" if woke else "normal"
    return None if rec.get("isMeta") is True else "unknown"


def _call_rows(rows: list) -> list:
    """応答の行の列から、呼び出しごとの採用行（同じ `message.id` の最も新しい物理の行）をファイルの順に並べる。"""
    order: list = []
    adopted: dict = {}
    for i, rec in enumerate(rows):
        if not transcript.is_reply_line(rec):
            continue
        key = transcript.message_id(rec) or ("row", i)
        if key not in adopted:
            order.append(key)
        adopted[key] = rec
    return [adopted[k] for k in order]


def _sum_usage(calls: list) -> Usage:
    usages = [_usage_row(c) for c in calls]
    split = [_split_writes(u) for u in usages]
    priced = all(s is not None and sum(s) == _amount(u, _WRITE) and _WRITE in u for s, u in zip(split, usages))
    one_hour = sum(s[0] for s in split) if priced else None
    five_min = sum(s[1] for s in split) if priced else None
    return Usage(len(calls), sum(_amount(u, _READ) for u in usages), sum(_amount(u, "output_tokens") for u in usages), one_hour, five_min, priced)


def _wake_start(rows: list, after_id: str) -> Any:
    """`after_id` の応答の直後の最初の入力が起床の文ならその位置、ほかは返す `Reply`。"""
    ends = [i for i, rec in enumerate(rows) if transcript.is_reply_line(rec) and transcript.message_id(rec) == after_id]
    if not ends:
        return Reply("truncated", None, None)
    for i in range(ends[-1] + 1, len(rows)):
        if transcript.is_boundary(rows[i]):
            return Reply("truncated", None, None)
        kind = _input_kind(rows[i])
        if kind == "wake":
            return i
        if kind is not None:
            return Reply("preempted" if kind == "normal" else "unknown", None, None)
    return Reply("unknown", None, None)


def _wake_body(rows: list) -> Optional[list]:
    """起床の文の次から次の入力の手前（無ければ末尾）までの行。間に圧縮の境があれば None。"""
    body = []
    for rec in rows:
        if transcript.is_boundary(rec):
            return None
        if _input_kind(rec) is not None:
            break
        body.append(rec)
    return body


def wake_reply(tail: str, after_id: str) -> Reply:
    """`after_id` の応答の直後の最初の入力が起床の文なら、その後の起床の呼び出しを読む。"""
    rows = _parsed_tail(tail)
    start = _wake_start(rows, after_id)
    if isinstance(start, Reply):
        return start
    body = _wake_body(rows[start + 1 :])
    if body is None:
        return Reply("truncated", None, None)
    calls = _call_rows(body)
    if not calls:
        return Reply("unknown", None, None)
    return Reply("wake", _amount(_usage_row(calls[0]), _READ), _sum_usage(calls))


def wake_opening(tail: str, message_id: str) -> Opening:
    """`message_id` の応答を始めた入力と、その入力の直後の最初の呼び出しの読み出しを返す。"""
    rows = _parsed_tail(tail)
    first = next((i for i, rec in enumerate(rows) if transcript.is_reply_line(rec) and transcript.message_id(rec) == message_id), None)
    if first is None:
        return Opening("unknown")
    for i in range(first - 1, -1, -1):
        if transcript.is_boundary(rows[i]):
            return Opening("unknown")
        kind = _input_kind(rows[i])
        if kind is None:
            continue
        if kind != "normal":
            return Opening(kind)
        at = transcript.timestamp_seconds(rows[i].get(transcript.TIMESTAMP))
        calls = _call_rows(rows[i + 1 :])
        if at is None or not calls:
            return Opening("unknown")
        return Opening("normal", at, _amount(_usage_row(calls[0]), _READ))
    return Opening("unknown")


# ---------------------------------------------------------------- 値と判定


def _as_number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TableError(f"{what} が数でない: {value!r}")
    return float(value)


def _mapping_or_refuse(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise TableError(f"{what} が辞書でない: {value!r}")
    return value


def _above_zero(value: float, what: str) -> float:
    if value <= 0:
        raise TableError(f"{what} は 0 より大きくする: {value}")
    return value


def ratios(get: Callable[[str, str], Any], model: str, ttl: str) -> Ratios:
    """上限の設定から倍率と値を読む。辞書でない・数でない・範囲の外の値は `TableError`。"""
    write = _mapping_or_refuse(get("cache.write-ratio", "ratios"), "cache.write-ratio の ratios")
    read = _mapping_or_refuse(get("cache.read-ratio", "ratios"), "cache.read-ratio の ratios")
    if ttl not in write or any(k not in write for k in TTL_SECONDS):
        raise TableError(f"cache.write-ratio の ratios に寿命 {ttl} の倍率が無い")
    r_raw = read[model] if model in read else get("cache.read-ratio", "default")
    w_1h, w_5m, w = (_above_zero(_as_number(write[k], f"書き込みの倍率 {k}"), f"書き込みの倍率 {k}") for k in ("1h", "5m", ttl))
    r = _above_zero(_as_number(r_raw, f"読み出しの倍率 {model}"), f"読み出しの倍率 {model}")
    out = _above_zero(_as_number(get("cache.output-ratio", "default"), "出力の倍率"), "出力の倍率")
    d = _as_number(get("cache.wake-cost", "count"), "起床の費用")
    if d < 1:
        raise TableError(f"起床の費用（cache.wake-cost の count）は 1 以上にする: {d}")
    margin = _as_number(get("cache.wake", "seconds"), "起床の時点の余白")
    interval = _above_zero(_as_number(get("cache.wake", "interval"), "確かめる間隔"), "確かめる間隔")
    _check_wake(margin, interval)
    hit_ratio = _as_number(get("cache.hit", "ratio"), "当たりの境")
    return Ratios(w, w_1h, w_5m, r, d, out, margin, interval, hit_ratio)


def _check_wake(margin: float, interval: float) -> None:
    """余白が 0 以上 1 時間未満で、1 回の眠りで世代を確かめる回数が `MAX_CHECKS` 以内か。外れれば `TableError`。"""
    if not 0 <= margin < TTL_SECONDS["1h"]:
        raise TableError(f"起床の時点の余白（cache.wake の seconds）は 0 以上 {TTL_SECONDS['1h']} 未満にする: {margin}")
    if math.ceil((TTL_SECONDS["1h"] - margin) / interval) > MAX_CHECKS:
        raise TableError(f"1 回の眠りで世代を確かめる回数が {MAX_CHECKS} を越える（余白 {margin}・間隔 {interval}）")


def wake_at(at: float, ttl: Optional[str], wake_margin: float) -> Optional[float]:
    """起床の時点。1 時間の寿命だけ `at + 3600 − 余白`、ほか（5 分）は None（起床させない）。"""
    return at + TTL_SECONDS["1h"] - wake_margin if ttl == "1h" else None


def n_max(tokens: int, q: Ratios) -> int:
    """書き直し 1 回の費用に届く起床の回数 `floor((w − r) × S / (r × S + d))`。0 未満にしない。"""
    return max(0, math.floor((q.w - q.r) * tokens / (q.r * tokens + q.d)))


def cache_hit(read: int, before: int, ratio: float) -> bool:
    """最初の呼び出しの読み出しが、前の文脈の量の `ratio` 以上ならキャッシュに当たった。"""
    return read >= before * ratio


def wake_step(k: int, limit: int, spent: float, tokens: int, q: Ratios) -> Step:
    """次の手。回数の上限か、累計に次の起床を足すと今の書き直しの費用を越えるなら `stop`、ほかは `wake`。"""
    if k + 1 > limit:
        return Step("stop", "count")
    if spent + q.r * tokens + q.d > (q.w - q.r) * tokens:
        return Step("stop", "cost")
    return Step("wake")
