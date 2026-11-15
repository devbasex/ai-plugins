"""再起動の方針の判定の記録・会話の記録の読み取り・費用の見込みの較正（#3569。旧い `lib/context_decisions.py` と
`lib/context_calibrate.py` の移し先）。

置き場は旧いプラグインと同じ（`NDF_CONTEXT_DIR` → `<旧い状態の根>/context`）で、入れ替えの前は旧い
`context-policy.py` と hook も同じファイルを読み書きする。そのため行の形（キー・値の型）を足さない・変えない。

| ファイル | 中身 | 書き方 |
| --- | --- | --- |
| `decisions/<owner>__<repo>.decisions.jsonl` | 判定の記録（1 行 1 件） | 排他を取らず 1 回の書き込みで 1 行を追記する |
| `estimates/<owner>__<repo>.cost-estimates.json` | 費用の見込み（`calibrate` が書く） | 一時ファイルからの置き換え（後勝ち） |
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ndf.adapters import git
from ndf.domains.records import jsonl, usage_decide
from ndf.domains.relay import context_policy as cp
from ndf.domains.relay import registry, signals

SCHEMA = 1
LOCAL = "_local"
UNKNOWN_REPO = "unknown"
MARK_HANDOFF, MARK_RESTORED = "引継ぎ開始", "復元完了"
MARKS = (MARK_HANDOFF, MARK_RESTORED)
EVENT_DECISION = "判定"
UNKNOWN = "不明"
HANDOFF_PATH = re.compile(r"\.ndf/handoff/")
CACHE_1H = 3600  # 呼び出しの間隔がこれを超えたら、1 時間のキャッシュが切れたとみなす
ISSUE = re.compile(r"(?:^|[^0-9A-Za-z_/])#?([0-9]+)\b")
NOISE = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]+(?:\.[0-9]+)+")
_SLUG = re.compile(r"([^/:]+)/([^/]+?)(?:\.git)?/?$")


# --- 置き場 ------------------------------------------------------------------------------------------


def context_dir(environ: Mapping[str, str]) -> Path:
    """旧い置き場（`NDF_CONTEXT_DIR` → `<旧い状態の根>/context`）。"""
    explicit = environ.get("NDF_CONTEXT_DIR")
    return Path(explicit) if explicit else registry.legacy_state_root(environ) / "context"


def toplevel(cwd: Optional[Path]) -> Optional[Path]:
    """作業ディレクトリの git の根。git の外か、git が無ければ None。"""
    try:
        done = git.run_git(["rev-parse", "--show-toplevel"], cwd)
    except git.GitError:  # noqa: NDF401 外部コマンド: git が無ければ git の外として扱う
        return None
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def repo_key(root: Optional[Path]) -> str:
    """`<owner>__<repo>`（origin の URL から。旧い `usage_ledger.repo_key` と同じ）。git の外は `_local`。"""
    if root is None:
        return LOCAL
    try:
        done = git.run_git(["config", "--get", "remote.origin.url"], root)
    except git.GitError:  # noqa: NDF401 外部コマンド: origin を読めなければ旧いと同じ unknown
        return UNKNOWN_REPO
    m = _SLUG.search(done.stdout.strip())
    return f"{m.group(1)}__{m.group(2)}" if m else UNKNOWN_REPO


def decisions_path(repo: str, environ: Mapping[str, str]) -> Path:
    return context_dir(environ) / "decisions" / f"{repo}.decisions.jsonl"


def estimates_path(repo: str, environ: Mapping[str, str]) -> Path:
    return context_dir(environ) / "estimates" / f"{repo}.cost-estimates.json"


def parse_ts(text: Any) -> Optional[float]:
    """記録の時刻（ISO 8601）を UNIX 時刻（秒）にする。読めなければ None。"""
    at = usage_decide.parse_time(text)
    return at.timestamp() if at is not None else None


# --- 作業の識別子 ------------------------------------------------------------------------------------


def work_id(refs: str) -> str:
    """作業の識別子（課題番号の昇順・空白区切り）。refs は `#829 #830` の形の並び。"""
    nums = sorted({int(r.lstrip("#")) for r in refs.split() if r.lstrip("#").isdigit()})
    return " ".join(f"#{n}" for n in nums)


def issue_refs(text: str) -> str:
    """課題番号を `#829 #830` の形で並べる。日付・版数・小数は先に取り除く。"""
    refs: list[str] = []
    for line in text.split("\n"):
        refs += [f"#{m.group(1)}" for m in ISSUE.finditer(NOISE.sub("", line))]
    return " ".join(refs)


def recorded_work(text: str, sid: Optional[str]) -> str:
    """渡した作業の識別子を、集計が結び付けられる形にする。課題番号を含めば昇順の並び。含まずセッションの
    識別子があれば空（読むときに `fill_work` が補う）。どちらでもなければ渡した文字のまま。"""
    refs = work_id(issue_refs(text))
    if refs:
        return refs
    return "" if sid else text


# --- 判定の記録 ----------------------------------------------------------------------------------------


def record_row(session_id: Optional[str], work: str, applied: cp.Resolved, event: Optional[str], reason: Optional[str]) -> dict[str, Any]:
    """`record` が書く行（判定をしない。`band` は null）。`event` を渡せば印の行、渡さなければ再起動の行。
    列は旧い `context_decisions.boundary_row` と同じ。"""
    row: dict[str, Any] = {
        "schema": SCHEMA,
        "at": jsonl.now(),
        "runtime": "claude",
        "source": "record",
        "session_id": session_id,
        "transcript": None,
        "model": None,
        "work": work,
        "event": EVENT_DECISION,
        "stage": None,
        "context": None,
        "window": None,
        "effective_limit": None,
        "band": None,
        "verdict": None,
        "reason": None,
        "basis": None,
        "estimate": None,
        "missing": [],
        "relay": False,
        "passed_once": False,
    }
    row.update(applied.as_dict())
    row.update({"event": event} if event else {"stage": "cut", "verdict": cp.RESTART, "reason": reason})
    return row


def append(repo: str, row: Mapping[str, Any], environ: Mapping[str, str]) -> Path:
    """判定の記録へ 1 行を追記する（1 回の書き込みで 1 行。排他は取らない）。書けなければ `OSError`。"""
    path = decisions_path(repo, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def read(repo: Optional[str], environ: Mapping[str, str]) -> tuple[list[dict[str, Any]], int]:
    """判定の記録の行（`at` の順）と飛ばした行の数。repo を省くと全リポジトリ。読めなければ `OSError`。"""
    d = context_dir(environ) / "decisions"
    paths = [d / f"{repo}.decisions.jsonl"] if repo else sorted(d.glob("*.decisions.jsonl")) if d.is_dir() else []
    rows: list[dict[str, Any]] = []
    skipped = 0
    for p in paths:
        got, broken = jsonl.read_rows(p)
        rows += got
        skipped += broken
    return sorted(rows, key=lambda r: str(r.get("at")) if r.get("at") else ""), skipped


def fill_work(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """作業の識別子が空の行を、同じセッションの前の行の値で補う。行は `at` の順で渡す。"""
    last: dict[str, str] = {}
    out = []
    for r in rows:
        sid, work = r.get("session_id"), r.get("work") if r.get("work") else ""
        if not work and sid in last:
            r = {**r, "work": last[sid]}
        elif work and sid:
            last[sid] = work
        out.append(r)
    return out


def work_of(r: Mapping[str, Any]) -> str:
    return r["work"] if r.get("work") else ""


def of_work(rows: list[dict[str, Any]], work: str) -> list[dict[str, Any]]:
    return [r for r in rows if work_of(r) == work]


def sessions_of(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """セッション ID → {transcript, rows}。session_id の無い行は入れない。"""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        sid = r.get("session_id")
        if not sid:
            continue
        s = out.setdefault(sid, {"transcript": None, "rows": []})
        s["rows"].append(r)
        if r.get("transcript") and not s["transcript"]:
            s["transcript"] = r["transcript"]
    return out


def attach_marks(marks: list[dict[str, Any]], spans: Mapping[str, tuple[float, float]]) -> dict[Optional[str], list[dict[str, Any]]]:
    """session_id の無い印を、`at` を時間の範囲に含むセッションへ割り当てる。どれにも入らなければ None へ。"""
    out: dict[Optional[str], list[dict[str, Any]]] = defaultdict(list)
    for m in marks:
        sid = m.get("session_id")
        at = parse_ts(m.get("at"))
        if not sid and at is not None:
            sid = next((s for s, (a, b) in spans.items() if a <= at <= b), None)
        out[sid].append(m)
    return out


# --- 会話の記録 ---------------------------------------------------------------------------------------


def _int(raw: Mapping[str, Any], key: str) -> int:
    v = raw.get(key)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def cache_writes(usage: Mapping[str, Any]) -> tuple[int, int]:
    """usage の書き込みを (5 分, 1 時間) に分ける。内訳が無ければ全部を 5 分に数える。"""
    cc = usage.get("cache_creation")
    if not isinstance(cc, dict):
        return max(0, _int(usage, "cache_creation_input_tokens")), 0
    w1h = _int(cc, "ephemeral_1h_input_tokens")
    if "ephemeral_5m_input_tokens" in cc:
        return _int(cc, "ephemeral_5m_input_tokens"), w1h
    return max(0, _int(usage, "cache_creation_input_tokens") - w1h), w1h


def usage_call(usage: Mapping[str, Any], ts: Optional[float], model: Optional[str]) -> dict[str, Any]:
    """API 呼び出し 1 回の usage を {ts, model, inp, out, read, w5, w1h, context} にする。"""
    w5, w1h = cache_writes(usage)
    inp, out, read_ = (_int(usage, k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))
    return {"ts": ts, "model": model, "inp": inp, "out": out, "read": read_, "w5": w5, "w1h": w1h, "context": inp + read_ + w5 + w1h}


def _is_auto_compact(e: Mapping[str, Any]) -> bool:
    meta = e.get("compactMetadata")
    return e.get("type") == "system" and e.get("subtype") == "compact_boundary" and isinstance(meta, dict) and meta.get("trigger") == "auto"


def _user_text(e: Mapping[str, Any]) -> Optional[str]:
    """利用者の発言（メタの行を除く）の本文。文字列でなければ JSON にする。発言でなければ None。"""
    if e.get("type") != "user" or e.get("isMeta"):
        return None
    msg = e.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _entries(path: Any) -> Optional[list[dict[str, Any]]]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:  # noqa: NDF401 ファイル: 読めない会話の記録は呼び出しの無いセッションとして数える
        return None
    return jsonl.parse_lines(text.splitlines())[0]


class _Calls:
    """会話の記録の 1 つのファイルの呼び出しを集める（同じ `message.id` は最後の usage を採り、時刻は最初の行）。"""

    def __init__(self, seen: set[Any]) -> None:
        self.seen, self.calls, self.here = seen, [], {}
        self.compacts, self.first_user = 0, None

    def take(self, e: Mapping[str, Any]) -> None:
        if _is_auto_compact(e):
            self.compacts += 1
            return
        if self.first_user is None:
            self.first_user = _user_text(e)
        msg = e.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if e.get("type") != "assistant" or not isinstance(usage, dict) or not isinstance(msg, dict):
            return
        mid = msg["id"] if msg.get("id") else e.get("uuid")
        if mid in self.seen and mid not in self.here:
            return
        self.seen.add(mid)
        call = usage_call(usage, parse_ts(e.get("timestamp")), msg.get("model"))
        if mid in self.here:
            prev = self.calls[self.here[mid]]["ts"]
            call["ts"] = prev if prev is not None else call["ts"]
            self.calls[self.here[mid]] = call
        else:
            self.here[mid] = len(self.calls)
            self.calls.append(call)


def read_calls(path: Any, seen: Optional[set[Any]] = None) -> tuple[list[dict[str, Any]], Optional[int], bool]:
    """会話の記録の API 呼び出しと、自動圧縮の回数と、引継ぎ文書で始まったか。読めなければ ([], None, False)。
    `seen` に前のファイルで数えた id があれば、このファイルでは数えない。"""
    entries = _entries(path) if path else None
    if entries is None:
        return [], None, False
    got = _Calls(set() if seen is None else seen)
    for e in entries:
        got.take(e)
    return got.calls, got.compacts, bool(got.first_user and HANDOFF_PATH.search(got.first_user))


# --- 見込みの較正 -------------------------------------------------------------------------------------


def _median(values: list[float]) -> Optional[float]:
    return float(statistics.median(values)) if values else None


def _mark_times(marks: Iterable[Mapping[str, Any]], event: str) -> list[float]:
    return sorted(t for m in marks if m.get("event") == event and (t := parse_ts(m.get("at"))))


def _restore_sample(calls: list[dict[str, Any]], marks: list[dict[str, Any]], by_next: bool) -> Optional[tuple[int, int]]:
    """引継ぎ文書で始まり復元の印があるセッションの、最初の復元までの (直前のコンテキスト量, 呼び出し数)。"""
    restored = _mark_times(marks, MARK_RESTORED)
    if not (by_next and restored):
        return None
    before = [c for c in calls if c["ts"] is not None and c["ts"] <= restored[0]]
    return (before[-1]["context"], len(before)) if before else None


def _handoff_sample(calls: list[dict[str, Any]], marks: list[dict[str, Any]]) -> Optional[tuple[int, int]]:
    """最後の引継ぎの印から後の (呼び出し数, 出力 token の合計)。印が無ければ None。"""
    handoffs = _mark_times(marks, MARK_HANDOFF)
    if not handoffs:
        return None
    after = [c for c in calls if c["ts"] is not None and c["ts"] >= handoffs[-1]]
    return len(after), sum(c["out"] for c in after)


def _stage_segments(calls: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """「継続」の判定で区切った工程ごとの (工程, 呼び出し数, キャッシュ切れの数)。最後の区間と空の区間は除く。"""
    launches = sorted((t, r["stage"]) for r in rows if r.get("stage") and r.get("verdict") == cp.CONTINUE and (t := parse_ts(r.get("at"))))
    out = []
    for (start, stage), nxt in zip(launches, [t for t, _ in launches[1:]] + [None]):
        if nxt is None:
            continue
        seg = [c for c in calls if c["ts"] is not None and start <= c["ts"] < nxt]
        if seg:
            gaps = sum(1 for a, b in zip(seg, seg[1:]) if b["ts"] - a["ts"] > CACHE_1H)
            out.append((stage, len(seg), gaps))
    return out


def _sampled(sessions: Mapping[str, Any], cache: Mapping[str, Any], by_session: Mapping[Any, Any]) -> dict[str, Any]:
    acc: dict[str, Any] = {"rq": [], "rn": [], "hh": [], "ho": [], "k": defaultdict(list), "m": defaultdict(list)}
    for sid, s in sessions.items():
        calls, _, by_next = cache[sid]
        marks = by_session[sid] if sid in by_session else []
        restore = _restore_sample(calls, marks, by_next)
        if restore is not None:
            acc["rq"].append(restore[0])
            acc["rn"].append(restore[1])
        handoff = _handoff_sample(calls, marks)
        if handoff is not None:
            acc["hh"].append(handoff[0])
            acc["ho"].append(handoff[1])
        for stage, n, gaps in _stage_segments(calls, s["rows"]):
            for key in (stage, "*"):
                acc["k"][key].append(n)
                acc["m"][key].append(gaps)
    return acc


def at_least(r: Mapping[str, Any], since: float) -> bool:
    at = parse_ts(r.get("at"))
    return at is not None and at >= since


def estimate(rows: list[dict[str, Any]], since: Optional[float]) -> dict[str, Any]:
    """判定の記録と会話の記録から費用の見込みを作る。標本が 3 未満の値は None。"""
    rows = [r for r in fill_work(rows) if since is None or at_least(r, since)]
    sessions = sessions_of(rows)
    cache = {sid: read_calls(s["transcript"], set()) for sid, s in sessions.items()}
    spans = {sid: (min(ts), max(ts)) for sid, got in cache.items() if (ts := [c["ts"] for c in got[0] if c["ts"]])}
    acc = _sampled(sessions, cache, attach_marks([r for r in rows if r.get("event") in MARKS], spans))

    def med(values: list[float]) -> Optional[float]:
        return _median(values) if len(values) >= cp.MIN_SAMPLES else None

    return {
        "restore_context": med(acc["rq"]),
        "restore_calls": med(acc["rn"]),
        "handoff_calls": med(acc["hh"]),
        "handoff_output": med(acc["ho"]),
        "stage_calls": {k: _median(v) for k, v in acc["k"].items()},
        "stage_expiries": {k: _median(v) for k, v in acc["m"].items()},
        "samples": {"restore": len(acc["rq"]), "handoff": len(acc["hh"])} | {f"stage:{k}": len(v) for k, v in acc["k"].items()},
        "at": jsonl.now(),
    }


def calibrate(root: Optional[Path], days: int, now: float, environ: Mapping[str, str]) -> dict[str, Any]:
    """そのリポジトリの直近 `days` 日の記録から見込みを較正して書く。書けなければ `OSError`（前の見込みは残る）。"""
    repo = repo_key(root)
    rows, _ = read(repo, environ)
    body = estimate(rows, since=now - days * 86400)
    path = estimates_path(repo, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    signals.replace_json(path, body)
    return body
