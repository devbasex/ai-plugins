"""pr のステップのハンドラー（#1142 の C1）。push と GitHub への書き込みを行う唯一のハンドラーである。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import gh_call
from pr_mode import with_mode_line
from supervise_lib.claude import TAIL
from supervise_lib.prompts import PR_SYSTEM

PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"  # PR 本文の末尾の署名（1 度だけ）
CHANGES_HEADING = "## 利用者向けの変化"  # 配布の説明文（release-steps.py notes）の材料になる PR 本文の節


def user_changes(step: dict, title: str) -> str:
    """PR 本文の「利用者向けの変化」の節。ステップの changes（無ければ summary、それも無ければ題名）から組む。"""
    text = (step.get("changes") or step.get("summary") or title or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()] or ["無し"]
    items = [l if l.startswith(("- ", "* ")) else f"- {l}" for l in lines]
    return CHANGES_HEADING + "\n\n" + "\n".join(items)


class PrStep:
    """push して Draft の Pull Request を作る。既にあれば本文だけを書き直す。本文は LLM に書かせてよい。"""

    kind = "pr"

    def git(self, ctx, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=ctx.cwd, capture_output=True, text=True).stdout.rstrip()

    def with_appended(self, ctx, body: str, step: dict) -> str:
        """pr のステップの `append`（{state_dir} を置き換えたパス）のうち、あるファイルの中身を署名の前へそのまま足す。"""
        extra = []
        for raw in step.get("append", []):
            f = Path(str(raw).replace("{state_dir}", str(ctx.state.dir)))
            if f.is_file():
                extra.append(f.read_text().strip())
        if not extra:
            return body
        at = body.rfind(PR_FOOTER)
        head, tail = (body[:at], body[at:]) if at >= 0 else (body, "")
        return head.rstrip() + "\n\n" + "\n\n".join(extra) + "\n\n" + tail

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        """push して Draft の Pull Request を作る。既にあれば本文だけを書き直す。LLM を使わない。"""
        base = step.get("base") or ctx.base_branch()
        if not base:
            msg = "PR の宛先（起点のブランチ）が分からない（ステップの base、計画か .ndf/worktree.json の base_branch）"
            ctx.state.cur.update(exit=2, text=msg)
            return False, msg
        branch, err = self._push(ctx)
        if err is not None:
            return False, err
        title, changes, issues, body = self._machine_body(ctx, step, base, branch)
        if step.get("body", "llm") == "llm":
            body = self._llm_body(ctx, step, body, changes, issues)
        body = self.with_appended(ctx, body, step)
        body = with_mode_line(body, ctx.plan.get("モード"), self.passed_stages(ctx, step))
        return self._publish(ctx, base, branch, title, body)

    def _push(self, ctx) -> tuple[str, str | None]:
        """HEAD を push する。`(ブランチ, 失敗の出力 | None)`。"""
        branch = self.git(ctx, "rev-parse", "--abbrev-ref", "HEAD")
        push = subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], cwd=ctx.cwd,
                              capture_output=True, text=True)
        if push.returncode != 0:
            ctx.state.cur.update(exit=push.returncode, text=push.stderr)
            return branch, push.stderr
        return branch, None

    def _machine_body(self, ctx, step: dict, base: str, branch: str) -> tuple[str, str, str, str]:
        """PR の材料を集めて機械生成の本文を作る。`(タイトル, 変更の節, 課題, 本文)`。"""
        subprocess.run(["git", "fetch", "-q", "origin", base], cwd=ctx.cwd, capture_output=True, text=True)
        rng = f"origin/{base}..HEAD"
        commits = self.git(ctx, "log", "--reverse", "--format=- %s", rng) or "- （無し）"
        # 変更の統計は起点との merge-base から数える（起点より古いブランチで、他の PR の変更を削除として載せない）
        stat = self.git(ctx, "diff", "--stat", f"origin/{base}...HEAD").splitlines()
        tests = []
        for sid, r in ctx.state.results.items():
            if r.get("type") == "run":
                last = next((l for l in reversed(r.get("text", "").splitlines()) if l.strip()), "")
                tests.append(f"| {sid} | {r.get('exit')} | {last[:120]} |")
        issues = " ".join(f"#{i}" for i in ctx.plan.get("課題", []))
        docs = "\n".join(f"- `{d}`" for d in step.get("docs", [])) or "- 無し"
        title = step.get("title") or (self.git(ctx, "log", "--reverse", "--format=%s", rng).splitlines() or [branch])[0]
        changes = user_changes(step, title)
        body = f"""{step.get('summary', '')}

{changes}

## 課題と設計

- 課題: {issues}
{docs}

## コミット

{commits}

## 変更の統計

```text
{chr(10).join(stat[-15:])}
```

## テスト（supervise.py の run のステップ）

| ステップ | exit | 最後の行 |
| --- | ---: | --- |
{chr(10).join(tests) or '| 無し | | |'}

{PR_FOOTER}
"""
        return title, changes, issues, body

    def _llm_body(self, ctx, step: dict, body: str, changes: str, issues: str) -> str:
        """LLM で本文を補う。使えない応答のときは機械生成の本文を返す。"""
        design = ""
        for d in step.get("docs", []):
            f = Path(ctx.cwd) / d
            if f.is_file():
                design += f"\n### {d}\n" + f.read_text()[:TAIL]
        res = ctx.claude.call(PR_SYSTEM, f"課題: {issues}\n要約の手がかり: {step.get('summary', '')}\n\n"
                              f"## 材料\n{body}\n## 設計文書（抜粋）{design or ' 無し'}",
                              None, ctx.cwd, step.get("timeout", 600))
        ctx.claude.record_usage("judge", res)
        if res["ok"] and res["text"].strip():
            body = res["text"].strip() + "\n"
            if not re.search(rf"^{CHANGES_HEADING}\s*$", body, re.M):
                body = changes + "\n\n" + body
            if PR_FOOTER not in body:
                body = body.rstrip() + f"\n\n{PR_FOOTER}\n"
        return body

    def _publish(self, ctx, base: str, branch: str, title: str, body: str) -> tuple[bool, str]:
        """既存の PR があれば本文を書き直し、無ければ Draft で作る。"""
        found = gh_call.gh(["pr", "list", "--head", branch, "--state", "open", "--json", "url", "--jq", ".[0].url"],
                           cwd=ctx.cwd).stdout.rstrip()
        if found:
            p = gh_call.gh(["pr", "edit", found, "--body", body], cwd=ctx.cwd)
            url = found
        else:
            p = gh_call.gh(["pr", "create", "--draft", "--base", base, "--title", title, "--body", body], cwd=ctx.cwd)
            url = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
        if url:
            ctx.plan["Pull Request"] = url
        ctx.state.cur.update(exit=p.returncode, text=(url + "\n" + p.stderr).strip())
        return p.returncode == 0, url

    def passed_stages(self, ctx, step: dict | None = None) -> list[str]:
        """通した工程（ステップの stage）を通った順に重ねずに返す。step を渡せばそのステップの工程も含める。"""
        stages: list[str] = []
        ids = [e.get("id") for e in ctx.state.log] + ([step["id"]] if step else [])
        for sid in ids:
            stage = (ctx.steps.get(sid) or {}).get("stage")
            if stage and stage not in stages:
                stages.append(stage)
        return stages
