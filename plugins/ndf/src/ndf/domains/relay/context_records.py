"""文脈の切り替えの判定の記録・会話の記録の読み取り・費用の見込みの較正（#3569。旧い `lib/context_decisions.py` と
`lib/context_calibrate.py` の移し先）。

保存先は旧いプラグインと同じ（`NDF_CONTEXT_DIR` → `<旧い状態の根>/context`）で、入れ替えの前は旧い
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
from itertools import pairwise
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ndf import identity
from ndf.adapters import git
from ndf.contracts import facts
from ndf.domains.records import jsonl, usage_decide
from ndf.domains.relay import context_thresholds as ct
from ndf.domains.relay import signals

SCHEMA = 1
LOCAL = "_local"
MARK_HANDOFF, MARK_RESTORED = "引継ぎ開始", "復元完了"
MARKS = (MARK_HANDOFF, MARK_RESTORED)
EVENT_DECISION = "判定"
UNKNOWN = "不明"
HANDOFF_PATH = re.compile(re.escape(facts.value("locations", "ndf-handoff", "path") + "/"))
CACHE_1H = 3600  # 呼び出しの間隔がこれを超えたら、1 時間のキャッシュが切れたとみなす
ISSUE = re.compile(r"(?:^|[^0-9A-Za-z_/])#?([0-9]+)\b")
NOISE = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}|[0-9]+(?:\.[0-9]+)+")
_SLUG = re.compile(r"([^/:]+)/([^/]+?)(?:\.git)?/?$")


# --- 保存先 ------------------------------------------------------------------------------------------


def context_dir(environ: Mapping[str, str]) -> Path:
    """旧い保存先（`NDF_CONTEXT_DIR` → `<旧い状態の根>/context`）。"""
    explicit = environ.get("NDF_CONTEXT_DIR")
    return Path(explicit) if explicit else identity.legacy_state_root(environ) / "context"


def toplevel(cwd: Optional[Path]) -> Optional[Path]:
    """作業ディレクトリの git の根。git の外か、git が無ければ None。"""
    try:
        done = git.run_git(["rev-parse", "--show-toplevel"], cwd)
    except git.GitError:  # noqa: NDF401 外部コマンド: git が無ければ git の外として扱う
        return None
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def repo_key(root: Optional[Path]) -> str:
    """`<owner>__<repo>`（リモート名のリモートの URL から。旧い `usage_log.repo_key` と同じ）。git の外は `_local`。"""
    if root is None:
        return LOCAL
    try:
        done = git.run_git(["config", "--get", f"remote.{git.Git(root).remote()}.url"], root)
    except git.GitError:  # noqa: NDF401 外部コマンド: リモートか設定を読めなければ旧いと同じ unknown
        return "unknown"
    m = _SLUG.search(done.stdout.strip())
    return f"{m.group(1)}__{m.group(2)}" if m else "unknown"


def _decisions_dir(environ: Mapping[str, str]) -> Path:
    return context_dir(environ) / "decisions"


def decisions_path(repo: str, environ: Mapping[str, str]) -> Path:
    return _decisions_dir(environ) / f"{repo}.decisions.jsonl"


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


def record_row(session_id: Optional[str], work: str, applied: ct.Thresholds, event: Optional[str], reason: Optional[str]) -> dict[str, Any]:
    """`record` が書く行（判定をしない。`band` は null）。`event` を渡せばマーカーの行、渡さなければ再起動の行。
    列は旧い `context_decisions.boundary_row` と同じで、2 つの値（`decide_from`・`limit`・`sources`）を `applied` から書く。
    理由の語に無い `reason` は `ValueError`。消した方針の列（`policy`・`margin`・`config_error`）は書かない（#3995 の決定 1）。"""
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
    row.update({"event": event} if event else {"stage": "cut", "verdict": ct.Verdict.RESTART, "reason": ct.Reason(reason)})
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
    d = _decisions_dir(environ)
    paths = [decisions_path(repo, environ)] if repo else sorted(d.glob("*.decisions.jsonl")) if d.is_dir() else []
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
        sid, work = r.get("session_id"), r.get("work") if r.get("work") else ""  # noqa: NDF501 ファイル: 作業の識別子の無い行は下で前の行から補う
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


def attach_markers(markers: list[dict[str, Any]], spans: Mapping[str, tuple[float, float]]) -> dict[Optional[str], list[dict[str, Any]]]:
    """session_id の無いマーカーを、`at` を時間の範囲に含むセッションへ割り当てる。どれにも入らなければ None へ。"""
    out: dict[Optional[str], list[dict[str, Any]]] = defaultdict(list)
    for m in markers:
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
        self.compacts: list[Optional[float]] = []  # 自動圧縮の時刻（無ければ直前の呼び出しの時刻）
        self.first_user = None

    def take(self, e: Mapping[str, Any]) -> None:
        if _is_auto_compact(e):
            self._take_compact(e)
            return
        if self.first_user is None:
            self.first_user = _user_text(e)
        self._take_usage(e)

    def _take_compact(self, e: Mapping[str, Any]) -> None:
        """自動圧縮の時刻を記録する（行に無ければ直前の呼び出しの時刻）。"""
        at = parse_ts(e.get("timestamp"))
        self.compacts.append(at if at is not None else (self.calls[-1]["ts"] if self.calls else None))

    def _take_usage(self, e: Mapping[str, Any]) -> None:
        """assistant の usage を 1 つの呼び出しとしてマージする。前のファイルで数えた id は数えない。"""
        msg = e.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if e.get("type") != "assistant" or not isinstance(usage, dict) or not isinstance(msg, dict):
            return
        mid = msg["id"] if msg.get("id") else e.get("uuid")
        if mid in self.seen and mid not in self.here:
            return
        self.seen.add(mid)
        self._put(mid, usage_call(usage, parse_ts(e.get("timestamp")), msg.get("model")))

    def _put(self, mid: Any, call: dict[str, Any]) -> None:
        """同じ `mid` の呼び出しがあれば最後の usage で置き換え（時刻は最初の行のものを保つ）、無ければ足す。"""
        if mid in self.here:
            prev = self.calls[self.here[mid]]["ts"]
            call["ts"] = prev if prev is not None else call["ts"]
            self.calls[self.here[mid]] = call
        else:
            self.here[mid] = len(self.calls)
            self.calls.append(call)


def read_calls(path: Any, seen: Optional[set[Any]] = None) -> tuple[list[dict[str, Any]], Optional[list[Optional[float]]], bool]:
    """会話の記録の API 呼び出しと、自動圧縮の時刻の並びと、引継ぎ文書で始まったか。読めなければ ([], None, False)。
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


def _mark_times(markers: Iterable[Mapping[str, Any]], event: str) -> list[float]:
    return sorted(t for m in markers if m.get("event") == event and (t := parse_ts(m.get("at"))) is not None)


def mark_bounds(markers: list[dict[str, Any]], by_next: bool) -> tuple[Optional[float], Optional[float]]:
    """(復元の期間の終わり, 引継ぎの期間の始まり)。復元は引継ぎ文書で始まったセッションの最初の「復元完了」、
    引継ぎは最後の「引継ぎ開始」。マーカーが無ければそれぞれ None。集計（`context_report`）と較正が同じセグメントを使う。"""
    restored = _mark_times(markers, MARK_RESTORED)
    handoffs = _mark_times(markers, MARK_HANDOFF)
    return (restored[0] if by_next and restored else None), (handoffs[-1] if handoffs else None)


def restore_sample(calls: list[dict[str, Any]], restore_end: Optional[float]) -> Optional[tuple[int, int]]:
    """引継ぎ文書で始まり復元のマーカーがあるセッションの、最初の復元までの (直前のコンテキスト量, 呼び出し数)。"""
    if restore_end is None:
        return None
    before = [c for c in calls if c["ts"] is not None and c["ts"] <= restore_end]
    return (before[-1]["context"], len(before)) if before else None


def _handoff_sample(calls: list[dict[str, Any]], handoff_from: Optional[float]) -> Optional[tuple[int, int]]:
    """最後の引継ぎのマーカーから後の (呼び出し数, 出力 token の合計)。マーカーが無ければ None。"""
    if handoff_from is None:
        return None
    after = [c for c in calls if c["ts"] is not None and c["ts"] >= handoff_from]
    return len(after), sum(c["out"] for c in after)


def _stage_segments(calls: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """「継続」の判定で区切った工程ごとの (工程, 呼び出し数, キャッシュ切れの数)。最後のセグメントと空のセグメントは除く。"""
    launches = sorted(
        (t, r["stage"]) for r in rows if r.get("stage") and r.get("verdict") == ct.Verdict.CONTINUE and (t := parse_ts(r.get("at")))
    )
    out = []
    for (start, stage), (nxt, _) in pairwise(launches):
        seg = [c for c in calls if c["ts"] is not None and start <= c["ts"] < nxt]
        if not seg:
            continue
        gaps = sum(1 for a, b in pairwise(seg) if b["ts"] - a["ts"] > CACHE_1H)
        out.append((stage, len(seg), gaps))
    return out


def _sampled(sessions: Mapping[str, Any], cache: Mapping[str, Any], by_session: Mapping[Any, Any]) -> dict[str, Any]:
    acc: dict[str, Any] = {
        "restore_context": [],
        "restore_calls": [],
        "handoff_calls": [],
        "handoff_output": [],
        "stage_calls": defaultdict(list),
        "stage_expiries": defaultdict(list),
    }
    for sid, s in sessions.items():
        calls, _, by_next = cache[sid]
        markers = by_session[sid] if sid in by_session else []
        restore_end, handoff_from = mark_bounds(markers, by_next)
        restore = restore_sample(calls, restore_end)
        if restore is not None:
            acc["restore_context"].append(restore[0])
            acc["restore_calls"].append(restore[1])
        handoff = _handoff_sample(calls, handoff_from)
        if handoff is not None:
            acc["handoff_calls"].append(handoff[0])
            acc["handoff_output"].append(handoff[1])
        for stage, n, gaps in _stage_segments(calls, s["rows"]):
            for key in (stage, "*"):
                acc["stage_calls"][key].append(n)
                acc["stage_expiries"][key].append(gaps)
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
    acc = _sampled(sessions, cache, attach_markers([r for r in rows if r.get("event") in MARKS], spans))

    def med(values: list[float]) -> Optional[float]:
        return _median(values) if len(values) >= ct.MIN_SAMPLES else None

    return {
        "restore_context": med(acc["restore_context"]),
        "restore_calls": med(acc["restore_calls"]),
        "handoff_calls": med(acc["handoff_calls"]),
        "handoff_output": med(acc["handoff_output"]),
        "stage_calls": {k: _median(v) for k, v in acc["stage_calls"].items()},
        "stage_expiries": {k: _median(v) for k, v in acc["stage_expiries"].items()},
        "samples": {"restore": len(acc["restore_context"]), "handoff": len(acc["handoff_calls"])}
        | {f"stage:{k}": len(v) for k, v in acc["stage_calls"].items()},
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
