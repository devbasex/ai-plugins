"""mvv_gate_pr.py: `mvv-gate.py` の材料にする Pull Request の事実と設計文書の取得（#1078）。取れなければ `Back` を投げる。"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import gh_call
import gh_rest


class Back(Exception):
    """承認ゲートへ戻す（利用者の承認を求める）理由。"""


def gh_or_back(args: list[str], repo: str | None, cwd: Path) -> str:
    """`gh` を 1 回呼び（`lib/gh_call.py`）、標準出力を返す。失敗は Back。"""
    r = gh_call.gh([*args] + (["--repo", repo] if repo else []), cwd=str(cwd))
    if r.returncode == 127:
        raise Back("gh が無い")
    if r.returncode != 0:
        raise Back(f"gh {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def pr_facts(n: int, repo: str | None, cwd: Path) -> dict:
    """PR の題名・本文・先頭のコミットと、変更したファイルの全件（rename の前のパスを含む）。取れなければ Back。

    REST の `pulls/<n>/files` は全ページでも 3000 件で黙って切れるため、取れた件数が `changedFiles` と
    合わなければ全件を確かめられないとして Back にする。
    """
    r = gh_rest.view_json("pr", n, "title,body,headRefOid,changedFiles", repo, cwd=str(cwd))
    if r.returncode != 0:
        raise Back("gh が無い" if r.returncode == 127 else f"gh pr view {n}: {r.stderr.strip()[:300]}")
    try:
        info = json.loads(r.stdout)
    except ValueError:
        raise Back(f"PR #{n} の出力を読めない")
    f = gh_rest.pr_files(n, repo, cwd=str(cwd))
    if f.returncode != 0:
        raise Back("gh が無い" if f.returncode == 127 else f"PR #{n} の変更したファイルを読めない: {f.stderr.strip()[:300]}")
    files = json.loads(f.stdout)
    changed = info.get("changedFiles") if isinstance(info, dict) else None
    if not isinstance(changed, int) or isinstance(changed, bool) or changed != len(files):
        raise Back(f"PR #{n} の変更したファイルを全件読めない（取れた {len(files)} 件 / changedFiles {changed}）")
    return {**info, "files": files}


def pr_material(n: int, info: dict) -> str:
    files = "\n".join(f"- {f['path']} (+{f.get('additions', 0)} -{f.get('deletions', 0)})" for f in info.get("files", []))
    return f"## Pull Request #{n}: {info.get('title', '')}\n\n{info.get('body') or ''}\n\n### 変更したファイル\n{files}"


def design_docs(info: dict) -> list[str]:
    """PR が足したか直した `issues/` の設計文書のパス（消しただけのものは読めないので除く）。"""
    return [
        f["path"]
        for f in info.get("files", [])
        if f.get("path", "").startswith("issues/")
        and f["path"].endswith(".md")
        and "design" in Path(f["path"]).name
        and not (f.get("additions", 0) == 0 and f.get("deletions", 0) > 0)
    ]


def design_material(n: int, path: str, info: dict, repo: str | None, cwd: Path) -> str:
    """PR の先頭のコミットの設計文書の中身を材料の 1 件にする。取れなければ Back。"""
    ref = info.get("headRefOid") or ""
    if not ref:
        raise Back(f"PR #{n} の先頭のコミットが分からず、設計文書 {path} を読めない")
    endpoint = f"repos/{repo or '{owner}/{repo}'}/contents/{urllib.parse.quote(path)}?ref={ref}"
    try:
        text = gh_or_back(["api", "-H", "Accept: application/vnd.github.raw", endpoint], None, cwd)
    except Back as e:
        raise Back(f"PR #{n} の設計文書 {path} を読めない: {e}")
    return f"## 設計文書 {path}（PR #{n}）\n\n{text}"
