"""ステップのハンドラーの型と、run・judge のハンドラー（#1142 の C1）。

ハンドラーはステップの型と 1 対 1 で、`execute(ctx, step)` を持つ。`ctx`（`StepContext`）は `Engine` が作り、
プラン・ステップ・作業場所・実行の状態（`state`）・claude の呼び出し（`claude`）・遅れの見張り（`slow`）・
待ちの間に呼ぶ `tick` を渡す。ハンドラーは `engine` を import しない。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from typing import Any, Protocol

import gh_call
from supervise_lib import decl, paths, plan as plan_mod
from supervise_lib.claude import TAIL, run_ticking
from supervise_lib.prompts import JUDGE_SYSTEM
from supervise_lib.slow import SLOW_EXIT


def is_gate(code: int | None) -> bool:
    """run のステップの終了コード 10〜19 は共通の契約の関門（lib/step_result.py の EXIT_GATE）。"""
    return code is not None and 10 <= code <= 19


def parse_decision(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"decision": "stop", "reason": f"判断の答えを読めない: {(text or '')[:200]}"}


def last_json(text: str) -> dict | None:
    """出力の最後の JSON の行（step_result の形）を読む。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


class StepContext:
    """ハンドラーと遅れの見張りへ渡す、1 本のプランの実行の文脈。`cwd` は前のステップが作業場所を消すと
    元のリポジトリへ移る（`Engine.keep_cwd`）。`tick`・`slow`・`claude` は `Engine` が作った後に入れる。"""

    def __init__(self, plan: dict, steps: dict[str, dict], state, plan_path: str = "") -> None:
        self.plan, self.steps, self.state, self.plan_path = plan, steps, state, plan_path
        self.cwd = plan["作業場所"]
        self.tick = lambda: None
        self.slow: Any = None
        self.claude: Any = None

    def fill_pr(self, cmd: str) -> tuple[str, str | None]:
        """cmd / args の {pr} を Pull Request の番号に、{pr_url} を URL に置き換える。(cmd, 誤りの文) を返す。"""
        if "{pr}" not in cmd and "{pr_url}" not in cmd:
            return cmd, None
        value = str(self.plan.get("Pull Request") or "")
        number = plan_mod.pr_number(value)
        if not number:
            return cmd, "cmd の {pr} を置き換える Pull Request がまだ無い"
        if "{pr_url}" in cmd:
            url = value if "/pull/" in value else gh_call.gh(
                ["pr", "view", number, "--json", "url", "--jq", ".url"], cwd=self.cwd).stdout.strip()
            if not url:
                return cmd, f"cmd の {{pr_url}} を置き換える Pull Request #{number} の URL を読めない"
            cmd = cmd.replace("{pr_url}", url)
        return cmd.replace("{pr}", number), None

    def base_branch(self) -> str | None:
        """起点のブランチ。計画の base_branch、無ければ作業場所の .ndf/worktree.json の base_branch。"""
        return self.plan.get("base_branch") or decl.declared_base_of([self.cwd])

    def inputs_text(self, step: dict) -> str:
        parts = []
        for sid in step.get("inputs", []):
            r = self.state.results.get(sid)
            if r:
                parts.append(f"### ステップ {sid}（exit={r.get('exit')}）\n{r['text'][-TAIL:]}")
        return "\n\n".join(parts) or "（入力なし）"


class StepHandler(Protocol):
    """ステップの型ごとのハンドラー。`kind` はステップの `type`。"""

    kind: str

    def execute(self, ctx: StepContext, step: dict) -> Any: ...


class RunStep:
    """run のステップ: コマンドを実行して終わるまで待つ。LLM を使わない。"""

    kind = "run"

    @staticmethod
    def is_skip(step: dict, code: int | None) -> bool:
        return bool(step.get("skip_to")) and code == step.get("skip_code", 3)

    def no_reports(self, ctx) -> str:
        """テストに成果物を作らせない PYTEST_ADDOPTS。計画の no_reports、無ければ .ndf/supervise.json の
        test.no_reports。どちらも無ければ足さない。"""
        if "no_reports" in ctx.plan:
            return str(ctx.plan["no_reports"] or "")
        try:
            test = decl.read_decl([ctx.cwd], decl.SUPERVISE_DECL).get("test") or {}
        except decl.DeclError:
            return ""
        return str(test.get("no_reports") or "") if isinstance(test, dict) else ""

    def run_cmd(self, ctx, step: dict, extra_addopts: str = "") -> tuple[int, str]:
        cmd = step.get("cmd") or paths.PRESETS.get(step.get("preset", ""), "")
        if not cmd:
            return 2, f"ステップ {step['id']} に cmd も知っている preset も無い"
        cmd, err = ctx.fill_pr(cmd)
        if err:
            return 2, err
        if "{base}" in cmd:
            base = ctx.base_branch()
            if not base:
                return 2, "cmd の {base} を置き換える起点のブランチが無い（計画か .ndf/worktree.json の base_branch）"
            cmd = cmd.replace("{base}", base)
        cmd = cmd.replace("{state_dir}", str(ctx.state.dir))
        env = dict(os.environ)
        # 親の run のステップから受け継いだ no_reports は、reports: true なら外す
        no_reports = self.no_reports(ctx)
        inherited = env.get("PYTEST_ADDOPTS", "")
        addopts = [(inherited.replace(no_reports, "") if no_reports else inherited).strip()]
        if no_reports and not step.get("reports"):
            addopts.append(no_reports)
        addopts.append(extra_addopts)
        env["PYTEST_ADDOPTS"] = " ".join(a for a in addopts if a)
        ctx.state.run_log = ctx.state.dir / "run-stderr.log"
        try:
            p = run_ticking(cmd, ctx.tick, ctx.state.every, shell=True, cwd=step.get("cwd", ctx.cwd),
                            timeout=step.get("timeout", 3600), env=env, err_path=ctx.state.run_log)
            return p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            return 124, f"打ち切り（{e.timeout} 秒）"
        finally:
            ctx.state.run_log = None

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        started = time.time()
        code, text = self.run_cmd(ctx, step)
        if (code not in (0, 124, SLOW_EXIT) and not is_gate(code) and step.get("rerun_failed")
                and not self.is_skip(step, code)):
            # 落ちたテストだけを走らせ直す。通れば揺れとして成功にする
            code2, text2 = self.run_cmd(ctx, step, "--lf")
            ctx.state.cur["rerun"] = {"exit": code2}
            text = f"{text}\n\n## 落ちたテストだけの再実行（exit={code2}）\n{text2}"
            if code2 == 0:
                code, text = 0, text + "\n再実行で通った（揺れとして進む）"
        ctx.state.cur.update(exit=code, text=text, seconds=round(time.time() - started, 1))
        return code == 0, text


class JudgeStep:
    """judge のステップ: 最小構成の claude -p に次の手を選ばせる。返り値は判断の辞書。"""

    kind = "judge"

    def execute(self, ctx, step: dict) -> dict:
        choices = step.get("choices")
        prompt = (f"フェーズ: {ctx.plan.get('フェーズ')} / 課題: {ctx.plan.get('課題')}\n"
                  f"問い: {step['question']}\n"
                  + (f"選べる値: {', '.join(choices)}（関門なら gate、止めるなら stop）\n" if choices else "")
                  + f"作業ディレクトリ: {ctx.state.work}（worker の作業ファイルの置き場所）\n"
                  + f"\n## 規則\n{ctx.plan.get('規則', '（無し）')}\n\n## 結果\n{ctx.inputs_text(step)}")
        res = ctx.claude.call(JUDGE_SYSTEM, prompt, None, ctx.cwd, step.get("timeout", 600))
        ctx.claude.record_usage("judge", res)
        d = parse_decision(res["text"]) if res["ok"] else {"decision": "stop", "reason": res["text"][:200]}
        ctx.state.cur.update(exit=0, text=json.dumps(d, ensure_ascii=False), seconds=res["seconds"])
        return d
