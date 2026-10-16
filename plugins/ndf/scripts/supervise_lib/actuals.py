"""supervise の実行を見積もりの実績の記録（`estimate_records`）へ残す（#1856 の F5）。

実行の始まり（`run_start`）・ステップの終わり（`step`）・実行の終わり（`run_end`）を 1 行ずつ追記する。
SIGINT・SIGTERM では `run_end` を「取り消し」で書いてから、元のシグナルの扱いへ渡す。終わりの行が無い実行は
読む側が中断として数える。書けなくても実行の結果は変えず、`progress.jsonl` に `estimate_record` の行を残す。
"""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import clock
import estimate_records as er
import schema

RUN_FILE = "actuals-run.json"  # 状態ディレクトリに置く、今の実行の識別子と始まりの時刻
OUTCOMES = {"完了": "成功", "関門": "承認ゲート"}  # それ以外の結果は失敗
_OPEN: set["ActualsLog"] = set()
_INSTALLED = False


class ActualsLog:
    """1 本のプランの実行の実績の書き手。`state` は `RunState`（状態ディレクトリ・記録・途中の報告）。"""

    def __init__(self, state, plan: dict) -> None:
        self.state = state
        self.plan = plan
        self.plan_path = ""
        self.retries: dict[str, int] = {}

    def _records(self) -> Path:
        root = self.plan.get("リポジトリ") or self.plan.get("作業場所") or os.getcwd()
        return er.estimate_dir(root)

    def _write(self, row: dict) -> None:
        try:
            err = er.append_actual(self._records(), row)
        except (schema.ShapeError, er.DeclError) as e:
            err = str(e)
        if err:
            self.state.progress_write({"kind": "estimate_record", "ok": False, "reason": err[:300]})

    def _current(self) -> dict:
        try:
            data = json.loads((self.state.dir / RUN_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def start(self, start: str | None, at: str | None = None) -> None:
        """実行の始まり。同じプランの何回目かは `progress.jsonl` の `run` の行の数で数える。

        `at` は `run_line` が書いた `run` の行の `at`。識別子は取り込み（`estimate_import`）と同じく、状態ディレクトリの
        実体とその `at` から作る（同じ実行を取り込みが別の実行として足さない）。"""
        at = at or clock.now_iso()
        plan = self.plan_path or str(self.state.dir)
        rid = er.actual_run_id(self.state.dir, at)
        try:
            (self.state.dir / RUN_FILE).write_text(json.dumps({"run": rid, "began": time.time()}), encoding="utf-8")
        except OSError:
            return
        rows = [r for r in _progress_rows(self.state.progress) if r.get("kind") == "run"]
        issues = [int(i) for i in self.plan.get("課題") or [] if isinstance(i, int) or str(i).isdigit()]
        self._write(
            {
                "kind": "run_start",
                "at": at,
                "run": rid,
                "plan": plan,
                "issues": issues,
                "phase": self.plan.get("フェーズ") or None,
                "from": start,
                "attempt": max(1, len(rows)),
                "imported": False,
            }
        )
        _OPEN.add(self)
        install_cancel_handler()

    def step(self, cur: dict) -> None:
        """ステップの終わり。`retry` は遅れの見張りがこのステップを打ち直した回数（この行より前）。"""
        rid = self._current().get("run")
        if not rid:
            return
        sid = str(cur.get("id") or "")
        retry = self.retries.get(sid, 0)
        if (cur.get("slow") or {}).get("act") == "retry":
            self.retries[sid] = retry + 1
        llm = cur.get("llm") or {}
        step = next((s for s in self.plan.get("steps") or [] if s.get("id") == sid), {})
        self._write(
            {
                "kind": "step",
                "run": rid,
                "step": sid,
                "type": str(cur.get("type") or ""),
                "exit": cur.get("exit") if isinstance(cur.get("exit"), int) else None,
                "seconds": cur.get("seconds") if isinstance(cur.get("seconds"), (int, float)) else None,
                "cost_usd": _step_cost_usd(cur.get("type"), llm),
                "runtime": _runtime(step),
                "models": sorted(llm.get("models") or {}) or None,
                "retry": retry,
            }
        )

    def end(self, result: str) -> None:
        """実行の終わり。結果（完了・関門・止まった ほか）か「取り消し」を終わり方へ写す。"""
        cur = self._current()
        _OPEN.discard(self)
        if not cur.get("run"):
            return
        outcome = "取り消し" if result == "取り消し" else OUTCOMES.get(result, "失敗")
        self._write(
            {
                "kind": "run_end",
                "run": cur["run"],
                "outcome": outcome,
                "wall_seconds": round(max(0.0, time.time() - float(cur.get("began") or time.time())), 1),
                "config": self.config(),
            }
        )
        (self.state.dir / RUN_FILE).unlink(missing_ok=True)

    def config(self) -> dict:
        """実行の構成。モデルは流したステップの記録から、ハーネスは work・drive のステップの起動先から取る。
        レビューの方式・テストの方式・並列の数は実行の中で決まらないため空（不明）にする。"""
        steps = {s.get("id"): s for s in self.plan.get("steps") or []}
        models = sorted({m for e in self.state.log for m in ((e.get("llm") or {}).get("models") or {})})
        ran = {e.get("id") for e in self.state.log}
        harness = sorted({_runtime(steps[i]) for i in ran if i in steps and steps[i].get("type") in ("work", "drive")})
        return {
            "models": models or None,
            "harness": "+".join(h for h in harness if h) or None,
            "review": None,
            "test": None,
            "parallel": None,
        }


def _step_cost_usd(kind, llm: dict) -> float | None:
    """ステップの USD。LLM を呼ばないステップ（run・pr）は 0、LLM の費用を取れなかった呼び出しを含むステップは空（不明）。"""
    if llm.get("cost_unknown"):
        return None
    if isinstance(llm.get("cost"), (int, float)):
        return llm["cost"]
    return 0.0 if kind in ("run", "pr") else None


def _runtime(step: dict) -> str | None:
    if step.get("type") not in ("work", "drive", "judge"):
        return None
    return str(step.get("runtime") or "claude-p")


def _progress_rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for raw in lines:
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def install_cancel_handler() -> None:
    """SIGINT・SIGTERM で開いている実行へ「取り消し」を書き、元の扱いへ渡す。主スレッドの外では何もしない。

    耐久ワークフローの中（`prepare_step` から `ActualsLog.start`）は主スレッドの外のため、`supervise.py run` / `queue`
    は耐久の記録を開く前（`flow.launch_run` / `launch_queue`）に主スレッドでここを呼ぶ。"""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, _cancel_handler(sig, signal.getsignal(sig)))
    except ValueError:
        return
    _INSTALLED = True


def _cancel_handler(sig, prev):
    def handle(signum, frame):
        for log in list(_OPEN):
            try:
                log.end("取り消し")
            except Exception:  # noqa: BLE001  取り消しの記録が書けなくても元の扱いへ渡す
                pass
        if callable(prev):
            prev(signum, frame)
        elif prev != signal.SIG_IGN:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    return handle
