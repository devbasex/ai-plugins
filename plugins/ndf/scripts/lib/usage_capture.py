#!/usr/bin/env python3
"""使用量の取り出し: ランタイムごとに呼び出し 1 回の使用量を読み、使用量の帳簿へ 1 行を渡す（#1953）。

帳簿へ書くのは起動の共有処理（`launch-cli.sh`・`supervise_lib/claude.py` の `call_claude`・`auth.py` のモデルの確認・
`jev._post`）と SubagentStop の hook（`usage-subagent.py`）だけで、どれもこのモジュールを通す。取り出しは使用量・
モデル・セッション ID だけを読み、プロンプトの本文・環境変数の値・認証情報を行へ入れない（I6）。

| ランタイム | 出所 |
| --- | --- |
| claude | `--output-format json` の stdout の `usage`・`modelUsage` |
| codex | err の `session id:` から `$CODEX_HOME/sessions` の rollout の最後の `token_count`。無ければ err の `tokens used` |
| kiro | `~/.kiro/sessions/cli/*.json` のうち作業ディレクトリと起動の時刻で 1 つに絞れたものの `metering_usage`（credit） |
| agy | `--output-format json` の stdout の `usage` |
| jev | 要求の数・問いの数と、応答に `usage` があればその中身 |

取れないときは `usage` を `"unknown"` にする（I3）。記録の失敗で呼び出しを落とさない（I5）。

呼び出し元は引数で受け、無ければ環境変数 `NDF_USAGE_SOURCE`・`NDF_USAGE_KIND`・`NDF_USAGE_PLAN`・`NDF_USAGE_STEP`
から読む。名乗りが無ければ `unknown`。

CLI: `usage_capture.py record-cli --stem <パス> --runtime <claude|codex|kiro|agy> [--rc N] [--status S] [--model M]`
（`launch-cli.sh` から打つ。常に終了コード 0 で、標準出力には何も出さない）。

ライブラリなので、標準ライブラリとライブラリだけを import する（I1）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_ledger  # noqa: E402

UNKNOWN = usage_ledger.UNKNOWN
CLI_RUNTIMES = ("claude", "codex", "kiro", "agy")
SESSION_ID = re.compile(r"session id:\s*([0-9a-fA-F-]{8,})")
TOKENS_USED = re.compile(r"tokens used\s*\n\s*([\d,]+)")


def caller(source: Optional[str] = None, kind: Optional[str] = None, plan: Optional[str] = None, step: Optional[str] = None) -> dict:
    """呼び出し元。引数が優先し、無ければ環境変数（C2）、どちらも無ければ `unknown`（plan・step は空）。"""
    env = os.environ
    return {
        "source": source or env.get("NDF_USAGE_SOURCE") or UNKNOWN,
        "kind": kind or env.get("NDF_USAGE_KIND") or UNKNOWN,
        "plan": plan if plan is not None else env.get("NDF_USAGE_PLAN", ""),
        "step": step if step is not None else env.get("NDF_USAGE_STEP", ""),
    }


def status_of(rc: Optional[int]) -> str:
    return "ok" if rc == 0 else "error"


def _parse_time(value) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?", value)
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + (float("0" + m.group(2)) if m.group(2) else 0.0)


def _read_json(path: Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- ランタイムごとの取り出し ---


def codex_usage(err_text: str, home: Optional[Path] = None) -> tuple[object, str, Optional[str]]:
    """(使用量, モデル, セッション ID)。rollout の最後の `token_count` の `info.total_token_usage` を鍵のまま返す。"""
    m = SESSION_ID.search(err_text or "")
    sid = m.group(1) if m else None
    usage: object = UNKNOWN
    model = ""
    if sid:
        root = Path(home or os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
        for path in sorted(root.glob(f"**/rollout-*-{sid}.jsonl")) if root.is_dir() else []:
            for line in _read(path).splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                p = d.get("payload") if isinstance(d, dict) else None
                if not isinstance(p, dict):
                    continue
                if d.get("type") == "turn_context" and isinstance(p.get("model"), str):
                    model = p["model"]
                total = (p.get("info") or {}).get("total_token_usage") if p.get("type") == "token_count" else None
                if isinstance(total, dict) and total:
                    usage = dict(total)
    if usage == UNKNOWN:
        t = TOKENS_USED.search(err_text or "")
        if t:
            usage = {"total_tokens": int(t.group(1).replace(",", ""))}
    return usage, model, sid


def kiro_usage(workdir: str, started: float, ended: float, home: Optional[Path] = None) -> tuple[object, str, Optional[str]]:
    """(使用量, モデル, セッション ID)。作業ディレクトリが一致し、起動から終わりの間に作られたセッションの記録が
    ちょうど 1 つのときだけ、その `metering_usage`（credit）の和と往復の数を返す。0 か 2 つ以上なら `"unknown"`。"""
    root = Path(home or Path.home()) / ".kiro" / "sessions" / "cli"
    want = os.path.realpath(workdir) if workdir else ""
    found = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:  # 記録は数万本になる。起動より前に書き終えたものは中を読まない
            if path.stat().st_mtime < started - 1:
                continue
        except OSError:
            continue
        d = _read_json(path)
        if not isinstance(d, dict) or os.path.realpath(str(d.get("cwd") or "")) != want:
            continue
        created = _parse_time(d.get("created_at"))
        if created is not None and started - 1 <= created <= ended + 1:
            found.append(d)
    if len(found) != 1:
        return UNKNOWN, "", None
    d = found[0]
    meta = ((d.get("session_state") or {}).get("conversation_metadata") or {}).get("user_turn_metadatas") or []
    credits = sum(
        float(u.get("value") or 0)
        for t in meta
        if isinstance(t, dict)
        for u in t.get("metering_usage") or []
        if isinstance(u, dict) and u.get("unit") == "credit"
    )
    model = (((d.get("session_state") or {}).get("rts_model_state") or {}).get("model_info") or {}).get("model_id") or ""
    usage = {"credits": round(credits, 6), "turns": len(meta)} if meta else UNKNOWN
    return usage, str(model), d.get("session_id")


def agy_usage(stdout_text: str) -> tuple[object, Optional[str]]:
    """(使用量, 会話の ID)。`--output-format json` の stdout の `usage`。"""
    try:
        d = json.loads(stdout_text or "")
    except ValueError:
        return UNKNOWN, None
    if not isinstance(d, dict):
        return UNKNOWN, None
    u = d.get("usage")
    usage = u if isinstance(u, dict) and any(isinstance(v, (int, float)) and v for v in u.values()) else UNKNOWN
    return usage, d.get("conversation_id")


def transcript_usage(path: Path) -> tuple[object, str]:
    """Claude Code の会話の記録 1 本の (使用量, 主なモデル)。assistant の `message.usage` を `message.id` ごとに 1 回足す。"""
    keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
    total = dict.fromkeys(keys, 0)
    w5 = w1h = 0
    seen: set = set()
    models: Counter = Counter()
    for line in _read(path).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row, dict) and row.get("type") == "assistant" else None
        if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            continue
        mid = msg.get("id") or id(msg)
        if mid in seen:
            continue
        seen.add(mid)
        u = msg["usage"]
        for k in keys:
            total[k] += int(u.get(k) or 0)
        a, b = usage_ledger.cache_writes(u)
        w5, w1h = w5 + a, w1h + b
        if isinstance(msg.get("model"), str) and not msg["model"].startswith("<"):
            models[msg["model"]] += 1
    if not seen:
        return UNKNOWN, ""
    total["cache_creation"] = {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h}
    return total, models.most_common(1)[0][0] if models else ""


# --- 帳簿へ書く口 ---


def record_claude(
    data: dict,
    *,
    cwd: str,
    source: Optional[str] = None,
    kind: Optional[str] = None,
    plan: Optional[str] = None,
    step: Optional[str] = None,
    seconds: Optional[float] = None,
    status: str = "ok",
) -> bool:
    """`claude -p --output-format json` の結果 1 回を Claude の帳簿へ書く。`data` が空なら使用量は `"unknown"`。"""
    try:
        rec = usage_ledger.UsageRecord.from_claude(data, **caller(source, kind, plan, step), seconds=seconds, status=status)
    except Exception as e:  # noqa: BLE001  記録の失敗で呼び出しを落とさない（I5）
        print(f"usage_capture: 行を作れない: {e}", file=sys.stderr)
        return False
    return usage_ledger.append_safely(cwd, rec)


def _record(runtime: str, cwd: str, who: dict, **fields) -> bool:
    try:
        rec = usage_ledger.UsageRecord(runtime=runtime, **who, **fields)
    except Exception as e:  # noqa: BLE001
        print(f"usage_capture: 行を作れない: {e}", file=sys.stderr)
        return False
    return usage_ledger.append_safely(cwd, rec)


def record_output(
    runtime: str,
    *,
    cwd: str,
    stdout: str,
    stderr: str,
    status: str,
    started: float,
    who: dict,
    model: str = "",
    seconds: Optional[float] = None,
) -> bool:
    """CLI の出力 1 回から使用量を読み、そのランタイムの帳簿へ 1 行を書く。"""
    seconds = round(time.time() - started, 1) if seconds is None else seconds
    if runtime == "claude":
        try:
            data = json.loads(stdout or "")
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        if status == "ok" and data.get("is_error"):
            status = "error"
        return record_claude(data, cwd=cwd, **who, seconds=seconds, status=status)
    sid = None
    if runtime == "codex":
        usage, seen_model, sid = codex_usage(stderr)
        model = seen_model or model
    elif runtime == "kiro":
        usage, seen_model, sid = kiro_usage(cwd, started, time.time())
        model = seen_model or model
    elif runtime == "agy":
        usage, sid = agy_usage(stdout)
    else:
        usage = UNKNOWN
    return _record(runtime, cwd, who, usage=usage, status=status, model=model, seconds=seconds, session_id=sid)


def record_probe(runtime: str, *, cwd: str, stdout: str, stderr: str, rc: Optional[int], started: float, model: str = "") -> bool:
    """参加者の確認のモデルの呼び出し 1 回（`kind=probe`）。"""
    status = "timeout" if rc is None else status_of(rc)
    return record_output(
        runtime, cwd=cwd, stdout=stdout, stderr=stderr, status=status, started=started, who=caller(kind="probe"), model=model
    )


def record_jev(body: dict, payload: Optional[dict], *, started: float, cwd: Optional[str] = None) -> bool:
    """Jev の要求 1 回を `jev` の帳簿へ書く。要求の数と問いの数を必ず持ち、応答に `usage` があればそのまま足す。"""
    questions = body.get("questions") if isinstance(body, dict) else None
    usage: dict = {"requests": 1, "questions": len(questions) if isinstance(questions, dict) else 0}
    extra = payload.get("usage") if isinstance(payload, dict) else None
    if isinstance(extra, dict):
        usage.update({k: v for k, v in extra.items() if k not in usage})
    return _record(
        "jev",
        cwd or os.getcwd(),
        caller(kind="evaluate"),
        usage=usage,
        status="ok" if payload is not None else "error",
        model=str(body.get("model") or "") if isinstance(body, dict) else "",
        seconds=round(time.time() - started, 1),
    )


def record_subagent(hook: dict) -> bool:
    """SubagentStop の hook の入力 1 回を Claude の帳簿へ書く（`source=subagent`・`kind=subagent`）。"""
    hook = hook if isinstance(hook, dict) else {}
    path = hook.get("agent_transcript_path")
    usage, model = transcript_usage(Path(path)) if isinstance(path, str) and path else (UNKNOWN, "")
    who = caller(source="subagent", kind="subagent")
    return _record(
        "claude",
        str(hook.get("cwd") or os.getcwd()),
        who,
        usage=usage,
        model=model,
        session_id=hook.get("agent_id"),
        parent_session_id=hook.get("session_id"),
        agent_type=hook.get("agent_type"),
        agent_id=hook.get("agent_id"),
    )


def record_cli(stem: str, runtime: str, rc: Optional[int] = None, status: Optional[str] = None, model: str = "") -> bool:
    """`launch-cli.sh` の 1 回の起動を、`<stem>-stdout.log`・`-err.log`・`-launch.json` から帳簿へ書く。"""
    launch = _read_json(Path(f"{stem}-launch.json"))
    launch = launch if isinstance(launch, dict) else {}
    started = _parse_time(launch.get("started_at")) or time.time()
    cwd = str(launch.get("workdir") or os.getcwd())
    status = status or status_of(rc)
    return record_output(
        runtime,
        cwd=cwd,
        stdout=_read(Path(f"{stem}-stdout.log")),
        stderr=_read(Path(f"{stem}-err.log")),
        status=status,
        started=started,
        who=caller(),
        model=model,
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("record-cli", help="launch-cli.sh の 1 回の起動を帳簿へ書く")
    rc.add_argument("--stem", required=True)
    rc.add_argument("--runtime", required=True, choices=CLI_RUNTIMES)
    rc.add_argument("--rc", type=int, default=None)
    rc.add_argument("--status", choices=usage_ledger.STATUSES, default=None)
    rc.add_argument("--model", default="")
    try:
        a = ap.parse_args(argv)
        record_cli(a.stem, a.runtime, a.rc, a.status, a.model)
    except SystemExit:
        return 0
    except Exception as e:  # noqa: BLE001  常に 0（I5）
        print(f"usage_capture: 記録できない: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
