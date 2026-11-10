#!/usr/bin/env python3
"""配布の PR と文書だけの PR で重いジョブ（pytest・runtime smoke）を省けるかを差分から決める（#1055・#2925）。

省くのは次のどれかに当たる Pull Request だけ。push では常に回す（develop で通った
という記録を、省かずに回した結果だけにするため）。

1. PR の差分（merge-base..head）が版数と説明だけ。版上げの PR（release/v<版> → develop）
2. PR の差分が文書のファイルだけ（NDF の差分の分類 `lib/diff_kinds.py`。宣言は `.ndf/project.json` の `docs`）。
   テストが読む文書があれば `docs_tests` に対応するテストを出し、文書のテストのジョブが走らせる
3. head の first-parent の祖先 A のうち、A..head の差分が版数と説明だけで、A が develop への
   push でこの workflow を通ったものがある。本番の PR（develop → main）。base が merge-base
   から中身を足していないこと（マージの結果が head の木と同じになること）も求める

「版数と説明だけ」の判定:
- 文書のファイルのうちテストが読まないものは中身を問わない。行数の上限・リンク・古さは
  runtime-plugin-validate.yml が PR ごとに必ず確かめるため、ここで省いても検査は抜けない
- MANIFEST_RE に合う JSON は、変わった行が全部 `"version"` か `"description"` の行
- それ以外のファイルが 1 つでも変われば省かない

`BEHAVIOR_AS_CODE` の workflow（runtime smoke）は、振る舞いを決める文書（Skill の本文など）をコードとして扱う。
frontmatter がランタイムの読み込みを変えるため（#2925 の決定 8）。差分の一覧か分け方の宣言が読めなければ省かない。

出力: `skip=true|false`・`reason=...`・`docs_tests=...`（空白区切り）を標準出力へ書き、GITHUB_OUTPUT があればそこへも足す。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

require("pathmatch")
import diff_kinds  # noqa: E402

BEHAVIOR_AS_CODE = {"runtime-plugin-smoke.yml"}
MANIFEST_RE = re.compile(
    r"^(\.claude-plugin/marketplace\.json"
    r"|plugins/.+/(\.claude-plugin/|\.codex-plugin/)?plugin\.json)$"
)
MANIFEST_LINE_RE = re.compile(r'^\s*"(version|description)"\s*:')
ANCESTOR_LIMIT = 20


def git(root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=False)


class Decision(NamedTuple):
    skip: bool
    reason: str
    docs_tests: tuple[str, ...] = ()


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


def kinds_of(root: str, a: str, b: str, behavior_as_code: bool = False) -> diff_kinds.DiffKinds:
    """a..b の差分の分類。`behavior_as_code` なら振る舞いを決める文書をコードへ移す。"""
    files = diff_kinds.git_changed(root, f"{a}..{b}")
    if files is None:
        return diff_kinds.classify_files(None, None, f"git diff {a[:12]}..{b[:12]} が失敗")
    k = diff_kinds.classify_root(root, files)
    if behavior_as_code and k.behavior:
        k.code = [f for f in files if f in k.code or f in k.behavior]
        k.docs = [f for f in k.docs if f not in k.behavior]
    return k


def version_only(root: str, a: str, b: str, behavior_as_code: bool = False) -> tuple[bool, str]:
    """a..b の差分が版数と説明だけか。(判定, 省かない理由) を返す。"""
    k = kinds_of(root, a, b, behavior_as_code)
    if k.unknown:
        return False, k.unknown
    untested = set(k.docs) - set(k.tested)
    for f in k.docs + k.code:
        if f in untested:
            continue
        if MANIFEST_RE.match(f) and manifest_lines_only(root, a, b, f):
            continue
        return False, f"{f} が版数・説明・テストの読まない文書の外で変わった"
    return True, ""


def gh_api(path: str) -> dict:
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:300])
    return json.loads(p.stdout or "{}")


def passed_on_branch(repo: str, workflow: str, sha: str, branch: str, api: Callable[[str], dict] = gh_api) -> bool:
    """sha が branch への push でこの workflow を成功で通ったか。"""
    path = f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&event=push&branch={branch}&status=success&per_page=5"
    try:
        data = api(path)
    except (RuntimeError, ValueError):
        return False
    return any(
        r.get("head_sha") == sha and r.get("event") == "push" and r.get("head_branch") == branch and r.get("conclusion") == "success"
        for r in data.get("workflow_runs") or []
    )


def decide(
    root: str, event: str, base: str, head: str, repo: str, workflow: str, branch: str = "develop", api: Callable[[str], dict] = gh_api
) -> Decision:
    if event != "pull_request":
        return Decision(False, f"{event or '不明'} のイベントでは省かない")
    mb = git(root, "merge-base", base, head)
    if mb.returncode != 0:
        return Decision(False, "merge-base が求まらない")
    as_code = workflow in BEHAVIOR_AS_CODE
    k = kinds_of(root, mb.stdout.strip(), head, as_code)
    if k.docs_only():
        tests = () if as_code else tuple(k.tests)
        return Decision(True, f"差分が文書のファイルだけ（文書 {len(k.docs)} ファイル・テストが読む文書 {len(k.tested)} ファイル）", tests)
    ok, why = version_only(root, mb.stdout.strip(), head, as_code)
    if ok:
        return Decision(True, "差分が版数と説明だけ")
    # base が merge-base から中身を足していなければ、マージの結果は head と同じ木になる
    # （main は develop のマージしか受けないので、main のマージのコミットは develop に無くてよい）
    if git(root, "diff", "--quiet", mb.stdout.strip(), base).returncode != 0:
        return Decision(False, f"{why}（base に head の無い変更がある）")
    revs = git(root, "rev-list", "--first-parent", f"--max-count={ANCESTOR_LIMIT}", head)
    for sha in revs.stdout.split():
        if sha != head and not version_only(root, sha, head, as_code)[0]:
            break
        if passed_on_branch(repo, workflow, sha, branch, api):
            return Decision(True, f"{sha[:12]} が {branch} で通り、そこからの差分が版数と説明だけ")
    return Decision(False, f"{why}（{branch} で通った同じ中身のコミットが無い）")


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
        d = Decision(False, "--base と --head が無い")
    else:
        d = decide(a.root, a.event, a.base, a.head, a.repo, a.workflow, a.branch)
    out = f"skip={'true' if d.skip else 'false'}\nreason={d.reason}\ndocs_tests={' '.join(d.docs_tests)}\n"
    sys.stdout.write(out)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
