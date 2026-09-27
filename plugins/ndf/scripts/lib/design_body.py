"""設計 PR の本文の「決めたこと」を設計文書の決定の見出しへ揃える（`pr-body-decisions.sh sync`）。

`fix-steps.py finalize`（送るコミットが無いとき）と、修正を送る側（`result_posts.py fix` /
`state.py merge-fix`）が同じ呼び方を使う。終了コードの意味はスクリプトの冒頭が正本。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "pr-body-decisions.sh"
LABELS = {0: "synced", 1: "mismatch", 2: "unreadable", 3: "invalid_call"}


def sync(pr: int, repo: str | None = None, script: str | Path | None = None) -> dict[str, Any]:
    """揃えて、`{"name", "result", "code", "reason"}` を返す。スクリプトが無ければ `missing`。"""
    path = Path(script) if script else SCRIPT
    if not path.is_file():
        return {"name": "pr-body-decisions", "result": "missing", "code": None, "reason": f"無い: {path}"}
    cmd = ["bash", str(path), "sync", str(pr)] + (["--repo", repo] if repo else [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    label = LABELS.get(p.returncode, "failed")
    return {"name": "pr-body-decisions", "result": label, "code": p.returncode, "reason": (p.stderr.strip() or p.stdout.strip())[:300]}


def sync_after_push(repo: str, pr: int, head_branch: str, pushed: bool) -> str | None:
    """修正コミットを送った後に揃える。揃わなかったときだけ理由を返す。

    **送る前に揃えない。** スクリプトは設計文書を PR の head のコミットから読むため、送る前に揃えると
    修正で変えた見出しが本文へ載らない。対象の判定（head が `design/`）はスクリプトが持ち、ここで
    先に見るのは対象外の PR で GitHub を読まないためだけである。
    """
    if not (pushed and str(head_branch or "").startswith("design/")):
        return None
    got = sync(pr, repo or None)
    return None if got["result"] == "synced" else f"pr-body-decisions.sh sync が {got['result']}（{got['code']}）: {got['reason']}"
