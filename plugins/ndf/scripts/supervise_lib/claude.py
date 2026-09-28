"""claude -p の起動と、利用上限・使用量の帳簿（#1142 の C1）。`lib/` と `prompts` だけを import する。

`ClaudeRunner.call` が claude -p を呼ぶ唯一の口である（work / drive の worker / judge / slow / pr）。
ハンドラーと遅れの見張りは、`Engine` が渡す `ctx` の `claude` を通して呼ぶ。
"""

from __future__ import annotations

import json
import math
import os
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import claude_accounts as ca
import procs
import usage_ledger
from claude_usage import LIMIT_EPOCH, kind_of_text, limit_reset_at  # noqa: F401  上限の文言の読みは部品が持つ
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
SERENA_MCP = {
    "mcpServers": {
        "serena": {
            "type": "stdio",
            "command": "uvx",
            "args": [
                "--from",
                "serena-agent==1.7.0",
                "serena",
                "start-mcp-server",
                "--context",
                "claude-code",
                "--project-from-cwd",
                "--add-mode",
                "no-memories",
                "--add-mode",
                "no-onboarding",
                "--enable-web-dashboard",
                "False",
            ],
            "env": {"SERENA_HOME": ".serena"},
        }
    }
}
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch"
TAIL = 6000  # LLM へ渡す出力の末尾の文字数
LIMIT_RETRY = 900  # 利用上限の解除時刻が読めないときの待ち（秒）。計画の "limit_retry_seconds"
LIMIT_WAIT_MAX = 10800  # 利用上限の待ちの最大（秒）。計画の "limit_wait_max"
TICK = 5.0  # 子プロセスの待ちを区切って見る秒数の上限


def kill_group(p: subprocess.Popen) -> None:
    """子をプロセスグループごと止める（shell=True のステップの孫も残さない。lib/procs.py）。止まらなければ 5 秒で見切る。"""
    procs.stop_tree(p.pid, grace=0)
    # 先頭が先に終わった（ゾンビの）グループの残りを procs.stop_tree は止めない。孫を残さないためグループへも送る
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        p.communicate(timeout=5)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass


def run_ticking(
    cmd, tick=None, every: float = TICK, timeout: float | None = None, input: str | None = None, err_path: Path | None = None, **kw
) -> subprocess.CompletedProcess:
    """subprocess.run と同じく待つが、every 秒ごとに tick() を呼ぶ（長いステップの待ちの中で進行を書く）。

    err_path を渡すと stderr をそのファイルへ書かせる（待ちの間に最後の行を読めるように）。
    子は新しいセッションで起こす。打ち切りは子のプロセスグループを止めて subprocess.TimeoutExpired を投げる。
    tick() が例外を投げたら（遅れの見張りの打ち切り）、子のプロセスグループを止めてから投げ直す。"""
    errf = open(err_path, "w", encoding="utf-8") if err_path else None
    try:
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errf or subprocess.PIPE,
            text=True,
            start_new_session=True,
            **kw,
        )
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


def minimal_args(system: str) -> list[str]:
    """claude -p を最小構成（設定・MCP・スラッシュコマンドを読まず、会話を残さない）で起動する引数。"""
    return [
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--system-prompt",
        system,
    ]


def claude_cmd(system: str, tools: str | None, cwd: str, full: bool = False, serena: bool = False, resume: str | None = None) -> list[str]:
    base = shlex.split(os.environ.get("NDF_SUPERVISE_CLAUDE", "claude"))
    if full:
        # Skill を回すステップ（cross-review など）。設定・プラグイン・Skill・hook をそのまま読む
        # 新しい文脈の claude -p。本体の会話なのでキャッシュはサブスクリプションなら 1 時間。
        # 報告が無いまま終わったときに --resume で起こし直すため、会話は残す
        return (
            base
            + [
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "acceptEdits",
                "--allowed-tools",
                FULL_TOOLS,
                "--append-system-prompt",
                system,
            ]
            + (["--resume", resume] if resume else [])
        )
    cmd = base + minimal_args(system)
    if tools:
        allowed = tools
        if serena:
            cmd += ["--mcp-config", json.dumps(SERENA_MCP)]
            allowed += ",mcp__serena"
        cmd += ["--tools", tools, "--allowed-tools", allowed, "--permission-mode", "acceptEdits", "--add-dir", cwd]
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


def call_claude(
    system: str,
    prompt: str,
    tools: str | None,
    cwd: str,
    timeout: int,
    full: bool = False,
    serena: bool = False,
    resume: str | None = None,
    env: dict | None = None,
    tick=None,
    every: float = TICK,
    child_env: dict | None = None,
) -> dict:
    """claude -p を 1 回呼び、結果の本文と使用量を返す（既定は最小構成）。

    `env` は環境に足す変数（認証の切り替え）。`child_env` を渡すと環境をそれで置き換える（アカウントの切り替え）。利用上限で落ちたら `"limit": true` と、読めれば
    解除の時刻（UNIX 時刻）を `"resets_at"` に残す。`tick` は待ちの間に every 秒ごとに呼ぶ。
    """
    started = time.time()
    try:
        p = run_ticking(
            claude_cmd(system, tools, cwd, full, serena, resume),
            tick,
            every,
            input=prompt,
            cwd=cwd,
            timeout=timeout,
            env=child_env if child_env is not None else ({**os.environ, **env} if env else None),
        )
    except subprocess.TimeoutExpired:
        return ClaudeCall(
            ok=False, text=f"打ち切り（{timeout} 秒）", usage={}, seconds=timeout, kind=claude_kind(system, full), model_usage=None
        )
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    if not isinstance(data, dict):
        data = {"result": p.stdout, "is_error": p.returncode != 0}
    ok = p.returncode == 0 and not data.get("is_error")
    text = data.get("result") or p.stderr[-TAIL:]
    limit = not ok and is_usage_limit("\n".join([str(data.get("result") or ""), p.stderr, p.stdout]))
    return ClaudeCall(
        {
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
        }
    )


def is_usage_limit(text: str) -> bool:
    """利用上限で落ちたか。lib/monitor.py の USAGE LIMIT の表と同じ文言で照合する。"""
    return any(rx.search(text or "") for rx in USAGE_LIMIT_FATAL) or bool(LIMIT_EPOCH.search(text or ""))


class UsageLimit(Exception):
    """利用上限の待ちが最大を超えた。ステップの失敗とは区別して止まる。"""


class AuthUnavailable(UsageLimit):
    """登録済みのアカウントのトークンを得られず、替えるアカウントも従量の接続も無い。起動した時の環境で呼ばずに止まる。"""


class AccountsLimited(Exception):
    """今のアカウントのトークンを渡せず、他のアカウントは上限なだけ（待てば戻る）。上限と同じく解除まで待つ。"""

    def __init__(self, message: str, resets_at: float | None):
        super().__init__(message)
        self.resets_at = resets_at


class ClaudeRunner:
    """claude -p を呼ぶ唯一の口。利用上限の待ちと認証の切り替え、使用量の数えと帳簿への追記を持つ。

    `ctx` から読むもの: `plan`（上限の設定）・`state`（今のステップ `cur`・切り替えた認証 `switched`・`every`）・
    `tick`（待ちの間に呼ぶ）・`slow`（上限の待ちを遅れの経過から外す）・`plan_path`・`cwd`。
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # 動いている区間のアカウント（起動したときの環境の NDF_CLAUDE_ACCOUNT）。プランはこのトークンを更新しない（I5）
        self.section = os.environ.get(ca.NAME_ENV) or None
        # 次の呼び出しのアカウント（`metered` は従量の接続）。None は起動したときの環境のまま
        self.account = self.section
        self.metered_told = False  # 壊れた保存の宣言の 1 行を出したか

    def call(self, system: str, prompt: str, tools: str | None, cwd: str, timeout: int, **kw) -> ClaudeCall:
        """claude -p を呼ぶ。利用上限をここで扱う。

        登録済みのアカウントが 2 つ以上あれば、上限に当たったアカウントを除いて最も上限から遠いものへ替え、待たずに
        同じ呼び出しをやり直す。候補が無ければ NDF_SUPERVISE_CLAUDE_FALLBACK（従量の接続）へ移り、以後の呼び出しも
        それで起動する（起動のたびに戻れるかを確かめる）。登録が 1 つ以下なら今までどおり、FALLBACK があれば
        その変数を足して 1 度だけ起動し直す。それでも上限なら、解除の時刻 + 1 分（読めなければ "limit_retry_seconds"）
        まで待って同じ呼び出しを起動し直す。待ちの合計が "limit_wait_max" を超えるなら UsageLimit を投げる。
        待ちの実際の秒数は NDF_SUPERVISE_LIMIT_SLEEP で短くできる（試験用）。
        """
        ctx, st = self.ctx, self.ctx.state
        retry = ctx.plan.get("limit_retry_seconds", LIMIT_RETRY)
        wait_max = ctx.plan.get("limit_wait_max", LIMIT_WAIT_MAX)
        fallback = ca.fallback_env()
        self._tell_metered_problem()
        multi = ca.registered() >= 2
        tried_fallback, waited = False, 0.0
        tried: set[str] = set()  # この呼び出しで上限に当たったアカウント
        kw = {"tick": ctx.tick, "every": st.every, **kw}
        while True:
            try:
                child = self.child_env(timeout, fallback) if multi else None
            except AccountsLimited as e:
                waited += self._wait_for_reset({"resets_at": e.resets_at, "text": str(e)}, retry, wait_max, waited)
                continue
            res = call_claude(system, prompt, tools, cwd, timeout, child_env=child, **kw)
            if res.get("limit"):
                self.note_limit(res)
                if multi and self.account != ca.METERED:
                    if self._switch_after_limit(res, tried, fallback):
                        continue
                elif not multi and fallback and not tried_fallback:
                    tried_fallback = True
                    # 宣言より優先される親の認証（OAuth トークン・AUTH_TOKEN・Bedrock/Vertex など）を外した環境で呼ぶ
                    metered = ca.account_env(ca.METERED, dict(os.environ))
                    res = self._try_fallback_once(
                        fallback, lambda: call_claude(system, prompt, tools, cwd, timeout, child_env=metered, **kw)
                    )
            if not res.get("limit"):
                return res
            waited += self._wait_for_reset(res, retry, wait_max, waited)
            tried.clear()  # 待った後は上限の解けたアカウントを選び直せる

    def _tell_metered_problem(self) -> None:
        """保存した従量の接続の宣言が壊れていれば、最初の呼び出しの前に標準エラーと進捗ログへ 1 行出す（#1468 の I5）。"""
        if self.metered_told:
            return
        self.metered_told = True
        bad = ca.metered_problem()
        if bad:
            print("supervise: " + bad, file=sys.stderr)
            self.ctx.state.progress_write({"kind": "metered_invalid", "detail": bad})

    def _switch_after_limit(self, res: dict, tried: set[str], fallback: dict) -> bool:
        """上限に当たったアカウントを記録し、登録済みのアカウントか従量の接続へ替える。替えたら真。"""
        kind = kind_of_text(res.get("text") or "")
        if self.account:
            ca.note_limit(self.account, kind, res.get("resets_at"))
            tried.add(self.account)
        nxt = ca.choose(exclude=tried, keep=self.keep())
        if nxt.name:
            self.switch(nxt.name, kind)
            return True
        if fallback:
            self.switch(ca.METERED, kind, keys=list(fallback))
            return True
        return False

    def _try_fallback_once(self, fallback: dict, call) -> ClaudeCall:
        """旧来の FALLBACK の変数を足して起動し直す（登録が 1 つ以下のとき、1 度だけ）。"""
        st = self.ctx.state
        for k in fallback:
            if k not in st.switched:
                st.switched.append(k)
        st.cur["auth"] = "切り替え（" + ", ".join(fallback) + "）"
        res = call()
        if res.get("limit"):
            self.note_limit(res)
        return res

    def _wait_for_reset(self, res: dict, retry: float, wait_max: float, waited: float) -> float:
        """解除の時刻まで待ち、待った秒数を返す。待ちの合計が wait_max を超えるなら UsageLimit を投げる。"""
        ctx, st = self.ctx, self.ctx.state
        wait = max(0.0, res["resets_at"] + 60 - time.time()) if res.get("resets_at") else float(retry)
        if waited + wait > wait_max:
            raise UsageLimit(
                f"利用上限の待ちが最大 {wait_max} 秒を超える（待った {round(waited)} 秒、"
                f"次の待ち {round(wait)} 秒）: {(res.get('text') or '')[:200]}"
            )
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
        st.cur["limit_waited"] = round(st.cur.get("limit_waited", 0) + wait, 1)
        return wait

    def keep(self) -> set[str]:
        """トークンを更新しないアカウント（動いている区間のもの）。"""
        return {self.section} if self.section else set()

    def child_env(self, timeout: float = 0, fallback: dict | None = None) -> dict | None:
        """次の呼び出しの環境（登録が 2 つ以上のとき）。None は起動したときの環境のまま（アカウントを持たないとき）。

        従量の接続で動いている間は、起動のたびに登録済みのアカウントへ戻れるかを確かめる（閾値未満のものだけ）。
        今のアカウントのトークンが得られなければ（期限切れ・期限まで `timeout` 秒以下・再登録が要る）別のアカウントを
        選び、無ければ従量の接続（`fallback`）へ移る。それも無く、他のアカウントが上限なだけなら AccountsLimited を
        投げて解除まで待たせる。候補が 1 つも無ければ AuthUnavailable を投げる（起動した時の古いトークンで呼ばない）。"""
        if self.account == ca.METERED:
            c = ca.choose(keep=self.keep(), min_left=timeout)
            if c.name and (c.score is None or c.score < ca.switch_at()):
                self.switch(c.name, "recovered")
            else:
                return ca.account_env(ca.METERED, dict(os.environ))
        if self.account is None:
            return None
        env = ca.account_env(self.account, dict(os.environ), None if self.account in self.keep() else ca.REFRESH_BEFORE, timeout)
        if env is not None:
            return env
        c = ca.choose(exclude={self.account}, keep=self.keep(), min_left=timeout)
        env = ca.account_env(c.name, dict(os.environ), None if c.name in self.keep() else ca.REFRESH_BEFORE, timeout) if c.name else None
        if env is not None:
            self.switch(c.name, "auth")
            return env
        if fallback:
            self.switch(ca.METERED, "auth", keys=list(fallback))
            return ca.account_env(ca.METERED, dict(os.environ))
        if c.earliest:
            name, until = c.earliest
            raise AccountsLimited(
                f"アカウント {self.account} のトークンを渡せず、替えるアカウント {name} は上限にある",
                None if until == math.inf else until,
            )
        raise AuthUnavailable(f"アカウント {self.account} のトークンを得られず、替えるアカウントも従量の接続の宣言も無い")

    def switch(self, to: str, reason: str, keys: list[str] | None = None) -> None:
        """次の呼び出しのアカウントを替え、プランの状態（`auth`・`switched`）と途中の報告へ 1 行残す（I12）。"""
        st = self.ctx.state
        before, self.account = self.account, to
        if to == ca.METERED:
            text = "従量の接続（" + ", ".join(keys or []) + "）"
            short = text
        else:
            text = f"アカウント {to}（{reason}）"
            short = f"アカウント {to}"
        st.cur["auth"] = text
        if short not in st.switched:
            st.switched.append(short)
        row = {"kind": "account", "step": st.cur.get("id"), "reason": reason, "from": before, "to": to}
        if keys:
            row["keys"] = keys
        st.progress_write(row)

    def note_limit(self, res: dict) -> None:
        cur = self.ctx.state.cur
        cur["limit"] = True
        cur["limit_hits"] = cur.get("limit_hits", 0) + 1
        if res.get("resets_at"):
            cur["limit_resets"] = datetime.fromtimestamp(res["resets_at"]).astimezone().isoformat(timespec="minutes")

    def record_usage(self, kind: str, res: dict) -> None:
        """1 回の呼び出しの使用量を実行の状態へ数え、使用量の帳簿へ 1 行足す（I8）。

        追記に失敗しても呼び出しの結果は失わない（`usage_ledger.append_safely`）。
        """
        ctx = self.ctx
        ctx.state.add_usage(kind, res)
        rec = usage_ledger.UsageRecord(
            source="supervise",
            kind=res.get("kind") or kind,
            usage=res.get("usage", {}),
            plan=ctx.plan_path,
            step=str(ctx.state.cur.get("id") or ""),
            model_usage=res.get("model_usage"),
            cost_usd=res.get("cost"),
            turns=res.get("turns"),
            seconds=res.get("seconds"),
            session_id=res.get("session"),
        )
        usage_ledger.append_safely(ctx.cwd, rec)
