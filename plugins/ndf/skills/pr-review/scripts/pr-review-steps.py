#!/usr/bin/env python3
"""pr-review-steps.py: `/ndf:pr-review` の手順のうち、判断の要らない収集・判定・投稿を行う（#860）。

    pr-review-steps.py collect  [<PR番号>] [--branch] [--focus AREA] [--out-dir D] [--repo R]
    pr-review-steps.py finish   --findings F (--pr N | --branch) [--reviewer NAME] [--out-dir D] [--repo R]
    pr-review-steps.py delegate <codex|agy> [<PR番号>] [--branch] [--focus AREA] [--out-dir D] [--repo R]
                                [--timeout 秒]

collect   レビューの対象を集め、レビューの文脈ファイル `<D>/context.md` を書く。PR モードは
          `gh_parts.py pr-info --with diff,threads`、`--branch` は `repo.existing_base_branch` で起点を決めて
          `git diff` / `git log` を集める。前回の指摘ファイル `<D>/findings.json` を消す
finish    指摘ファイルを検査し（重要度・段）、本来の判定を決める。PR モードは `payload.json` と `result.json`
          を書いて `gh_parts.py review-post --round 0 --seat pr-review-<レビューする者>` を 1 回呼ぶ。
          `--branch` は投稿せず `<D>/report.md` を書く
delegate  collect → 観点（SKILL.md の `## 観点`）と文脈ファイルからプロンプトを組む →
          `external-ai.py run <CLI> --phase review`（上限つきの待ち）→ finish を 1 回で行う。
          上限越え・結果なし・読めない指摘ファイルのときは投稿しない

結果は 1 行の JSON（形は `scripts/lib/README.md`。`tool` は `pr-review`）。読み手は `status` を見る。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_MD = HERE.parent / "SKILL.md"
PLUGIN_ROOT = HERE.parents[2]
LIB = PLUGIN_ROOT / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import gh_call  # noqa: E402
import gh_sections  # noqa: E402
import repo as repo_lib  # noqa: E402
from step_result import (  # noqa: E402
    EXIT_PRECONDITION,
    EXIT_UNREADABLE,
    EXIT_VIOLATION,
    StepError,
    emit,
    git,
    git_root,
    main_with,
    result,
)

TOOL = "pr-review"
GH_PARTS = LIB / "gh_parts.py"
EXTERNAL_AI = PLUGIN_ROOT / "skills" / "external-ai" / "scripts" / "external-ai.py"
SEVERITIES = ("critical", "major", "minor", "nit")
BLOCKING = ("critical", "major")
STAGES = ("spec", "quality")
DEFAULT_STAGE = "quality"  # 段を書かない指摘の段
DELEGATES = ("codex", "agy")
PERSPECTIVE_HEADING = "## 観点"
DIFF_FILE = re.compile(r"^diff --git a/.+? b/(.+)$", re.MULTILINE)

FINDINGS_GUIDE = """## 指摘ファイルの書き方

指摘の全件と総評を `{path}` へ JSON で書く。**投稿しない**（判定と投稿はスクリプトが行う）。指摘が無ければ `comments` を空の配列にする。

```json
{{
  "summary": "総評（設計・横断の所見だけ。個別の指摘を繰り返さない）",
  "comments": [
    {{"path": "src/foo.py", "line": 42, "severity": "major", "stage": "quality",
     "category": "可読性", "body": "70 行の関数。〇〇 と △△ に分ける"}},
    {{"severity": "major", "stage": "spec", "category": "受け入れ条件",
     "body": "受け入れ条件 4 の並び順を満たすテストが無い"}}
  ]
}}
```

| 鍵 | 必須 | 規則 |
| --- | --- | --- |
| `summary` | 任意 | 文字列 |
| `comments[].severity` | 必須 | `critical` / `major` / `minor` / `nit` のどれか |
| `comments[].stage` | 任意 | 第 1 段（仕様適合）を満たさない指摘は `spec`、第 2 段は `quality`。省けば `quality` |
| `comments[].category` | 任意 | 分類。本文の先頭に `[重要度 / 分類]` として付く |
| `comments[].path` / `line` | 任意 | 差分に現れるファイルと、差分に含まれる行（追加行・コンテキスト行）。行を指せない指摘は省く（総評へ回る） |
| `comments[].body` | 必須 | 空でない文字列。問題と直し方 |
"""

DELEGATE_RULES = """# レビューの依頼

あなたはこの変更のレビューする者である。次を守る。

- **投稿しない。** GitHub と git へ書かない（`gh api` / `gh pr review` / `gh pr comment` / `git commit` / `git push` を打たない）
- **リポジトリのファイルを編集しない。** 書いてよいのは下の「指摘ファイルの書き方」が指すファイルだけ
- 観点の第 1 段（仕様適合）→ 第 2 段（コード品質）の順に見る
- 最後に指摘ファイルを書いて終える
"""


# ---------------- 指摘ファイル・本来の判定 ----------------


def _stage(c: dict) -> str:
    """指摘の段。書かれていなければ DEFAULT_STAGE。"""
    return c.get("stage", DEFAULT_STAGE)


def check_findings(data) -> list[str]:
    """指摘ファイルの中身の誤り（I3）。空なら読める。"""
    if not isinstance(data, dict):
        return ["最上位がオブジェクトでない"]
    errs: list[str] = []
    if not isinstance(data.get("summary", ""), str):
        errs.append("summary が文字列でない")
    comments = data.get("comments")
    if not isinstance(comments, list):
        return [*errs, "comments が配列でない"]
    for n, c in enumerate(comments):
        if not isinstance(c, dict):
            errs.append(f"comments[{n}]: オブジェクトでない")
            continue
        if c.get("severity") not in SEVERITIES:
            errs.append(f"comments[{n}]: 重要度 {c.get('severity')!r} は {' / '.join(SEVERITIES)} のどれでもない")
        if _stage(c) not in STAGES:
            errs.append(f"comments[{n}]: 段 {c.get('stage')!r} は {' / '.join(STAGES)} のどれでもない")
        if not isinstance(c.get("body"), str) or not c["body"].strip():
            errs.append(f"comments[{n}]: body が空")
    return errs


def decide_event(comments: list[dict]) -> dict:
    """指摘の集合から本来の判定を決める（I4）。副作用を持たない。"""
    by_severity = {s: sum(1 for c in comments if c.get("severity") == s) for s in SEVERITIES}
    spec_unmet = sum(1 for c in comments if _stage(c) == "spec")
    if spec_unmet or any(by_severity[s] for s in BLOCKING):
        intent = "REQUEST_CHANGES"
    elif comments:
        intent = "COMMENT"
    else:
        intent = "APPROVE"
    return {"intent": intent, "by_severity": by_severity, "spec_unmet": spec_unmet}


def _prefixed(c: dict) -> str:
    sev, cat, body = c["severity"], str(c.get("category") or "").strip(), c["body"].strip()
    if body.startswith((f"[{sev} /", f"[{sev}]")):
        return body
    return f"[{sev} / {cat}] {body}" if cat else f"[{sev}] {body}"


def _finding_place(c: dict) -> str:
    path, line = c.get("path"), c.get("line")
    return f"`{path}:{line}` " if path and line is not None else (f"`{path}` " if path else "")


def _payload_summary(data: dict, spec: list[dict]) -> str:
    """payload の summary。仕様適合の見出しと一覧（あれば）に総評を続ける。"""
    parts = []
    if spec:
        parts.append("### 仕様適合（満たさない）\n\n" + "\n".join(f"- {_finding_place(c)}{_prefixed(c)}" for c in spec))
    if str(data.get("summary") or "").strip():
        parts.append(data["summary"].strip())
    return "\n\n".join(parts)


def _skip_in_payload(c: dict) -> bool:
    """位置を持たない仕様適合の指摘は summary にだけ載せ、comments から落とす。"""
    return _stage(c) == "spec" and not (c.get("path") and c.get("line") is not None)


def _payload_comment(c: dict) -> dict:
    item = {"severity": c["severity"], "body": _prefixed(c)}
    for k in ("path", "line"):
        if c.get(k) is not None:
            item[k] = c[k]
    return item


def build_payload(data: dict) -> dict:
    """指摘ファイルから `review-post` へ渡す payload を組む。指摘ファイルは書き換えない（決定 9）。"""
    comments = data["comments"]
    spec = [c for c in comments if _stage(c) == "spec"]
    return {"summary": _payload_summary(data, spec), "comments": [_payload_comment(c) for c in comments if not _skip_in_payload(c)]}


def load_findings(path: Path) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"指摘ファイルが無い: {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"指摘ファイルが JSON として読めない（{e}）"]
    errs = check_findings(data)
    return (None, errs) if errs else (data, [])


# ---------------- 子のプロセス ----------------


def _child(argv: list[str], cwd: Path | None = None) -> tuple[dict, int]:
    """`step_result` の 1 行を出すスクリプトを argv の配列で起動し、`(結果, 終了コード)` を返す。"""
    p = subprocess.run([sys.executable, *map(str, argv)], cwd=cwd, capture_output=True, text=True)
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    try:
        obj = json.loads(lines[-1]) if lines else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        why = (p.stderr.strip() or p.stdout.strip())[-300:]
        return {"status": "stopped", "summary": f"{Path(str(argv[0])).name} の結果を読めない: {why}", "items": [], "metrics": {}}, (
            p.returncode or EXIT_UNREADABLE
        )
    return obj, p.returncode


# ---------------- collect ----------------


def _out_slug(repo: str | None, root: Path | None) -> str:
    return repo_lib.slug(repo or (repo_lib.owner_repo(root) if root else None) or gh_call.resolve_repo(None)) or "local"


def out_dir(a, pr: int | None, branch: str | None, root: Path | None) -> Path:
    if a.out_dir:
        d = Path(a.out_dir)
    else:
        tail = f"pr{pr}" if pr is not None else "branch-" + (branch or "HEAD").replace("/", "-")
        d = Path(tempfile.gettempdir()) / "ndf" / "pr-review" / f"{_out_slug(a.repo, root)}-{tail}"
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def pr_of_current_branch(root: Path, repo: str | None) -> int:
    """今のブランチを head とする開いた PR（決定 14）。無ければ 3。"""
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    args = ["pr", "view", *([branch, "--repo", repo] if repo else []), "--json", "number,state"]
    r = gh_call.gh(args, cwd=str(root))
    try:
        d = json.loads(r.stdout) if r.returncode == 0 else {}
    except ValueError:
        d = {}
    if not isinstance(d, dict) or str(d.get("state") or "").upper() != "OPEN" or not d.get("number"):
        raise StepError(f"今のブランチ {branch or '(不明)'} に開いた PR が無い（PR 番号を渡す）", EXIT_PRECONDITION)
    return int(d["number"])


def _thread_lines(threads: list[dict]) -> str:
    if not threads:
        return "なし"
    out = []
    for t in threads:
        where = f"{t.get('path') or '(位置なし)'}:{t.get('line') if t.get('line') is not None else '-'}"
        out.append(f"- `{where}` {str(t.get('body') or '').strip()}")
    return "\n".join(out)


def collect_pr(a, root: Path, pr: int) -> tuple[dict, list[str], Path]:
    d = out_dir(a, pr, None, root)
    argv = [GH_PARTS, "pr-info", str(pr), "--with", "diff,threads", "--out-dir", d, *(["--repo", a.repo] if a.repo else [])]
    info, code = _child(argv, cwd=root)
    if code != 0 or info.get("status") != "ok":
        raise StepError(f"PR #{pr} を取得できない: {info.get('summary')}", EXIT_UNREADABLE)
    unavailable = (info.get("metrics") or {}).get("unavailable") or []
    if unavailable:
        # 重複防止なしでは進めない（I7）。差分が無ければレビューできない
        raise StepError(f"PR #{pr} の {', '.join(unavailable)} を取得できない", EXIT_UNREADABLE)
    items = info.get("items") or []
    meta = next((i for i in items if i.get("kind") == "pr"), {})
    diff_path = next((i.get("path") for i in items if i.get("kind") == "diff"), None)
    threads = [i for i in items if i.get("kind") == "thread"]
    files = DIFF_FILE.findall(Path(diff_path).read_text(encoding="utf-8")) if diff_path else []
    sections = _pr_sections(root, pr, meta, diff_path, files, threads)
    metrics = {
        "mode": "pr",
        "pr": pr,
        "head_sha": meta.get("head_sha"),
        "base_branch": meta.get("base_branch"),
        "changed_files": len(files),
        "unresolved_threads": len(threads),
    }
    return metrics, sections, d


def _pr_sections(root: Path, pr: int, meta: dict, diff_path: str | None, files: list[str], threads: list[dict]) -> list[str]:
    """PR のレビュー担当へ渡す文脈の節。取得済みの情報だけから組み、外部へアクセスしない。"""
    return [
        "## 対象\n\n"
        + "\n".join(
            [
                f"- repo: {meta.get('repo', '')}",
                f"- PR: #{pr} {meta.get('url', '')}",
                f"- 題: {meta.get('title', '')}",
                f"- head の SHA: {meta.get('head_sha', '')}",
                f"- ベースブランチ: {meta.get('base_branch', '')}",
                f"- 作業ディレクトリ: {root}",
            ]
        ),
        "## 受け入れ条件の在りか\n\nPR の本文（下にそのまま載せる）。本文にもプランにも無ければ、その不在を指摘する。\n\n"
        + (str(meta.get("body") or "").strip() or "（本文なし）"),
        f"## 差分\n\n- 差分のファイル: `{diff_path}`\n- 変更ファイル（{len(files)}）:\n" + "\n".join(f"  - `{f}`" for f in files),
        "## 未解決のスレッド\n\n" + _thread_lines(threads) + "\n\n同じ位置へ本文と同じ趣旨の指摘を出さない。同じ位置でも趣旨が違えば出す。",
    ]


def collect_branch(a, root: Path) -> tuple[dict, list[str], Path]:
    base, why = repo_lib.existing_base_branch(root)
    if base is None:
        raise StepError(f"ベースブランチを決められない: {why}", EXIT_PRECONDITION)
    f = git(root, "fetch", "origin", base, check=False)
    if f.returncode != 0:
        raise StepError(f"git fetch origin {base} が失敗: {f.stderr.strip()[:300]}", EXIT_UNREADABLE)
    ref = f"origin/{base}"
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    d = out_dir(a, None, branch, root)
    names = git(root, "diff", ref, "--name-only").stdout.strip()
    stat = git(root, "diff", ref, "--stat").stdout.rstrip()
    log = git(root, "log", f"{ref}..HEAD", "--oneline").stdout.rstrip()
    diff_path = d / "branch.diff"
    diff_path.write_text(git(root, "diff", ref).stdout, encoding="utf-8")
    files = [n for n in names.splitlines() if n.strip()]
    sections = [
        f"## 対象\n\n- ブランチ: {branch}\n- ベースブランチ: `{ref}`（{why}）\n- 作業ディレクトリ: {root}",
        "## 受け入れ条件の在りか\n\n`issues/` の実装計画か要求のコピーから取る。見つからなければ「受け入れ条件が見つからない」と書き、推測で埋めない。",
        f"## 差分\n\n- 差分のファイル: `{diff_path}`\n\n変更ファイル:\n\n```text\n{names}\n```\n\n統計:\n\n```text\n{stat}\n```\n\n"
        f"コミット履歴:\n\n```text\n{log}\n```",
    ]
    metrics = {"mode": "branch", "pr": None, "head_sha": None, "base_branch": base, "changed_files": len(files), "unresolved_threads": None}
    return metrics, sections, d


def collect(a) -> tuple[dict, Path]:
    """E1〜E4。文脈ファイルを書き、`metrics` と出力の置き場を返す。"""
    if a.branch and a.pr is not None:
        raise StepError("PR 番号と --branch は同時に渡さない", EXIT_UNREADABLE)
    root = git_root(None, "git の作業ツリーの中で呼ぶ")
    if a.branch:
        metrics, sections, d = collect_branch(a, root)
    else:
        pr = a.pr if a.pr is not None else pr_of_current_branch(root, a.repo)
        metrics, sections, d = collect_pr(a, root, pr)
    findings = d / "findings.json"
    findings.unlink(missing_ok=True)  # 前回の指摘を今回のものとして投稿しない
    if a.focus:
        sections.append(f"## 重点\n\n`{a.focus}`。該当する観点を優先し、他の観点は重大なものだけを指摘する。")
    sections.append(FINDINGS_GUIDE.format(path=findings))
    context = d / "context.md"
    context.write_text("# レビューの文脈\n\n" + "\n\n".join(s.rstrip() for s in sections) + "\n", encoding="utf-8")
    return {**metrics, "context": str(context), "findings": str(findings), "root": str(root)}, d


def cmd_collect(a) -> None:
    metrics, _ = collect(a)
    target = f"PR #{metrics['pr']}" if metrics["pr"] is not None else f"ブランチ（起点 origin/{metrics['base_branch']}）"
    emit(result(TOOL, "ok", f"{target} の文脈ファイルを書いた: {metrics['context']}", metrics=metrics), 0)


# ---------------- finish ----------------


def branch_report(data: dict, verdict: dict) -> str:
    comments = data["comments"]

    def lines(pick) -> str:
        got = [f"- {_finding_place(c)}{_prefixed(c)}" for c in comments if pick(c)]
        return "\n".join(got) or "- なし"

    by = verdict["by_severity"]
    return (
        "## レビュー結果\n\n"
        "### 概要\n\n- 件数: " + " / ".join(f"{s} {by[s]}" for s in SEVERITIES) + "\n\n"
        "### 第 1 段: 仕様適合（満たさない）\n\n" + lines(lambda c: _stage(c) == "spec") + "\n\n"
        "### 第 2 段: Issues（要修正）\n\n" + lines(lambda c: _stage(c) == "quality" and c["severity"] in BLOCKING) + "\n\n"
        "### 第 2 段: Suggestions（改善提案）\n\n"
        + lines(lambda c: _stage(c) == "quality" and c["severity"] not in BLOCKING)
        + "\n\n"
        + (f"### 総評\n\n{data['summary'].strip()}\n" if str(data.get("summary") or "").strip() else "")
    )


def _finish_branch(data: dict, verdict: dict, metrics: dict, d: Path) -> "NoReturn":  # noqa: F821
    """`--branch` の経路。報告を書き、投稿せずに終える。"""
    report = d / "report.md"
    report.write_text(branch_report(data, verdict), encoding="utf-8")
    emit(result(TOOL, "ok", f"本来の判定 {verdict['intent']}（投稿しない）: {report}", metrics={**metrics, "report": str(report)}), 0)


def _post_review(data: dict, verdict: dict, metrics: dict, pr: int, reviewer: str, d: Path, repo: str | None) -> "NoReturn":  # noqa: F821
    """PR の経路。payload と result を書き、`review-post` で投稿して終える。"""
    payload, res = d / "payload.json", d / "result.json"
    payload.write_text(json.dumps(build_payload(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    res.write_text(
        json.dumps({"event": verdict["intent"], "by_severity": verdict["by_severity"]}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    argv = [
        GH_PARTS,
        "review-post",
        "--payload",
        payload,
        "--result",
        res,
        "--pr",
        str(pr),
        "--round",
        "0",
        "--seat",
        f"pr-review-{reviewer}",
    ]
    posted, code = _child([*argv, *(["--repo", repo] if repo else [])])
    items = [i for i in posted.get("items") or [] if isinstance(i, dict)]
    review = next((i for i in items if i.get("kind") == "review"), {})
    metrics.update({"posted_as": review.get("posted_as"), "review_url": review.get("review_url"), "payload": str(payload)})
    if code != 0:
        emit(result(TOOL, "stopped", f"投稿できない（指摘ファイルと payload を残した）: {posted.get('summary')}", items, metrics), code)
    emit(result(TOOL, "ok", f"PR #{pr} へ {review.get('posted_as')} で投稿した（本来の判定 {verdict['intent']}）", items, metrics), 0)


def finish_review(findings: Path, pr: int | None, reviewer: str, d: Path, repo: str | None) -> None:
    """E8〜E9。指摘ファイルを検査し、本来の判定を決め、PR なら投稿し `--branch` なら報告を書く。"""
    data, errs = load_findings(findings)
    if data is None:
        emit(
            result(
                TOOL,
                "stopped",
                f"指摘ファイルを読めない（投稿しない）: {errs[0]}",
                [{"kind": "finding", "name": e, "result": "invalid"} for e in errs],
            ),
            EXIT_UNREADABLE,
        )
    verdict = decide_event(data["comments"])
    metrics = {**verdict, "findings": len(data["comments"]), "reviewer": reviewer}
    if pr is None:
        _finish_branch(data, verdict, metrics, d)
    _post_review(data, verdict, metrics, pr, reviewer, d, repo)


def cmd_finish(a) -> None:
    d = Path(a.out_dir) if a.out_dir else Path(a.findings).resolve().parent  # 既定は指摘ファイルの隣（collect の置き場）
    d.mkdir(parents=True, exist_ok=True)
    finish_review(Path(a.findings), None if a.branch else a.pr, a.reviewer, d, a.repo)


# ---------------- delegate ----------------


def perspectives() -> str:
    """観点の正本（SKILL.md の `## 観点`）。写しを持たない（決定 6）。"""
    text = gh_sections.get_section(SKILL_MD.read_text(encoding="utf-8"), PERSPECTIVE_HEADING)
    if not text:
        raise StepError(f"{SKILL_MD} に `{PERSPECTIVE_HEADING}` の節が無い", EXIT_UNREADABLE)
    return text


def delegate_prompt(context: str) -> str:
    return f"{DELEGATE_RULES}\n{PERSPECTIVE_HEADING}\n\n{perspectives().strip()}\n\n{context.strip()}\n"


def cmd_delegate(a) -> None:
    metrics, d = collect(a)
    prompt = d / "prompt.md"
    prompt.write_text(delegate_prompt(Path(metrics["context"]).read_text(encoding="utf-8")), encoding="utf-8")
    findings = Path(metrics["findings"])
    argv = [
        EXTERNAL_AI,
        "run",
        a.cli,
        "--phase",
        "review",
        "--prompt-file",
        prompt,
        "--output-file",
        findings,
        "--workdir",
        metrics["root"],
    ]
    argv += [*(["--timeout", str(a.timeout)] if a.timeout else []), *(["--poll", str(a.poll)] if a.poll else [])]
    ran, _ = _child(argv)
    rm = ran.get("metrics") or {}
    if rm.get("outcome") != "ok":
        item = {"kind": "cli", "name": a.cli, "result": str(rm.get("outcome") or "unknown"), "reason": str(rm.get("reason") or "")}
        emit(
            result(TOOL, "stopped", f"{a.cli} の指摘ファイルを回収できない（投稿しない）: {ran.get('summary')}", [item], metrics),
            EXIT_VIOLATION,
        )
    finish_review(findings, metrics["pr"], a.cli, d, a.repo)


# ---------------- CLI ----------------


def _target_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("pr", nargs="?", type=int)
    sp.add_argument("--branch", action="store_true", help="PR ではなく今のブランチの差分")
    sp.add_argument("--focus", default="", help="重点の観点")
    sp.add_argument("--out-dir")
    sp.add_argument("--repo")


def steps_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="pr-review-steps.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("collect", help="対象を集めて文脈ファイルを書く")
    _target_args(sp)
    sp.set_defaults(func=cmd_collect)
    sp = sub.add_parser("finish", help="指摘ファイルから判定し、投稿か報告をする")
    sp.add_argument("--findings", required=True)
    target = sp.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=int)
    target.add_argument("--branch", action="store_true")
    sp.add_argument("--reviewer", default="host", help="席の名前に入れるレビューする者（host / codex / agy）")
    sp.add_argument("--out-dir")
    sp.add_argument("--repo")
    sp.set_defaults(func=cmd_finish)
    sp = sub.add_parser("delegate", help="外部 AI にレビューさせて投稿する")
    sp.add_argument("cli", choices=DELEGATES)
    _target_args(sp)
    sp.add_argument("--timeout", type=int, help="external-ai.py run の上限（秒）。既定は工程 review の値")
    sp.add_argument("--poll", type=int, help="監視の周期（秒）。既定は external-ai.py の値")
    sp.set_defaults(func=cmd_delegate)
    return ap


def main(argv: list[str] | None = None) -> None:
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    main_with(steps_parser(), lambda _a: TOOL, argv)


if __name__ == "__main__":
    main()
