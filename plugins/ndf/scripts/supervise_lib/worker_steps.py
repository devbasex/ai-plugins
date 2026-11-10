"""work と drive のステップのハンドラー（#1142 の C1）。どちらも `call_worker` で worker を起動する。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import clock
import gh_rest
import slow_step
import step_read
from step_result import EXIT_INCOMPLETE
import usage_aggregate
from loop_drive import review_answer
from supervise_lib import paths, running
from supervise_lib.claude import TAIL, WORK_TOOLS, run_ticking
from supervise_lib.progress_watch import ProgressWatch
import limits
from supervise_lib.prompts import FULL_SYSTEM, PROGRESS_PROMPT, RESULT_PROMPT, RESUME_PROMPT, WORK_SYSTEM, WORKDIR_PROMPT
from supervise_lib.steps import RunStep, deliver_skills, is_gate, last_json
import project_mvv

# worker 1 回の上限（秒）と、駆動が pause で止まってよい回数の既定
DEFAULT_TIMEOUT = limits.seconds("supervise.worker-step")
DEFAULT_MAX_PAUSES = 12
TICK_GRACE = limits.seconds("supervise.worker-tick-grace")  # worker 自身の上限に、外側で打ち切るまで足す余裕（秒）
RESUMES = 3  # 作業の結果ファイルが無いまま応答を終えた full のステップを起こし直す回数
STEP_RESULT_PY = paths.HERE / "lib" / "step_result.py"


def result_file_of(ctx, step: dict) -> Path:
    """作業の結果ファイルの置き場（プランの状態ディレクトリの `<ステップの id>-result.json`。#2337 の決定 7）。"""
    # worker の作業場所は別の worktree のことがあるため、supervise の起動ディレクトリで絶対パスにして渡す
    return (Path(ctx.state.dir) / f"{step['id']}-result.json").resolve()


def result_prompt(path: Path) -> str:
    return RESULT_PROMPT.format(path=path, step_result=STEP_RESULT_PY)


def _timeout(step: dict) -> int:
    return step.get("timeout", DEFAULT_TIMEOUT)


def _elapsed_limit(step: dict) -> int | None:
    """worker の経過時間の上限（秒）。無進捗で打ち切るステップ（`stall`）は書いたときだけ持つ。"""
    return step.get("timeout") if step.get("stall") else _timeout(step)


def note_stalled(ctx, step: dict, stall: int, last_at: float, last_signal: str) -> None:
    """無進捗の打ち切りを進捗ログの `stalled` の行とステップの結果の `stalled` に残す（時間切れと分ける）。"""
    ctx.state.cur["stalled"] = {"seconds": round(time.time() - last_at, 1), "last_signal": last_signal}
    at = clock.iso(datetime.fromtimestamp(last_at).astimezone())
    ctx.state.progress_write({"kind": "stalled", "step": step.get("id"), "stall": stall, "last_at": at, "last_signal": last_signal})


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

    def call_worker(self, ctx, step: dict, prompt: str, cwd: str, name: str, block: str = "") -> dict:
        """worker を 1 回起動する。`runtime` があれば external-ai.py run、無ければ最小構成の claude -p。

        `block` は `deliver_skills` が返した Skill の本文の節で、種類の文と MVV の節の間へ入れる。"""
        rt = step.get("runtime")
        system = WORK_SYSTEM + block + self.mvv_system(ctx)
        if not rt or rt == "claude-p":
            return self.call_claude_worker(ctx, step, system, prompt, cwd)
        pf, of = ctx.state.dir / f"{name}-prompt.md", ctx.state.dir / f"{name}-output.md"
        pf.write_text(system + "\n\n" + prompt)
        started = time.time()
        limit = _elapsed_limit(step)
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
            str(limit or 0),  # 0 は経過時間の上限が無い
            *(["--stall-timeout", str(step["stall"])] if step.get("stall") else []),
        ]
        # 使用量の帳簿の呼び出し元（#1953 の決定 7）。行は external-ai.py の起動の共有処理がそのランタイムの帳簿へ書く
        env = {**os.environ, "NDF_USAGE_SOURCE": "supervise", "NDF_USAGE_KIND": "work"}
        env.update(NDF_USAGE_PLAN=getattr(ctx, "plan_path", None) or "", NDF_USAGE_STEP=str(step.get("id") or ""))
        try:
            p = run_ticking(cmd, ctx.tick, ctx.state.every, cwd=cwd, timeout=limit + TICK_GRACE if limit else None, env=env)
            out = step_read.read_completed(p, tool="external-ai")
        except subprocess.TimeoutExpired:
            out = {"status": "stopped", "summary": "打ち切り"}
        if step.get("stall") and ((out.get("items") or [{}])[0]).get("result") == "stalled":
            note_stalled(ctx, step, step["stall"], time.time() - step["stall"], "monitor_log")
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

    def call_claude_worker(self, ctx, step: dict, system: str, prompt: str, cwd: str):
        """最小構成の claude -p を起動する。`stall` のステップは会話を残し、進みの観測が締め切りを動かす。"""
        serena = bool(step.get("serena"))
        if not step.get("stall"):
            return ctx.claude.call(system, prompt, WORK_TOOLS, cwd, _timeout(step), serena=serena)
        st = ctx.state
        watch = ProgressWatch(
            step["stall"], st.session, lambda: st.pcount["worker"], lambda: slow_step.commit_count(cwd), step.get("timeout")
        )
        res = ctx.claude.call(system, prompt, WORK_TOOLS, cwd, watch.first_timeout(), serena=serena, watch=watch)
        if res.get("timed_out") and watch.stalled():
            note_stalled(ctx, step, watch.allowance, watch.last_at, watch.last_signal)
            res["text"] = f"無進捗で打ち切り（最後の進み: {watch.last_signal}。許容 {watch.allowance} 秒）"
        return res

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
        # 届ける Skill は最初の起動の前に 1 回だけ決める。起こし直し（resume）は同じ節を持つ system を渡す
        block, err = deliver_skills(ctx, step)
        if err:
            ctx.state.cur.update(exit=1, text=err, seconds=round(time.time() - started, 1))
            return False, err
        issues = self.issue_text(ctx, step)
        cwd = step.get("cwd", ctx.cwd)
        rule = self.concurrent_rule(ctx, step)
        # 作業の結果ファイルは前の試行の分を消してから起動する（前の試行の ok を読まない）
        rfile = result_file_of(ctx, step)
        rfile.unlink(missing_ok=True)
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
        prompt += "\n\n" + result_prompt(rfile)
        full = bool(step.get("full"))
        system = FULL_SYSTEM + block + self.mvv_system(ctx)
        if full:
            res = ctx.claude.call(system, prompt, WORK_TOOLS, cwd, _timeout(step), full=True)
        else:
            res = self.call_worker(ctx, step, prompt, cwd, step["id"], block)
        if not res.get("runtime"):  # runtime を指定した work は claude -p に数えない（帳簿から集める）
            ctx.claude.record_usage("work", res)
        # 作業の結果ファイルが無いまま応答を終えた Skill のステップは、同じ会話を起こし直す（3 回まで）。
        # ファイルを書いた worker は gate・stopped でも起こし直さない（続けさせても判断は変わらない）
        for _ in range(RESUMES):
            if not full or rfile.exists() or not res.get("session"):
                break
            res = ctx.claude.call(system, RESUME_PROMPT, WORK_TOOLS, cwd, _timeout(step), full=True, resume=res["session"])
            ctx.claude.record_usage("work", res)
        out = step_read.read_result_file(rfile, tool="worker")
        ctx.state.cur["work_result"] = {k: out[k] for k in ("status", "summary", "gaps", "next") if k in out}
        ok = bool(res["ok"]) and step_read.is_ok(out)
        text = res["text"] or ""
        if not step_read.is_ok(out):
            reasons = "; ".join(g["reason"] for g in out.get("gaps", []))
            text += f"\n作業の結果: {out['status']}: {out['summary']}" + (f"（{reasons}）" if reasons else "")
        if res.get("runtime"):
            note_runtimes(ctx, step, started)
        ctx.state.cur.update(exit=0 if ok else 1, text=text, seconds=res["seconds"])
        return ok, text


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
        block, err = deliver_skills(ctx, step)  # pause の worker の起動ごとに決めて記録する（#2376 の I7）
        if err:
            return err
        res = self.work.call_worker(ctx, step, prompt, wd, f"{step['id']}-{kind}-{len(ctx.state.cur['pauses'])}", block)
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
        code = 0 if ok else (EXIT_INCOMPLETE if out and out.get("status") == "stopped" and out.get("gaps") else 1)
        if not ok and out and out.get("status") == "gate" and is_gate((out.get("metrics") or {}).get("exit")):
            # 人へ渡す止まり（cross-refactoring の安全網など。#2457 決定 8）は承認ゲートの終了コードのまま控える
            code = int(out["metrics"]["exit"])
        if code == EXIT_INCOMPLETE:
            ctx.state.cur["gaps"] = list(out.get("gaps") or [])  # フェーズレポートの「欠けを残して進んだステップ」（#2047 の決定 5）
        ctx.state.cur.update(exit=code, text=text, seconds=round(time.time() - started, 1))
        return ok, text
