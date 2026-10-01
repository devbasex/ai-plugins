"""実行の状態（`<プラン>-state/`）の持ち主（#1142 の C1）。

`RunState` は、ステップの結果・記録（`state.json` の `log` と `llm`）・途中の報告（`progress.jsonl`）・
承認ゲート・報告（`report.md`）を持つ。書くのはこのクラスだけである。
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import clock
import jsonio
import legacy_names
import procs
import usage_ledger
from sprint_mvv import MVV_PACES
from supervise_lib.claude import TICK, watch_children

STEP_PID = "step.pid"  # 流れているステップの子のプロセスグループ（ステップが流れている間だけある）
REPORT_INTERVAL = 600  # 最後の行から動きが無いときに「まだ動いている」を足すまでの秒数。計画の "report_interval"
# worker の途中の報告を分ける語（スクリプトで見る。LLM は使わない）
PROGRESS_STOP = re.compile(r"止まった|止まる|進めない|進められない|判断が要る|できなかった|stuck", re.I)
PROGRESS_GATE = re.compile(r"関門|承認が要る|承認を待つ")
PROGRESS_FAIL = re.compile(r"失敗|落ちた|落ちる|エラー|\berror\b|\bfailed\b|traceback", re.I)


def counts_text(counts: dict) -> str:
    return " / ".join(f"{k} {v}" for k, v in counts.items() if v is not None) or "無し"


def _read_step_pid(path: Path) -> dict | None:
    return jsonio.read(path, missing=None, broken=None, want=dict)


def _write_step_pid(path: Path, data: dict) -> None:
    jsonio.write_atomic(path, data, indent=None)


def reap_orphans(state_dir: Path) -> str | None:
    """前の起動が残した `step.pid` の子のうち、pid が同じ起動時刻で生きているもののプロセスグループを止める。

    起動時刻を照らすのは、pid の使い回しで関係の無いグループを止めないため。前の起動が途中だったステップの id を返す
    （`step.pid` が無ければ None）。"""
    path = Path(state_dir) / STEP_PID
    data = _read_step_pid(path)
    if data is None:
        return None
    for c in data.get("children") or []:
        pid, created = c.get("pid"), c.get("create_time")
        now = procs.start_time(pid) if isinstance(pid, int) else None
        if now is None or created is None or abs(now - float(created)) > 0.01:
            continue
        try:
            os.killpg(int(c.get("pgid")), signal.SIGKILL)
        except (OSError, TypeError, ValueError):
            continue
        print(
            f"[ndf supervise] 落ちた前の起動がステップ {data.get('step')} で残した子のプロセスグループ {c.get('pgid')} を止めた",
            file=sys.stderr,
        )
    path.unlink(missing_ok=True)
    return data.get("step")


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
        self.llm = {"work": 0, "judge": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost": 0.0}
        self.last_stage = "無し"
        self.pace_recorded = False
        self.gates: list[dict] = []  # run のステップが返した関門（終了コード 10〜19）
        self.switched: list[str] = []  # 利用上限で切り替えた認証（変数の名前・アカウント・従量の接続）
        self.cur: dict = {}
        self.fail_counts: dict[str, int] = {}
        self.applied = 0  # 記録した（流したか組み直した）最後のステップの番号
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
        self.attention_log: list[dict] = []  # 書いた attention の行（関門で打ち直したときに知らせ直す）
        self.pcount = {"step": 0, "alive": 0, "worker": 0, "malformed": 0, "attention": 0, "slow": 0, "llm": 0, "llm_cost": 0.0}
        self.slow_events: list[dict] = []
        self.worker_recent: list[str] = []
        self.worker_last_at: float | None = None
        self.run_log: Path | None = None  # run のステップの stderr（待ちの間に最後の行を読む）
        self.project_mvv = None  # 判断の基準（lib/project_mvv.ProjectMvv）。judge か work の最初の 1 回で読む
        self.sprint_mvv: str | None = None  # スプリント MVV の本文（読んで無ければ ""）。最初の 1 回で読む

    def project_mvv_of(self, root):
        """プロジェクト MVV を実行の中で 1 回だけ読み、参照を state.json に残す（#1366）。"""
        if self.project_mvv is None:
            import project_mvv

            self.project_mvv = project_mvv.load_mvv(root)
        return self.project_mvv

    def sprint_mvv_of(self, plan: dict) -> str | None:
        """プランの `スプリント状態` の `mvv` からスプリント MVV の本文を実行の中で 1 回だけ読む（#1400 の決定 12）。

        状態が無い・`mvv` が無ければ None。ファイルが無い・状態の sha256 と一致しなければ None にし、conductor 向けに 1 行残す（I10）。"""
        if self.sprint_mvv is not None:
            return self.sprint_mvv or None
        import project_mvv

        path = legacy_names.read_key(plan, "スプリント状態")  # 改名の前のプランは旧名のキーで持つ（#1407）
        text, why = None, None
        if path:
            try:
                text, why = project_mvv.sprint_text_of(json.loads(Path(path).read_text(encoding="utf-8")))
            except (OSError, ValueError) as e:
                why = f"スプリントの状態を読めない: {e}"
        self.sprint_mvv = text or ""
        if why:
            self.attention("スプリント MVV を使えない", f"{why}。プロジェクト MVV だけで進める（{path}）")
        return text

    def mvv_block_of(self, root, plan: dict) -> tuple:
        """(プロジェクト MVV, スプリント MVV の本文か None)。スプリント MVV はプロジェクト MVV が承認済みのときだけ読む（決定 16）。"""
        mvv = self.project_mvv_of(root)
        return mvv, (self.sprint_mvv_of(plan) if mvv.approved else None)

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
        rec = {"step": self.cur.get("id"), "reason": reason, "text": text[:300]}
        self.attention_log.append(rec)
        self.progress_write({"kind": "attention", **rec})

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
        self.progress_write(
            {
                "kind": "step",
                "step": c.get("id"),
                "type": c.get("type"),
                "exit": c.get("exit"),
                "seconds": c.get("seconds"),
                "cost": (c.get("llm") or {}).get("cost", 0.0),
                "next": nxt or "end",
                "summary": summary.strip()[:160],
            }
        )

    # --- 流れているステップの子（孤児の片付け。決定 35） ---
    def begin_step(self, sid: str) -> str | None:
        """ステップを流す前に孤児を片付け、`step.pid` を起こす。前の起動が途中だったステップの id を返す。"""
        prev = reap_orphans(self.dir)
        _write_step_pid(self.dir / STEP_PID, {"step": sid, "children": []})
        watch_children(self.note_child)
        return prev

    def end_step(self) -> None:
        watch_children(None)
        (self.dir / STEP_PID).unlink(missing_ok=True)

    def note_child(self, pid: int, started: bool) -> None:
        """`step.pid` へ子（新しいセッションの先頭なので pgid = pid）と起動時刻を足すか外す。"""
        path = self.dir / STEP_PID
        data = _read_step_pid(path)
        if data is None:
            return
        data["children"] = [c for c in data.get("children") or [] if c.get("pid") != pid]
        if started:
            data["children"].append({"pgid": pid, "pid": pid, "create_time": procs.start_time(pid)})
        _write_step_pid(path, data)

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
        pace = plan.get("進め方")
        pace_first = pace in MVV_PACES and not self.pace_recorded
        self.pace_recorded = True
        for issue in plan.get("課題", []):
            if pace_first:  # 通過記録と本文の見出し行へ進め方を先に書く（まとめる工程を記録なしと数えない）
                subprocess.run(["bash", rec, str(issue), "pace", pace], cwd=cwd, capture_output=True, text=True)
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
        c = self.cur.setdefault("llm", {"calls": 0, "turns": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost": 0.0})
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
            m = models.setdefault(name, {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "cost": 0.0})
            m["calls"] += 1
            m["input"] += mu.get("inputTokens") or 0
            m["cache_read"] += mu.get("cacheReadInputTokens") or 0
            m["cache_write"] += mu.get("cacheCreationInputTokens") or 0
            m["output"] += mu.get("outputTokens") or 0
            m["cost"] = round(m["cost"] + (mu.get("costUSD") or 0.0), 4)

    def record(self, n: int, sid: str, nxt: str | None, next_type: str | None, is_gate) -> None:
        """終わったステップを残す: 結果・区切りの行・失敗の回数・出力ファイル・`state.json`。"""
        self.applied = max(self.applied, n)
        self.results[sid] = dict(self.cur)
        self.read_worker_lines()
        self.step_line(nxt)
        if (
            self.cur.get("exit") not in (0, None)
            and not is_gate(self.cur.get("exit"))
            and nxt
            and (self.cur.get("slow") or {}).get("act") != "retry"
        ):
            fails = self.fail_counts[sid] = self.fail_counts.get(sid, 0) + 1
            if fails >= 2 and next_type == "judge":
                self.attention(
                    "judge のステップで stop が出そう",
                    f"ステップ {sid} が {fails} 回落ちた（exit={self.cur.get('exit')}）。次は judge のステップ {nxt}",
                )
        self.out_path(n, sid).write_text(self.cur.get("text", ""))
        self.log.append({k: v for k, v in self.cur.items() if k != "text"})
        data = {"log": self.log, "llm": self.llm}
        if self.project_mvv is not None:
            import project_mvv

            data["project_mvv"] = project_mvv.record(self.project_mvv)
        (self.dir / "state.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))

    def snapshot(self) -> dict:
        """ステップをまたいで積む値の写し。耐久ステップの出力に入れ、落ちた後の続きで `replay` が戻す。"""
        return {
            "llm": dict(self.llm),
            "gates": [dict(g) for g in self.gates],
            "fail_counts": dict(self.fail_counts),
            "last_stage": self.last_stage,
            "pace_recorded": self.pace_recorded,
            "switched": list(self.switched),
            "slow_events": [dict(e) for e in self.slow_events],
            "pcount": dict(self.pcount),
            "attention_keys": sorted(self.attention_keys),
            "attention_log": [dict(a) for a in self.attention_log],
        }

    def replay(self, out: dict) -> bool:
        """記録のある耐久ステップ（`Engine.execute` の出力）から、ファイルを書かずに記録を組み直す。

        このプロセスで記録したステップ（番号が `applied` 以下）は組み直さない。組み直したら True。
        進捗ログの行・`.out`・`state.json` は書かないため、続けても同じ行は 2 度書かれない。"""
        if out["n"] <= self.applied:
            return False
        self.applied = out["n"]
        self.cur = dict(out["cur"])
        self.results[out["sid"]] = dict(self.cur)
        self.log.append({k: v for k, v in self.cur.items() if k != "text"})
        acc = out.get("acc") or {}
        self.llm = dict(acc.get("llm", self.llm))
        self.gates = [dict(g) for g in acc.get("gates", self.gates)]
        self.fail_counts = dict(acc.get("fail_counts", self.fail_counts))
        self.last_stage = acc.get("last_stage", self.last_stage)
        self.pace_recorded = acc.get("pace_recorded", self.pace_recorded)
        self.switched = list(acc.get("switched", self.switched))
        self.slow_events = [dict(e) for e in acc.get("slow_events", self.slow_events)]
        self.pcount = dict(acc.get("pcount", self.pcount))
        self.attention_keys = set(acc.get("attention_keys", self.attention_keys))
        self.attention_log = [dict(a) for a in acc.get("attention_log", self.attention_log)]
        return True

    def retell_gates(self, recorded: list[dict]) -> None:
        """関門の記録を返す打ち直しで、記録した関門の attention を進捗ログへ書き直す（conductor へもう一度知らせる）。"""
        for rec in recorded:
            self.progress_write({"kind": "attention", **rec})

    def _step_rows(self) -> str:
        """LLM を使ったステップの表の行。無ければ「無し」の 1 行。"""
        return (
            "\n".join(
                f"| {e['id']} | {e['llm']['turns']} | {e.get('seconds', '')} | {e['llm']['cache_read']} | "
                f"{e['llm']['cache_write']} | {e['llm']['output']} | ${e['llm']['cost']:.3f} |"
                for e in self.log
                if e.get("llm")
            )
            or "| 無し | | | | | | |"
        )

    def _extra_lines(self) -> str:
        """認証の切り替え・利用上限・遅れの行。記録があるものだけを出す。"""
        extra = ""
        if self.switched:
            extra += f"- 認証: 切り替え（{', '.join(self.switched)}）\n"
        limited = [e for e in self.log if e.get("limit")]
        if limited:
            extra += (
                "- 利用上限: "
                + "; ".join(
                    f"{e['id']} {e.get('limit_hits', 1)} 回（待ち {e.get('limit_waited', 0)} 秒"
                    + (f"・解除 {e['limit_resets']}" if e.get("limit_resets") else "")
                    + "）"
                    for e in limited
                )
                + "\n"
            )
        acted = [
            e
            for e in self.slow_events
            if e.get("act") != "wait" or e.get("by") == "llm" or (e.get("probe") or {}).get("action") == "remedied"
        ]
        if acted:
            extra += (
                "- 遅れ: "
                + "; ".join(
                    f"{e['step']} {e.get('round', 0)} 回目 {e['act']}（{e['by']}"
                    + (f"・{e['probe']['class']}" if e.get("probe") else "")
                    + "）"
                    for e in acted
                )
                + "\n"
            )
        return extra

    def write_report(self, plan: dict, result: str, reason: str) -> str:
        """`## フェーズの報告` を組み、`report.md` へ書いて返す。"""
        l = self.llm
        self.read_worker_lines()
        pc = self.pcount
        steps = " → ".join(f"{e['id']}" + (f"[{e['decision']}]" if "decision" in e else f"(exit={e.get('exit')})") for e in self.log)
        counts = {}
        for e in self.log:
            if "counts" in e:
                counts[e["id"]] = e["counts"]
        counts_line = "; ".join(f"{k}: {counts_text(v)}" for k, v in counts.items()) or "無し"
        if self.gates:
            gate_line = "; ".join(f"ステップ {g['id']}（exit={g['exit']}）" for g in self.gates)
        else:
            gate_line = "本番の系へ届く操作" if result == "関門" else "無し"
        presented = (
            ", ".join(
                [
                    *(g["presentation"] for g in self.gates if g.get("presentation")),
                    *(e["presentation"] for e in self.log if e.get("gate_as_ok") and e.get("presentation")),
                ]
            )
            or "無し"
        )
        text = f"""## フェーズの報告

- フェーズ: {plan.get("フェーズ")}
- 課題: {" ".join("#" + str(i) for i in plan.get("課題", []))}
- 結果: {result}
- 関門: {gate_line}
- 次のフェーズ: {plan.get("次のフェーズ", "無し") if result == "完了" else "無し"}
- Pull Request: {plan.get("Pull Request", "無し")}
- 最後に記録した工程: {self.last_stage}
- 使った worker: 修正 {l["work"]}（claude -p）/ 判断 {l["judge"]}（claude -p）
{self._extra_lines()}- 途中の報告: ステップ {pc["step"]} / まだ動いている {pc["alive"]} / worker {pc["worker"]}（形が違う {pc["malformed"]}）/ conductor 向け {pc["attention"]} / 遅れの調査 {pc["slow"]} / LLM へ回した {pc["llm"]} 回・${pc["llm_cost"]:.3f}（{self.progress}）
- 提示物: {presented}
- 理由: {reason}
- 通ったステップ: {steps}
- 件数: {counts_line}
- LLM の使用量: 入力 {l["input"]} / cache read {l["cache_read"]} / cache write {l["cache_write"]} / 出力 {l["output"]} / ${l["cost"]:.3f}
- 記録: {self.dir}

| ステップ | 往復 | 秒 | cache read | cache write | 出力 | 費用 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{self._step_rows()}
"""
        (self.dir / "report.md").write_text(text)
        return text
