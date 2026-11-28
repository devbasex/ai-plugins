"""進捗記録（受け入れ条件 6・3377-9）: 課題の工程表とスプリント PR の「通した工程」を、ステートマシンの終了のマーカーから書き直す。

本文のセクション（見出しから終わりのマーカーまで）だけを書き直すので、何度打っても同じ本文になる。`ndf.progress.run` から
分けた（行数の安全網のため。振る舞いは変えない）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Optional, Protocol

from filelock import Timeout

from ndf import identity
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, client
from ndf.core import issue_body
from ndf.progress import model, stages

# 行を終えた状態（`ndf.progress.run.FINISHED` と同じ、コントラクト `workflow/progress.json` のスプリントの行の種類）
FINISHED = model.rules()[("sprint", "finished")].kind

# 進捗記録のセクションの見出しと終わりのマーカー、時刻の表示の時間帯（JST）
ISSUE_SECTION = "## 進行"
PR_SECTION = "## 通した工程"
SECTION_END = f"<!-- {identity.FINAL_NAME}:section-end -->"
JST = timezone(timedelta(hours=9))


class View(Protocol):
    """進捗記録が読むステートマシンの中身（`ndf.progress.run.View` が持つ）。"""

    @property
    def rows(self) -> dict[str, model.RowState]: ...

    @property
    def snap(self) -> stages.Snapshot: ...

    def context(self) -> dict[str, Any]: ...


# 課題と PR の本文の読み書きと、その GitHub の実装（書き換えの関数と同じ場所に置く）
Records = issue_body.BodyStore
GitHubRecords = issue_body.GitHubBodies


def github_records(cwd: Path) -> Records:
    return GitHubRecords(GitHub(Git(cwd).remote_repo(), client()))


def _record_line(name: str, row: Optional[model.RowState]) -> str:
    if row is not None and row.state == FINISHED and row.at is not None:
        return f"- [x] {name} — {row.at.astimezone(JST):%Y-%m-%d %H:%M}"
    if row is not None and row.state in ("disabled", "cancelled"):
        why = f"（{row.reason}）" if row.reason else ""
        return f"- [ ] {name} — {model.label(row.state)}{why}"
    return f"- [ ] {name}"


def issue_section(v: View) -> str:
    """課題の工程表（セクション「進行」の中身）。全工程を並べ、終了のマーカーが付いた行だけにマーカーと時刻を付ける。"""
    rows = v.rows
    lines = [f"モード: {v.snap.mode}", ""] + [_record_line(s.name, rows.get(s.name)) for s in v.snap.stages]
    return "\n".join(lines)


def pr_section(v: View) -> str:
    """スプリント PR の「通した工程」。終了のマーカーが付いた行だけを並べる。"""
    rows = v.rows
    done = [_record_line(s.name, rows[s.name]) for s in v.snap.stages if s.name in rows and rows[s.name].state == FINISHED]
    return "\n".join(line.replace("- [x] ", "- ", 1) for line in done)


def replace_section(body: str, heading: str, content: str) -> str:
    """本文のセクション（見出しから終わりのマーカーまで）を書き直す。無ければ末尾へ足す。何度打っても同じ本文になる。"""
    block = f"{heading}\n\n{content}\n{SECTION_END}\n"
    pattern = re.compile(rf"^{re.escape(heading)}\n.*?^{re.escape(SECTION_END)}\n?", re.MULTILINE | re.DOTALL)
    if pattern.search(body):
        return pattern.sub(lambda _m: block, body, count=1)
    return f"{body.rstrip(chr(10))}\n\n{block}" if body.strip() else block


def _targets(v: View) -> list[tuple[str, int, str, str]]:
    ctx = v.context()
    out = [("issue", int(n), ISSUE_SECTION, issue_section(v)) for n in ctx["issues"]]
    if "pr" in ctx:
        out.append(("pr", int(ctx["pr"]), PR_SECTION, pr_section(v)))
    return out


def sync_records(v: View, cwd: Path, make: Callable[[Path], Records]) -> tuple[list[dict[str, Any]], list[str]]:
    """課題と PR の本文の進捗のセクションを、ステートマシンの終了のマーカーから書き直す。書けなかった保存先は誤りに載せる。

    `make` は保存先を作る（`run.Env.records`）。
    """
    try:
        records = make(cwd)
    except (GitError, GitHubError) as exc:
        return [], [f"進捗記録を書けない: {exc}"]
    items, errors = [], []
    for kind, number, heading, content in _targets(v):
        try:
            got = issue_body.rewrite(records, cwd, number, partial(replace_section, heading=heading, content=content))
            items.append({"kind": kind, "number": number, "changed": got.wrote})
        except (GitError, GitHubError, Timeout) as exc:
            errors.append(f"{kind} #{number} の進捗記録を書けない: {exc}")
    return items, errors
