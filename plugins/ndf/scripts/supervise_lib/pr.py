"""pr のステップのハンドラー（#1142 の C1）。push と GitHub への書き込みを行う唯一のハンドラーである。"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import gh_call
import gh_sections
import mdtable
from pr_mode import with_mode_line
from release_lib.others import MIGRATION_HEADING
from supervise_lib.claude import TAIL
from supervise_lib.paths import DECISIONS_SH
from supervise_lib.pr_materials import CHANGES_HEADING, Materials, gather_materials
from supervise_lib.prompts import PR_SYSTEM
from step_result import result

PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"  # PR 本文の末尾の署名（1 度だけ）
TITLE_MAX = 256  # 設計文書の H1 を題に使う上限（コードポイント。#1289 の決定 7）


def user_changes(step: dict, title: str) -> str:
    """PR 本文の「利用者向けの変化」の節。ステップの changes（無ければ summary、それも無ければ題名）から組む。"""
    text = (step.get("changes") or step.get("summary") or title or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()] or ["無し"]
    items = [l if l.startswith(("- ", "* ")) else f"- {l}" for l in lines]
    return CHANGES_HEADING + "\n\n" + "\n".join(items)


def migration_section(items: list[str] | None) -> str:
    """PR 本文の「移行の手順」の節。箇条が無ければ「- 無し」。"""
    return MIGRATION_HEADING + "\n\n" + ("\n".join(f"- {i}" for i in items or []) or "- 無し")


def with_migration(body: str, section: str, required: bool) -> str:
    """LLM の本文の「移行の手順」の節を確かめる。節が無ければ「利用者向けの変化」の節の後へ置き、required（要求の
    移行性の行か集めた移行の手順がある）なのに節が「無し」だけなら機械の節で差し替える（#1752 の I8）。"""
    found = re.search(rf"^{MIGRATION_HEADING}\s*$", body, re.M)
    if found and not (required and not gh_sections.section_items(body, MIGRATION_HEADING, 0)):
        return body
    if found:
        return gh_sections.replace_section(body, MIGRATION_HEADING, section)
    at = re.search(rf"^{CHANGES_HEADING}\s*$", body, re.M)
    cut = 0  # 「利用者向けの変化」の節が無ければ先頭へ置く
    if at:
        nxt = re.search(r"^## ", body[at.end() :], re.M)
        cut = at.end() + nxt.start() if nxt else len(body)
    head, tail = body[:cut].rstrip(), body[cut:].lstrip("\n")
    return (head + "\n\n" if head else "") + section + ("\n\n" + tail if tail else "\n")


def design_title(path: Path) -> str | None:
    """設計文書の題（#1289 の決定 1・7）。囲み（``` / ~~~）の外の最初の `# ` の行から `# ` を除き、前後の空白を
    落とした文。ファイルが無い・読めない・H1 が無い・空・`TITLE_MAX` コードポイントを超えるときは None。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fence = ""
    for line in text.splitlines():
        mark = line.lstrip()[:3]
        if mark in ("```", "~~~"):
            fence = "" if fence == mark else (fence or mark)
            continue
        if not fence and line.startswith("# "):
            title = line[2:].strip()
            return title if title and len(title) <= TITLE_MAX else None
    return None


def sync_title(cwd: str, pr: int, doc: str) -> dict:
    """設計 PR の題を設計文書の H1 に合わせ直す（#1289 の決定 9。`supervise.py sync-title`）。違うときだけ書き込み、
    H1 を読めないときは書き込まない（人が付けた題を代わりの題で上書きしない）。gh の失敗も承認ゲートを止めないため
    status は常に ok で、失敗は標準エラーへ出す。"""
    title = design_title(Path(cwd) / doc)
    if title is None:
        return result("supervise-sync-title", "ok", f"{doc} から題を読めない。題は書き直さない", [], {"changed": 0})
    now = gh_call.gh(["pr", "view", str(pr), "--json", "title", "--jq", ".title"], cwd=cwd)
    if now.returncode != 0:
        print(f"PR #{pr} の題を読めない: {now.stderr.strip()}", file=sys.stderr)
        return result("supervise-sync-title", "ok", f"PR #{pr} の題を読めない", [], {"changed": 0})
    if now.stdout.strip() == title:
        return result("supervise-sync-title", "ok", f"PR #{pr} の題は H1 と同じ", [], {"changed": 0})
    p = gh_call.gh(["pr", "edit", str(pr), "--title", title], cwd=cwd)
    if p.returncode != 0:
        print(f"PR #{pr} の題を書き直せない: {p.stderr.strip()}", file=sys.stderr)
        return result("supervise-sync-title", "ok", f"PR #{pr} の題を書き直せない", [], {"changed": 0})
    return result("supervise-sync-title", "ok", f"PR #{pr} の題を H1 に合わせた", [], {"changed": 1})


_REJECTED = re.compile(r"^\s*! \[", re.M)  # 相手が拒んだ ref の行（`! [rejected]`・`! [remote rejected]`）


def is_push_check_failure(returncode: int, output: str) -> bool:
    """push の失敗が push 前の検査（`pre-push` フック）の不合格か。終了コードが 1 で、相手が拒んだ ref の行が無いもの。
    先行の拒否（`! [rejected]`）と接続・相手のリポジトリの失敗（128）はコミットを直しても通らない。`fatal:` の行は
    資格情報の補助もフックの前に書くため、見分けに使わない（設計の「push の失敗の形」）。"""
    return returncode == 1 and not _REJECTED.search(output)


class PrStep:
    """push して Draft の Pull Request を作る。既にあれば本文だけを書き直す（`title_doc` の H1 を読めたときは題も）。
    本文は LLM に書かせてよい。"""

    kind = "pr"

    def git(self, ctx, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=ctx.cwd, capture_output=True, text=True).stdout.rstrip()

    def with_appended(self, ctx, body: str, step: dict, sections: list[str] | None = None) -> str:
        """材料の節（`sections`）と、pr のステップの `append`（{state_dir} を置き換えたパス）のうちあるファイルの中身を、
        署名の前へそのまま足す。"""
        extra = list(sections or [])
        for raw in step.get("append", []):
            f = Path(str(raw).replace("{state_dir}", str(ctx.state.dir)))
            if f.is_file():
                extra.append(f.read_text().strip())
        if not extra:
            return body
        at = body.rfind(PR_FOOTER)
        head, tail = (body[:at], body[at:]) if at >= 0 else (body, "")
        return head.rstrip() + "\n\n" + "\n\n".join(extra) + "\n\n" + tail

    def with_decisions(self, ctx, body: str, base: str) -> str:
        """設計の PR の本文へ「決めたこと」の節を作る時点で入れる（`pr-body-decisions.sh render`）。

        作った後に sync で書き直すと、本文の編集（edited）で CI の pr-body-decisions がもう 1 度起動する。
        読めなかったときは本文を変えない（後の sync のステップが直す）。
        """
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(body)
            path = f.name
        try:
            done = subprocess.run(
                ["bash", str(DECISIONS_SH), "render", "--base", base, "--body", path],
                cwd=ctx.cwd,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired):
            return body
        finally:
            Path(path).unlink(missing_ok=True)
        if done.returncode != 0 or not done.stdout:
            return body
        return done.stdout.decode("utf-8", "replace")

    def execute(self, ctx, step: dict) -> tuple[bool, str]:
        """push して Draft の Pull Request を作る。既にあれば本文だけを書き直す。本文は既定で LLM に書かせる（body が llm でないときは機械生成のまま）。"""
        base = step.get("base") or ctx.base_branch()
        if not base:
            msg = "PR の宛先（起点のブランチ）が分からない（ステップの base、計画か .ndf/worktree.json の base_branch）"
            ctx.state.cur.update(exit=2, text=msg)
            return False, msg
        branch, err = self._push(ctx)
        if err is not None:
            return False, err
        mats = self._materials(ctx, step, branch)
        title, changes, issues, body = self._machine_body(ctx, step, base, branch, mats)
        # 設計 PR の題は設計文書の H1（#1289 の決定 1）。読めなければ title のまま出し、既存の PR の題は書き直さない
        doc_title = design_title(Path(ctx.cwd) / step["title_doc"]) if step.get("title_doc") else None
        if step.get("body", "llm") == "llm":
            body = self._llm_body(ctx, step, body, changes, issues, mats)
        body = self.with_appended(ctx, body, step, mats.sections)
        body = with_mode_line(body, ctx.plan.get("モード"), self.passed_stages(ctx, step))
        if step.get("decisions"):
            body = self.with_decisions(ctx, body, base)
        return self._publish(ctx, base, branch, doc_title or title, body, retitle=doc_title is not None)

    def _push(self, ctx) -> tuple[str, str | None]:
        """HEAD を push する。`(ブランチ, 失敗の出力 | None)`。結果に押したコミット（`head`）を残し、失敗なら標準出力と
        標準エラーを続けた出力と、push 前の検査の不合格か（`push_check`。#1751 の決定 3・5・8）を残す。"""
        branch = self.git(ctx, "rev-parse", "--abbrev-ref", "HEAD")
        head = self.git(ctx, "rev-parse", "HEAD")
        ctx.state.cur["head"] = head
        push = subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD"], cwd=ctx.cwd, capture_output=True, text=True)
        if push.returncode == 0:
            return branch, None
        text = "\n".join(x for x in (push.stdout.strip(), push.stderr.strip()) if x) + "\n"
        check = is_push_check_failure(push.returncode, push.stdout + "\n" + push.stderr)
        prev = ctx.state.results.get(ctx.state.cur.get("id", "pr")) or {}
        if check and prev.get("push_check") and prev.get("head") == head:
            check = False
            text += f"同じコミット（{head[:12]}）で 2 回続けて push 前の検査に落ちた。修正へ回さずに止める\n"
        ctx.state.cur.update(exit=push.returncode, text=text, push_check=check)
        return branch, text

    def _materials(self, ctx, step: dict, branch: str) -> Materials:
        """ステップの `materials` から本文の材料を集める。手動確認の印は今の PR 本文から引き継ぐ。"""
        mats = step.get("materials")
        if not mats:
            return Materials()
        old = ""
        if mats.get("manual", True):
            p = gh_call.gh(["pr", "list", "--head", branch, "--state", "open", "--json", "body", "--jq", '.[0].body // ""'], cwd=ctx.cwd)
            old = p.stdout if p.returncode == 0 else ""
        return gather_materials(str(ctx.cwd), [int(i) for i in ctx.plan.get("課題", [])], mats, old)

    def _machine_body(self, ctx, step: dict, base: str, branch: str, mats: Materials | None = None) -> tuple[str, str, str, str]:
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
                tests.append([sid, r.get("exit"), last[:120]])
        issues = " ".join(f"#{i}" for i in ctx.plan.get("課題", []))
        docs = "\n".join(f"- `{d}`" for d in step.get("docs", [])) or "- 無し"
        title = step.get("title") or (self.git(ctx, "log", "--reverse", "--format=%s", rng).splitlines() or [branch])[0]
        mats = mats or Materials()
        if mats.changes is None:
            changes = user_changes(step, title)
        else:  # 集めた実装の PR の変化（変化のある PR の行だけ。決定 7）
            changes = CHANGES_HEADING + "\n\n" + ("\n".join(f"- {c}" for c in mats.changes) or "- 無し")
        design = "".join(f"\n{d}" for d in mats.design)
        body = f"""{step.get("summary", "")}

{changes}

{migration_section(mats.migration)}

## 課題と設計

- 課題: {issues}
{docs}{design}

## コミット

{commits}

## 変更の統計

```text
{chr(10).join(stat[-15:])}
```

## テスト（supervise.py の run のステップ）

{mdtable.table_markdown(("ステップ", "exit", "最後の行"), tests or [["無し", "", ""]], align=(None, "right", None))}

{PR_FOOTER}
"""
        return title, changes, issues, body

    def _llm_body(self, ctx, step: dict, body: str, changes: str, issues: str, mats: Materials | None = None) -> str:
        """LLM で本文を補う。使えない応答のときは機械生成の本文を返す。"""
        mats = mats or Materials()
        design = ""
        for d in step.get("docs", []):
            f = Path(ctx.cwd) / d
            if f.is_file():
                design += f"\n### {d}\n" + f.read_text()[:TAIL]
        res = ctx.claude.call(
            PR_SYSTEM,
            f"課題: {issues}\n要約の手がかり: {step.get('summary', '')}\n"
            + "".join(f"{n}\n" for n in mats.migration_notes)
            + f"\n## 材料\n{body}\n## 設計文書（抜粋）{design or ' 無し'}",
            None,
            ctx.cwd,
            step.get("timeout", 600),
        )
        ctx.claude.record_usage("judge", res)
        if res["ok"] and res["text"].strip():
            body = res["text"].strip() + "\n"
            if not re.search(rf"^{CHANGES_HEADING}\s*$", body, re.M):
                body = changes + "\n\n" + body
            body = with_migration(body, migration_section(mats.migration), bool(mats.migration))
            if PR_FOOTER not in body:
                body = body.rstrip() + f"\n\n{PR_FOOTER}\n"
        return body

    def _publish(self, ctx, base: str, branch: str, title: str, body: str, retitle: bool = False) -> tuple[bool, str]:
        """既存の PR があれば本文を書き直し（retitle なら題も。#1289 の決定 6）、無ければ Draft で作る。"""
        found = gh_call.gh(
            ["pr", "list", "--head", branch, "--state", "open", "--json", "url", "--jq", ".[0].url"], cwd=ctx.cwd
        ).stdout.rstrip()
        if found:
            p = gh_call.gh(["pr", "edit", found, *(["--title", title] if retitle else []), "--body", body], cwd=ctx.cwd)
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
