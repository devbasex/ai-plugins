"""承認の質問の回答を記録する guard（`launch-answer`。#1959 の決定 7）。止めない。

`AskUserQuestion` の PostToolUse で、質問の中身（`questions`）のハッシュと会話の id が `ndf launch-config show --question approve` の
残したリクエストと一致し、回答が質問の示した選択肢のどれかと同じときだけ、リクエストの識別子・選んだラベル・質問の id を
`launch-answers.jsonl` へ追記する。選択肢に無い回答（自由入力の補足や訂正）は記録しない。回答を人でなく AI が返しうる会話
（NDF が起動した AI・人のいない実行・オーガナイザーが質問を受けうる会話）でも書かない。回答の照らし合わせと承認の行は
`ndf.adapters.launch_approval` が持つ。記録の読み書き（所有者だけの普通のファイルの確かめ・書きかけの末尾の閉じ方）と
質問のハッシュは、承認のコマンドと共有する `ndf.adapters.launch_records` を使う（hook の import の表に収まる）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from ndf.adapters import launch_records as records
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
    if not isinstance(label, str) or label not in records.option_labels(questions):
        return passing()
    request = _request(svc.state_dir / _state_path("launch-questions"), records.canonical_sha(questions), ctx.session_id)
    if request is None:
        return passing()
    row = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "request_id": request,
        "label": label,
        "question_id": ctx.payload.get("tool_use_id"),
    }
    try:
        # 錠を取らない（1 回の write の追記は混ざらない）。錠の待ちの上限で、利用者の選んだ回答を捨てないためである
        records.write_row(svc.state_dir / _state_path("launch-answers"), row)
    except OSError:  # ほかのユーザーが読める記録・書けない記録には書かずに通す（hook は止めない）
        return passing()
    return passing(launch_answer="recorded")


def _state_path(file_id: str) -> str:
    return next(row["path"] for row in tables.load("domains/ndf-files.json")["rows"] if row["id"] == file_id)


def _request(path: Path, sha: str, session: str) -> Optional[str]:
    """同じ質問の中身と会話の id を持つ最後のリクエストの識別子。所有者だけの普通のファイルでなければ無いと同じ。"""
    found = None
    for row in records.whole_rows(path):
        if row.get("question_sha") == sha and row.get("session") == session:
            found = row.get("request_id")
    return found if isinstance(found, str) else None
