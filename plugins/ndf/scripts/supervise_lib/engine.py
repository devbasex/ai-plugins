"""プランを 1 本流す `Engine`（#1142 の C1）。

ステップの型からハンドラーを引き、成功・失敗・承認ゲート・遅れの打ち切り・利用上限で次のステップを決める。
記録は `RunState` が持ち、claude -p は `ClaudeRunner`、遅れの見張りは `SlowWatch` が持つ。
`engine` を import するのは `commands` と `flow` だけである。ステップの遷移のループは `flow.plan_workflow`（耐久ワークフロー）が
持ち、`Engine` は 1 ステップを流して次を決める判断のメソッドと、記録の組み直し（`replay`）を持つ。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import gh_call
import gh_quota
import runtime_policy
import slow_step as ss
from supervise_lib import decl, paths
from supervise_lib.claude import AuthUnavailable, ClaudeCall, ClaudeRunner, UsageLimit, claude_kind
from supervise_lib.plan import expand_parts, fail_kind_matches, normalize_plan, on_exit_error
from supervise_lib.pr import PrStep
from supervise_lib.slow import SLOW_EXIT, SlowAction, SlowWatch
from supervise_lib.state import RunState
from supervise_lib.steps import JudgeStep, RunStep, StepContext, is_gate, last_json
from supervise_lib.worker_steps import DriveStep, WorkStep


class PolicyClaudeRunner(ClaudeRunner):
    """ランタイムの宣言（#1598）を起動の前に照らす `ClaudeRunner`。

    judge・遅れの判定・PR の本文のように、ステップの `runtime` を通らずに claude を直接起動する呼び出しも、
    宣言が claude を許さなければ起動せずに失敗の結果を返す。`policy` は `Engine.check_runtimes` が読んで渡す
    （None は宣言が無い＝照らさない）。"""

    policy = None

    def call(self, system: str, prompt: str, tools: str | None, cwd: str, timeout: int, **kw) -> ClaudeCall:
        if self.policy is not None and not self.policy.allows("claude"):
            reason = self.policy.reason(["claude"], "claude を直接起動するステップ")
            kind = claude_kind(system, bool(kw.get("full")))
            return ClaudeCall(ok=False, text=f"claude を起動しない: {reason}", usage={}, seconds=0, kind=kind, model_usage=None)
        return super().call(system, prompt, tools, cwd, timeout, **kw)


class Engine:
    """1 本のプランの実行。`state` が記録を、`ctx` がハンドラーへ渡す文脈を持つ。"""

    def __init__(self, plan: dict, state_dir: Path, slow_args: list[str] | None = None, plan_path: str | None = None) -> None:
        self.plan = normalize_plan(plan)
        plan["steps"] = expand_parts(plan["steps"])
        self.steps = {s["id"]: s for s in plan["steps"]}
        self.order = [s["id"] for s in plan["steps"]]
        self.state = RunState(state_dir, self.plan)
        self.ctx = StepContext(self.plan, self.steps, self.state, str(Path(plan_path).resolve()) if plan_path else "")
        self.ctx.tick = self.tick
        self.slow = self.ctx.slow = SlowWatch(self.ctx, slow_args)
        self.ctx.claude = PolicyClaudeRunner(self.ctx)
        self.gh_limit_waits: dict[str, int] = {}  # judge の retry で GitHub の上限を待った回数（ステップごと）
        self.run_step = RunStep()
        self.handlers = {h.kind: h for h in (self.run_step, WorkStep(), DriveStep(), PrStep(), JudgeStep())}

    @property
    def cwd(self) -> str:
        return self.ctx.cwd

    def tick(self) -> None:
        """子プロセスの待ちの間に呼ぶ。worker の行を分け、動きが無ければ「まだ動いている」を足す。"""
        st = self.state
        st.read_worker_lines()
        if time.time() - st.last_line_at >= st.interval:
            line = {
                "kind": "alive",
                "step": st.cur.get("id"),
                "type": st.cur.get("type"),
                "elapsed": round(time.time() - st.step_started, 1),
                "worker": st.worker_last or "無し",
            }
            last = st.run_last_output()
            if last:
                line["last_output"] = last
            st.progress_write(line)
        self.slow.check_slow()

    def gh_limit_wait(self, sid: str) -> None:
        """judge が打ち直すステップの前の出力が GitHub の上限なら、回復の時刻まで待つ。

        回復の時刻は `gh api rate_limit` の graphql の reset（残りが 0 のときだけ。reset は枠が残っていても
        窓の終わりを返す）。読めない（残りがある・過ぎている）なら、同じステップの待ちごとに 60 秒から
        倍々にし、1 時間（graphql の窓の長さ）で頭打ちにする。待った秒は progress の `"kind": "gh-limit"` に残す。
        待ちの実際の秒数は NDF_SUPERVISE_LIMIT_SLEEP で短くできる（試験用）。
        """
        st = self.state
        prev = st.results.get(sid) or {}
        if prev.get("exit") in (0, None) or not gh_quota.is_rate_limited(prev.get("text", "")):
            return
        k = self.gh_limit_waits.get(sid, 0)
        self.gh_limit_waits[sid] = k + 1
        r = gh_call.gh(["api", "rate_limit", "--jq", ".resources.graphql | select(.remaining == 0) | .reset"])
        try:
            reset = float(r.stdout.strip()) if r.returncode == 0 else None
        except ValueError:
            reset = None
        known = reset is not None and reset > time.time()
        wait = reset - time.time() if known else min(60.0 * 2**k, 3600.0)
        short = os.environ.get("NDF_SUPERVISE_LIMIT_SLEEP")
        until = time.time() + (min(wait, float(short)) if short else wait)
        while time.time() < until:  # 待ちの間も「まだ動いている」を書く
            time.sleep(max(0.0, min(st.every, until - time.time())))
            self.tick()
        st.cur["gh_limit_waited"] = round(wait)
        st.progress_write(
            {
                "kind": "gh-limit",
                "step": sid,
                "waited": round(wait),
                "reset": datetime.fromtimestamp(reset).astimezone().isoformat(timespec="seconds") if known else None,
            }
        )

    def next_of(self, sid: str, step: dict) -> str | None:
        """成功したときの次のステップ。`next` が無ければ並びの次へ進むが、失敗したときにだけ通るステップ
        （どこかの `on_fail` が指すステップと、そこから `next` で戻るステップ）は飛ばす。"""
        if step.get("next"):
            return None if step["next"] == "end" else step["next"]
        fail_only = {s["on_fail"] for s in self.steps.values() if s.get("on_fail")}
        fail_only |= {
            s["id"]
            for s in self.steps.values()
            if s["type"] == "work"
            and s.get("next") in self.steps
            and s.get("inputs")
            and any(self.steps.get(i, {}).get("on_fail") for i in s["inputs"])
        }
        i = self.order.index(sid) + 1
        while i < len(self.order) and self.order[i] in fail_only:
            i += 1
        return self.order[i] if i < len(self.order) else None

    def ensure_worktree(self) -> str | None:
        """計画に branch があり作業場所が無ければ、作業ツリーを作る。誤りの文を返す（無ければ None）。"""
        return paths.ensure_worktree(self.plan)

    def keep_cwd(self) -> None:
        """前のステップ（merge の後片付け）が作業場所を消していたら、元のリポジトリで続ける。"""
        if Path(self.ctx.cwd).is_dir():
            return
        wt = str(self.plan["作業場所"])
        root = self.plan.get("リポジトリ") or (wt.split("/.worktrees/")[0] if "/.worktrees/" in wt else None)
        if root and Path(root).is_dir():
            self.ctx.cwd = root

    def report(self, result: str, reason: str) -> str:
        return self.state.write_report(self.plan, result, reason)

    def run(self, start: str | None = None) -> str:
        """プランを流して `## フェーズの報告` を返す。ステップの遷移は耐久ワークフロー（`flow.plan_workflow`）が持つ。"""
        from supervise_lib import flow  # flow が Engine を import するため、モジュールの循環を避けてここで読む

        return flow.run_engine(self, start)

    def setup(self, start: str | None) -> tuple[str, str] | None:
        """ステップを流す前の、ファイルを書かない準備（打ち直しでも毎回流す）。流さないなら (結果, 理由)。"""
        if start and not self.plan.get("Pull Request"):
            self._restore_pr()
        try:
            self.slow.cfg = self.slow.resolve_slow()
        except ss.SlowConfigError as e:
            return "止まった", f"slow の設定が読めない（{e.args[0]}）"
        bad = on_exit_error(self.plan["steps"])
        if bad:
            return "止まった", bad
        return self.check_runtimes()

    def check_runtimes(self) -> tuple[str, str] | None:
        """work / drive / judge ステップの起動先を、ランタイムの宣言（#1598）とすべて照らす。

        どのステップも流す前に照らし、外か宣言が壊れていれば (止まった, 理由) を返す（AC9・AC10）。
        `runtime` が無いか `claude-p` のステップと、`"full": true` のステップ（`runtime` を見ずに
        claude を直接起動する）と judge のステップは claude（`claude -p`）を起動する。宣言の場所は
        `decl.decl_roots` の候補のうち先頭で実在するもの。読んだ宣言は `PolicyClaudeRunner.policy` に渡し、
        Claude を直接起動する共通の口（`PolicyClaudeRunner.call`）も起動の前に照らす。
        """
        roots = decl.decl_roots(str(self.plan.get("作業場所") or self.cwd or "."), self.plan.get("リポジトリ"))
        root = next((r for r in roots if r.is_dir()), None)
        try:
            policy = runtime_policy.read_policy(root)
        except runtime_policy.RuntimePolicyError as e:
            return "止まった", str(e)
        self.ctx.claude.policy = policy
        if policy is None:
            return None
        for sid in self.order:
            step = self.steps[sid]
            kind = step.get("type")
            if kind not in ("work", "drive", "judge"):
                continue
            rt = step.get("runtime")
            direct = kind == "judge" or (kind == "work" and step.get("full")) or not rt or rt == "claude-p"
            target = "claude" if direct else str(rt)
            try:
                policy.require([target], f"ステップ {sid} の runtime")
            except runtime_policy.RuntimePolicyError as e:
                what = "judge（claude で判定する）" if kind == "judge" else "worker"
                return "止まった", f"ステップ {sid} の {what} を起動しない: {e}"
        return None

    def prepare(self, start: str | None) -> tuple[str, str] | None:
        """実行の条件と worktree の用意（耐久ステップの中で 1 回だけ流す）。流さないなら (結果, 理由)。"""
        if self.plan.get("実行の条件") and not start:
            skipped = self.check_condition(self.plan["実行の条件"])
            if skipped:
                return skipped
        err = self.ensure_worktree()
        if err:
            return "止まった", err
        return None

    def after_prepare(self) -> None:
        """worktree ができた後の、ファイルを書かない準備（遅れの見張りの履歴の置き場）。"""
        self.slow.history = ss.history_path(self.cwd, self.state.dir, self.slow.cfg.history)

    def execute(self, n: int, sid: str) -> dict:
        """n 番目のステップ `sid` を流して記録し、耐久ステップの出力（`replay` が組み直す材料）を返す。

        落ちた前の起動がこのステップの途中で止まっていれば、残した子のプロセスグループを止めてから流し、
        記録に `resumed` を付ける（孤児の片付け。決定 35）。"""
        st = self.state
        prev = st.begin_step(sid)
        try:
            nxt, result, reason = self._run_step(sid, self.steps[sid])
            if prev == sid:
                st.cur["resumed"] = True
            self.slow.end_watch()
            st.record(n, sid, nxt, (self.steps.get(nxt) or {}).get("type") if nxt else None, is_gate)
        finally:
            st.end_step()
        return {
            "n": n,
            "sid": sid,
            "nxt": nxt,
            "result": result,
            "reason": reason,
            "cur": dict(st.cur),
            "pr": self.plan.get("Pull Request"),
            "acc": st.snapshot(),
        }

    def replay(self, out: dict) -> None:
        """記録のある耐久ステップの出力から、ファイルを書かずに実行の状態を組み直す（このプロセスで流したステップは除く）。"""
        if self.state.replay(out) and out.get("pr"):
            self.plan["Pull Request"] = out["pr"]

    def _run_step(self, sid: str, step: dict) -> tuple[str | None, str | None, str | None]:
        """1 ステップを始めて流し、(次のステップ, 結果, 理由) を返す。結果を変えないときは結果・理由が None。"""
        st, slow = self.state, self.slow
        self.keep_cwd()
        st.record_stage(step.get("stage"), self.plan, self.cwd)
        st.cur = {"id": sid, "type": step["type"]}
        st.step_started = time.time()
        carry, slow.carry = slow.carry, None
        slow.watch = slow.start_watch(step, carry if carry and carry.step_id == sid else None)
        try:
            if step["type"] == "judge":
                return self._next_after_judge(sid, step)
            ok, _ = self.handlers[step["type"]].execute(self.ctx, step)
            return self._next_after_step(sid, step, ok)
        except UsageLimit as e:
            # 利用上限と、渡せるトークンが無いことはステップの失敗と区別する（on_fail・judge へ回さない）
            st.cur.setdefault("exit", 1)
            st.cur["text"] = str(e)
            return None, "止まった", "認証" if isinstance(e, AuthUnavailable) else "利用上限"
        except SlowAction as e:
            return self._next_after_slow(e, sid, step)

    def _final_result(self, result: str, reason: str) -> tuple[str, str]:
        """完了のまま終えても、関門を返したステップがあれば結果を関門にする。"""
        if result == "完了" and self.state.gates:
            result = "関門"
            if reason == "無し":
                reason = "; ".join(f"ステップ {g['id']} が関門を返した（exit={g['exit']}）" for g in self.state.gates)
        return result, reason

    def _restore_pr(self) -> None:
        """途中から再開するときは、前の実行の報告に残った Pull Request を {pr} に使う。"""
        prev = self.state.dir / "report.md"
        m = re.search(r"^- Pull Request: (\S*/pull/\d+)", prev.read_text(), re.M) if prev.is_file() else None
        if m:
            self.plan["Pull Request"] = m.group(1)

    def _next_after_judge(self, sid: str, step: dict) -> tuple[str | None, str | None, str | None]:
        """judge の決定から (次のステップ, 結果, 理由) を返す。結果を変えないときは結果・理由が None。"""
        d = self.handlers["judge"].execute(self.ctx, step)
        dec = d.get("decision", "stop")
        self.state.cur["decision"] = dec
        if dec == "next":
            return self.next_of(sid, step), None, None
        if dec == "stop":
            return None, "止まった", d.get("reason", "判断が止めた")
        if dec == "gate":
            return None, "関門", d.get("reason", "")
        if dec in self.steps:
            self.gh_limit_wait(dec)
            return dec, None, None
        return None, "止まった", f"判断が知らない値を返した: {dec}"

    def _next_after_step(self, sid: str, step: dict, ok: bool) -> tuple[str | None, str | None, str | None]:
        """run ほか judge 以外のステップの結果から (次のステップ, 結果, 理由) を返す。"""
        st = self.state
        is_run = step["type"] == "run"
        if is_run and self.run_step.is_skip(step, st.cur.get("exit")):
            st.cur["skipped"] = True
            return (None if step["skip_to"] == "end" else step["skip_to"]), None, None
        if is_run and step.get("on_exit") and str(st.cur.get("exit")) in step["on_exit"]:
            return self._next_on_exit(sid, step)
        if is_run and is_gate(st.cur.get("exit")) and step.get("gate_as_ok"):
            # 関門として数えない（MVV 判定が前もって通した関門 2 の提示物など）。提示物だけを写す
            st.cur["presentation"] = self.copy_presentation(step)
            st.cur["gate_as_ok"] = True
            return self.next_of(sid, step), None, None
        if is_run and is_gate(st.cur.get("exit")):
            self.take_gate(step)
            gnext = step.get("gate_next")
            return ((None if gnext == "end" else gnext) if gnext else self.next_of(sid, step)), None, None
        if ok:
            if is_run and step.get("pr_from"):
                self.take_pr(step["pr_from"])
            return self.back_or_next(sid, step), None, None
        if step.get("on_section_missing") and st.cur.get("section_missing"):
            # pr のステップが「設計と違う点」の節を欠いて PR を作らずに止まった（#1241 の I7）
            st.failed_step = sid
            return step["on_section_missing"], None, None
        if step.get("on_fail") and fail_kind_matches(step, st.cur):
            st.failed_step = sid
            return step["on_fail"], None, None
        return None, "止まった", f"ステップ {sid} が失敗した（exit={st.cur['exit']}）"

    def _next_on_exit(self, sid: str, step: dict) -> tuple[str | None, str | None, str | None]:
        """run のステップの `on_exit`（終了コード → ステップの id / end / stop）の行き先。`on_fail` と judge を通らない
        （release とマージのステップが CI の基盤待ち 75 で止まる。#1645 の決定 6）。"""
        code = self.state.cur.get("exit")
        dest = step["on_exit"][str(code)]
        if dest == "stop":
            summary = (last_json(self.state.cur.get("text", "")) or {}).get("summary") or ""
            return None, "止まった", f"ステップ {sid} が終了コード {code} で止めた: {summary}"
        return (None if dest == "end" else dest), None, None

    def take_pr(self, key: str) -> None:
        """run のステップの `pr_from`: 終了コード 0 の出力の最後の JSON の `metrics.<key>` が空でなければ、
        計画の Pull Request をその値にする（本番のリリースプランの record が本番のリリースの PR を渡す。#1273）。"""
        if self.state.cur.get("exit") != 0:
            return
        metrics = (last_json(self.state.cur.get("text", "")) or {}).get("metrics") or {}
        if metrics.get(key):
            self.plan["Pull Request"] = str(metrics[key])

    def back_or_next(self, sid: str, step: dict) -> str | None:
        """成功したときの次のステップ。`"back_to_failed": true` のステップ（judge の後の fix）は、最後に落ちた
        ステップが `next` より並びで前なら、そこからやり直す（直した後に落ちたステップを飛ばして先へ進まない。#1315）。"""
        nxt = self.next_of(sid, step)
        failed = self.state.failed_step
        if not step.get("back_to_failed") or failed not in self.order:
            return nxt
        if nxt is None or nxt not in self.order or self.order.index(failed) < self.order.index(nxt):
            return failed
        return nxt

    def _next_after_slow(self, e: SlowAction, sid: str, step: dict) -> tuple[str | None, str | None, str | None]:
        """遅れの見張りがステップを打ち切った後始末（子はプロセスグループごと止めてある）。"""
        st, slow = self.state, self.slow
        st.cur.update(
            exit=SLOW_EXIT,
            seconds=round(time.time() - st.step_started, 1),
            text=f"遅れで打ち切った（{e.action}）: {e.reason}" + (f"\n一次の調査: {e.summary}" if e.summary else ""),
        )
        st.cur["slow"] = {"act": e.action, "reason": e.reason}
        if e.action == "retry":
            slow.carry = slow.watch
            return sid, None, None
        if e.action == "fix" and step.get("on_fail"):
            st.failed_step = sid
            return step["on_fail"], None, None
        return None, "止まった", f"遅れ: {e.reason}"

    def check_condition(self, cond: dict) -> tuple[str, str] | None:
        """計画の実行の条件を、作業ツリーを作る前に打つ。流すなら None、流さないなら (結果, 理由)。"""
        cmd = str(cond.get("cmd") or "").replace("{state_dir}", str(self.state.dir))
        wt = Path(self.plan["作業場所"])
        cwd = self.plan.get("リポジトリ") or (
            str(wt) if wt.is_dir() else str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None
        )
        if cwd and not Path(cwd).is_dir():
            cwd = None
        started = time.time()
        try:
            p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=cond.get("timeout", 900))
            code, text = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, text = 124, f"打ち切り（{e.timeout} 秒）"
        out = last_json(text) or {}
        summary = str(out.get("summary") or text.strip()[-200:] or f"exit={code}")
        self.state.progress_write(
            {
                "kind": "step",
                "step": "実行の条件",
                "type": "run",
                "exit": code,
                "seconds": round(time.time() - started, 1),
                "summary": summary[:300],
            }
        )
        if code == 0:
            return None
        if code == cond.get("skip_code", 3):
            return "完了", f"実行の条件に当たらない（{summary}）"
        self.state.attention("止まった", f"実行の条件を判定できない（exit={code}）: {summary}")
        return "止まった", f"実行の条件を判定できない（exit={code}: {summary}）"

    def copy_presentation(self, step: dict) -> str | None:
        """結果 JSON の presentation_path を、`presentation_to` があればそこへ写してパスを返す。"""
        out = last_json(self.state.cur.get("text", "")) or {}
        path = out.get("presentation_path")
        dest = step.get("presentation_to")
        if path and dest and Path(path).is_file():
            target = Path(step.get("cwd", self.cwd)) / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            path = str(target)
        return path

    def take_gate(self, step: dict) -> None:
        """run のステップの関門を残す。提示物（結果 JSON の presentation_path）は `presentation_to` があれば写す。"""
        cur = self.state.cur
        path = self.copy_presentation(step)
        cur["gate"] = True
        if path:
            cur["presentation"] = path
        self.state.gates.append({"id": step["id"], "exit": cur.get("exit"), "presentation": path})
        self.state.attention(
            "関門", f"ステップ {step['id']} が関門を返した（exit={cur.get('exit')}）" + (f"。提示物 {path}" if path else "")
        )
