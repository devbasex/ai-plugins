"""Neural Development Factory のランナーの中の起票の依頼（#2216）。`issue-file.py create` から呼ぶ。

ランナーは環境変数 `NDF_ISSUE_REQUEST_FILE`（作業のディレクトリの外のファイル）を conductor へ渡す。この変数があり、
起票先が開発対象リポジトリと別のときは、承認資料を書かずにそのファイルへ 1 行（`{"repo", "title", "body"}` の JSON）を
足し、0（`items[].result` は `requested`）で終える。ランナーがその行を進捗の事象 `issue.requested` にして送り、
起票するかはサービスが判定し、起票しない先は下書きを人へ回す。ランナーの外では変数が無いため、今どおり承認資料で止まる。
"""

from __future__ import annotations

import json
import os

from step_result import EXIT_OK, EXIT_VIOLATION, emit, result

ENV = "NDF_ISSUE_REQUEST_FILE"


def request_if_runner(tool: str, repo: str, title: str, body: str, other: bool) -> bool:
    """ランナーの中で別のリポジトリ宛てなら依頼の 1 行を足して終える。そうでなければ `other` をそのまま返す。"""
    path = os.environ.get(ENV, "")
    if not path or not other:
        return other
    line = json.dumps({"repo": repo, "title": title, "body": body}, ensure_ascii=False)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        emit(result(tool, "stopped", f"起票の依頼を書けない: {path}（{exc}）"), EXIT_VIOLATION)
    item = {"repo": repo, "title": title, "result": "requested"}
    emit(result(tool, "ok", f"{repo} への起票をサービスへ依頼した", [item], {"other_repo": True}), EXIT_OK)
    return other
