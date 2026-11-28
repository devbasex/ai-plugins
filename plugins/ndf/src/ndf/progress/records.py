"""進捗記録（受け入れ条件 6・3377-9）: 課題の工程表とスプリント PR の「通した工程」を、ステートマシンの終了のマーカーから書き直す。

本文のセクション（見出しから終わりのマーカーまで）だけを書き直すので、何度打っても同じ本文になる。`ndf.progress.run` から
分けた（行数の安全網のため。振る舞いは変えない）。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf import identity
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, client
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


class Records(Protocol):
    """課題と PR の本文の読み書き。"""

    def read(self, number: int) -> str: ...

    def write(self, number: int, body: str) -> None: ...


@dataclass(frozen=True)
class GitHubRecords:
    hub: GitHub

    def read(self, number: int) -> str:
        return self.hub.issue_body(number).body

    def write(self, number: int, body: str) -> None:
        self.hub.rest("PATCH", f"/repos/{self.hub.repo}/issues/{int(number)}", {"body": body})


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


@dataclass(frozen=True)
class SyncFailure:
    """書けなかった進捗記録。`target`・`number` は保存先（保存先を作れなければ None）、`source` は失敗した接続先、
    `status` は HTTP の状態（無ければ 0）。`detail` は例外の文で、GitHub の応答本文を含むので保存しない（`stored`）。
    """

    target: Optional[str]
    number: Optional[int]
    source: str
    status: int
    detail: str

    def __str__(self) -> str:
        return f"{_where(self.target, self.number)}進捗記録を書けない: {self.detail}"

    def stored(self) -> dict[str, Any]:
        """進捗の DB に残してよい値（対象の種別・番号と失敗の分類）。"""
        return {"target": self.target, "number": self.number, "source": self.source, "status": self.status}


def _where(target: Optional[str], number: Optional[int]) -> str:
    return f"{target} #{number} の" if target is not None else ""


def failure_summary(stored: Mapping[str, Any]) -> str:
    """残した値（`SyncFailure.stored`）から組む、書けなかったことの文。"""
    http = f"。HTTP {stored['status']}" if stored["status"] else ""
    return f"{_where(stored['target'], stored['number'])}進捗記録を書けない（{stored['source']} の失敗{http}）"


def _failure(exc: Exception, target: Optional[str] = None, number: Optional[int] = None) -> SyncFailure:
    source = "GitHub" if isinstance(exc, GitHubError) else "Git"
    return SyncFailure(target, number, source, getattr(exc, "status", 0), str(exc))


def sync_records(v: View, cwd: Path, make: Callable[[Path], Records]) -> tuple[list[dict[str, Any]], list[SyncFailure]]:
    """課題と PR の本文の進捗のセクションを、ステートマシンの終了のマーカーから書き直す。書けなかった保存先は誤りに載せる。

    `make` は保存先を作る（`run.Env.records`）。
    """
    try:
        records = make(cwd)
    except (GitError, GitHubError) as exc:
        return [], [_failure(exc)]
    items, errors = [], []
    for kind, number, heading, content in _targets(v):
        try:
            body = records.read(number)
            new = replace_section(body, heading, content)
            if new != body:
                records.write(number, new)
            items.append({"kind": kind, "number": number, "changed": new != body})
        except GitHubError as exc:
            errors.append(_failure(exc, kind, number))
    return items, errors
