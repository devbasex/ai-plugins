"""CI と GitHub から測る設定の項目（CI の必須のチェック。#3569）。

**`gh` へ渡すのは読む要求だけである。** `gh api` は `--method GET` を付けて打つ。
`gh` の 1 回の呼び出しは、締め切りまでの残りと 30 秒の小さい方で打ち切る。締め切りに届いた後は起動しない。
呼ぶのは必須のチェック（`rules/branches`）の 1 か所だけで、ほかの値は読み手が無いので取りに行かない（#4516・#4521）。
外部の語（ruleset）は、ここで設定の語（必須のチェック）へ直す。
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import cli_floor
from ndf.domains.project.items import measured, obj, seq, unknown
from ndf.domains.project.measure_repo import Tree, call_limit

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
    """既定ブランチ `branch` の必須のチェック。既定ブランチが分からない・取れなければ `GhUnavailable`（空と読み違えて既存の値を消さない）。"""
    if not branch:
        raise GhUnavailable("リモートの既定ブランチが分からない（refs/remotes/<リモート>/HEAD が無い）")
    return required_contexts(gh.get(f"repos/{gh.repo}/rules/branches/{branch}"))


def measure(tree: Tree, repo: Optional[str], head: Optional[str], deadline: float) -> dict[str, Any]:
    """必須のチェックを測る。返すのは `{"ci", "notes"}`。

    `gh` を呼ぶのは `rules/branches/<head>` の 1 回だけで、`head` が無ければ呼ばずに `ci` を不明にする。
    """
    if not repo:
        return {"ci": unknown("origin が GitHub のリポジトリを指していない"), "notes": []}
    try:
        return {"ci": measured({"required_checks": _required_checks(Gh(tree.root, repo, deadline), head)}), "notes": []}
    except GhUnavailable as exc:
        return {"ci": unknown(str(exc)), "notes": [f"CI を測れない: {exc}"]}
