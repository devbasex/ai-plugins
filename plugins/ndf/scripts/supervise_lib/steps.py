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
from pathlib import Path
from typing import Any, Protocol

import gh_call
import stage_skills
import test_strategy as ts
from supervise_lib import decl, paths, plan as plan_mod
from supervise_lib.claude import TAIL, run_ticking
from supervise_lib.prompts import JUDGE_SYSTEM
import project_mvv


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


SKILLS_FORM = "本文"  # プランの worker への Skill の渡し方（本文を system プロンプトへ入れる。#2376 の決定 4）


def deliver_skills(ctx, step: dict) -> tuple[str, str | None]:
    """worker を起動する前に、ステップへ届ける Skill を決めて進捗ログへ 1 行書く（#2376 の I6・I7）。

    (本文の節, 誤り) を返す。節は system プロンプトの種類の文の後ろへ足す形（0 件なら空文字）。対応表か SKILL.md を
    読めなければ、行に `error` を付けて書き、誤りを返す（呼び手は worker を起動せずにステップを失敗にする）。"""
    rec = {"kind": "skills", "step": step.get("id"), "stage": step.get("stage"), "names": [], "form": SKILLS_FORM}
    try:
        names = stage_skills.resolve(step, stage_skills.load_table())
        section = stage_skills.render(names)
    except stage_skills.SkillsError as e:
        ctx.state.progress_write({**rec, "error": e.what})
        return "", f"届ける Skill を読めない: {e.what}"
    ctx.state.progress_write({**rec, "names": names})
    return ("\n\n" + section if section else ""), None


# ---- 計画の実行の条件（engine の実行の前と、queue の版の差し込みの前で共有する） ----

COND_RUN, COND_SKIP, COND_UNKNOWN = "run", "skip", "unknown"


def condition_cwd(plan: dict) -> str | None:
    """条件を打つ場所: プランの `リポジトリ`、無ければ作業場所（まだ無ければその親のリポジトリ）。"""
    wt = Path(plan.get("作業場所") or "")
    cwd = plan.get("リポジトリ") or (
        str(wt) if str(wt) and wt.is_dir() else str(wt).split("/.worktrees/")[0] if "/.worktrees/" in str(wt) else None
    )
    return cwd if cwd and Path(cwd).is_dir() else None


def run_condition(cond: dict, plan: dict, state_dir: str | Path) -> tuple[str, int, str, float]:
    """条件を打ち、(COND_RUN / COND_SKIP / COND_UNKNOWN, 終了コード, 要約, 秒) を返す。"""
    cmd = str(cond.get("cmd") or "").replace("{state_dir}", str(state_dir))
    started = time.time()
    try:
        p = subprocess.run(cmd, shell=True, cwd=condition_cwd(plan), capture_output=True, text=True, timeout=cond.get("timeout", 900))
        code, text = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        code, text = 124, f"打ち切り（{e.timeout} 秒）"
    out = last_json(text) or {}
    summary = str(out.get("summary") or text.strip()[-200:] or f"exit={code}")
    verdict = COND_RUN if code == 0 else COND_SKIP if code == cond.get("skip_code", 3) else COND_UNKNOWN
    return verdict, code, summary, round(time.time() - started, 1)


def prior_results(state) -> dict[str, dict]:
    """前の実行の結果（`--from` の再開で inputs が読む。#1676 の I7・I8）。前の実行の範囲（`RunState.prior_rows`）の
    `step` の行のうち、ステップごとに最後の行の `exit` と、出力ファイル（`out`）の本文を返す。`out` の無い古い行は `summary`。"""
    found: dict[str, dict] = {}
    for r in state.prior_rows():
        if r.get("kind") != "step" or not r.get("step"):
            continue
        out = Path(state.dir) / str(r.get("out") or "")
        try:
            text = out.read_text(encoding="utf-8", errors="replace") if r.get("out") else str(r.get("summary") or "")
        except OSError:
            text = str(r.get("summary") or "")
        found[str(r["step"])] = {"exit": r.get("exit"), "text": text}
    return found


TEST_LIMIT = re.compile(r"\{test_limit:(\w+):(\d+)\}")


def fill_test_limit(ctx, step: dict, cmd: str) -> tuple[str, Any]:
    """テストのステップの `{test_limit:<鍵>:<秒>}` を、開始の時点の上限に置き換える（#1631 の決定 3）。(cmd, timeout) を返す。

    上限 = max(プランの作成の時点の秒, 所要の記録と直近の全体テストの実測から決めた秒)。広がったら `timeout` を
    `with_margin(上限)` にし（ほかの余裕は足さない）、同じ秒を `test-run.py` の `--limit` へ渡す。決め直せない
    （プランの「テストの時間」の `basis` が読めない）ときは作成の時点の秒で流す。決めた値は `cur.test_limit` に残す。"""
    from supervise_lib.verify_steps import with_margin

    timeout = step.get("timeout", 3600)
    m = TEST_LIMIT.search(cmd)
    if not m:
        return cmd, timeout
    key, planned = m.group(1), int(m.group(2))
    limit, basis = planned, None
    b = (ctx.plan.get("テストの時間") or {}).get("basis") if isinstance(ctx.plan.get("テストの時間"), dict) else None
    if isinstance(b, dict) and b.get("key") and b.get("budget_minutes") is None:
        try:
            got = ts.unbudgeted_limits(ts.Strategy(str(b.get("strategy")), "plan"), b, step.get("cwd", ctx.cwd))
            limit, basis = max(planned, int(got[key])), got["basis"]
        except (OSError, ValueError, KeyError, TypeError):
            limit, basis = planned, None
    if limit > planned:
        timeout = max(int(timeout), with_margin(limit))
    cur = getattr(ctx.state, "cur", None)
    if isinstance(cur, dict):
        cur["test_limit"] = {"key": key, "planned": planned, "seconds": limit, "timeout": timeout, "basis": basis}
    return TEST_LIMIT.sub(str(limit), cmd), timeout


class StepContext:
    """ハンドラーと遅れの見張りへ渡す、1 本のプランの実行の文脈。`cwd` は前のステップが作業場所を消すと
    元のリポジトリへ移る（`Engine.keep_cwd`）。`tick`・`slow`・`claude` は `Engine` が作った後に入れる。"""

    def __init__(self, plan: dict, steps: dict[str, dict], state, plan_path: str = "") -> None:
        self.plan, self.steps, self.state, self.plan_path = plan, steps, state, plan_path
        self.cwd = plan["作業場所"]
        self.tick = lambda: None
        self.slow: Any = None
        self.claude: Any = None
        self.resumed = False  # `--from` で再開した実行か（Engine.setup が入れる）

    def fill_pr(self, cmd: str) -> tuple[str, str | None]:
        """cmd / args の {pr} を Pull Request の番号に、{pr_url} を URL に置き換える。(cmd, 誤りの文) を返す。"""
        if "{pr}" not in cmd and "{pr_url}" not in cmd:
            return cmd, None
        value = str(self.plan.get("Pull Request") or "")
        number = plan_mod.pr_number(value)
        if not number:
            where = Path(self.state.dir).resolve()  # 再開で読んだ状態の置き場（原因を後から追う）
            return cmd, f"cmd の {{pr}} を置き換える Pull Request がまだ無い（状態の置き場: {where}）"
        if "{pr_url}" in cmd:
            url = (
                value
                if "/pull/" in value
                else gh_call.gh(["pr", "view", number, "--json", "url", "--jq", ".url"], cwd=self.cwd).stdout.strip()
            )
            if not url:
                return cmd, f"cmd の {{pr_url}} を置き換える Pull Request #{number} の URL を読めない"
            cmd = cmd.replace("{pr_url}", url)
        return cmd.replace("{pr}", number), None

    def base_branch(self) -> str | None:
        """起点のブランチ。計画の base_branch、無ければ作業場所の .ndf/worktree.json の base_branch。"""
        return self.plan.get("base_branch") or decl.declared_base_of([self.cwd])

    def inputs_text(self, step: dict) -> str:
        """judge と work の入力。今の実行の結果 → 前の実行の最後の結果（`--from` の再開のとき）→ 無い（名前と理由）の順（#1676）。"""
        parts, prior = [], None
        for sid in step.get("inputs", []):
            r = self.state.results.get(sid)
            if r:
                parts.append(f"### ステップ {sid}（exit={r.get('exit')}）\n{r['text'][-TAIL:]}")
                continue
            if prior is None:
                prior = prior_results(self.state) if self.resumed else {}
            if sid in prior:
                parts.append(f"### ステップ {sid}（exit={prior[sid]['exit']}、前の実行）\n{prior[sid]['text'][-TAIL:]}")
            elif self.resumed:
                parts.append(f"### ステップ {sid}（今の実行でまだ流れておらず、前の実行の記録にも無い）")
            else:
                parts.append(f"### ステップ {sid}（この実行でまだ流れていない）")
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

    @staticmethod
    def extension(ctx, step: dict, path: Path, timeout) -> int | None:
        """延長のファイルの秒から、この呼び出しの開始からの締め切り（`with_margin`）を返す。今の `timeout` 以下なら None。
        延ばしたら `cur.extended_timeout` と進捗ログの 1 行に残す（#2047 の決定 4）。"""
        import drive_pause
        from supervise_lib.verify_steps import with_margin

        got = drive_pause.read_extension(path)
        if got is None:
            return None
        longer = with_margin(int(got[0]))
        if longer <= int(timeout):
            return None
        cur = ctx.state.cur
        if isinstance(cur, dict) and int((cur.get("extended_timeout") or {}).get("timeout") or 0) < longer:
            cur["extended_timeout"] = {"seconds": int(got[0]), "timeout": longer, "planned": int(timeout), "reason": got[1]}
            write = getattr(ctx.state, "progress_write", None)
            if write:
                write({"kind": "extend", "step": step.get("id"), "timeout": longer, "planned": int(timeout), "reason": got[1]})
        return longer

    def run_cmd(self, ctx, step: dict, extend_file: Path | None = None) -> tuple[int, str]:
        """コマンドを締め切りまで待つ。`extend_file` を渡すと（drive のステップ）、子へ `NDF_STEP_EXTEND_FILE` で置き場を渡し、
        子が書いた見込みの秒で締め切りを後ろへずらす（#2047 の決定 4）。"""
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
        cmd, timeout = fill_test_limit(ctx, step, cmd)
        env = dict(os.environ)
        # 親の run のステップから受け継いだ no_reports は、reports: true なら外す
        no_reports = self.no_reports(ctx)
        inherited = env.get("PYTEST_ADDOPTS", "")
        addopts = [(inherited.replace(no_reports, "") if no_reports else inherited).strip()]
        if no_reports and not step.get("reports"):
            addopts.append(no_reports)
        env["PYTEST_ADDOPTS"] = " ".join(a for a in addopts if a)
        # 子が起動する CLI の使用量の帳簿の行にプランとステップを付ける（#1953 の決定 7・決定 11）
        env.update(NDF_USAGE_PLAN=getattr(ctx, "plan_path", None) or "", NDF_USAGE_STEP=str(step.get("id") or ""))
        extend = None
        if extend_file is not None:
            import drive_pause

            env[drive_pause.EXTEND_ENV] = str(extend_file)
            extend = lambda started: (t := self.extension(ctx, step, Path(extend_file), timeout)) and started + t  # noqa: E731
        ctx.state.run_log = ctx.state.dir / "run-stderr.log"
        try:
            p = run_ticking(
                cmd,
                ctx.tick,
                ctx.state.every,
                shell=True,
                cwd=step.get("cwd", ctx.cwd),
                timeout=timeout,
                env=env,
                err_path=ctx.state.run_log,
                extend=extend,
            )
            return p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            return 124, f"打ち切り（{e.timeout} 秒）"
        finally:
            ctx.state.run_log = None

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        # 落ちたテストの走らせ直しは持たない。テストのステップは test-run.py が JUnit で見分ける（#1334）
        started = time.time()
        code, text = self.run_cmd(ctx, step)
        ctx.state.cur.update(exit=code, text=text, seconds=round(time.time() - started, 1))
        return code == 0, text


class JudgeStep:
    """judge のステップ: 最小構成の claude -p に次の手を選ばせる。返り値は判断の辞書。"""

    kind = "judge"

    def execute(self, ctx, step: dict) -> dict:
        choices = step.get("choices")
        # 判断の基準（#1366）。実行の中で 1 回だけ読む。スプリントが特定できればスプリント MVV も渡す（#1400）
        mvv, sprint = ctx.state.mvv_block_of(ctx.cwd, ctx.plan)
        prompt = (
            project_mvv.block(mvv, sprint) + "\n" + f"フェーズ: {ctx.plan.get('フェーズ')} / 課題: {ctx.plan.get('課題')}\n"
            f"問い: {step['question']}\n"
            + (f"選べる値: {', '.join(choices)}（関門なら gate、止めるなら stop）\n" if choices else "")
            + f"作業ディレクトリ: {ctx.state.work}（worker の作業ファイルの置き場所）\n"
            + f"\n## 規則\n{ctx.plan.get('規則', '（無し）')}\n\n## 結果\n{ctx.inputs_text(step)}"
        )
        res = ctx.claude.call(JUDGE_SYSTEM, prompt, None, ctx.cwd, step.get("timeout", 600))
        ctx.claude.record_usage("judge", res)
        d = parse_decision(res["text"]) if res["ok"] else {"decision": "stop", "reason": res["text"][:200]}
        d["basis"] = project_mvv.basis(d.get("basis"), mvv, sprint=sprint)  # 返されなければ「根拠なし」か「MVV なし」（止めない）
        ctx.state.cur.update(exit=0, text=json.dumps(d, ensure_ascii=False), seconds=res["seconds"], basis=d["basis"])
        return d
