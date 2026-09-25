#!/usr/bin/env python3
"""配布の PR で重いジョブ（pytest・runtime smoke）を省けるかを差分から決める（#1055）。

省くのは次のどちらかに当たる Pull Request だけ。push では常に回す（develop で通った
という記録を、省かずに回した結果だけにするため）。

1. PR の差分（merge-base..head）が版数と説明だけ。版上げの PR（release/v<版> → develop）
2. head の first-parent の祖先 A のうち、A..head の差分が版数と説明だけで、A が develop への
   push でこの workflow を通ったものがある。本番の PR（develop → main）。base が merge-base
   から中身を足していないこと（マージの結果が head の木と同じになること）も求める

「版数と説明だけ」の判定:
- DOC_FILES と DOC_RE の文書は中身を問わない（CHANGELOG・README の更新案内・版数を持つ文書・
  配布の段が書く記録）
- MANIFEST_RE に合う JSON は、変わった行が全部 `"version"` か `"description"` の行
- それ以外のファイルが 1 つでも変われば省かない

出力: `skip=true|false` と `reason=...` を標準出力へ書き、GITHUB_OUTPUT があればそこへも足す。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Callable

DOC_FILES = {
    "CHANGELOG.md",
    "README.md",
    "AGENTS.md",
    "docs/versioning-and-distribution.md",
}
DOC_RE = re.compile(
    r"^(plugins/.+/README\.md"
    # 本番の配布の段（.ndf/release.json）が書く記録。テストもコードも読まない
    r"|docs/metrics/ndf-token-usage/[^/]+\.(md|json))$"
)
MANIFEST_RE = re.compile(
    r"^(\.claude-plugin/marketplace\.json"
    r"|plugins/.+/(\.claude-plugin/|\.codex-plugin/|dev\.agy/)?plugin\.json)$"
)
MANIFEST_LINE_RE = re.compile(r'^\s*"(version|description)"\s*:')
ANCESTOR_LIMIT = 20


def git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False)


def changed_files(root: str, a: str, b: str) -> list[str] | None:
    p = git(root, "diff", "--name-only", "--no-renames", a, b)
    if p.returncode != 0:
        return None
    return [l for l in p.stdout.splitlines() if l]


def manifest_lines_only(root: str, a: str, b: str, path: str) -> bool:
    p = git(root, "diff", "-U0", "--no-renames", a, b, "--", path)
    if p.returncode != 0:
        return False
    for line in p.stdout.splitlines():
        if line.startswith(("new file", "deleted file")):
            return False
        if line.startswith(("+++ ", "--- ", "@@", "diff ", "index ")):
            continue
        if line.startswith(("+", "-")) and not MANIFEST_LINE_RE.match(line[1:]):
            return False
    return True


def version_only(root: str, a: str, b: str) -> tuple[bool, str]:
    """a..b の差分が版数と説明だけか。(判定, 省かない理由) を返す。"""
    files = changed_files(root, a, b)
    if files is None:
        return False, f"git diff {a[:12]}..{b[:12]} が失敗"
    for f in files:
        if f in DOC_FILES or DOC_RE.match(f):
            continue
        if MANIFEST_RE.match(f) and manifest_lines_only(root, a, b, f):
            continue
        return False, f"{f} が版数と説明の外で変わった"
    return True, ""


def gh_api(path: str) -> dict:
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return json.loads(p.stdout or "{}")


def passed_on_branch(repo: str, workflow: str, sha: str, branch: str,
                     api: Callable[[str], dict] = gh_api) -> bool:
    """sha が branch への push でこの workflow を成功で通ったか。"""
    path = (f"repos/{repo}/actions/workflows/{workflow}/runs"
            f"?head_sha={sha}&event=push&branch={branch}&status=success&per_page=5")
    try:
        data = api(path)
    except (RuntimeError, ValueError):
        return False
    return any(r.get("head_sha") == sha and r.get("event") == "push"
               and r.get("head_branch") == branch and r.get("conclusion") == "success"
               for r in data.get("workflow_runs") or [])


def decide(root: str, event: str, base: str, head: str, repo: str, workflow: str,
           branch: str = "develop", api: Callable[[str], dict] = gh_api) -> tuple[bool, str]:
    if event != "pull_request":
        return False, f"{event or '不明'} のイベントでは省かない"
    mb = git(root, "merge-base", base, head)
    if mb.returncode != 0:
        return False, "merge-base が求まらない"
    ok, why = version_only(root, mb.stdout.strip(), head)
    if ok:
        return True, "差分が版数と説明だけ"
    # base が merge-base から中身を足していなければ、マージの結果は head と同じ木になる
    # （main は develop のマージしか受けないので、main のマージのコミットは develop に無くてよい）
    if git(root, "diff", "--quiet", mb.stdout.strip(), base).returncode != 0:
        return False, f"{why}（base に head の無い変更がある）"
    revs = git(root, "rev-list", "--first-parent", f"--max-count={ANCESTOR_LIMIT}", head)
    for sha in revs.stdout.split():
        if sha != head and not version_only(root, sha, head)[0]:
            break
        if passed_on_branch(repo, workflow, sha, branch, api):
            return True, f"{sha[:12]} が {branch} で通り、そこからの差分が版数と説明だけ"
    return False, f"{why}（{branch} で通った同じ中身のコミットが無い）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    ap.add_argument("--base", default="")
    ap.add_argument("--head", default="")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--workflow", required=True, help="workflow のファイル名（例: pytest.yml）")
    ap.add_argument("--branch", default="develop")
    a = ap.parse_args(argv)
    if a.event == "pull_request" and not (a.base and a.head):
        skip, reason = False, "--base と --head が無い"
    else:
        skip, reason = decide(a.root, a.event, a.base, a.head, a.repo, a.workflow, a.branch)
    out = f"skip={'true' if skip else 'false'}\nreason={reason}\n"
    sys.stdout.write(out)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
