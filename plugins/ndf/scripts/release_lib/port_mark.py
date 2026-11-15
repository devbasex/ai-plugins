"""リリースの PR の本文に ndf-next の port 検査（#3343）の印を持たせる（#3573。`release_lib/reviewed.py` から分けた）。

- `NO_PORT_LINE`: 本文へ入れる行。リリースの PR を新しく作るときは本文の組み立てに入れる
- `ensure_no_port_line`: 使い回す PR（前の版のスクリプトが作った PR）の本文に印が無ければ足し、打ち直しを待つ
"""

from __future__ import annotations

import json

import gh_parts
import limits
import waits
from step_result import StepError, gh_json

# 旧いプラグインを変える PR に ndf-next の検査（規則 port。#3343）が求める行。印は plugins/ndf-next/src/ndf/checks/rules.json の
# no_port_mark。リリースの PR は版数と CHANGELOG を上げて取り込み済みの PR を運ぶだけで、移す変更は元の PR が持つ
NO_PORT_LINE = "移さない理由（ndf-next）: リリースの PR は版数と CHANGELOG を上げて取り込み済みの PR を運ぶだけで、移す変更は元の PR が持つ"

_FAILED_BUCKETS = ("fail", "cancel")


def _failed_checks(root, n) -> set[str]:
    """PR のチェックのうち、今落ちている（fail / cancel）ものの名前。読めなければ空。"""
    p = gh_parts.gh(["pr", "checks", n, "--json", "name,bucket"], cwd=root)
    try:
        rows = json.loads(p.stdout or "[]") or []
    except (TypeError, ValueError):
        return set()
    return {str(r.get("name")) for r in rows if isinstance(r, dict) and r.get("bucket") in _FAILED_BUCKETS}


def ensure_no_port_line(root, pr):
    """使い回す PR（前の版のスクリプトが作った -sync の PR など）の本文に NO_PORT_LINE が無ければ、本文の末尾へ足して pr を返す。
    本文のほかの行（解いたパスなど）は残す。本文の編集（edited）で ndf-next の port 検査が打ち直される（#3343）。
    編集の前に落ちていたチェックは、打ち直しが登録されて落ちた状態から外れるまで待つ（上限は `release-steps.port-rerun`）。
    待たずに戻ると、続く CI の待ちが打ち直す前の失敗を読んで止まる。上限まで外れなければ、そのまま CI の待ちに判定を任せる。"""
    n = str(pr["number"]) if pr else ""
    body = ((gh_json(root, ["pr", "view", n, "--json", "body"], f"gh pr view #{n}") or {}).get("body") or "") if pr else NO_PORT_LINE
    if NO_PORT_LINE not in body:
        before = _failed_checks(root, n)
        p = gh_parts.gh(["pr", "edit", n, "--body-file", "-"], f"{body.rstrip()}\n\n{NO_PORT_LINE}\n", cwd=root)
        if p.returncode != 0:
            raise StepError(f"gh pr edit #{n}（本文の書き直し）が失敗: {p.stderr.strip()[:300]}")
        if before:
            waits.wait_until(
                lambda: _failed_checks(root, n), lambda now: not (now & before), policy=limits.policy("release-steps.port-rerun")
            )
    return pr
