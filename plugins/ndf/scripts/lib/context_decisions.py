"""境界の判定の記録・費用の見込み・窓と適用値の受け渡し・作業ごとの集計（#2262）。

状態ディレクトリは `NDF_CONTEXT_DIR` → `<状態の根>/context` の順（根は `state_home.container_home`。決定 12。
`CLAUDE_PLUGIN_DATA` は statusline に渡らないため使わない）。

| ファイル | 中身 | 保持 |
| --- | --- | --- |
| `decisions/<owner>__<repo>.decisions.jsonl` | 判定の記録（1 行 1 件、追記だけ） | 消さない |
| `estimates/<owner>__<repo>.cost-estimates.json` | 費用の見込み（`calibrate` が書く） | 上書き |
| `sessions/<sid>.window.json` | statusline が書く窓の大きさとモデル | 7 日 |
| `sessions/<sid>.applied.json` | `publish` と hook が書く適用値 | 7 日 |

較正（`calibrate`）と、`context-policy.py report` の集計は会話の記録の全体を読む。hook はここの関数のうち、記録の追記と
小さな JSON の読み書きだけを使う（決定 6）。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
import subprocess
from pathlib import Path
from typing import Iterator, Mapping

import clock
import context_policy as cp
import limits
import jsonio
from state_home import container_home
from context_policy import REASONS  # noqa: F401  sprint-state.py の --reason の選択肢
from usage_ledger import cache_writes, now_utc

SCHEMA = 1
SESSION_DAYS = 7
LOCAL = "_local"
MARK_HANDOFF, MARK_RESTORED = "引継ぎ開始", "復元完了"
MARKS = (MARK_HANDOFF, MARK_RESTORED)
EVENT_DECISION = "判定"
UNKNOWN = "不明"
HANDOFF_PATH = re.compile(r"\.ndf/handoff/")


# --- 置き場 ------------------------------------------------------------------------------------------


def context_state_dir(env: Mapping | None = None) -> Path:
    if env is None:  # 読む変数を名前で書く（契約の検査が読みを拾う）
        env = {
            "NDF_CONTEXT_DIR": os.environ.get("NDF_CONTEXT_DIR"),
            "NDF_STATE_HOME": os.environ.get("NDF_STATE_HOME"),
            "XDG_STATE_HOME": os.environ.get("XDG_STATE_HOME"),
            "HOME": os.environ.get("HOME"),
        }
    if env.get("NDF_CONTEXT_DIR"):
        return Path(env["NDF_CONTEXT_DIR"])
    return container_home({**env, "HOME": env.get("HOME") or "/tmp"}) / "context"


def decisions_repo(root) -> str:
    """`<owner>__<repo>`（`usage_ledger.repo_key`）。git の外は `_local`。"""
    if not root:
        return LOCAL
    import usage_ledger

    return usage_ledger.repo_key(str(root))


def toplevel(cwd: str | None) -> str | None:
    """作業ディレクトリの git の根。git の外か、git が無ければ None。"""
    try:
        p = subprocess.run(
            ["git", "-C", cwd or ".", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=limits.seconds("context-decisions.git"),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def work_id(refs: str) -> str:
    """作業の識別子（課題番号の昇順・空白区切り。決定 7）。refs は `#829 #830` の形の並び。"""
    nums = sorted({int(r.lstrip("#")) for r in refs.split() if r.lstrip("#").isdigit()})
    return " ".join(f"#{n}" for n in nums)


def stage_of(key: str) -> str:
    """hook の起動の鍵（`skill\t<名>\t<引数>` / `agent\t<説明>` / `plan\t<コマンド>`）から工程の列の値。"""
    kind, _, rest = key.partition("\t")
    if kind == "skill":
        return "skill:" + rest.split("\t", 1)[0]
    if kind == "agent":
        return f"agent:{rest.split(':', 1)[0]}"
    return "plan"


parse_ts = clock.epoch_seconds  # 記録の時刻（ISO 8601）を UNIX 時刻（秒）にする。読めなければ None


# --- 判定の記録 ----------------------------------------------------------------------------------------


def boundary_row(**cols) -> dict:
    """判定の記録の 1 行。列の既定は設計の「判定の記録の 1 行」の表の空の値。"""
    row = {
        "schema": SCHEMA,
        "at": now_utc(),
        "runtime": "claude",
        "source": "hook",
        "session_id": None,
        "transcript": None,
        "model": None,
        "work": "",
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
    applied = cols.pop("applied", None)
    if applied is not None:
        d = applied.to_dict()
        row.update({k: d[k] for k in ("policy", "decide_from", "limit", "margin", "sources", "config_error")})
    verdict = cols.pop("verdict_obj", None)
    if verdict is not None:
        row.update(
            band=verdict.band,
            verdict=verdict.verdict,
            reason=verdict.reason,
            basis=verdict.basis,
            missing=list(verdict.missing),
            effective_limit=verdict.effective_limit,
            estimate=verdict.estimate.to_dict() if verdict.estimate else None,
        )
    row.update(cols)
    return row


def decisions_path(repo: str, env: Mapping | None = None) -> Path:
    return context_state_dir(env) / "decisions" / f"{repo}.decisions.jsonl"


def append_decision(row: dict, repo: str, env: Mapping | None = None) -> bool:
    """1 行を追記する。書けなければ False（判定は変えない。I9）。"""
    try:
        decisions_path(repo, env).parent.mkdir(parents=True, exist_ok=True)
        with open(context_state_dir(env) / "decisions" / f"{repo}.decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return False
    return True


def read_decisions(repo: str | None = None, env: Mapping | None = None) -> list[dict]:
    """判定の記録の行（`at` の順）。repo を省くと全リポジトリ。壊れた行は飛ばす。"""
    d = context_state_dir(env) / "decisions"
    files = [d / f"{repo}.decisions.jsonl"] if repo else sorted(d.glob("*.decisions.jsonl")) if d.is_dir() else []
    rows = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                rows.append(r)
    return sorted(rows, key=lambda r: str(r.get("at") or ""))


def fill_work(rows: list[dict]) -> list[dict]:
    """作業の識別子が空の行を、同じセッションの前の行の値で補う（決定 7）。行は `at` の順で渡す。"""
    last: dict[str, str] = {}
    out = []
    for r in rows:
        sid, work = r.get("session_id"), r.get("work") or ""
        if not work and sid in last:
            r = {**r, "work": last[sid]}
        elif work and sid:
            last[sid] = work
        out.append(r)
    return out


def last_denial(rows: list[dict], work: str, within: float = 1800, now: float | None = None) -> dict | None:
    """同じ作業の識別子で、直前 `within` 秒の内にある最後の拒否の行（`sprint-state.py next` が理由を引き継ぐ）。"""
    now = time.time() if now is None else now
    for r in reversed(fill_work(rows)):
        at = parse_ts(r.get("at"))
        if at is None or now - at > within:
            break
        if r.get("verdict") == cp.DENY and (r.get("work") or "") == work:
            return r
    return None


# --- 見込み・受け渡し ---------------------------------------------------------------------------------


def estimates_path(repo: str, env: Mapping | None = None) -> Path:
    return context_state_dir(env) / "estimates" / f"{repo}.cost-estimates.json"


def read_cost_estimates(repo: str, env: Mapping | None = None) -> dict:
    return jsonio.read(estimates_path(repo, env), missing={}, broken={}, want=dict)


def write_cost_estimates(body: dict, repo: str, env: Mapping | None = None) -> bool:
    try:
        jsonio.write_atomic(context_state_dir(env) / "estimates" / f"{repo}.cost-estimates.json", body, indent=None)
    except OSError:
        return False
    return True


def session_file(sid: str, kind: str, env: Mapping | None = None) -> Path:
    return context_state_dir(env) / "sessions" / f"{sid}.{kind}.json"


def read_window(sid: str, env: Mapping | None = None) -> int | None:
    """statusline が書いた窓の大きさ。無い・形が違えば None（推測で埋めない。I7）。"""
    w = jsonio.read(session_file(sid, "window", env), missing={}, broken={}, want=dict).get("window")
    return w if cp.is_positive_int(w) else None


def read_applied(sid: str, env: Mapping | None = None) -> dict:
    return jsonio.read(session_file(sid, "applied", env), missing={}, broken={}, want=dict)


def write_applied(sid: str, applied, env: Mapping | None = None) -> bool:
    """適用値の受け渡しのファイルを書く（中身が同じなら書かない）。7 日より古い受け渡しのファイルを消す。"""
    path = session_file(sid, "applied", env)
    body = applied.to_dict()
    if read_applied(sid, env) == body:
        return True
    try:
        jsonio.write_atomic(context_state_dir(env) / "sessions" / f"{sid}.applied.json", body, indent=None)
    except OSError:
        return False
    now = time.time()
    for old in path.parent.glob("*.json"):
        try:
            if now - old.stat().st_mtime > SESSION_DAYS * 86400:
                old.unlink()
        except OSError:
            pass
    return True


# --- 会話の記録 ---------------------------------------------------------------------------------------


def usage_call(usage: Mapping, ts: float | None, model) -> dict:
    """API 呼び出し 1 回の usage を {ts, model, inp, out, read, w5, w1h, context} にする（会話の記録と使用量の帳簿で共通）。"""
    w5, w1h = cache_writes(usage)
    inp, out, read = (int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"))
    return {"ts": ts, "model": model, "inp": inp, "out": out, "read": read, "w5": w5, "w1h": w1h, "context": inp + read + w5 + w1h}


def _is_auto_compact(e: dict) -> bool:
    meta = e.get("compactMetadata")
    return e.get("type") == "system" and e.get("subtype") == "compact_boundary" and isinstance(meta, dict) and meta.get("trigger") == "auto"


def _user_text(e: dict) -> str | None:
    """利用者の発言（メタの行を除く）の本文。文字列でなければ JSON にする。発言でなければ None。"""
    if e.get("type") != "user" or e.get("isMeta"):
        return None
    content = (e.get("message") if isinstance(e.get("message"), dict) else {}).get("content")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _json_lines(path) -> Iterator[dict] | None:
    """JSONL の辞書の行を 1 行ずつ返す（全行を辞書のまま保持しない）。読めなければ None。壊れた行と辞書でない行は飛ばす。"""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return (e for e in map(_loads_or_none, lines) if isinstance(e, dict))


def _loads_or_none(line: str):
    try:
        return json.loads(line)
    except ValueError:
        return None


def read_calls(path, seen: set | None = None) -> tuple[list[dict], int | None, bool]:
    """会話の記録の API 呼び出し（同じ `message.id` は 1 回。I13）と、自動圧縮の回数と、ndf-next で始まったか。
    同じ `message.id` の行は最後の usage を採る（ストリーミングで後の行ほど output_tokens が増える。scripts/token-usage.py と同じ）。
    時刻は最初に現れた行のもの。`seen` に前のファイルで数えた id があれば、このファイルでは数えない。
    呼び出しは {ts, model, inp, out, read, w5, w1h, context}。読めなければ ([], None, False)。"""
    seen = set() if seen is None else seen
    entries = _json_lines(path)
    if entries is None:
        return [], None, False
    calls, here = [], {}  # here はこのファイルで数えた message.id → calls の位置
    compacts, first_user = 0, None
    for e in entries:  # 呼び出し・最初の利用者発言・自動圧縮を 1 回の走査で集める
        if _is_auto_compact(e):
            compacts += 1
            continue
        if first_user is None:
            first_user = _user_text(e)
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        usage = msg.get("usage")
        if e.get("type") != "assistant" or not isinstance(usage, dict):
            continue
        mid = msg.get("id") or e.get("uuid")
        if mid in seen and mid not in here:
            continue
        seen.add(mid)
        call = usage_call(usage, parse_ts(e.get("timestamp")), msg.get("model"))
        if mid in here:
            call["ts"] = calls[here[mid]]["ts"] or call["ts"]
            calls[here[mid]] = call
        else:
            here[mid] = len(calls)
            calls.append(call)
    return calls, compacts, bool(first_user and HANDOFF_PATH.search(first_user))


# --- セッションと印 ---------------------------------------------------------------------------------


def sessions_of(rows: list[dict]) -> dict[str, dict]:
    """セッション ID → {transcript, rows}。session_id の無い行は入れない。"""
    out: dict[str, dict] = {}
    for r in rows:
        sid = r.get("session_id")
        if not sid:
            continue
        s = out.setdefault(sid, {"transcript": None, "rows": []})
        s["rows"].append(r)
        if r.get("transcript") and not s["transcript"]:
            s["transcript"] = r["transcript"]
    return out


def attach_marks(marks: list[dict], spans: dict[str, tuple[float, float]]) -> dict[str, list[dict]]:
    """session_id の無い印を、`at` を時間の範囲に含むセッションへ割り当てる。どれにも入らなければ捨てずに None へ。"""
    out: dict = defaultdict(list)
    for m in marks:
        sid = m.get("session_id")
        at = parse_ts(m.get("at"))
        if not sid and at is not None:
            sid = next((s for s, (a, b) in spans.items() if a <= at <= b), None)
        out[sid].append(m)
    return out


def record_row(runtime: str, source: str, work: str, applied, context: int | None = None, event: str | None = None, reason=None) -> dict:
    """CLI と sprint-state.py が書く行（判定をしない。`band` は null）。`event` を渡せば印の行、渡さなければ再起動の行。"""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") if runtime == "claude" else None
    row = boundary_row(runtime=runtime, source=source, session_id=sid or None, work=work, context=context, applied=applied)
    return row | ({"event": event} if event else {"stage": "cut", "verdict": cp.RESTART, "reason": reason})


def record_restart(work: str, reason: str | None = None, cwd: str | None = None, runtime: str = "claude") -> bool:
    """`ndf-next` を出すときの判定「再起動」を書き、見込みを較正し直す（`sprint-state.py next`。決定 6・11）。

    理由を渡さなければ、直前 30 分の同じ作業の拒否の理由を引き継ぎ、無ければ現行方式はカットポイント・
    試行方式は手動にする。失敗しても投げない（ndf-next は出す）。"""
    try:
        root = toplevel(cwd)
        repo = decisions_repo(root)
        applied = cp.resolve_policy(root)
        if reason is None:
            prev = last_denial(read_decisions(repo), work)
            reason = (prev or {}).get("reason") or (cp.REASON_CUTPOINT if applied.policy == cp.CURRENT else cp.REASON_MANUAL)
        ok = append_decision(record_row(runtime, "sprint-state", work, applied, reason=reason), repo)
        import context_calibrate  # 較正は会話の記録の全体を読む重い側。循環を避けて関数の中で読む

        context_calibrate.calibrate_repo(root)
        return ok
    except Exception:  # noqa: BLE001 — 記録の失敗で ndf-next を止めない
        return False


def record_limits(sessions: list, cli_limit: int | None = None, env: Mapping | None = None) -> tuple:
    """skill-stats の「割る候補」の上限を記録ごとに決める（決定 14）。(セッション → 上限の関数, meta)。

    `--window-limit` を渡せば全記録へ当てる。渡さなければ、そのセッションの最後の判定の記録の運用上限を使い、
    判定の記録が無いセッションと方針の列が無い行は現行方式の 200,000 とする（I12）。"""
    if cli_limit:
        return (lambda _sid: cli_limit), {"window_limit": cli_limit, "window_limit_source": "cli"}
    limits: dict[str, int] = {}
    for r in read_decisions(None, env):
        sid, limit = r.get("session_id"), r.get("limit")
        if sid and "policy" not in r:
            limits[sid] = cp.CURRENT_LIMIT
        elif sid and isinstance(limit, int) and not isinstance(limit, bool):
            limits[sid] = limit
    meta = {
        "window_limit": cp.CURRENT_LIMIT,
        "window_limit_source": "records",
        "records_without_decisions": sum(1 for s in sessions if s not in limits),
    }
    return (lambda sid: limits.get(sid, cp.CURRENT_LIMIT)), meta
