"""承認の質問の回答を記録する guard（`launch-answer`。#1959 の決定 7）。止めない。

`AskUserQuestion` の PostToolUse で、質問の中身（`questions`）のハッシュと会話の id が `ndf launch-config show --question approve` の
残したリクエストと一致するときだけ、リクエストの識別子・選んだラベル・質問の id を `launch-answers.jsonl` へ追記する。回答を人でなく AI が
返しうる会話（NDF が起動した AI・人のいない実行・オーガナイザーが質問を受けうる会話）では書かない。回答の照らし合わせと承認の行は
`ndf.adapters.launch_approval` が持つ。hook の import の表（`ndf.contracts` を読まない）に収めるため、そのモジュールは読まず、
同じ形（キーの順にそろえた JSON の SHA-256、所有者だけが読める追記）をここで書く。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from ndf.domains.hooks import tables
from ndf.domains.hooks.verdict import Verdict, passing


def unattended(env) -> bool:
    """回答を人でなく AI が返しうる会話か（`launch-config show` も同じ判定で質問を作らない）。"""
    return any(env.get(name) for name in ("NDF_AI_CHILD", "NDF_FACTORY_RUN", "NDF_RELAY_PLAN", "NDF_ORGANIZER_DIR"))


def check(ctx, svc) -> Verdict:
    if unattended(ctx.env):
        return passing()
    questions = ctx.tool_input.get("questions")
    response = ctx.payload.get("tool_response")
    answers = response.get("answers") if isinstance(response, dict) else None
    if not (isinstance(questions, list) and questions and isinstance(questions[0], dict) and isinstance(answers, dict)):
        return passing()
    label = answers.get(questions[0].get("question"))
    sha = hashlib.sha256(json.dumps(questions, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    request = _request(svc.state_dir / _state_path("launch-questions"), sha, ctx.session_id)
    if request is None or not isinstance(label, str):
        return passing()
    row = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "request_id": request,
        "label": label,
        "question_id": ctx.payload.get("tool_use_id"),
    }
    _append_answer(svc.state_dir / _state_path("launch-answers"), row)
    return passing(launch_answer="recorded")


def _state_path(file_id: str) -> str:
    return next(row["path"] for row in tables.load("domains/ndf-files.json")["rows"] if row["id"] == file_id)


def _request(path: Path, sha: str, session: str) -> Optional[str]:
    """同じ質問の中身と会話の id を持つ最後のリクエストの識別子。"""
    found = None
    for row in _request_rows(path):
        if row.get("question_sha") == sha and row.get("session") == session:
            found = row.get("request_id")
    return found if isinstance(found, str) else None


def _request_rows(path: Path) -> list[dict[str, Any]]:
    """改行で終わる行の JSON のオブジェクト。読めないファイルは行が無いと同じ。"""
    try:
        lines = path.read_bytes().split(b"\n")[:-1]
    except OSError:  # リクエストの記録が無い・読めない会話は、記録する回答が無い
        return []
    return [row for row in map(tables.json_object, (line.decode("utf-8", "replace") for line in lines)) if row is not None]


def _append_answer(path: Path, row: dict[str, Any]) -> None:
    """所有者だけが読める記録へ 1 行足す。ほかのユーザーが読めるファイルには書かない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "ab") as f:
        st = os.fstat(f.fileno())
        if st.st_uid == os.getuid() and not st.st_mode & 0o077:
            f.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
