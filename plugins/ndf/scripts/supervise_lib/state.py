"""実行の状態（`<プラン>-state/`）の持ち主（#1142 の C1）。

`RunState` は、ステップの結果・記録（`state.json` の `log` と `llm`）・途中の報告（`progress.jsonl`）・
承認ゲート・報告（`report.md`）を持つ。書くのはこのクラスだけである。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import clock
import usage_ledger
from supervise_lib.claude import TICK

REPORT_INTERVAL = 600  # 最後の行から動きが無いときに「まだ動いている」を足すまでの秒数。計画の "report_interval"
# worker の途中の報告を分ける語（スクリプトで見る。LLM は使わない）
PROGRESS_STOP = re.compile(r"止まった|止まる|進めない|進められない|判断が要る|できなかった|stuck", re.I)
PROGRESS_GATE = re.compile(r"関門|承認が要る|承認を待つ")
PROGRESS_FAIL = re.compile(r"失敗|落ちた|落ちる|エラー|\berror\b|\bfailed\b|traceback", re.I)


def counts_text(counts: dict) -> str:
    return " / ".join(f"{k} {v}" for k, v in counts.items() if v is not None) or "無し"


class RunState:
    """1 本のプランの実行の状態。`dir` は状態ディレクトリ、`cur` は今のステップの記録。"""

    def __init__(self, state_dir: Path, plan: dict) -> None:
        self.dir = state_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        # 計画ごとの作業ディレクトリ。状態ディレクトリの下なので並行する計画どうしで重ならない
        self.work = (self.dir / "work").resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.results: dict[str, dict] = {}
        self.log: list[dict] = []
        self.llm = {"work": 0, "judge": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                    "output": 0, "cost": 0.0}
        self.last_stage = "無し"
        self.pace_recorded = False
        self.gates: list[dict] = []      # run のステップが返した関門（終了コード 10〜19）
        self.switched: list[str] = []    # 利用上限で足した認証の変数の名前
        self.cur: dict = {}
        self.fail_counts: dict[str, int] = {}
        # 途中の報告（progress.jsonl）。LLM を使わずスクリプトで書き・分ける
        self.progress = self.dir / "progress.jsonl"
        self.interval = float(plan.get("report_interval", REPORT_INTERVAL))
        self.every = max(0.05, min(TICK, self.interval / 4))
        self.progress_seen = self.progress.stat().st_size if self.progress.is_file() else 0
        self.last_line_at = time.time()
        self.step_started = time.time()
        self.worker_last = ""
        self.worker_counts: dict[str, int] = {}
        self.attention_keys: set[str] = set()
        self.pcount = {"step": 0, "alive": 0, "worker": 0, "malformed": 0, "attention": 0, "slow": 0, "llm": 0,
                       "llm_cost": 0.0}
        self.slow_events: list[dict] = []
        self.worker_recent: list[str] = []
        self.worker_last_at: float | None = None
        self.run_log: Path | None = None  # run のステップの stderr（待ちの間に最後の行を読む）

    # --- 途中の報告 ---
    def progress_write(self, rec: dict) -> None:
        rec = {"kind": rec.pop("kind"), "at": clock.now_iso(), **rec}
        with open(self.progress, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.progress_seen = self.progress.stat().st_size
        self.last_line_at = time.time()
        if rec["kind"] in self.pcount:
            self.pcount[rec["kind"]] += 1

    def attention(self, reason: str, text: str) -> None:
        """conductor の判断が要る出来事を 1 行残す（同じ理由と文は 1 度だけ）。"""
        key = f"{reason}\n{text}"
        if key in self.attention_keys:
            return
        self.attention_keys.add(key)
        self.progress_write({"kind": "attention", "step": self.cur.get("id"), "reason": reason, "text": text[:300]})

    def classify_worker(self, text: str) -> None:
        """worker の 1 行を語と繰り返しで分け、conductor の判断が要るものだけを attention にする。"""
        norm = re.sub(r"(?<![#\d])\d+", "N", text.strip())  # 件数や秒は畳み、課題番号（#906）は残す
        n = self.worker_counts[norm] = self.worker_counts.get(norm, 0) + 1
        if PROGRESS_GATE.search(text):
            self.attention("関門", text)
        elif PROGRESS_STOP.search(text):
            self.attention("止まった", text)
        elif PROGRESS_FAIL.search(text) and n >= 2:
            self.attention("同じ失敗の繰り返し", text)
        elif n >= 3:
            self.attention("止まった（同じ報告の繰り返し）", text)

    def read_worker_lines(self) -> None:
        """前に読んだ所から後の progress.jsonl を読み、worker の行を分ける。"""
        if not self.progress.is_file() or self.progress.stat().st_size <= self.progress_seen:
            return
        with open(self.progress, "rb") as f:
            f.seek(self.progress_seen)
            data = f.read()
        end = data.rfind(b"\n") + 1  # 書きかけの行は次に読む
        if not end:
            return
        self.progress_seen += end
        self.last_line_at = time.time()
        for raw in data[:end].decode("utf-8", "replace").splitlines():
            if not raw.strip():
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                d = None
            if not isinstance(d, dict):
                self.pcount["malformed"] += 1
                continue
            if d.get("kind") != "worker":
                continue  # supervise.py が書いた行
            if not isinstance(d.get("text"), str) or not d["text"].strip():
                self.pcount["malformed"] += 1
                continue
            self.pcount["worker"] += 1
            self.worker_last = d["text"].strip()[:300]
            self.worker_last_at = time.time()
            self.worker_recent = (self.worker_recent + [self.worker_last])[-5:]
            self.classify_worker(self.worker_last)

    def run_last_output(self) -> str | None:
        """run のステップが stderr へ書いた最後の空でない行（run のステップの待ちの間だけ）。"""
        path = self.run_log
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path else ""
        except OSError:
            return None
        return next((l.strip()[:300] for l in reversed(text.splitlines()) if l.strip()), None)

    def step_line(self, nxt: str | None) -> None:
        """ステップの切り替わりの 1 行（id・type・exit・秒・費用・次・要約）。"""
        c = self.cur
        text = c.get("text", "") or ""
        summary = c.get("decision") or next((l for l in reversed(text.splitlines()) if l.strip()), "")
        self.progress_write({"kind": "step", "step": c.get("id"), "type": c.get("type"), "exit": c.get("exit"),
                             "seconds": c.get("seconds"), "cost": (c.get("llm") or {}).get("cost", 0.0),
                             "next": nxt or "end", "summary": summary.strip()[:160]})

    # --- 記録 ---
    def out_path(self, n: int, sid: str) -> Path:
        return self.dir / f"{n:02d}-{sid}.out"

    def record_stage(self, stage: str | None, plan: dict, cwd: str) -> None:
        """工程が変わったら、計画の `記録` のスクリプトで課題ごとに通過を記録する。"""
        if not stage or stage == self.last_stage:
            return
        self.last_stage = stage
        rec = plan.get("記録")
        if not rec:
            return
        pace_first = plan.get("進め方") == "fast" and not self.pace_recorded
        self.pace_recorded = True
        for issue in plan.get("課題", []):
            if pace_first:  # 通過記録と本文の見出し行へ進め方を先に書く（まとめる工程を記録なしと数えない）
                subprocess.run(["bash", rec, str(issue), "pace", "fast"], cwd=cwd, capture_output=True, text=True)
            subprocess.run(["bash", rec, str(issue), "stage", stage], cwd=cwd, capture_output=True, text=True)

    def add_usage(self, kind: str, res: dict) -> None:
        """1 回の呼び出しの使用量を、計画の合計（`llm`）と今のステップの内訳（`cur["llm"]`）へ足す。"""
        u = res.get("usage", {})
        w5, w1h = usage_ledger.cache_writes(u)
        self.llm[kind] += 1
        self.llm["input"] += u.get("input_tokens", 0)
        self.llm["cache_read"] += u.get("cache_read_input_tokens", 0)
        self.llm["cache_write"] += u.get("cache_creation_input_tokens", 0)
        self.llm["output"] += u.get("output_tokens", 0)
        self.llm["cost"] += res.get("cost") or 0.0
        # ステップごとの内訳（往復の回数・トークン・費用）。同じステップで複数回呼べば足し合わせる
        c = self.cur.setdefault("llm", {"calls": 0, "turns": 0, "input": 0, "cache_read": 0,
                                        "cache_write": 0, "output": 0, "cost": 0.0})
        c["calls"] += 1
        c["turns"] += res.get("turns") or 0
        c["input"] += u.get("input_tokens", 0)
        c["cache_read"] += u.get("cache_read_input_tokens", 0)
        c["cache_write"] += u.get("cache_creation_input_tokens", 0)
        c["output"] += u.get("output_tokens", 0)
        c["cost"] = round(c["cost"] + (res.get("cost") or 0.0), 4)
        # 書き込みの 5 分と 1 時間、モデルごとの呼び出し回数とトークン（#1142 の不足 f。キーの追加だけ）
        c["cache_write_5m"] = c.get("cache_write_5m", 0) + w5
        c["cache_write_1h"] = c.get("cache_write_1h", 0) + w1h
        models = c.setdefault("models", {})
        for name, mu in (res.get("model_usage") or {}).items():
            mu = mu if isinstance(mu, dict) else {}
            m = models.setdefault(name, {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                                         "output": 0, "cost": 0.0})
            m["calls"] += 1
            m["input"] += mu.get("inputTokens") or 0
            m["cache_read"] += mu.get("cacheReadInputTokens") or 0
            m["cache_write"] += mu.get("cacheCreationInputTokens") or 0
            m["output"] += mu.get("outputTokens") or 0
            m["cost"] = round(m["cost"] + (mu.get("costUSD") or 0.0), 4)

    def record(self, n: int, sid: str, nxt: str | None, next_type: str | None, is_gate) -> None:
        """終わったステップを残す: 結果・区切りの行・失敗の回数・出力ファイル・`state.json`。"""
        self.results[sid] = dict(self.cur)
        self.read_worker_lines()
        self.step_line(nxt)
        if (self.cur.get("exit") not in (0, None) and not is_gate(self.cur.get("exit")) and nxt
                and (self.cur.get("slow") or {}).get("act") != "retry"):
            fails = self.fail_counts[sid] = self.fail_counts.get(sid, 0) + 1
            if fails >= 2 and next_type == "judge":
                self.attention("judge のステップで stop が出そう",
                               f"ステップ {sid} が {fails} 回落ちた（exit={self.cur.get('exit')}）。次は judge のステップ {nxt}")
        self.out_path(n, sid).write_text(self.cur.get("text", ""))
        self.log.append({k: v for k, v in self.cur.items() if k != "text"})
        (self.dir / "state.json").write_text(json.dumps(
            {"log": self.log, "llm": self.llm}, ensure_ascii=False, indent=1))

    def write_report(self, plan: dict, result: str, reason: str) -> str:
        """`## フェーズの報告` を組み、`report.md` へ書いて返す。"""
        l = self.llm
        self.read_worker_lines()
        pc = self.pcount
        steps = " → ".join(f"{e['id']}" + (f"[{e['decision']}]" if "decision" in e else
                                           f"(exit={e.get('exit')})") for e in self.log)
        rows = "\n".join(
            f"| {e['id']} | {e['llm']['turns']} | {e.get('seconds', '')} | {e['llm']['cache_read']} | "
            f"{e['llm']['cache_write']} | {e['llm']['output']} | ${e['llm']['cost']:.3f} |"
            for e in self.log if e.get("llm")) or "| 無し | | | | | | |"
        counts = {}
        for e in self.log:
            if "counts" in e:
                counts[e["id"]] = e["counts"]
        counts_line = "; ".join(f"{k}: {counts_text(v)}" for k, v in counts.items()) or "無し"
        if self.gates:
            gate_line = "; ".join(f"ステップ {g['id']}（exit={g['exit']}）" for g in self.gates)
        else:
            gate_line = "本番の系へ届く操作" if result == "関門" else "無し"
        presented = ", ".join([*(g["presentation"] for g in self.gates if g.get("presentation")),
                               *(e["presentation"] for e in self.log if e.get("gate_as_ok") and e.get("presentation"))
                               ]) or "無し"
        extra = ""
        if self.switched:
            extra += f"- 認証: 切り替え（{', '.join(self.switched)}）\n"
        limited = [e for e in self.log if e.get("limit")]
        if limited:
            extra += "- 利用上限: " + "; ".join(
                f"{e['id']} {e.get('limit_hits', 1)} 回（待ち {e.get('limit_waited', 0)} 秒"
                + (f"・解除 {e['limit_resets']}" if e.get("limit_resets") else "") + "）" for e in limited) + "\n"
        acted = [e for e in self.slow_events
                 if e.get("act") != "wait" or e.get("by") == "llm" or (e.get("probe") or {}).get("action") == "remedied"]
        if acted:
            extra += "- 遅れ: " + "; ".join(
                f"{e['step']} {e.get('round', 0)} 回目 {e['act']}（{e['by']}"
                + (f"・{e['probe']['class']}" if e.get("probe") else "") + "）" for e in acted) + "\n"
        text = f"""## フェーズの報告

- フェーズ: {plan.get('フェーズ')}
- 課題: {' '.join('#' + str(i) for i in plan.get('課題', []))}
- 結果: {result}
- 関門: {gate_line}
- 次のフェーズ: {plan.get('次のフェーズ', '無し') if result == '完了' else '無し'}
- Pull Request: {plan.get('Pull Request', '無し')}
- 最後に記録した工程: {self.last_stage}
- 使った worker: 修正 {l['work']}（claude -p）/ 判断 {l['judge']}（claude -p）
{extra}- 途中の報告: ステップ {pc['step']} / まだ動いている {pc['alive']} / worker {pc['worker']}（形が違う {pc['malformed']}）/ conductor 向け {pc['attention']} / 遅れの調査 {pc['slow']} / LLM へ回した {pc['llm']} 回・${pc['llm_cost']:.3f}（{self.progress}）
- 提示物: {presented}
- 理由: {reason}
- 通ったステップ: {steps}
- 件数: {counts_line}
- LLM の使用量: 入力 {l['input']} / cache read {l['cache_read']} / cache write {l['cache_write']} / 出力 {l['output']} / ${l['cost']:.3f}
- 記録: {self.dir}

| ステップ | 往復 | 秒 | cache read | cache write | 出力 | 費用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{rows}
"""
        (self.dir / "report.md").write_text(text)
        return text
