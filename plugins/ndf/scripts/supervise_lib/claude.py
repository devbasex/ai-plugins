"""claude -p の起動と、利用上限・使用量の帳簿（#1142 の C1）。`lib/` と `prompts` だけを import する。

`ClaudeRunner.call` が claude -p を呼ぶ唯一の口である（work / drive の worker / judge / slow / pr）。
ハンドラーと遅れの見張りは、`Engine` が渡す `ctx` の `claude` を通して呼ぶ。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import usage_ledger
from monitor import USAGE_LIMIT_FATAL  # 利用上限の文言の表
from supervise_lib.prompts import JUDGE_SYSTEM, PR_SYSTEM, SLOW_SYSTEM


class ClaudeCall(dict):
    """claude -p の 1 回の結果。辞書のまま読め（`ok`・`text`・`usage`・`cost`・`turns`・`session`・`seconds`・`limit`）、
    帳簿の材料を属性でも読める。"""

    @property
    def usage(self) -> dict:
        return self.get("usage") or {}

    @property
    def model_usage(self) -> dict | None:
        return self.get("model_usage")

    @property
    def cost(self) -> float | None:
        return self.get("cost")


WORK_TOOLS = "Read,Edit,Write,Bash,Grep,Glob"
# work のステップに載せる MCP は Serena だけ（mcp-serena の .mcp.json と同じ起動）。シンボル単位で読み・直し、
# 大きなファイルの全文を読まずに済ませる。Tool の定義で起動の固定費が約 1.1 万増える（実測: 1 関数の修正で
# $0.047 → $0.131）ため既定では載せず、ステップに "serena": true を書いたときだけ載せる
SERENA_MCP = {"mcpServers": {"serena": {
    "type": "stdio", "command": "uvx",
    "args": ["--from", "serena-agent==1.7.0", "serena", "start-mcp-server", "--context", "claude-code",
             "--project-from-cwd", "--add-mode", "no-memories", "--add-mode", "no-onboarding",
             "--enable-web-dashboard", "False"],
    "env": {"SERENA_HOME": ".serena"}}}}
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch"
TAIL = 6000  # LLM へ渡す出力の末尾の文字数
LIMIT_RETRY = 900      # 利用上限の解除時刻が読めないときの待ち（秒）。計画の "limit_retry_seconds"
LIMIT_WAIT_MAX = 10800  # 利用上限の待ちの最大（秒）。計画の "limit_wait_max"
# claude の古い形の上限の文言（`Claude AI usage limit reached|<解除の UNIX 時刻>`）
LIMIT_EPOCH = re.compile(r"usage limit reached\|(\d{9,11})", re.I)
LIMIT_RESETS = re.compile(r"resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?", re.I)
TICK = 5.0             # 子プロセスの待ちを区切って見る秒数の上限


def kill_group(p: subprocess.Popen) -> None:
    """子をプロセスグループごと止める（shell=True のステップの孫も残さない）。止まらなければ 5 秒で見切る。"""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        p.kill()
    try:
        p.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass


def run_ticking(cmd, tick=None, every: float = TICK, timeout: float | None = None, input: str | None = None,
                err_path: Path | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run と同じく待つが、every 秒ごとに tick() を呼ぶ（長いステップの待ちの中で進行を書く）。

    err_path を渡すと stderr をそのファイルへ書かせる（待ちの間に最後の行を読めるように）。
    子は新しいセッションで起こす。打ち切りは子のプロセスグループを止めて subprocess.TimeoutExpired を投げる。
    tick() が例外を投げたら（遅れの見張りの打ち切り）、子のプロセスグループを止めてから投げ直す。"""
    errf = open(err_path, "w", encoding="utf-8") if err_path else None
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=errf or subprocess.PIPE, text=True,
                             start_new_session=True, **kw)
    except BaseException:
        if errf:
            errf.close()
        raise
    deadline = time.time() + timeout if timeout else None
    first = True
    try:
        while True:
            wait = every if deadline is None else max(0.01, min(every, deadline - time.time()))
            try:
                out, err = p.communicate(input if first else None, timeout=wait)
                if errf:
                    errf.close()
                    err = Path(err_path).read_text(encoding="utf-8", errors="replace")
                return subprocess.CompletedProcess(cmd, p.returncode, out, err)
            except subprocess.TimeoutExpired:
                first = False
                if deadline is not None and time.time() >= deadline:
                    kill_group(p)
                    raise subprocess.TimeoutExpired(cmd, timeout) from None
                if tick:
                    try:
                        tick()
                    except BaseException:
                        kill_group(p)
                        raise
    finally:
        if errf and not errf.closed:
            errf.close()


def claude_cmd(system: str, tools: str | None, cwd: str, full: bool = False,
               serena: bool = False, resume: str | None = None) -> list[str]:
    base = shlex.split(os.environ.get("NDF_SUPERVISE_CLAUDE", "claude"))
    if full:
        # Skill を回すステップ（cross-review など）。設定・プラグイン・Skill・hook をそのまま読む
        # 新しい文脈の claude -p。本体の会話なのでキャッシュはサブスクリプションなら 1 時間。
        # 報告が無いまま終わったときに --resume で起こし直すため、会話は残す
        return base + ["-p", "--output-format", "json",
                       "--permission-mode", "acceptEdits", "--allowed-tools", FULL_TOOLS,
                       "--append-system-prompt", system] + (["--resume", resume] if resume else [])
    cmd = base + [
        "-p", "--output-format", "json", "--no-session-persistence",
        "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
        "--system-prompt", system,
    ]
    if tools:
        allowed = tools
        if serena:
            cmd += ["--mcp-config", json.dumps(SERENA_MCP)]
            allowed += ",mcp__serena"
        cmd += ["--tools", tools, "--allowed-tools", allowed, "--permission-mode", "acceptEdits",
                "--add-dir", cwd]
    else:
        cmd += ["--tools", ""]
    model = os.environ.get("NDF_SUPERVISE_MODEL")
    if model:
        cmd += ["--model", model]
    return cmd


def claude_kind(system: str, full: bool) -> str:
    """使用量の帳簿の `kind`（work / full / judge / slow / pr）。system プロンプトで見分ける。"""
    if full:
        return "full"
    return {JUDGE_SYSTEM: "judge", SLOW_SYSTEM: "slow", PR_SYSTEM: "pr"}.get(system, "work")


def call_claude(system: str, prompt: str, tools: str | None, cwd: str, timeout: int,
                full: bool = False, serena: bool = False, resume: str | None = None,
                env: dict | None = None, tick=None, every: float = TICK) -> dict:
    """claude -p を 1 回呼び、結果の本文と使用量を返す（既定は最小構成）。

    `env` は環境に足す変数（認証の切り替え）。利用上限で落ちたら `"limit": true` と、読めれば
    解除の時刻（UNIX 時刻）を `"resets_at"` に残す。`tick` は待ちの間に every 秒ごとに呼ぶ。
    """
    started = time.time()
    try:
        p = run_ticking(claude_cmd(system, tools, cwd, full, serena, resume), tick, every, input=prompt,
                        cwd=cwd, timeout=timeout, env={**os.environ, **env} if env else None)
    except subprocess.TimeoutExpired:
        return ClaudeCall(ok=False, text=f"打ち切り（{timeout} 秒）", usage={}, seconds=timeout,
                          kind=claude_kind(system, full), model_usage=None)
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    if not isinstance(data, dict):
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    ok = p.returncode == 0 and not data.get("is_error")
    text = data.get("result") or p.stderr[-TAIL:]
    limit = not ok and is_usage_limit("\n".join([str(data.get("result") or ""), p.stderr, p.stdout]))
    return ClaudeCall({
        "ok": ok,
        "text": text,
        "usage": data.get("usage") or {},
        "model_usage": data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else None,
        "kind": claude_kind(system, full),
        "cost": data.get("total_cost_usd"),
        "turns": data.get("num_turns"),
        "session": data.get("session_id"),
        "seconds": round(time.time() - started, 1),
        "limit": limit,
        "resets_at": limit_reset_at("\n".join([str(data.get("result") or ""), p.stderr])) if limit else None,
    })


def is_usage_limit(text: str) -> bool:
    """利用上限で落ちたか。lib/monitor.py の USAGE LIMIT の表と同じ文言で照合する。"""
    return any(rx.search(text or "") for rx in USAGE_LIMIT_FATAL) or bool(LIMIT_EPOCH.search(text or ""))


def limit_reset_at(text: str, now: float | None = None) -> float | None:
    """上限の文言から解除の時刻（UNIX 時刻）を読む。読めなければ None。

    読む形: `usage limit reached|<UNIX 時刻>` と `resets 3pm (Asia/Tokyo)` / `resets at 15:30`。
    時刻だけの形は、今より後の最初のその時刻（時間帯が無ければ手元の時間帯）とする。
    """
    now = time.time() if now is None else now
    m = LIMIT_EPOCH.search(text or "")
    if m:
        return float(m.group(1))
    m = LIMIT_RESETS.search(text or "")
    if not m:
        return None
    hour, minute, ampm, zone = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        tz = ZoneInfo(zone.strip()) if zone else None
    except (KeyError, ValueError):
        tz = None
    cur = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
    at = cur.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return at.timestamp()


def fallback_env() -> dict:
    """NDF_SUPERVISE_CLAUDE_FALLBACK（`KEY=VALUE` を空白区切り）を読む。"""
    out = {}
    for tok in shlex.split(os.environ.get("NDF_SUPERVISE_CLAUDE_FALLBACK", "")):
        k, sep, v = tok.partition("=")
        if sep and k:
            out[k] = v
    return out


class UsageLimit(Exception):
    """利用上限の待ちが最大を超えた。ステップの失敗とは区別して止まる。"""


class ClaudeRunner:
    """claude -p を呼ぶ唯一の口。利用上限の待ちと認証の切り替え、使用量の数えと帳簿への追記を持つ。

    `ctx` から読むもの: `plan`（上限の設定）・`state`（今のステップ `cur`・切り替えた認証 `switched`・`every`）・
    `tick`（待ちの間に呼ぶ）・`slow`（上限の待ちを遅れの経過から外す）・`plan_path`・`cwd`。
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def call(self, system: str, prompt: str, tools: str | None, cwd: str, timeout: int, **kw) -> ClaudeCall:
        """claude -p を呼ぶ。利用上限をここで扱う。

        上限に当たったら、NDF_SUPERVISE_CLAUDE_FALLBACK があればその変数を足して 1 度だけ起動し直す。
        それでも上限なら、解除の時刻 + 1 分（読めなければ "limit_retry_seconds"）まで待って同じ呼び出しを
        起動し直す。待ちの合計が "limit_wait_max" を超えるなら UsageLimit を投げる。
        待ちの実際の秒数は NDF_SUPERVISE_LIMIT_SLEEP で短くできる（試験用）。
        """
        ctx, st = self.ctx, self.ctx.state
        retry = ctx.plan.get("limit_retry_seconds", LIMIT_RETRY)
        wait_max = ctx.plan.get("limit_wait_max", LIMIT_WAIT_MAX)
        fallback = fallback_env()
        tried_fallback, waited = False, 0.0
        kw = {"tick": ctx.tick, "every": st.every, **kw}
        while True:
            res = call_claude(system, prompt, tools, cwd, timeout, **kw)
            if res.get("limit"):
                self.note_limit(res)
                if fallback and not tried_fallback:
                    tried_fallback = True
                    for k in fallback:
                        if k not in st.switched:
                            st.switched.append(k)
                    st.cur["auth"] = "切り替え（" + ", ".join(fallback) + "）"
                    res = call_claude(system, prompt, tools, cwd, timeout, env=fallback, **kw)
                    if res.get("limit"):
                        self.note_limit(res)
            if not res.get("limit"):
                return res
            wait = max(0.0, res["resets_at"] + 60 - time.time()) if res.get("resets_at") else float(retry)
            if waited + wait > wait_max:
                raise UsageLimit(f"利用上限の待ちが最大 {wait_max} 秒を超える（待った {round(waited)} 秒、"
                                 f"次の待ち {round(wait)} 秒）: {(res.get('text') or '')[:200]}")
            short = os.environ.get("NDF_SUPERVISE_LIMIT_SLEEP")
            paused_at = time.time()
            until = paused_at + (min(wait, float(short)) if short else wait)
            ctx.slow.paused = True  # 上限の待ちは遅れと見なさない（待った秒を経過から引く）
            try:
                while time.time() < until:  # 待ちの間も「まだ動いている」を書く
                    time.sleep(max(0.0, min(st.every, until - time.time())))
                    ctx.tick()
            finally:
                ctx.slow.paused = False
                if ctx.slow.watch:
                    ctx.slow.watch.paused += time.time() - paused_at
            waited += wait
            st.cur["limit_waited"] = round(st.cur.get("limit_waited", 0) + wait, 1)

    def note_limit(self, res: dict) -> None:
        cur = self.ctx.state.cur
        cur["limit"] = True
        cur["limit_hits"] = cur.get("limit_hits", 0) + 1
        if res.get("resets_at"):
            cur["limit_resets"] = datetime.fromtimestamp(res["resets_at"]).astimezone().isoformat(
                timespec="minutes")

    def record_usage(self, kind: str, res: dict) -> None:
        """1 回の呼び出しの使用量を実行の状態へ数え、使用量の帳簿へ 1 行足す（I8）。

        追記に失敗しても呼び出しの結果は失わない（`usage_ledger.append_safely`）。
        """
        ctx = self.ctx
        ctx.state.add_usage(kind, res)
        rec = usage_ledger.UsageRecord(
            source="supervise", kind=res.get("kind") or kind, usage=res.get("usage", {}), plan=ctx.plan_path,
            step=str(ctx.state.cur.get("id") or ""), model_usage=res.get("model_usage"), cost_usd=res.get("cost"),
            turns=res.get("turns"), seconds=res.get("seconds"), session_id=res.get("session"))
        usage_ledger.append_safely(ctx.cwd, rec)
