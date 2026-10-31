"""work と drive のステップのハンドラー（#1142 の C1）。どちらも `call_worker` で worker を起動する。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gh_rest
import usage_aggregate
from loop_drive import review_answer
from supervise_lib import paths, running
from supervise_lib.claude import TAIL, WORK_TOOLS, run_ticking
from supervise_lib.prompts import FULL_SYSTEM, PROGRESS_PROMPT, REPORT_DONE, REPORT_NOT_DONE, RESUME_PROMPT, WORK_SYSTEM, WORKDIR_PROMPT
from supervise_lib.steps import RunStep, last_json
import project_mvv

# worker 1 回の上限（秒）と、駆動が pause で止まってよい回数の既定
DEFAULT_TIMEOUT = 1800
DEFAULT_MAX_PAUSES = 12
TICK_GRACE = 120  # worker 自身の上限に、外側で打ち切るまで足す余裕（秒）


def _timeout(step: dict) -> int:
    return step.get("timeout", DEFAULT_TIMEOUT)


def note_runtimes(ctx, step: dict, started: float) -> None:
    """ステップの中で起動した CLI の帳簿の行を、ランタイムごとに `cur["llm_runtimes"]` へ集める（#1953 の決定 11）。"""
    found = usage_aggregate.step_totals(getattr(ctx, "plan_path", None) or "", str(step.get("id") or ""), started)
    if found:
        ctx.state.cur["llm_runtimes"] = found


class WorkStep:
    """work のステップ: 1 つの作業を worker（最小構成の claude -p か external-ai.py run）に行わせる。"""

    kind = "work"

    def mvv_system(self, ctx) -> str:
        """worker のシステムプロンプトの末尾へ足す MVV の節（#1400 の決定 5）。プロジェクト MVV が承認済みのときだけ
        `project_mvv.block` の出力をそのまま返し（スプリントが特定できればスプリント MVV も入る）、ほかは空文字。"""
        mvv, sprint = ctx.state.mvv_block_of(ctx.cwd, ctx.plan)
        return "\n\n" + project_mvv.block(mvv, sprint) if mvv.approved else ""

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
        system = WORK_SYSTEM + self.mvv_system(ctx)
        if not rt or rt == "claude-p":
            return ctx.claude.call(system, prompt, WORK_TOOLS, cwd, _timeout(step), serena=bool(step.get("serena")))
        pf, of = ctx.state.dir / f"{name}-prompt.md", ctx.state.dir / f"{name}-output.md"
        pf.write_text(system + "\n\n" + prompt)
        started = time.time()
        cmd = [
            sys.executable,
            str(paths.EXTERNAL_AI),
            "run",
            rt,
            "--prompt-file",
            str(pf),
            "--output-file",
            str(of),
            "--phase",
            step.get("phase", "implement"),
            "--workdir",
            cwd,
            "--timeout",
            str(_timeout(step)),
        ]
        # 使用量の帳簿の呼び出し元（#1953 の決定 7）。行は external-ai.py の起動の共有処理がそのランタイムの帳簿へ書く
        env = {**os.environ, "NDF_USAGE_SOURCE": "supervise", "NDF_USAGE_KIND": "work"}
        env.update(NDF_USAGE_PLAN=getattr(ctx, "plan_path", None) or "", NDF_USAGE_STEP=str(step.get("id") or ""))
        try:
            p = run_ticking(cmd, ctx.tick, ctx.state.every, cwd=cwd, timeout=_timeout(step) + TICK_GRACE, env=env)
            out = last_json(p.stdout) or {}
        except subprocess.TimeoutExpired:
            out = {"status": "stopped", "summary": "打ち切り"}
        text = of.read_text() if of.is_file() else out.get("summary", "")
        return {
            "ok": out.get("status") == "ok",
            "text": text,
            "usage": {},
            "cost": None,
            "turns": None,
            "seconds": round(time.time() - started, 1),
            "runtime": rt,
        }

    def concurrent_rule(self, ctx, step: dict) -> str:
        """実装の指示文へ足す除外の行（#1248・#1674）。担当を起動する時点で走っている他のプランの `触るファイル`
        のうち、ディレクトリでないものを入れる。入れたものと外したディレクトリを進捗ログ（`kind: concurrent`）へ書く。"""
        if step.get("kind") != "実装":
            return ""
        root = step.get("cwd", ctx.cwd) or "."
        found = running.others(ctx.plan_path) if ctx.plan_path else []
        dirs = [f for f in found if paths.is_dir_entry(f, root)]
        files = [f for f in found if f not in dirs]
        ctx.state.progress_write({"kind": "concurrent", "step": step.get("id"), "files": files, "dirs": dirs})
        return "\n並行して別のプランが次を触る。それらは変えない: " + "、".join(files) if files else ""

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        started = time.time()
        issues = self.issue_text(ctx, step)
        cwd = step.get("cwd", ctx.cwd)
        rule = self.concurrent_rule(ctx, step)
        prompt = (
            f"作業: {step.get('kind', '修正')}\n作業場所: {cwd}\n\n"
            + (f"{issues}\n\n## 指示\n" if issues else "")
            + (step["prompt"].rstrip("\n") + rule if rule else step["prompt"])
            + f"\n\n## 入力\n{ctx.inputs_text(step)}\n\n"
            + WORKDIR_PROMPT.format(path=ctx.state.work)
            + "\n\n"
            + PROGRESS_PROMPT.format(path=ctx.state.progress.resolve())
        )
        if step.get("full"):
            # Skill の本文が手順を持つ。プロンプトは Skill の呼び出しをそのまま渡す
            prompt = step["prompt"]
        full = bool(step.get("full"))
        system = FULL_SYSTEM + self.mvv_system(ctx)
        if full:
            res = ctx.claude.call(system, prompt, WORK_TOOLS, cwd, _timeout(step), full=True)
        else:
            res = self.call_worker(ctx, step, prompt, cwd, step["id"])
        if not res.get("runtime"):  # runtime を指定した work は claude -p に数えない（帳簿から集める）
            ctx.claude.record_usage("work", res)
        # 報告が無いまま応答を終えた Skill のステップは、同じ会話を起こし直す（supervisor へ SendMessage で
        # 続けさせていたのと同じ。3 回まで）。「判断が要る / できなかった」を報告して止まった worker は、
        # 続けさせても判断が変わらないため起こし直さず、下の判定で失敗にして conductor へ戻す
        for _ in range(3):
            text = res["text"] or ""
            if not full or REPORT_DONE.search(text) or REPORT_NOT_DONE.search(text) or not res.get("session"):
                break
            res = ctx.claude.call(system, RESUME_PROMPT, WORK_TOOLS, cwd, _timeout(step), full=True, resume=res["session"])
            ctx.claude.record_usage("work", res)
        if (full and not REPORT_DONE.search(res["text"] or "")) or REPORT_NOT_DONE.search(res["text"] or ""):
            res["ok"] = False
        if res.get("runtime"):
            note_runtimes(ctx, step, started)
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

    def drive_loop(
        self, ctx, step: dict, cmd: str, depth: int = 0, inner: list[dict] | None = None, extend_file: Path | None = None
    ) -> tuple[bool, dict | None, str]:
        """駆動を打ち、pause のたびに worker へ渡して打ち直す。(成功, 最後の結果, 出力) を返す。

        入れ子の駆動（最終ゲートの item.command）の metrics は `inner` へ足す（外側の結果は内側の件数を持たない）。"""
        cwd = step.get("cwd", ctx.cwd)
        texts = []
        max_pauses = step.get("max_pauses", DEFAULT_MAX_PAUSES)
        for _ in range(max_pauses + 1):
            code, text = self.run.run_cmd(ctx, {**step, "cmd": cmd}, extend_file)
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
                ok, sub = self._drive_nested(ctx, step, item, res_file, depth, inner, texts, extend_file)
                if not ok:
                    return False, sub, "\n".join(texts) + f"\n{kind} の駆動が止まった"
                continue
            err = self._delegate_pause(ctx, step, item, kind, res_file, cwd, texts)
            if err:
                return False, out, "\n".join(texts) + f"\n{err}"
        return False, None, "\n".join(texts) + f"\npause が上限 {max_pauses} を超えた"

    def _drive_nested(
        self,
        ctx,
        step: dict,
        item: dict,
        res_file: Path,
        depth: int,
        inner: list[dict] | None,
        texts: list[str],
        extend_file: Path | None = None,
    ) -> tuple[bool, dict | None]:
        """item.command の駆動を回し、通れば件数から答えを結果ファイルへ書く。(成功, 内側の最後の結果) を返す。"""
        ok, sub, sub_text = self.drive_loop(ctx, step, item["command"], depth + 1, inner, extend_file)
        if inner is not None and sub and isinstance(sub.get("metrics"), dict):
            inner.append(sub["metrics"])
        texts.append(sub_text)
        if ok:
            res_file.write_text(json.dumps(review_answer(sub.get("metrics"))))
        return ok, sub

    def _delegate_pause(self, ctx, step: dict, item: dict, kind: str, res_file: Path, cwd: str, texts: list[str]) -> str | None:
        """pause を worker へ渡す。失敗したら理由を返す。"""
        pf = item.get("prompt_file")
        if not pf or not Path(pf).is_file():
            return f"{kind} の prompt_file が無い"
        wd = str(item.get("cwd") or cwd)  # 駆動が作業ディレクトリを示せば、それが worker の作業場所
        prompt = f"作業: {kind}\n作業場所: {wd}\n\n{Path(pf).read_text()}\n\n終えたら結果ファイル {res_file} を書く。"
        res = self.work.call_worker(ctx, step, prompt, wd, f"{step['id']}-{kind}-{len(ctx.state.cur['pauses'])}")
        if not res.get("runtime"):  # runtime の worker の分はステップの終わりに帳簿から集める
            ctx.claude.record_usage("work", res)
        texts.append(f"## {kind} の worker\n{res['text'][-TAIL:]}")
        if not res_file.is_file():
            return f"{kind} の worker が結果ファイルを書かなかった"
        return None

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
        # ステップの延長の置き場（#2047 の決定 4）。試行ごとに分け、前の試行のファイルは読まない
        extend_file = Path(ctx.state.dir) / f"{len(getattr(ctx.state, 'log', []) or []) + 1:02d}-{step['id']}.extend.json"
        extend_file.unlink(missing_ok=True)
        ok, out, text = self.drive_loop(ctx, step, cmd, inner=inner, extend_file=extend_file)
        note_runtimes(ctx, step, started)
        if out or inner:
            ctx.state.cur["counts"] = dict((out or {}).get("metrics") or {})
            if inner:
                ctx.state.cur["counts"]["inner"] = inner[-1]
        # 欠けの残る終わり（stopped と gaps。収束しなかったなど）は終了コード 6 として控え、on_exit で分けられるようにする
        code = 0 if ok else (6 if out and out.get("status") == "stopped" and out.get("gaps") else 1)
        if code == 6:
            ctx.state.cur["gaps"] = list(out.get("gaps") or [])  # フェーズレポートの「欠けを残して進んだステップ」（#2047 の決定 5）
        ctx.state.cur.update(exit=code, text=text, seconds=round(time.time() - started, 1))
        return ok, text
