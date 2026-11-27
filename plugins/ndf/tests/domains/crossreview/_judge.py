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
    """判定役の起動か（引数が判定役の system の文を持つ）。"""
    return criteria_decide.verify_text()["system"] in spec.argv


def listed_rows(spec: agents.Launch) -> list[dict]:
    """指示が指す指摘の一覧のファイルの行（ファイルは実行の終わりに消えるので、起動の中で読む）。"""
    [path] = re.findall(r"^指摘の一覧のファイル: (.+)$", spec.argv[-1], re.MULTILINE)
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def materials(spec: agents.Launch) -> list[str]:
    """指示が指す、指摘の一覧と差分のファイル。"""
    prompt = spec.argv[-1]
    listed = re.findall(r"^指摘の一覧のファイル: (.+)$", prompt, re.MULTILINE)
    [diffs] = re.findall(r"^(?:このラウンドの差分|差分): (.+?)(?:（.*）)?$", prompt, re.MULTILINE)
    return [*listed, *diffs.split("・")]


def output(body: dict, read: list[str]) -> str:
    """ファイル `read` を `Read` してから結果 `body` を返した、claude の 1 歩ごとの行の出力。"""
    rows: list[dict] = []
    for n, path in enumerate(read):
        use = {"type": "tool_use", "id": f"t{n}", "name": "Read", "input": {"file_path": path}}
        done = {"type": "tool_result", "tool_use_id": f"t{n}", "content": "読んだ中身"}
        rows += [{"type": "assistant", "message": {"content": [use]}}, {"type": "user", "message": {"content": [done]}}]
    rows.append({"type": "result", **body})
    return "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)


def holding(row: dict) -> dict:
    """起きると示せた回答。"""
    return {"id": row["id"], "holds": True, "condition": "利用者が使う", "path": "doc.md:1", "result": "止まる", "reason": "", "tracks": []}


def answer(spec: agents.Launch, verdict: Callable[[dict], Optional[dict]]) -> agents.AgentStatus:
    """指示に載った指摘ごとに `verdict` の返す回答（None は回答しない）を、claude の出力の形で書いて完了にする。"""
    rows = listed_rows(spec)
    body = json.dumps({"verdicts": [v for row in rows if (v := verdict(row)) is not None]}, ensure_ascii=False)
    spec.paths.out.parent.mkdir(parents=True, exist_ok=True)
    spec.paths.out.write_text(output({"result": body, "permission_denials": []}, materials(spec)), encoding="utf-8")
    return agents.AgentStatus(spec.agent, agents.AgentState.DONE, 0, "", ())
