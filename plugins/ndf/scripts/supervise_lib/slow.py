"""遅れの見張り（#1142 の C1）。run・work・drive のステップの待ちの中で、想定より遅いステップに手を打つ。

`SlowWatch` は `Engine` が持ち、ハンドラーからは `ctx.slow` として使う。`ctx` から読むもの: `plan`・`steps`・
`cwd`・`state`（途中の報告と worker の行）・`claude`（判定の claude -p）・`base_branch()`・`plan_path`。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import clock
import slow_step as ss  # 遅れの見張りの材料
from supervise_lib import decl, plan as plan_mod
from supervise_lib.claude import TAIL
from supervise_lib.prompts import SLOW_SYSTEM


SLOW_EXIT = 125  # 遅れの見張りがステップを打ち切った（124 の打ち切り・10〜19 の関門と分ける）


class SlowAction(Exception):
    """遅れの見張りがステップを打ち切るときに投げる。action は retry / fix / stop。"""

    def __init__(self, action: str, reason: str, summary: str = ""):
        super().__init__(f"{action}: {reason}")
        self.action, self.reason, self.summary = action, reason, summary


@dataclass
class StepWatch:
    """走っているステップ 1 つの見張りの状態。ステップの開始で作り、ステップの終わりで捨てる。"""
    step_id: str
    type: str
    started: float
    expected: float
    basis: dict
    next_check: float
    waits: int = 0
    llm_calls: int = 0
    off: bool = False
    out_size: int = 0
    worker_seen: int = 0
    commits: int | None = None
    round: int = 0
    retries: int = 0
    paused: float = 0.0
    probes: list = field(default_factory=list)


class SlowWatch:
    """遅れの見張り。`watch` は走っているステップの `StepWatch`、`carry` は見張りの retry で打ち直すステップへ
    引き継ぐ見張り、`busy` は調査と判定の間、`paused` は利用上限の待ちの間（経過に入れない）。"""

    def __init__(self, ctx, args: list[str] | None = None) -> None:
        self.ctx = ctx
        self.args = list(args or [])
        self.cfg = ss.SlowConfig()
        self.history: Path | None = None
        self.watch: StepWatch | None = None
        self.carry: StepWatch | None = None
        self.busy = False
        self.paused = False

    def resolve_slow(self) -> ss.SlowConfig:
        """引数 → 計画の slow → 宣言の slow → 既定で設定を重ね、ステップの expected と probe の形を確かめる。"""
        try:
            roots = decl.decl_roots(self.ctx.cwd, self.ctx.plan.get("リポジトリ"))
            declared = decl.read_decl(roots, decl.SUPERVISE_DECL).get("slow")
        except decl.DeclError:
            raise ss.SlowConfigError(decl.SUPERVISE_DECL) from None
        cfg = ss.resolve_config(ss.parse_overrides(self.args), self.ctx.plan.get("slow"), declared)
        for s in self.ctx.steps.values():
            exp = s.get("expected")
            if exp is not None and (isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= 0):
                raise ss.SlowConfigError(f"ステップ {s['id']} の expected")
            probe = s.get("probe", "output")
            if not (probe in ("output", "worker") or probe is False
                    or (isinstance(probe, dict) and isinstance(probe.get("cmd"), str) and probe["cmd"])):
                raise ss.SlowConfigError(f"ステップ {s['id']} の probe")
        return cfg

    def start_watch(self, step: dict, carry: StepWatch | None = None) -> StepWatch | None:
        """ステップの見張りを始める。run・work・drive のステップだけ。carry は見張りの retry で打ち直す前の見張り。"""
        if step["type"] not in ss.WATCHED_TYPES or not self.cfg.enabled:
            return None
        if carry:
            expected, basis, retries = carry.expected, carry.basis, carry.retries + 1
        else:
            hist = ss.read_history(self.history, self.ctx.plan.get("フェーズ") or "?", step["id"],
                                   self.cfg.window) if self.history else []
            expected, basis = ss.expected_for(hist, self.cfg, step.get("expected"))
            retries = 0
        commits = ss.commit_count(step.get("cwd", self.ctx.cwd)) if step["type"] == "work" else None
        return StepWatch(step_id=step["id"], type=step["type"], started=time.time(), expected=expected,
                         basis=basis, next_check=expected, retries=retries,
                         worker_seen=self.ctx.state.pcount["worker"], commits=commits)

    def end_watch(self) -> None:
        """ステップの終わり。成功か関門なら所要（利用上限の待ちを除く）を履歴へ積み、見張りを捨てる。"""
        w, self.watch = self.watch, None
        cur = self.ctx.state.cur
        if not w or not self.history or not ss.keeps(cur.get("exit")) or cur.get("slow"):
            return
        seconds = max(0.0, time.time() - w.started - w.paused)
        err = ss.append_history(self.history, ss.history_record(
            self.ctx.plan.get("フェーズ"), w.step_id, w.type, seconds, cur["exit"], self.ctx.plan_path, clock.now_iso()))
        if err:
            cur["slow_history"] = err

    def slow_elapsed(self) -> float:
        w = self.watch
        return time.time() - w.started - w.paused if w else 0.0

    def slow_write(self, rec: dict) -> None:
        self.ctx.state.slow_events.append(dict(rec))
        self.ctx.state.progress_write(rec)

    def check_slow(self) -> None:
        """tick の中で呼ぶ。経過が次の確認に達したら一次の調査を流し、手を決めて打つ。"""
        w = self.watch
        if not w or w.off or self.busy or self.paused:
            return
        el = self.slow_elapsed()
        if el < w.next_check:
            return
        self.busy = True
        try:
            self.handle_slow(w, round(el, 1))
        finally:
            self.busy = False

    def handle_slow(self, w: StepWatch, el: float) -> None:
        cfg, sid = self.cfg, w.step_id
        exp = round(w.expected, 1)
        base = {"kind": "slow", "step": sid, "type": w.type, "elapsed": el, "expected": exp, "basis": w.basis}
        if w.llm_calls >= cfg.max_llm:
            w.off = True
            self.slow_write({**base, "round": w.round, "act": "off", "by": "rule"})
            self.ctx.state.attention("遅れ", f"ステップ {sid} の遅れの判定が上限 {cfg.max_llm} 回に達した。ステップの timeout まで待つ")
            return
        w.round += 1
        probe = self.slow_probe(w)
        brief = {k: probe.get(k) for k in ("name", "class", "action", "summary")}
        w.probes.append(brief)
        line = {**base, "round": w.round, "probe": brief}
        action = probe.get("action")
        if action in ("wait", "remedied") and w.waits < cfg.max_waits:
            w.waits += 1
            w.next_check = round(el + w.expected, 1)
            self.slow_write({**line, "act": "wait", "by": "rule", "next_check": w.next_check})
            if action == "remedied":
                self.ctx.state.attention("遅れ", f"ステップ {sid} が想定 {exp} 秒を超えた（{round(el)} 秒）: {brief['summary']}。待ち直す")
            return
        llm = None
        if action in ("retry", "fix", "stop"):
            act, by, reason = action, "rule", str(brief.get("summary") or "")
        else:
            w.llm_calls += 1
            d = self.judge_slow(w, el)
            by, reason = "llm", d["reason"]
            llm = {"reason": reason, "cost": d.get("cost") or 0.0, "seconds": d.get("seconds")}
            act = d["decision"] if d["ok"] else "wait"
            if not d["ok"]:
                self.ctx.state.attention("遅れ", f"ステップ {sid} の遅れの判定を読めない（{reason[:200]}）")
        if act == "retry" and w.retries >= cfg.max_retry:
            act, reason = "stop", f"{reason}（retry の上限 {cfg.max_retry} 回を超えた）"
        if llm:
            line["llm"] = llm
        if act == "wait":
            w.waits = 0
            wait_s = w.expected
            if llm and d["ok"] and isinstance(d.get("wait_seconds"), (int, float)) \
                    and not isinstance(d["wait_seconds"], bool):
                wait_s = min(max(float(d["wait_seconds"]), 60.0), w.expected)
            w.next_check = round(el + wait_s, 1)
            self.slow_write({**line, "act": "wait", "by": by, "next_check": w.next_check})
            if llm and d["ok"]:
                self.ctx.state.attention("遅れ", f"ステップ {sid} が想定 {exp} 秒を超えた（{round(el)} 秒）: 判定 wait（{reason}）")
            return
        self.slow_write({**line, "act": act, "by": by})
        self.ctx.state.attention("遅れ", f"ステップ {sid} を打ち切った（{act}）: {reason}")
        raise SlowAction(act, reason, str(brief.get("summary") or ""))

    def probe_values(self, step: dict) -> dict:
        """probe の cmd の置き換え: {pr} {base} {branch} {state_dir}。"""
        cwd = step.get("cwd", self.ctx.cwd)
        origin = str(self.ctx.plan.get("起点") or "")
        base = origin[len("origin/"):] if origin.startswith("origin/") else (origin or self.ctx.base_branch() or "")
        branch = subprocess.run(["git", "branch", "--show-current"], cwd=cwd, capture_output=True,
                                text=True).stdout.strip() if Path(cwd).is_dir() else ""
        return {"pr": plan_mod.pr_number(self.ctx.plan.get("Pull Request")), "base": base, "branch": branch,
                "state_dir": str(self.ctx.state.dir)}

    def slow_probe(self, w: StepWatch) -> dict:
        """ステップの probe（既定は run・drive が output、work が worker）で一次の調査を流す。"""
        step = self.ctx.steps[w.step_id]
        kind = step.get("probe", "worker" if w.type == "work" else "output")
        if kind is False:
            return {"name": "none", "class": "unknown", "action": "judge", "summary": "調べずに判定へ回す（probe: false）"}
        if isinstance(kind, dict):
            return ss.probe_cmd(kind["cmd"], self.probe_values(step), step.get("cwd", self.ctx.cwd),
                                self.cfg.probe_timeout)
        if kind == "worker":
            self.ctx.state.read_worker_lines()
            new_lines = self.ctx.state.pcount["worker"] - w.worker_seen
            w.worker_seen = self.ctx.state.pcount["worker"]
            c = ss.commit_count(step.get("cwd", self.ctx.cwd))
            new_commits = c - w.commits if c is not None and w.commits is not None else 0
            w.commits = c
            since = time.time() - self.ctx.state.worker_last_at if self.ctx.state.worker_last_at else None
            return ss.probe_worker(new_lines, new_commits, self.ctx.state.worker_last, since)
        probe, w.out_size = ss.probe_output(self.ctx.state.run_log, w.out_size)
        return probe

    def judge_slow(self, w: StepWatch, el: float) -> dict:
        """材料を最小構成の claude -p（Tool なし）に渡し、retry / fix / stop / wait から 1 つを選ばせる。
        返り値: {"ok", "decision", "reason", "wait_seconds", "cost", "seconds"}。読めなければ ok が偽。"""
        step = self.ctx.steps[w.step_id]
        spec = {k: step.get(k) for k in ("id", "type", "cmd", "kind", "timeout") if step.get(k) is not None}
        spec["on_fail"] = bool(step.get("on_fail"))
        if w.type == "work":
            tail = "\n".join(self.ctx.state.worker_recent) or "（worker の行なし）"
        else:
            try:
                log = self.ctx.state.run_log
                tail = log.read_text(encoding="utf-8", errors="replace")[-TAIL:] if log else ""
            except OSError:
                tail = ""
        hist = ss.read_history(self.history, self.ctx.plan.get("フェーズ") or "?", w.step_id,
                               self.cfg.window) if self.history else []
        prompt = (f"フェーズ: {self.ctx.plan.get('フェーズ')} / 課題: {self.ctx.plan.get('課題')}\n"
                  f"## ステップ\n{json.dumps(spec, ensure_ascii=False)}\n\n"
                  f"## 経過と想定\n経過 {el} 秒 / 想定 {round(w.expected, 1)} 秒 / 根拠 "
                  f"{json.dumps(w.basis, ensure_ascii=False)}\n\n"
                  f"## 一次の調査（古い順）\n" + "\n".join(json.dumps(p, ensure_ascii=False) for p in w.probes)
                  + f"\n\n## 出力の末尾\n{tail or '（出力なし）'}\n\n"
                  f"## 同じステップの履歴の所要（秒、古い順）\n{hist or '無し'}\n\n"
                  "## 手の意味\nretry = ステップを止めて同じステップを打ち直す / fix = ステップを止めて on_fail へ / "
                  "stop = 計画を止める / wait = 待ち直す（wait_seconds を付けてよい）")
        res = self.ctx.claude.call(SLOW_SYSTEM, prompt, None, str(self.ctx.state.work), int(self.cfg.judge_timeout))
        self.ctx.claude.record_usage("judge", res)
        self.ctx.state.pcount["llm"] += 1
        self.ctx.state.pcount["llm_cost"] += res.get("cost") or 0.0
        out = {"ok": False, "cost": res.get("cost"), "seconds": res.get("seconds")}
        if not res.get("ok"):
            return {**out, "decision": "wait", "reason": str(res.get("text") or "")[:200]}
        m = re.search(r"\{.*\}", res.get("text") or "", re.S)
        try:
            d = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            d = None
        if not isinstance(d, dict) or d.get("decision") not in ss.DECISIONS:
            return {**out, "decision": "wait", "reason": f"答えを読めない: {(res.get('text') or '')[:200]}"}
        return {**out, "ok": True, "decision": d["decision"], "reason": str(d.get("reason") or "")[:300],
                "wait_seconds": d.get("wait_seconds")}
