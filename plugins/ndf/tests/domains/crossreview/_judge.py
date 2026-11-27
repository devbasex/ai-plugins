"""偽の判定役（#4363）。`ndf cross-review run` を偽の起動で通すテストが使う。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from ndf.adapters import agents
from ndf.domains.crossreview import criteria_decide


def is_judge(spec: agents.Launch) -> bool:
    """判定役の起動か（指示が判定役の system の文を持つ）。"""
    return criteria_decide.verify_text()["system"] in spec.argv[-1]


def listed_rows(spec: agents.Launch) -> list[dict]:
    """指示が指す指摘の一覧のファイルの行（ファイルは実行の終わりに消えるので、起動の中で読む）。"""
    [path] = re.findall(r"^指摘の一覧のファイル: (.+)$", spec.argv[-1], re.MULTILINE)
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def holding(row: dict) -> dict:
    """起きると示せた回答。"""
    return {"id": row["id"], "holds": True, "condition": "利用者が使う", "path": "doc.md:1", "result": "止まる", "reason": "", "tracks": []}


def answer(spec: agents.Launch, verdict: Callable[[dict], Optional[dict]]) -> agents.AgentStatus:
    """指示に載った指摘ごとに `verdict` の返す回答（None は回答しない）を、claude の出力の形で書いて完了にする。"""
    rows = listed_rows(spec)
    body = json.dumps({"verdicts": [v for row in rows if (v := verdict(row)) is not None]}, ensure_ascii=False)
    spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
    spec.paths.out.write_text(json.dumps({"result": body}, ensure_ascii=False), encoding="utf-8")
    return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())
