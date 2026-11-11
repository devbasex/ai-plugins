"""並列の計画のオーガナイザーの切り替えの判定（#3006）。`parallel.py wait` が上限で戻るときに 1 回だけ使う。

- `read_inputs(main, name, interval)`: 材料（答えていない問い・ラッパーとの位置・問いの間隔・会話の文脈・費用の見込み）を読む。
  読めない材料は None にして `missing` に名前を入れ、例外を外へ出さない（I5）
- `judge(inputs)`: 材料から「切り替える」「報告して待つ」を出す純粋な関数。ファイル・子プロセス・言語モデルを使わない
- `report(main, name, interval)`: `wait` の `metrics.switch` に載せる辞書

判定の規則（順に見て、最初に当たった行で決める。条件と閾値の説明は `references/parallel.md` の「長い待ちの切り替え」）:
1. 答えていない問いが 1 件以上（読めなければ 1 件以上として扱う）→ 報告して待つ
2. ラッパーの直接の子でない（`relay.py is-child` が 0 でない）→ 報告して待つ
3. 待ちの見込み W が出せない（経過を超える問いの間隔が 3 件未満）→ 報告して待つ
4. 文脈 C か、新しいセッションの文脈 Q が出せない → W ≥ 4 時間なら切り替える
5. 報告を続ける費用 n·r·C > 切り替える費用 h·r·C + 5·H + 2·Q + q·r·Q + n·r·Q なら切り替える（n = ⌈W ÷ T⌉）
"""

from __future__ import annotations

import json
import math
import os
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import context_policy as cp
from token_rates import read_rate

SWITCH, STAY = "切り替える", "報告して待つ"
LONG_WAIT_S = 14_400  # 文脈を読めないときに切り替える待ちの見込みの閾値（決定 3。実測で最も大きかった文脈が切り替えに変わる時点）
DEFAULT_HANDOFF_CALLS = 3.0  # 較正した値が無いときの引継ぎの呼び出し回数 h
DEFAULT_HANDOFF_OUTPUT = 2_000.0  # 較正した値が無いときの引継ぎの出力 token H
DEFAULT_RESTORE_CALLS = 5.0  # 較正した値が無いときの復元の呼び出し回数 q
CONTEXT_POLICY = Path(__file__).resolve().parent.parent / "context-policy.py"
TAIL_LINES = 200  # 最後の文脈を探す会話の記録の末尾の行数（`hook_lib/token_guard.TAIL_LINES` と同じ）


@dataclass
class SwitchInputs:
    unanswered: int | None = None
    relay: str | None = None
    gaps_s: list[float] = field(default_factory=list)
    elapsed_s: float | None = None
    context: int | None = None
    model: str | None = None
    base_context: int | None = None
    estimates: cp.Estimates = field(default_factory=cp.Estimates)
    interval_s: float = 1800.0
    missing: list[str] = field(default_factory=list)


@dataclass
class SwitchVerdict:
    verdict: str
    reason: str
    wait_s: float | None = None
    samples: int = 0
    cost: dict | None = None
    missing: list[str] = field(default_factory=list)
    defaults: list[str] = field(default_factory=list)
    base_context: float | None = None


def remaining_wait(gaps_s: list[float], elapsed_s: float | None) -> tuple[float | None, int]:
    """(経過を条件にした残りの待ちの中央値, 標本の数)。経過 e を超える間隔 g だけを選び、`g − e` の中央値を返す。
    標本が `MIN_SAMPLES` 未満なら None。"""
    if elapsed_s is None:
        return None, 0
    rest = [g - elapsed_s for g in gaps_s if g > elapsed_s]
    if len(rest) < cp.MIN_SAMPLES:
        return None, len(rest)
    return float(statistics.median(rest)), len(rest)


def _restart_terms(est: cp.Estimates, base_context: int | None) -> tuple[float, float, float | None, float, list[str]]:
    """(h, H, Q, q, 既定値を使った項)。較正の標本が 3 件以上あればその値、無ければ既定値（Q はこの会話の最初の文脈）。"""
    s, used = est.samples, []
    handoff_ok = s.get("handoff", 0) >= cp.MIN_SAMPLES
    restore_ok = s.get("restore", 0) >= cp.MIN_SAMPLES

    def pick(ok: bool, value, fallback, key: str):
        if ok and value is not None:
            return value
        used.append(key)
        return fallback

    h = pick(handoff_ok, est.handoff_calls, DEFAULT_HANDOFF_CALLS, "handoff_calls")
    big_h = pick(handoff_ok, est.handoff_output, DEFAULT_HANDOFF_OUTPUT, "handoff_output")
    q = pick(restore_ok, est.restore_calls, DEFAULT_RESTORE_CALLS, "restore_calls")
    big_q = pick(restore_ok, est.restore_context, None if base_context is None else float(base_context), "restore_context")
    return h, big_h, big_q, q, used


def judge(x: SwitchInputs) -> SwitchVerdict:
    """判定の規則の順に見て、最初に当たった行で決める（I1〜I3・I5）。"""
    missing = list(x.missing)
    w, samples = remaining_wait(x.gaps_s, x.elapsed_s)
    base = dict(wait_s=w, samples=samples, missing=missing)
    if x.unanswered is None or x.unanswered > 0:
        n = "読めない（1 件以上として扱う）" if x.unanswered is None else f"{x.unanswered} 件ある"
        return SwitchVerdict(STAY, f"答えていない問いが{n}", **base)
    if x.relay != "relay":
        return SwitchVerdict(STAY, f"ラッパーの直接の子でない（{x.relay or '読めない'}）", **base)
    if w is None:
        if "wait_s" not in missing:
            missing.append("wait_s")
        return SwitchVerdict(STAY, f"待ちの見込みを出せない（標本 {samples} 件。{cp.MIN_SAMPLES} 件から）", **base)
    h, big_h, big_q, q, defaults = _restart_terms(x.estimates, x.base_context)
    if x.context is None or big_q is None:
        verdict = SWITCH if w >= LONG_WAIT_S else STAY
        rel = "以上" if verdict == SWITCH else "未満"
        reason = f"文脈を読めず、待ちの見込み {w:.0f} 秒が閾値 {LONG_WAIT_S} 秒{rel}"
        return SwitchVerdict(verdict, reason, defaults=defaults, base_context=big_q, **base)
    n = math.ceil(w / max(1.0, x.interval_s))
    r, c = read_rate(x.model), float(x.context)
    stay = n * r * c
    switch = cp.restart_fixed_cost(c, x.model, h, big_h, big_q, q) + n * r * big_q
    cost = {"stay": round(stay), "switch": round(switch), "reports": n}
    if stay > switch:
        return SwitchVerdict(SWITCH, f"報告を続ける費用 {stay:,.0f} が切り替える費用 {switch:,.0f} を超える", cost=cost, defaults=defaults, base_context=big_q, **base)  # fmt: skip
    return SwitchVerdict(STAY, f"報告を続ける費用 {stay:,.0f} が切り替える費用 {switch:,.0f} 以下", cost=cost, defaults=defaults, base_context=big_q, **base)  # fmt: skip


# --- 材料の読み取り ------------------------------------------------------------------------------------


def _at(v) -> float | None:
    try:
        return datetime.strptime(str(v), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def question_gaps(rows: list[dict], now: float) -> tuple[list[float], float | None]:
    """(計画の台帳の問いの行の時刻の間隔, 最後の問いの行から今までの経過)。問いの行が無ければ ([], None)。"""
    import parallel_ledger as plg

    times = sorted(t for r in rows if r.get("kind") in plg.QUESTIONS and (t := _at(r.get("at"))) is not None)
    if not times:
        return [], None
    return [b - a for a, b in zip(times, times[1:])], max(0.0, now - times[-1])


def _transcript() -> Path | None:
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or ""
    if not sid:
        return None
    import transcript_agents

    path, _subs = transcript_agents.session_paths(sid)
    return path


def _usage(line: str) -> tuple[int, str | None] | None:
    """assistant の行の (文脈量, モデル)。文脈量は入力・キャッシュの読み・書きの和。ほかの行と読めない行は None。"""
    if '"assistant"' not in line:
        return None
    try:
        msg = json.loads(line).get("message") or {}
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            return None
        total = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    except (ValueError, TypeError, AttributeError):
        return None
    model = msg.get("model")
    return total, model if isinstance(model, str) else None


def session_context(path: Path) -> tuple[int | None, str | None, int | None]:
    """(最後の文脈 C, モデル, 最初の文脈)。末尾の `TAIL_LINES` 行と、先頭から最初の応答までだけを読む（非機能の性能）。
    `hook_lib/token_guard.py` の `last_context`・`first_context_tokens` と同じ量を読む。hook の側を読み込むと、
    `parallel.py` が hook の設定の読みまで届くため、ここで読む。"""
    first = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if (got := _usage(line)) is not None:
                first = got[0]
                break
        f.seek(0)
        tail = [u for line in deque(f, maxlen=TAIL_LINES) if (u := _usage(line)) is not None]
    if not tail:
        return None, None, first
    return tail[-1][0], tail[-1][1], first


def _relay_position() -> str:
    """ラッパーとの位置。`relay`（直接の子）か `outside`。判定は `relay.py is-child` の 1 か所に任せる（親のたどりは同じ）。"""
    import subprocess
    import sys

    from parallel_files import RELAY

    got = subprocess.run([sys.executable, str(RELAY), "is-child"], capture_output=True, text=True, timeout=10)
    return "relay" if got.returncode == 0 else "outside"


def read_inputs(main: Path, name: str, interval: float, now: float | None = None) -> SwitchInputs:
    """材料を読む。どの材料も読めなければ None にし、`missing` に名前を入れる（I5）。"""
    import time

    x = SwitchInputs(interval_s=float(interval))
    now = time.time() if now is None else now
    try:
        import parallel_ledger as plg
        import parallel_status as pst

        rows = plg.read_ledger_rows(main, name, 0).rows
        try:
            x.unanswered = len(pst.unanswered(main, name, rows))
        except Exception:  # noqa: BLE001
            x.missing.append("unanswered")
        x.gaps_s, x.elapsed_s = question_gaps(rows, now)
    except Exception:  # noqa: BLE001
        x.missing.append("unanswered")
    try:
        x.relay = _relay_position()
    except Exception:  # noqa: BLE001
        x.missing.append("relay")
    try:
        path = _transcript()
        if path is not None:
            x.context, x.model, x.base_context = session_context(path)
    except Exception:  # noqa: BLE001
        pass
    if x.context is None:
        x.missing.append("context")
    if x.base_context is None:
        x.missing.append("base_context")
    try:
        import context_decisions as cd

        x.estimates = cp.Estimates.from_dict(cd.read_cost_estimates(cd.decisions_repo(str(main))))
    except Exception:  # noqa: BLE001
        x.missing.append("estimates")
    return x


def record_line(name: str, context: int | None) -> str:
    """切り替えを再起動の判定の記録へ残す 1 行（理由「待ち」）。"""
    tail = f" --context {context}" if context is not None else ""
    return f'python3 {CONTEXT_POLICY} record --verdict {cp.RESTART} --reason {cp.REASON_WAIT} --work "parallel {name}"{tail}'


def to_metrics(x: SwitchInputs, v: SwitchVerdict, name: str) -> dict:
    return {
        "verdict": v.verdict,
        "reason": v.reason,
        "inputs": {
            "unanswered": x.unanswered,
            "relay": x.relay,
            "samples": v.samples,
            "elapsed_s": None if x.elapsed_s is None else round(x.elapsed_s),
            "wait_s": None if v.wait_s is None else round(v.wait_s),
            "context": x.context,
            "base_context": None if v.base_context is None else round(v.base_context),
            "model": x.model,
            "read_rate": read_rate(x.model),
            "interval_s": x.interval_s,
        },
        "cost": v.cost,
        "defaults": v.defaults,
        "missing": v.missing,
        "record": record_line(name, x.context) if v.verdict == SWITCH else None,
    }


def report(main: Path, name: str, interval: float) -> dict:
    """`wait` の `metrics.switch`。読み取りか判定が例外を出しても「報告して待つ」を返す（I5）。"""
    try:
        x = read_inputs(main, name, interval)
        return to_metrics(x, judge(x), name)
    except Exception as exc:  # noqa: BLE001
        x = SwitchInputs(interval_s=float(interval), missing=["all"])
        return to_metrics(x, SwitchVerdict(STAY, f"材料を読めない（{type(exc).__name__}）", missing=["all"]), name)
