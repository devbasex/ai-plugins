"""プランを 1 本流す `Engine`（#1142 の C1）。

ステップの型からハンドラーを引き、成功・失敗・承認ゲート・遅れの打ち切り・利用上限で次のステップを決める。
記録は `RunState` が持ち、claude -p は `ClaudeRunner`、遅れの見張りは `SlowWatch` が持つ。
`engine` を import するのは `commands` だけである。
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
import slow_step as ss
from supervise_lib import paths
from supervise_lib.claude import ClaudeRunner, UsageLimit
from supervise_lib.plan import expand_parts, normalize_plan
from supervise_lib.pr import PrStep
from supervise_lib.slow import SLOW_EXIT, SlowAction, SlowWatch
from supervise_lib.state import RunState
from supervise_lib.steps import JudgeStep, RunStep, StepContext, is_gate, last_json
from supervise_lib.worker_steps import DriveStep, WorkStep


class Engine:
    """1 本のプランの実行。`state` が記録を、`ctx` がハンドラーへ渡す文脈を持つ。"""

    def __init__(self, plan: dict, state_dir: Path, slow_args: list[str] | None = None,
                 plan_path: str | None = None) -> None:
        self.plan = normalize_plan(plan)
        plan["steps"] = expand_parts(plan["steps"])
        self.steps = {s["id"]: s for s in plan["steps"]}
        self.order = [s["id"] for s in plan["steps"]]
        self.state = RunState(state_dir, self.plan)
        self.ctx = StepContext(self.plan, self.steps, self.state,
                               str(Path(plan_path).resolve()) if plan_path else "")
        self.ctx.tick = self.tick
        self.slow = self.ctx.slow = SlowWatch(self.ctx, slow_args)
        self.ctx.claude = ClaudeRunner(self.ctx)
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
            line = {"kind": "alive", "step": st.cur.get("id"), "type": st.cur.get("type"),
                    "elapsed": round(time.time() - st.step_started, 1), "worker": st.worker_last or "無し"}
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
        wait = reset - time.time() if known else min(60.0 * 2 ** k, 3600.0)
        short = os.environ.get("NDF_SUPERVISE_LIMIT_SLEEP")
        until = time.time() + (min(wait, float(short)) if short else wait)
        while time.time() < until:  # 待ちの間も「まだ動いている」を書く
            time.sleep(max(0.0, min(st.every, until - time.time())))
            self.tick()
        st.cur["gh_limit_waited"] = round(wait)
        st.progress_write({"kind": "gh-limit", "step": sid, "waited": round(wait),
                           "reset": datetime.fromtimestamp(reset).astimezone().isoformat(timespec="seconds")
                           if known else None})

    def next_of(self, sid: str, step: dict) -> str | None:
        """成功したときの次のステップ。`next` が無ければ並びの次へ進むが、失敗したときにだけ通るステップ
        （どこかの `on_fail` が指すステップと、そこから `next` で戻るステップ）は飛ばす。"""
        if step.get("next"):
            return None if step["next"] == "end" else step["next"]
        fail_only = {s["on_fail"] for s in self.steps.values() if s.get("on_fail")}
        fail_only |= {s["id"] for s in self.steps.values()
                      if s["type"] == "work" and s.get("next") in self.steps and s.get("inputs")
                      and any(self.steps.get(i, {}).get("on_fail") for i in s["inputs"])}
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
        st, slow = self.state, self.slow
        sid = start or self.order[0]
        if start and not self.plan.get("Pull Request"):
            # 途中から再開するときは、前の実行の報告に残った Pull Request を {pr} に使う
            prev = st.dir / "report.md"
            m = re.search(r"^- Pull Request: (\S*/pull/\d+)", prev.read_text(), re.M) if prev.is_file() else None
            if m:
                self.plan["Pull Request"] = m.group(1)
        result, reason = "完了", "無し"
        limit = self.plan.get("上限", 30)
        n = 0
        try:
            slow.cfg = slow.resolve_slow()
        except ss.SlowConfigError as e:
            return self.report("止まった", f"slow の設定が読めない（{e.args[0]}）")
        if self.plan.get("実行の条件") and not start:
            skipped = self.check_condition(self.plan["実行の条件"])
            if skipped:
                return skipped
        err = self.ensure_worktree()
        if err:
            return self.report("止まった", err)
        slow.history = ss.history_path(self.cwd, st.dir, slow.cfg.history)
        while sid:
            n += 1
            if n > limit:
                result, reason = "止まった", f"ステップの数が上限 {limit} を超えた"
                break
            step = self.steps.get(sid)
            if step is None:
                result, reason = "止まった", f"知らないステップ: {sid}"
                break
            self.keep_cwd()
            st.record_stage(step.get("stage"), self.plan, self.cwd)
            st.cur = {"id": sid, "type": step["type"]}
            st.step_started = time.time()
            carry, slow.carry = slow.carry, None
            slow.watch = slow.start_watch(step, carry if carry and carry.step_id == sid else None)
            try:
                if step["type"] == "judge":
                    d = self.handlers["judge"].execute(self.ctx, step)
                    dec = d.get("decision", "stop")
                    st.cur["decision"] = dec
                    if dec == "next":
                        nxt = self.next_of(sid, step)
                    elif dec == "stop":
                        result, reason, nxt = "止まった", d.get("reason", "判断が止めた"), None
                    elif dec == "gate":
                        result, reason, nxt = "関門", d.get("reason", ""), None
                    elif dec in self.steps:
                        self.gh_limit_wait(dec)
                        nxt = dec
                    else:
                        result, reason, nxt = "止まった", f"判断が知らない値を返した: {dec}", None
                else:
                    ok, _ = self.handlers[step["type"]].execute(self.ctx, step)
                    is_run = step["type"] == "run"
                    if is_run and self.run_step.is_skip(step, st.cur.get("exit")):
                        nxt = None if step["skip_to"] == "end" else step["skip_to"]
                        st.cur["skipped"] = True
                    elif is_run and is_gate(st.cur.get("exit")) and step.get("gate_as_ok"):
                        # 関門として数えない（MVV 判定が前もって通した関門 2 の提示物など）。提示物だけを写す
                        st.cur["presentation"] = self.copy_presentation(step)
                        st.cur["gate_as_ok"] = True
                        nxt = self.next_of(sid, step)
                    elif is_run and is_gate(st.cur.get("exit")):
                        self.take_gate(step)
                        gnext = step.get("gate_next")
                        nxt = (None if gnext == "end" else gnext) if gnext else self.next_of(sid, step)
                    elif ok:
                        nxt = self.next_of(sid, step)
                    elif step.get("on_fail"):
                        nxt = step["on_fail"]
                    else:
                        result, reason, nxt = "止まった", f"ステップ {sid} が失敗した（exit={st.cur['exit']}）", None
            except UsageLimit as e:
                # 利用上限はステップの失敗と区別する（on_fail・judge へ回さない）
                st.cur.setdefault("exit", 1)
                st.cur["text"] = str(e)
                result, reason, nxt = "止まった", "利用上限", None
            except SlowAction as e:
                # 遅れの見張りがステップを打ち切った（子はプロセスグループごと止めてある）
                st.cur.update(exit=SLOW_EXIT, seconds=round(time.time() - st.step_started, 1),
                              text=f"遅れで打ち切った（{e.action}）: {e.reason}"
                                   + (f"\n一次の調査: {e.summary}" if e.summary else ""))
                st.cur["slow"] = {"act": e.action, "reason": e.reason}
                if e.action == "retry":
                    nxt, slow.carry = sid, slow.watch
                elif e.action == "fix" and step.get("on_fail"):
                    nxt = step["on_fail"]
                else:
                    result, reason, nxt = "止まった", f"遅れ: {e.reason}", None
            slow.end_watch()
            st.record(n, sid, nxt, (self.steps.get(nxt) or {}).get("type") if nxt else None, is_gate)
            sid = nxt
        if result == "完了" and st.gates:
            result = "関門"
            if reason == "無し":
                reason = "; ".join(f"ステップ {g['id']} が関門を返した（exit={g['exit']}）" for g in st.gates)
        return self.report(result, reason)

    def check_condition(self, cond: dict) -> str | None:
        """計画の実行の条件を、作業ツリーを作る前に打つ。流すなら None、流さないなら報告を返す。"""
        cmd = str(cond.get("cmd") or "").replace("{state_dir}", str(self.state.dir))
        wt = Path(self.plan["作業場所"])
        cwd = self.plan.get("リポジトリ") or (str(wt) if wt.is_dir() else
                                          str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None)
        if cwd and not Path(cwd).is_dir():
            cwd = None
        started = time.time()
        try:
            p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                               timeout=cond.get("timeout", 900))
            code, text = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, text = 124, f"打ち切り（{e.timeout} 秒）"
        out = last_json(text) or {}
        summary = str(out.get("summary") or text.strip()[-200:] or f"exit={code}")
        self.state.progress_write({"kind": "step", "step": "実行の条件", "type": "run", "exit": code,
                                   "seconds": round(time.time() - started, 1), "summary": summary[:300]})
        if code == 0:
            return None
        if code == cond.get("skip_code", 3):
            return self.report("完了", f"実行の条件に当たらない（{summary}）")
        self.state.attention("止まった", f"実行の条件を判定できない（exit={code}）: {summary}")
        return self.report("止まった", f"実行の条件を判定できない（exit={code}: {summary}）")

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
        self.state.attention("関門", f"ステップ {step['id']} が関門を返した（exit={cur.get('exit')}）"
                             + (f"。提示物 {path}" if path else ""))
