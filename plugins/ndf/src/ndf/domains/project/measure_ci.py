"""CI と GitHub から測る設定の項目（P4 の必須のチェック・P6 の PR のマージ先・P8 の GitHub の分。#3569）。

**`gh` へ渡すのは読む要求だけである。** `gh api` は `--method GET` を付けて打つ。
`gh` の 1 回の呼び出しは、締め切りまでの残りと 30 秒の小さい方で打ち切る。締め切りに届いた後は起動しない。
呼ぶのは必須のチェック（`rules/branches`）・閉じた PR（`pulls`）・課題（`issues`）の 3 か所だけで、CI の実行・ジョブ・
成果物は読み手が無いので取りに行かない（#4516 の決定 3）。外部の語（ruleset）は、ここで設定の語（必須のチェック）へ直す。
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import cli_floor
from ndf.domains.project.items import measured, obj, question, seq, unknown
from ndf.domains.project.measure_repo import Tree, call_limit, url_hosts

TIME_UP = "時間切れ"


class GhUnavailable(Exception):
    """`gh` が使えない（無い・未認証・ネットワークが無い・時間切れ）。理由を持つ。"""


class Gh:
    """締め切りを持つ、読むだけの `gh api` の呼び出し。"""

    def __init__(
        self,
        root: Path,
        repo: str,
        deadline: float,
        *,
        clock: Optional[Callable[[], float]] = None,
        run: Optional[Callable[..., subprocess.CompletedProcess[bytes]]] = None,
    ) -> None:
        """`clock` は締め切りと比べる時計、`run` は `gh` を起動する関数。省略すれば呼ぶ時点の `time.monotonic`・`subprocess.run`。"""
        self.root, self.repo, self.deadline = root, repo, deadline
        self.clock, self.run = clock, run

    def raw(self, path: str) -> bytes:
        left = self.deadline - (self.clock or time.monotonic)()
        if left <= 0:
            raise GhUnavailable(TIME_UP)
        try:
            cli_floor.refuse_old("gh", "gh", GhUnavailable, None, self.root)
            done = (self.run or subprocess.run)(
                ["gh", "api", "--method", "GET", path], cwd=str(self.root), capture_output=True, timeout=min(call_limit(), left)
            )
        except FileNotFoundError:
            raise GhUnavailable("gh が無い") from None
        except subprocess.TimeoutExpired:
            raise GhUnavailable(TIME_UP) from None
        except OSError as exc:
            raise GhUnavailable(f"gh を起動できない: {exc}") from None
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise GhUnavailable(f"gh が失敗した: {(err if err else ['終了コード ' + str(done.returncode)])[-1][:200]}")
        return done.stdout

    def get(self, path: str) -> Any:
        body = self.raw(path).decode("utf-8")
        try:
            return json.loads(body) if body else None
        except ValueError:
            raise GhUnavailable(f"gh の出力を読めない: {path}") from None


def required_contexts(rules: Any) -> list[str]:
    """`rules/branches/<branch>` の応答（規則の配列）→ 必須のチェックの名前（重複なく整列）。"""
    out = {
        c["context"]
        for r in seq(rules)
        if isinstance(r, dict) and r.get("type") == "required_status_checks"
        for c in seq(obj(r.get("parameters")).get("required_status_checks"))
        if isinstance(c, dict) and c.get("context")
    }
    return sorted(out)


def _required_checks(gh: Gh, branch: Optional[str]) -> list[str]:
    """既定ブランチ `branch` の必須のチェック。取れなければ `GhUnavailable`（未認証などで空と読み違えて既存の値を消さない）。"""
    if not branch:
        return []
    return required_contexts(gh.get(f"repos/{gh.repo}/rules/branches/{branch}"))


def _measure_merges(gh: Gh) -> tuple[dict[str, int], list[str]]:
    """閉じた PR のマージ先別のマージ数と、本文に出たホスト名。"""
    pulls = [p for p in seq(gh.get(f"repos/{gh.repo}/pulls?state=closed&per_page=100")) if isinstance(p, dict)]
    merges: dict[str, int] = {}
    for pr in pulls:
        if pr.get("merged_at"):
            ref = obj(pr.get("base")).get("ref")
            merges[ref] = merges[ref] + 1 if ref in merges else 1
    hosts = {h for pr in pulls for h in url_hosts(str(pr["body"]) if pr.get("body") else "")}
    return merges, sorted(hosts)


def measure(tree: Tree, repo: Optional[str], head: Optional[str], deadline: float) -> dict[str, Any]:
    """P4 と、P6・P8 の GitHub の分を測る。返すのは `{"ci", "merges", "github_issues", "hosts", "notes"}`。

    `gh` を呼ぶのは `rules/branches/<head>`（`head` があるとき）・閉じた PR・課題の 3 回だけである。
    """
    out: dict[str, Any] = {"merges": None, "github_issues": None, "hosts": [], "notes": []}
    if not repo:
        return {**out, "ci": unknown("origin が GitHub のリポジトリを指していない")}
    gh = Gh(tree.root, repo, deadline)
    try:
        out["ci"] = measured({"required_checks": _required_checks(gh, head)})
    except GhUnavailable as exc:
        out["ci"] = unknown(str(exc))
        out["notes"].append(f"CI を測れない: {exc}")
    try:
        out["merges"], out["hosts"] = _measure_merges(gh)
        issues = seq(gh.get(f"repos/{repo}/issues?state=all&per_page=100"))
        out["github_issues"] = sum(1 for i in issues if isinstance(i, dict) and "pull_request" not in i)
    except GhUnavailable as exc:
        out["notes"].append(f"PR のマージ先と GitHub Issues を測れない: {exc}")
    return out


def measure_issues(repo_part: dict[str, Any], ci_part: dict[str, Any]) -> dict[str, Any]:
    """P8 の質問。課題の SSOT の候補と根拠（名前とホスト名だけ。URL は持たない）。"""
    hosts = sorted(set(repo_part["hosts"]) | set(seq(ci_part.get("hosts"))))
    found = {
        "markdown": bool(repo_part["markdown_files"]),
        "github": bool(ci_part.get("github_issues")),
        "redmine": any("redmine" in h for h in hosts),
        "external": any("redmine" not in h for h in hosts),
    }
    candidates = [k for k, hit in found.items() if hit]
    issues = ci_part["github_issues"] if ci_part.get("github_issues") is not None else "不明"
    evidence = [
        {"text": f"issues/*.md: {repo_part['markdown_files']} 本"},
        {"text": f"GitHub Issues（直近 100 件のうち）: {issues}"},
        {"text": f"PR の本文とテンプレートのホスト名: {'・'.join(hosts) if hosts else '無し'}"},
    ]
    if ci_part.get("merges") is None and not candidates:
        return unknown("課題の保存先を測れない")
    return question(candidates, evidence)
