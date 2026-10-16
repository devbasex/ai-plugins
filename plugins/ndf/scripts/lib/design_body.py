"""設計 PR の本文の「決めたこと」を設計文書の決定の見出しへ揃える（`pr-body-decisions.sh sync`）。

`fix-steps.py finalize`（送るコミットが無いとき）と、修正を送る側（`result_posts.py fix` /
`state.py merge-fix` / `state.py record-fix`）が同じ呼び方を使う。終了コードの意味はスクリプトの冒頭が正本。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[1] / "pr-body-decisions.sh"
LABELS = {0: "synced", 1: "mismatch", 2: "unreadable", 3: "invalid_call"}


def sync_decisions(pr: int, repo: str | None = None, script: str | Path | None = None, after_push: str | None = None) -> dict[str, Any]:
    """揃えて、`{"name", "result", "code", "reason"}` を返す。スクリプトが無ければ `missing`。

    `after_push` は送ったコミット。渡すと、スクリプトが PR の head がそこへ届くまで上限の内で待ち、
    書いた後の読み直しも上限の内で繰り返す（#1644）。
    """
    path = Path(script) if script else SCRIPT
    if not path.is_file():
        return {"name": "pr-body-decisions", "result": "missing", "code": None, "reason": f"無い: {path}"}
    cmd = ["bash", str(path), "sync", str(pr)] + (["--repo", repo] if repo else [])
    if after_push:
        cmd += ["--after-push", after_push]
    p = subprocess.run(cmd, capture_output=True, text=True)
    label = LABELS.get(p.returncode, "failed")
    return {"name": "pr-body-decisions", "result": label, "code": p.returncode, "reason": (p.stderr.strip() or p.stdout.strip())[:300]}


def sync_after_push(repo: str, pr: int, head_branch: str, sent: str) -> str | None:
    """修正コミットを送った後に揃える。揃わなかったときだけ理由を返す。

    `sent` は送ったコミット（送っていなければ空）。**送る前に揃えない。** スクリプトは設計文書を PR の
    head のコミットから読むため、送る前に揃えると修正で変えた見出しが本文へ載らない。PR の head が
    `sent` へ届くまでの待ちはスクリプトが持ち、呼び出し元には書かない。対象の判定（head が `design/`）も
    スクリプトが持ち、ここで先に見るのは対象外の PR で GitHub を読まないためだけである。
    """
    if not (sent and str(head_branch or "").startswith("design/")):
        return None
    got = sync_decisions(pr, repo or None, after_push=sent)
    return None if got["result"] == "synced" else f"pr-body-decisions.sh sync が {got['result']}（{got['code']}）: {got['reason']}"
