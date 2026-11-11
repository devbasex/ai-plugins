"""claude -p の起動と、利用上限・使用量の帳簿への記録（#1142 の C1・#1953）。`lib/` と `prompts`・`children` だけを import する。

`ClaudeRunner.call` が claude -p を呼ぶ唯一の口である（work / drive の worker / judge / slow / pr）。
ハンドラーと遅れの見張りは、`Engine` が渡す `ctx` の `claude` を通して呼ぶ。
"""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import claude_accounts as ca
import claude_api_keys as ak
from claude_settings import metered_settings
import limits
import usage_capture
from claude_usage import LIMIT_EPOCH, is_credit_low, kind_of_text, limit_reset_at  # noqa: F401  上限の文言の読みは部品が持つ
from monitor import USAGE_LIMIT_FATAL  # 利用上限の文言の表
from supervise_lib.children import TICK, kill_group, run_ticking, watch_children  # noqa: F401  子の待ちは children が持つ
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


# 外部の文書（規約・API の仕様・一次情報の記事）を原文で読む Tool。最小構成は --tools に無い Tool を使えず、
# full は --allowed-tools に無い Tool を拒否されるため、両方の一覧へこの組から足す
WEB_TOOLS = "WebFetch,WebSearch"
WORK_TOOLS = "Read,Edit,Write,Bash,Grep,Glob," + WEB_TOOLS
# work のステップに載せる MCP は Serena だけ（mcp-serena の .mcp.json と同じ起動）。シンボル単位で読み・直し、
# 大きなファイルの全文を読まずに済ませる。Tool の定義で起動の固定費が約 1.1 万増える（実測: 1 関数の修正で
# $0.047 → $0.131）ため既定では載せず、ステップに "serena": true を書いたときだけ載せる
SERENA_MCP = {
    "mcpServers": {
        "serena": {
            "type": "stdio",
            "command": "uvx",
            "args": (
                "--from serena-agent==1.7.0 serena start-mcp-server --context claude-code --project-from-cwd "
                "--add-mode no-memories --add-mode no-onboarding --enable-web-dashboard False"
            ).split(),
            "env": {"SERENA_HOME": ".serena"},
        }
    }
}
FULL_TOOLS = "Read,Edit,Write,Bash,Grep,Glob,Skill,Agent,Monitor,SendMessage,ToolSearch," + WEB_TOOLS
TAIL = 6000  # LLM へ渡す出力の末尾の文字数


def minimal_args(system: str) -> list[str]:
    """claude -p を最小構成（設定・MCP・スラッシュコマンドを読まず、会話を残さない）で起動する引数。"""
    head = ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]
    return head + ["--disable-slash-commands", "--system-prompt", system]


def claude_cmd(
    system: str, tools: str | None, cwd: str, full: bool = False, serena: bool = False, resume: str | None = None, base: str | None = None
) -> list[str]:
    """起動の引数。`base` は claude の起動のコマンド（既定は NDF_SUPERVISE_CLAUDE か `claude`）。"""
    base = shlex.split(base or os.environ.get("NDF_SUPERVISE_CLAUDE", "claude"))
    if full:
        # Skill を回すステップ（cross-review など）。設定・プラグイン・Skill・hook をそのまま読む
        # 新しい文脈の claude -p。本体の会話なのでキャッシュはサブスクリプションなら 1 時間。
        # 報告が無いまま終わったときに --resume で起こし直すため、会話は残す
        args = ["-p", "--output-format", "json", "--permission-mode", "acceptEdits", "--allowed-tools", FULL_TOOLS]
        return base + args + ["--append-system-prompt", system] + (["--resume", resume] if resume else [])
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
    """使用量の帳簿の `kind`（work / full / judge / slow / pr）。system プロンプトで見分ける（pr は後ろに Skill の節が付くため前方一致）。"""
    if full:
        return "full"
    return "pr" if system.startswith(PR_SYSTEM) else {JUDGE_SYSTEM: "judge", SLOW_SYSTEM: "slow"}.get(system, "work")


def _output_data(stdout: str, returncode: int) -> dict:
    """claude -p の出力を辞書へそろえる。JSON の辞書でなければ本文を `result` に、終了コードの失敗を `is_error` に置く。"""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        data = {"result": stdout, "is_error": returncode != 0}
    return data


def _failure_kinds(data: dict, result: str, p: subprocess.CompletedProcess, ok: bool) -> tuple[bool, bool, bool]:
    """失敗の分類（残高切れ・利用上限・認証の失敗）。互いに排他で、この順に先に当たったほうを採る。"""
    # API クレジットの残高切れ（#2855 の決定 6）。上限と認証の失敗より先に読む
    credit = not ok and is_credit_low("\n".join([result, p.stderr]))
    limit = not ok and not credit and is_usage_limit("\n".join([result, p.stderr, p.stdout]))
    # 認証の失敗（`result` が `Failed to authenticate` で始まるか、`api_error_status` が 401。#1576 の実測 H）
    auth = not ok and not credit and not limit and (result.startswith("Failed to authenticate") or data.get("api_error_status") == 401)
    return credit, limit, auth


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
    caller: dict | None = None,
    base: str | None = None,
    session: dict | None = None,
    watch=None,
    api_key: str | None = None,
) -> dict:
    """claude -p を 1 回呼び、結果の本文と使用量を返す（既定は最小構成）。Claude の使用量の帳簿へ 1 行を書く（#1953）。

    `env` は環境に足す変数（認証の切り替え）。`child_env` を渡すと環境をそれで置き換える（アカウントの切り替え）。利用上限で落ちたら `"limit": true` と、読めれば
    解除の時刻（UNIX 時刻）を `"resets_at"` に残す。`tick` は待ちの間に every 秒ごとに呼ぶ。
    `caller` は帳簿の呼び出し元（`source`・`kind`・`plan`・`step`）。`kind` の既定は `claude_kind`、ほかは環境変数（C2）。
    `session` を渡すと、会話を残す起動（full で resume でない）の id を `--session-id` で決め、id と設定の置き場を入れる（#1623）。
    `watch`（進みの観測）を渡すと、最小構成でも会話を残し、締め切りを `watch.deadline` で動かす。打ち切りは結果の `timed_out` に残す（#2505）。
    `api_key` は API キーで起動したときのキーの名前で、帳簿の行に載せる（値は載せない。#2930）。
    帳簿への記録に失敗しても結果は変わらない（I5）。"""
    started = time.time()
    kind = claude_kind(system, full)
    who = {"kind": kind, **(caller or {}), **({"api_key": api_key} if api_key else {})}
    cmd = claude_cmd(system, tools, cwd, full, serena, resume, base)  # 従量の接続は宣言を --settings でも渡す（#1543）
    run_env = child_env if child_env is not None else ({**os.environ, **env} if env else None)
    # 子の中の Skill が起動する CLI・SubagentStop の行も、今回の呼び出し元のプラン・ステップで数える（継承した別プランを上書きする）
    usage_env = {f"NDF_USAGE_{k.upper()}": str(v) for k, v in (caller or {}).items() if k in ("plan", "step") and v is not None}
    if usage_env:
        run_env = {**(run_env if run_env is not None else os.environ), **usage_env}
    if session is not None and (full or watch) and not resume:  # 遅れの見張りと進みの観測がセッションの記録の伸びを進みに数える
        session.update(id=str(uuid.uuid4()), config=(run_env or os.environ).get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))
        cmd = [arg for arg in cmd if arg != "--no-session-persistence"] + ["--session-id", session["id"]]
    try:
        p = run_ticking(
            metered_settings(cmd, child_env if child_env is not None else os.environ, cwd, cmd.index("-p")),
            tick,
            every,
            input=prompt,
            cwd=cwd,
            timeout=timeout,
            env=run_env,
            **({"extend": watch.deadline, "follow": True} if watch else {}),  # 記録が遅れて現れたら長い許容時間の締め切りを戻す
        )
    except OSError:
        usage_capture.record_claude({}, cwd=cwd, **who, seconds=round(time.time() - started, 1), status="error")
        raise
    except subprocess.TimeoutExpired as e:
        waited = round(e.timeout)  # 締め切りを動かしたときは動かした後の秒
        usage_capture.record_claude({}, cwd=cwd, **who, seconds=waited, status="timeout")
        return ClaudeCall(ok=False, text=f"打ち切り（{waited} 秒）", usage={}, seconds=waited, kind=kind, model_usage=None, timed_out=True)
    data = _output_data(p.stdout, p.returncode)
    ok = p.returncode == 0 and not data.get("is_error")
    text = data.get("result") or p.stderr[-TAIL:]
    result = str(data.get("result") or "")
    credit, limit, auth = _failure_kinds(data, result, p, ok)
    usage_capture.record_claude(data, cwd=cwd, **who, seconds=round(time.time() - started, 1), status="ok" if ok else "error")
    return ClaudeCall(
        {
            "ok": ok,
            "text": text,
            "usage": data.get("usage") or {},
            "model_usage": data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else None,
            "kind": kind,
            "cost": data.get("total_cost_usd"),
            "turns": data.get("num_turns"),
            "session": data.get("session_id"),
            "seconds": round(time.time() - started, 1),
            "limit": limit,
            "auth": auth,
            "credit": credit,
            "resets_at": limit_reset_at("\n".join([result, p.stderr])) if limit else None,
        }
    )


def is_usage_limit(text: str) -> bool:
    """利用上限で落ちたか。lib/monitor.py の USAGE LIMIT の表と同じ文言で照合する。"""
    return any(rx.search(text or "") for rx in USAGE_LIMIT_FATAL) or bool(LIMIT_EPOCH.search(text or ""))


class UsageLimit(Exception):
    """利用上限の待ちが最大を超えた。ステップの失敗とは区別して止まる。"""


class AuthUnavailable(UsageLimit):
    """登録済みのアカウントを使えず、替えるアカウントも従量の接続も無い。起動した時の環境で呼ばずに止まる。"""


class AccountsLimited(Exception):
    """今のアカウントを使えず、他のアカウントは上限なだけ（待てば戻る）。上限と同じく解除まで待つ。"""

    def __init__(self, message: str, resets_at: float | None):
        super().__init__(message)
        self.resets_at = resets_at


class ClaudeRunner:
    """claude -p を呼ぶ唯一の口。利用上限の待ちと認証の切り替え、報告の使用量の数えを持つ（帳簿へは `call_claude` が書く）。

    `ctx` から読むもの: `plan`（上限の設定）・`state`（今のステップ `cur`・切り替えた認証 `switched`・`every`）・
    `tick`（待ちの間に呼ぶ）・`slow`（上限の待ちを遅れの経過から外す）・`plan_path`・`cwd`。
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        # 動いている区間のアカウント（起動したときの環境の NDF_CLAUDE_ACCOUNT）。プランはこのトークンを更新しない（#1576 の I8）
        self.section = os.environ.get(ca.NAME_ENV) or None
        # 次の呼び出しのアカウント（`metered` は従量の接続）。None は起動したときの環境のまま
        self.account = self.section
        self.metered_told = False  # 壊れた保存の宣言の 1 行を出したか
        self.running: list[str | None] = []  # 実行中の呼び出しのアカウント
        # 登録済み API キー（#2855）。`account` とは別に持ち、外したら元のアカウントの選定へそのまま戻る
        self.keys = ak.KeyRoute(ctx.state, account=lambda: self.account)

    def call(self, system: str, prompt: str, tools: str | None, cwd: str, timeout: int, **kw) -> ClaudeCall:
        """claude -p を呼ぶ。利用上限をここで扱う。

        登録済みのアカウントが 2 つ以上あれば、上限に当たったアカウントを除いて起動先の決定で選び直し、待たずに
        同じ呼び出しをやり直す。候補が無ければ NDF_SUPERVISE_CLAUDE_FALLBACK（従量の接続）へ移り、以後の呼び出しも
        それで起動する（起動のたびに戻れるかを確かめる）。登録が 1 つ以下なら今までどおり、FALLBACK があれば
        その変数を足して 1 度だけ起動し直す。それでも上限なら、解除の時刻 + 1 分（読めなければ "limit_retry_seconds"）
        まで待って同じ呼び出しを起動し直す。待ちの合計が "limit_wait_max" を超えるなら UsageLimit を投げる。
        待ちの実際の秒数は NDF_SUPERVISE_LIMIT_SLEEP で短くできる（試験用）。
        API キーは起動先の決定に従って先に試し（#2855・#2930。`KeyRoute`）、上限で替えるアカウントが無いときも、その時点の
        決定が API キーを選べば従量の接続と待ちの前に試す。
        """
        ctx, st = self.ctx, self.ctx.state
        retry = ctx.plan.get("limit_retry_seconds", limits.seconds("supervise.limit-retry"))
        wait_max = ctx.plan.get("limit_wait_max", limits.seconds("supervise.limit-wait-max"))
        fallback = ca.fallback_env()
        self._tell_metered_problem()
        multi = ca.registered() >= 2
        tried_fallback, waited = False, 0.0
        tried: set[str] = set()  # この呼び出しで上限に当たったアカウント
        auth_failed: set[str] = set()  # この呼び出しで認証が通らなかったアカウント（同じアカウントを 2 度試さない）
        keys = self.keys.attempt(self.keep, cwd)
        who = {"source": "supervise", "plan": getattr(ctx, "plan_path", None) or "", "step": str(st.cur.get("id") or "")}
        kw = {"tick": ctx.tick, "every": st.every, "caller": who, "session": getattr(st, "session", None), **kw}
        direct = lambda env, key=None: call_claude(system, prompt, tools, cwd, timeout, child_env=env, api_key=key, **kw)  # noqa: E731
        once = lambda env, key=None: self._call_running(lambda: direct(env, key))  # noqa: E731
        while True:
            if (res := keys.run(once)) is not None:  # API キーで決着した
                return res
            self._return_from_key(multi)
            try:
                child = keys.env(self.child_env(fallback) if multi else None)
            except AccountsLimited as e:
                if not keys.left():
                    waited += self._wait_for_reset({"resets_at": e.resets_at, "text": str(e)}, retry, wait_max, waited)
                continue
            res = once(child)
            if self._retry_on_auth(res, multi, auth_failed):
                continue
            if res.get("limit"):
                res, tried_fallback = self._handle_limit(res, multi, tried, fallback, keys, tried_fallback, direct)
                if res is None:
                    continue
            if not res.get("limit"):
                return res
            if keys.left():
                continue
            waited += self._wait_for_reset(res, retry, wait_max, waited)
            tried.clear()  # 待った後は上限の解けたアカウントを選び直せる
            keys.reset()

    def _return_from_key(self, multi: bool) -> None:
        """受け継いだキーを外した入れ子で、アカウントを持たなければ親が残した戻り先（`KeyRoute.back`）へ替える。

        キーの環境は親のアカウントを外している（`claude_api_keys.env_with_key`）。今のアカウントとして持つことで、
        戻り先のログインが通らないときも認証の失敗の振り替え（`_retry_on_auth`）と上限の切り替えが働く。"""
        back = self.keys.back
        if multi and self.keys.dropped and self.account is None and back and back in ca.names():
            self.switch(back, "api_key")

    def _retry_on_auth(self, res: dict, multi: bool, auth_failed: set[str]) -> bool:
        """認証が通らなかった登録済みのアカウントを記録する。同じ呼び出しを別のアカウントでやり直すなら真。"""
        if res.get("auth") and multi and self.account not in (None, ca.METERED) and self.account not in auth_failed:
            # 認証の失敗の観測を残し、次の child_env が今のアカウントを除いて選び直す（上限のときと同じ経路。#1576 の E9）
            auth_failed.add(self.account)
            ca.note_auth_failed(self.account)
            return True
        return False

    def _handle_limit(
        self, res: dict, multi: bool, tried: set[str], fallback: dict, keys, tried_fallback: bool, direct
    ) -> tuple[ClaudeCall | None, bool]:
        """上限に当たった後の替え先を選ぶ。返すのは（結果, FALLBACK を試したか）で、結果が None ならやり直す。

        結果に上限が残っていれば呼び手が API キーか解除の待ちへ進む。登録が 2 つ以上ならアカウント・API キー・従量の
        接続の順に替え、1 つ以下なら API キー、次に FALLBACK を 1 度だけ試す。
        """
        self.note_limit(res)
        if multi and self.account != ca.METERED:
            if self._switch_after_limit(res, tried, fallback, keys.left):
                return None, tried_fallback
        elif not multi and keys.left():
            return None, tried_fallback
        elif not multi and fallback and not tried_fallback:
            # 宣言より優先される親の認証（OAuth トークン・AUTH_TOKEN・Bedrock/Vertex など）を外した環境で呼ぶ
            metered = ca.account_env(ca.METERED, dict(os.environ))
            return self._try_fallback_once(fallback, lambda: direct(metered)), True
        return res, tried_fallback

    def _tell_metered_problem(self) -> None:
        """保存した従量の接続の宣言が壊れていれば、最初の呼び出しの前に標準エラーと進捗ログへ 1 行出す（#1468 の I5）。"""
        if self.metered_told:
            return
        self.metered_told = True
        bad = ca.metered_problem()
        if bad:
            print("supervise: " + bad, file=sys.stderr)
            self.ctx.state.progress_write({"kind": "metered_invalid", "detail": bad})

    def _switch_after_limit(self, res: dict, tried: set[str], fallback: dict, key_left=None) -> bool:
        """上限に当たったアカウントを記録し、登録済みのアカウント・API キー（`key_left()` が真）・従量の接続の順に替える。替えたら真。"""
        kind = kind_of_text(res.get("text") or "")
        if self.account:
            ca.note_limit(self.account, kind, res.get("resets_at"))
            tried.add(self.account)
        nxt = ca.choose(exclude=tried, keep=self.keep())
        if nxt.name:
            self.switch(nxt.name, kind, decision=nxt.decision)
            return True
        if key_left is not None and key_left():
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

    def _call_running(self, call) -> ClaudeCall:
        """呼び出しの間、そのアカウントを「動いている」に数える（NDF がトークンを更新しない。#1576 の I8）。"""
        name = self.account
        self.running.append(name)
        try:
            return call()
        finally:
            self.running.remove(name)

    def keep(self) -> set[str]:
        """トークンを更新しないアカウント（動いている区間のものと、実行中の呼び出しのもの）。"""
        return {n for n in (self.section, *self.running) if n and n != ca.METERED}

    def note_account_dir(self, row: dict) -> None:
        """アカウントの設定ディレクトリの用意の結果を進捗ログの `account_dir` の行に残す（名前と理由の語だけ。I14）。"""
        st = self.ctx.state
        st.progress_write({"kind": "account_dir", "step": st.cur.get("id"), **row})

    def child_env(self, fallback: dict | None = None) -> dict | None:
        """次の呼び出しの環境（登録が 2 つ以上のとき）。None は起動したときの環境のまま（アカウントを持たないとき）。

        従量の接続の間は、起動のたびに起動先の決定が候補のアカウントを返すかを確かめ、返せば戻す。アカウントの間は起動のたびに
        選び直し、今のアカウントが最良の候補と同じ組になければ替える（#2930 の決定 6）。今のアカウントを使えなければ（再登録が要る・
        認証の失敗の観測がある・設定ディレクトリを用意できない）別のアカウント、従量の接続（`fallback`）の順に替える。どれも無く、
        他が上限なだけなら AccountsLimited（解除まで待つ）、候補が無ければ AuthUnavailable を投げる（起動した時の環境で呼ばない）。"""
        environ = dict(os.environ)
        if self.account == ca.METERED:
            c = ca.choose(keep=self.keep())
            if not c.recoverable():
                return ca.account_env(ca.METERED, environ)
            self.switch(c.name, "recovered", decision=c.decision)
        if self.account is None:
            return None
        c = ca.choose(keep=self.keep(), current=self.account)  # 起動のたびに選び直す。同じ組なら替えない（#2930 の決定 6）
        if c.name and c.name != self.account:
            self.switch(c.name, "rule", decision=c.decision)
        acc = ca.load_account(self.account)
        if not (acc and acc.auth_held(time.time())) and (env := ca.account_env(self.account, environ, self.note_account_dir)):
            return env
        c, env = ca.choose_env(exclude={self.account}, keep=self.keep(), base=environ, note=self.note_account_dir)
        if env is not None:
            self.switch(c.name, "auth", decision=c.decision)
            return env
        if fallback:
            self.switch(ca.METERED, "auth", keys=list(fallback))
            return ca.account_env(ca.METERED, environ)
        if c.earliest:
            name, until = c.earliest
            raise AccountsLimited(
                f"アカウント {self.account} を使えず、替えるアカウント {name} は上限にある", None if until == math.inf else until
            )
        raise AuthUnavailable(f"アカウント {self.account} を使えず、替えるアカウントも従量の接続の宣言も無い")

    def switch(self, to: str, reason: str, keys: list[str] | None = None, decision=None) -> None:
        """次の呼び出しのアカウントを替え、プランの状態（`auth`・`switched`）と途中の報告へ 1 行残す（I12）。

        `decision`（起動先の決定）があれば、規則の名前・組・根拠の値を行に足す（#2930 の受け入れ条件 23）。"""
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
        if decision is not None:
            row.update(rule=decision.rule, account_rule=decision.account_rule, evidence=decision.evidence)
        st.progress_write(row)

    def note_limit(self, res: dict) -> None:
        cur = self.ctx.state.cur
        cur["limit"] = True
        cur["limit_hits"] = cur.get("limit_hits", 0) + 1
        if res.get("resets_at"):
            cur["limit_resets"] = datetime.fromtimestamp(res["resets_at"]).astimezone().isoformat(timespec="minutes")

    def record_usage(self, kind: str, res: dict) -> None:
        """1 回の呼び出しの使用量を実行の状態（報告の数え上げ）へ足す。帳簿へは `call_claude` が書く（#1953 の決定 2）。"""
        self.ctx.state.add_usage(kind, res)
