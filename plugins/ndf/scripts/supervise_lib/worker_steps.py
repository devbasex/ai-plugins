"""work と drive のステップのハンドラー（#1142 の C1）。どちらも `call_worker` で worker を起動する。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import gh_rest
from supervise_lib import paths
from supervise_lib.claude import TAIL, WORK_TOOLS, run_ticking
from supervise_lib.prompts import FULL_SYSTEM, PROGRESS_PROMPT, REPORT_DONE, REPORT_NOT_DONE, RESUME_PROMPT, WORK_SYSTEM, WORKDIR_PROMPT
from supervise_lib.steps import RunStep, last_json


class WorkStep:
    """work のステップ: 1 つの作業を worker（最小構成の claude -p か external-ai.py run）に行わせる。"""

    kind = "work"

    def issue_text(self, ctx, step: dict) -> str:
        nums = step.get("issues")
        if nums is True:
            nums = ctx.plan.get("課題", [])
        parts = []
        for n in nums or []:
            p = gh_rest.view_json("issue", n, "title,body", cwd=ctx.cwd)
            try:
                d = json.loads(p.stdout)
                parts.append(f"## 課題 #{n}: {d.get('title', '')}\n\n{d.get('body', '')}")
            except json.JSONDecodeError:
                parts.append(f"## 課題 #{n}\n\n（本文を取れない。gh issue view {n} で読む）")
        return "\n\n".join(parts)

    def call_worker(self, ctx, step: dict, prompt: str, cwd: str, name: str) -> dict:
        """worker を 1 回起動する。`runtime` があれば external-ai.py run、無ければ最小構成の claude -p。"""
        rt = step.get("runtime")
        if not rt or rt == "claude-p":
            return ctx.claude.call(WORK_SYSTEM, prompt, WORK_TOOLS, cwd, step.get("timeout", 1800),
                                   serena=bool(step.get("serena")))
        pf, of = ctx.state.dir / f"{name}-prompt.md", ctx.state.dir / f"{name}-output.md"
        pf.write_text(WORK_SYSTEM + "\n\n" + prompt)
        started = time.time()
        cmd = [sys.executable, str(paths.EXTERNAL_AI), "run", rt, "--prompt-file", str(pf), "--output-file", str(of),
               "--phase", step.get("phase", "implement"), "--workdir", cwd,
               "--timeout", str(step.get("timeout", 1800))]
        try:
            p = run_ticking(cmd, ctx.tick, ctx.state.every, cwd=cwd, timeout=step.get("timeout", 1800) + 120)
            out = last_json(p.stdout) or {}
        except subprocess.TimeoutExpired:
            out = {"status": "stopped", "summary": "打ち切り"}
        text = of.read_text() if of.is_file() else out.get("summary", "")
        return {"ok": out.get("status") == "ok", "text": text, "usage": {}, "cost": None, "turns": None,
                "seconds": round(time.time() - started, 1), "runtime": rt}

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        issues = self.issue_text(ctx, step)
        cwd = step.get("cwd", ctx.cwd)
        prompt = (f"作業: {step.get('kind', '修正')}\n作業場所: {cwd}\n\n"
                  + (f"{issues}\n\n## 指示\n" if issues else "")
                  + f"{step['prompt']}\n\n## 入力\n{ctx.inputs_text(step)}\n\n"
                  + WORKDIR_PROMPT.format(path=ctx.state.work) + "\n\n"
                  + PROGRESS_PROMPT.format(path=ctx.state.progress.resolve()))
        if step.get("full"):
            # Skill の本文が手順を持つ。プロンプトは Skill の呼び出しをそのまま渡す
            prompt = step["prompt"]
        full = bool(step.get("full"))
        if full:
            res = ctx.claude.call(FULL_SYSTEM, prompt, WORK_TOOLS, cwd, step.get("timeout", 1800), full=True)
        else:
            res = self.call_worker(ctx, step, prompt, cwd, step["id"])
        ctx.claude.record_usage("work", res)
        # 報告が無いまま応答を終えた Skill のステップは、同じ会話を起こし直す（supervisor へ SendMessage で
        # 続けさせていたのと同じ。3 回まで）
        for _ in range(3):
            if not full or REPORT_DONE.search(res["text"] or "") or not res.get("session"):
                break
            res = ctx.claude.call(FULL_SYSTEM, RESUME_PROMPT, WORK_TOOLS, cwd, step.get("timeout", 1800),
                                  full=True, resume=res["session"])
            ctx.claude.record_usage("work", res)
        if (full and not REPORT_DONE.search(res["text"] or "")) or REPORT_NOT_DONE.search(res["text"] or ""):
            res["ok"] = False
        ctx.state.cur.update(exit=0 if res["ok"] else 1, text=res["text"], seconds=res["seconds"])
        return res["ok"], res["text"]


class DriveStep:
    """drive のステップ: 駆動（cross-review / cross-refactoring の drive.py）を run として回し、pause のときだけ
    worker に判断・修正をさせて駆動へ返す。"""

    kind = "drive"

    def __init__(self) -> None:
        self.run, self.work = RunStep(), WorkStep()

    def drive_cmd(self, step: dict) -> str:
        if step.get("cmd"):
            return step["cmd"]
        script = paths.DRIVES.get(step.get("drive", ""))
        return f"python3 {script} {step.get('args', '')}".strip() if script else ""

    def drive_loop(self, ctx, step: dict, cmd: str, depth: int = 0,
                   inner: list[dict] | None = None) -> tuple[bool, dict | None, str]:
        """駆動を打ち、pause のたびに worker へ渡して打ち直す。(成功, 最後の結果, 出力) を返す。

        入れ子の駆動（最終ゲートの item.command）の metrics は `inner` へ足す（外側の結果は内側の件数を持たない）。"""
        cwd = step.get("cwd", ctx.cwd)
        texts = []
        for _ in range(step.get("max_pauses", 12) + 1):
            code, text = self.run.run_cmd(ctx, {**step, "cmd": cmd})
            texts.append(text)
            out = last_json(text)
            if out is None:
                return False, None, "\n".join(texts) + f"\n駆動の結果 JSON を読めない（exit={code}）"
            if out.get("status") == "ok":
                return True, out, "\n".join(texts)
            item = (out.get("items") or [{}])[0]
            if out.get("status") != "gate" or not item.get("result_file"):
                return False, out, "\n".join(texts)
            kind = item.get("pause", out.get("next", "pause"))
            ctx.state.cur.setdefault("pauses", []).append(kind)
            res_file = Path(item["result_file"])
            if item.get("command") and depth == 0:
                ok, sub, sub_text = self.drive_loop(ctx, step, item["command"], depth + 1, inner)
                if inner is not None and sub and isinstance(sub.get("metrics"), dict):
                    inner.append(sub["metrics"])
                texts.append(sub_text)
                if not ok:
                    return False, sub, "\n".join(texts) + f"\n{kind} の駆動が止まった"
                res_file.write_text(json.dumps({"review_status": (sub.get("metrics") or {}).get("review_status")
                                                or "unknown"}))
                continue
            pf = item.get("prompt_file")
            if not pf or not Path(pf).is_file():
                return False, out, "\n".join(texts) + f"\n{kind} の prompt_file が無い"
            wd = str(item.get("cwd") or cwd)  # 駆動が作業ディレクトリを示せば、それが worker の作業場所
            prompt = (f"作業: {kind}\n作業場所: {wd}\n\n{Path(pf).read_text()}\n\n"
                      f"終えたら結果ファイル {res_file} を書く。")
            res = self.work.call_worker(ctx, step, prompt, wd, f"{step['id']}-{kind}-{len(ctx.state.cur['pauses'])}")
            ctx.claude.record_usage("work", res)
            texts.append(f"## {kind} の worker\n{res['text'][-TAIL:]}")
            if not res_file.is_file():
                return False, out, "\n".join(texts) + f"\n{kind} の worker が結果ファイルを書かなかった"
        return False, None, "\n".join(texts) + f"\npause が上限 {step.get('max_pauses', 12)} を超えた"

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        started = time.time()
        cmd = self.drive_cmd(step)
        if not cmd:
            ctx.state.cur.update(exit=2, text=f"ステップ {step['id']} に cmd も知っている drive も無い")
            return False, ctx.state.cur["text"]
        cmd, err = ctx.fill_pr(cmd)
        if err:
            ctx.state.cur.update(exit=2, text=err)
            return False, err
        # 入れ子の駆動（最終ゲートの item.command）の metrics を控える。外側の結果は内側の件数を持たない
        inner: list[dict] = []
        ok, out, text = self.drive_loop(ctx, step, cmd, inner=inner)
        if out or inner:
            ctx.state.cur["counts"] = dict((out or {}).get("metrics") or {})
            if inner:
                ctx.state.cur["counts"]["inner"] = inner[-1]
        ctx.state.cur.update(exit=0 if ok else 1, text=text, seconds=round(time.time() - started, 1))
        return ok, text
