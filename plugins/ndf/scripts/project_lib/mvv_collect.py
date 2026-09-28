"""mvv_collect.py: プロジェクト MVV の候補の材料を集める（`project-mvv.py collect`・#1366）。LLM を呼ばない。

git・`gh`（読み取りだけ）・ファイルの読み込みだけで、出典つきの材料を組む。コミット数と課題数の両方が閾値に満たなければ
傾向モードで、README・指示書・依頼文だけを材料にする（I9）。秘密の形は `secret.redact` で伏せる。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import project_mvv as pm
from step_result import EXIT_PRECONDITION, StepError

from project_lib.secret import is_secret_path, redact

MAX_SOURCE = 2000  # 材料 1 件の上限の文字数
MAX_COMMITS = 400  # 材料に入れるコミットの件名の数（新しい順）
MAX_ISSUES = 200  # 材料に入れる課題の数（新しい順）
ISSUE_LIMIT = 5000  # 課題の件数を数える上限
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md", "GEMINI.md")
README_FILES = ("README.md", "README.rst", "README.txt", "README")


class Deadline:
    def __init__(self, budget: float):
        self.end = time.monotonic() + budget

    def left(self) -> float:
        return self.end - time.monotonic()

    def timeout(self) -> float:
        return max(1.0, min(30.0, self.left()))


def _run_bounded(cmd: list[str], root: Path, dl: Deadline) -> subprocess.CompletedProcess | str:
    """1 回のコマンド。失敗の理由は文字列で返す。"""
    if dl.left() <= 0:
        return "時間切れ"
    try:
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=dl.timeout())
    except FileNotFoundError:
        return f"{cmd[0]} が無い"
    except subprocess.TimeoutExpired:
        return f"{' '.join(cmd[:3])} が打ち切られた"
    if p.returncode != 0:
        return f"{' '.join(cmd[:3])}: {(p.stderr or p.stdout).strip()[:200]}"
    return p


def _clean(text: str) -> str:
    return "\n".join(redact(ln) for ln in (text or "").splitlines())[:MAX_SOURCE]


def _doc_sources(root: Path, rel: str, kind: str) -> list[dict]:
    """ファイルを `## ` の節ごとの出典に分ける。ref は `パス:行`。"""
    if is_secret_path(rel):
        return []
    try:
        text = (root / rel).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = text.splitlines()
    starts = [0] + [i for i, ln in enumerate(lines) if ln.startswith("## ") and i > 0]
    out = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[s:e]).strip()
        if chunk:
            out.append({"kind": kind, "ref": f"{rel}:{s + 1}", "text": _clean(chunk)})
    return out


def _decision_docs(root: Path) -> list[str]:
    d = root / "docs"
    if not d.is_dir():
        return []
    found = sorted(str(p.relative_to(root)) for p in d.rglob("*.md") if "decision" in p.name.lower())
    return found[:5]


def _issue_repo(root: Path) -> str | None:
    import repo

    return repo.owner_repo(root)


def _collect_commits(root: Path, dl, missing: list) -> tuple[int, list[str]]:
    """コミットの数と件名。読めなければ missing に残す。"""
    commits: list[str] = []
    n_commits = 0
    p = _run_bounded(["git", "rev-list", "--count", "HEAD"], root, dl)
    if isinstance(p, str):
        missing.append({"what": "git の履歴", "reason": p})
    else:
        n_commits = int(p.stdout.strip() or 0)
        q = _run_bounded(["git", "log", "--no-merges", f"-{MAX_COMMITS}", "--format=%h %s"], root, dl)
        if isinstance(q, str):
            missing.append({"what": "コミットの件名", "reason": q})
        else:
            commits = [ln for ln in q.stdout.splitlines() if ln.strip()]
    return n_commits, commits


def _collect_issues(root: Path, dl, missing: list) -> list[dict]:
    """課題の一覧。読めなければ missing に残す。"""
    issues: list[dict] = []
    slug = _issue_repo(root)
    if not slug:
        missing.append({"what": "課題", "reason": "origin が GitHub でない（課題を読まない）"})
    else:
        q = _run_bounded(
            ["gh", "issue", "list", "--repo", slug, "--state", "all", "--limit", str(ISSUE_LIMIT), "--json", "number,title,body"], root, dl
        )
        if isinstance(q, str):
            missing.append({"what": "課題", "reason": q})
        else:
            try:
                issues = json.loads(q.stdout or "[]")
            except ValueError:
                missing.append({"what": "課題", "reason": "gh の出力を読めない"})
    return issues


def _collect_sources(root: Path, a, mode: str, issues: list[dict], commits: list[str], missing: list) -> list[dict]:
    """素材（依頼文・README・指示書と、history なら決定の文書・課題・コミット）を集める。"""
    raw: list[dict] = []
    if a.request_file:
        try:
            raw.append({"kind": "request", "ref": a.request_file, "text": _clean(Path(a.request_file).read_text(encoding="utf-8"))})
        except OSError as e:
            missing.append({"what": "依頼文", "reason": str(e)})
    for rel in README_FILES:
        if (root / rel).is_file():
            raw += _doc_sources(root, rel, "readme")
            break
    for rel in INSTRUCTION_FILES:
        if (root / rel).is_file():
            raw += _doc_sources(root, rel, "instructions")
    if mode == "history":
        for rel in _decision_docs(root):
            raw += _doc_sources(root, rel, "doc")
        for it in sorted(issues, key=lambda x: -int(x.get("number") or 0))[:MAX_ISSUES]:
            raw.append({"kind": "issue", "ref": f"#{it.get('number')}", "text": _clean(f"{it.get('title', '')}\n\n{it.get('body') or ''}")})
        raw += [{"kind": "commit", "ref": redact(ln), "text": redact(ln)} for ln in commits]
    return raw


def collect_materials(root: Path, a) -> dict:
    started = time.monotonic()
    dl = Deadline(a.budget)
    settings, err = pm.read_settings(root)
    if err:
        raise StepError(err, EXIT_PRECONDITION)
    th = {
        "commits": a.trend_commits if a.trend_commits is not None else settings["trend_commits"],
        "issues": a.trend_issues if a.trend_issues is not None else settings["trend_issues"],
    }
    missing: list[dict] = []
    n_commits, commits = _collect_commits(root, dl, missing)
    issues = _collect_issues(root, dl, missing)
    counts = {"commits": n_commits, "issues": len(issues)}
    mode = "trend" if counts["commits"] < th["commits"] and counts["issues"] < th["issues"] else "history"
    raw = _collect_sources(root, a, mode, issues, commits, missing)
    sources = [{"id": f"S{i}", **s} for i, s in enumerate(raw, 1)]
    return {
        "root": str(root),
        "mode": mode,
        "counts": counts,
        "thresholds": th,
        "sources": sources,
        "missing": missing,
        "seconds": round(time.monotonic() - started, 2),
    }
