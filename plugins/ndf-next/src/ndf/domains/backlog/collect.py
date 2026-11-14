"""候補の経路の収集。課題ごとに、候補へ入った経路（`selection_decide.ROUTES`）の集まりを作る。

| 経路 | 入る課題 |
| --- | --- |
| `manual` | 利用者が `--add` で渡した open の課題 |
| `carried` | 前回の候補の収集の持ち越しと、前回の深さの打ち切りの残りのうち、今も open の課題（#1640） |
| `commit-subject` | 起点（`--since-ref`）から HEAD までのコミットの件名が `#番号` で指す課題 |
| `diff-path` | 起点から HEAD までに変わったファイルのパスを本文に持つ課題 |
| `diff-identifier` | 起点から HEAD までに消え、HEAD のどこにも残らない識別子を題か本文に持つ課題 |
| `sub-issue` | 起点の後に閉じた課題を親に持つ課題（親の欄か、本文の「親 … #番号」） |
| `no-milestone` | マイルストーンの無い課題 |
| `closed-milestone` | 見直しの回の、起点の後に閉じた課題と同じマイルストーンの課題 |
| `unscored` | 見積の記録が無い・見積の後に書き換わった課題 |
| `all` | 見直しの回の open の全件 |
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters.git import Git, GitError
from ndf.domains.backlog import estimates_decide, model
from ndf.domains.backlog import selection_decide as sel

_ISSUE_REF = re.compile(r"#(\d+)\b")
_PARENT_REF = re.compile(r"親[^\n#]{0,20}#(\d+)\b")
# 本文の言及として数えるパスの最短の長さ（短い名前は無関係の語に当たる）
MIN_PATH = 6
# 消えた識別子として拾う語の形と最短の長さ。記号か小文字→大文字の切れ目を含まない語は、ありふれた単語と区別できない
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*[A-Za-z0-9_]")
_CAMEL = re.compile(r"[a-z][A-Z]")
MIN_IDENTIFIER = 6


class Found:
    """課題ごとの経路の集まりと、収集の途中の注記。"""

    def __init__(self, open_numbers: Iterable[int]) -> None:
        self.open = set(open_numbers)
        self.routes: dict[int, set[str]] = {}
        self.notes: list[str] = []

    def add(self, numbers: Iterable[int], route: str) -> None:
        for n in numbers:
            if n in self.open:
                self.routes.setdefault(n, set()).add(route)


def ref_time(git: Git, ref: str) -> str:
    """起点のコミットの時刻（UTC の ISO 8601。GitHub の時刻と文字列で比べられる形）。"""
    stamp = git.run("log", "-1", "--format=%ct", ref).strip()
    return datetime.fromtimestamp(int(stamp), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def commit_refs(git: Git, since_ref: str) -> set[int]:
    """起点から HEAD までのコミットの件名が指す番号。"""
    subjects = git.run("log", "--format=%s", f"{since_ref}..HEAD")
    return {int(m) for m in _ISSUE_REF.findall(subjects)}


def path_mentions(issues: Iterable[Mapping[str, Any]], paths: Iterable[str]) -> set[int]:
    """変わったファイルのパスを本文に持つ課題。"""
    needles = [p for p in paths if len(p) >= MIN_PATH]
    return {i["number"] for i in issues if any(p in i["body"] for p in needles)}


def is_identifier(token: str) -> bool:
    return len(token) >= MIN_IDENTIFIER and (any(c in token for c in "_-.") or _CAMEL.search(token) is not None)


def removed_identifiers(diff: str) -> set[str]:
    """差分（`-U0`）の消えた行にだけ現れる識別子。"""
    removed: set[str] = set()
    added: set[str] = set()
    for line in diff.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("-"):
            removed.update(_TOKEN.findall(line[1:]))
        elif line.startswith("+"):
            added.update(_TOKEN.findall(line[1:]))
    return {t for t in removed - added if is_identifier(t)}


def _still_present(git: Git, token: str) -> bool:
    """HEAD のどこかに `token` が残るか。git grep の失敗（終了コード 1 以外）は `GitError`。"""
    done = git.runner(["grep", "-q", "-F", "-e", token, "HEAD"], git.cwd)
    if done.returncode not in (0, 1):
        raise GitError(f"git grep が失敗（終了コード {done.returncode}）: {done.stderr.strip()}")
    return done.returncode == 0


def mentions(text: str, term: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text) is not None


def identifier_mentions(git: Git, issues: list[dict[str, Any]], since_ref: str) -> set[int]:
    """起点から HEAD までに消え、HEAD に残らない識別子を題か本文に持つ課題。"""
    terms = removed_identifiers(git.run("diff", "--no-renames", "-U0", since_ref, "HEAD"))
    hits = {t: [i["number"] for i in issues if mentions(f"{i['title']}\n{i['body']}", t)] for t in sorted(terms)}
    return {n for t, ns in hits.items() if ns and not _still_present(git, t) for n in ns}


def sub_issue_of_closed(issues: Iterable[Mapping[str, Any]], closed: set[int]) -> set[int]:
    """閉じた課題を親に持つ open の課題（親の欄か本文の「親 … #番号」）。"""
    out = set()
    for i in issues:
        named = {int(m) for m in _PARENT_REF.findall(i["body"])}
        if i["parent"] in closed or named & closed:
            out.add(i["number"])
    return out


def in_closed_milestones(issues: Iterable[Mapping[str, Any]], closed: Iterable[Mapping[str, Any]]) -> set[int]:
    """閉じた課題と同じマイルストーンにある open の課題。"""
    touched = {c["milestone"] for c in closed if c["milestone"]}
    return {i["number"] for i in issues if i["milestone"] in touched}


class Ask(NamedTuple):
    """候補の収集の求め。`closed` は起点の後に閉じた課題（起点が無ければ空）。"""

    since_ref: Optional[str]
    manual: list[int]
    carried: list[int]
    depth: str
    closed: tuple[dict[str, Any], ...] = ()


def collect(issues: list[dict[str, Any]], cwd: Path, ask: Ask, stored: Mapping[str, Any]) -> Found:
    """経路ごとに候補を集める。"""
    since_ref, manual, carried, depth, closed = ask
    found = Found(i["number"] for i in issues)
    missing = sorted(set(manual) - found.open)
    if missing:
        found.notes.append("--add のうち open でない課題は外した: " + " ".join(f"#{n}" for n in missing))
    found.add(manual, sel.MANUAL)
    found.add(carried, sel.CARRIED)
    found.add((i["number"] for i in issues if i["milestone"] is None), sel.NO_MILESTONE)
    digests = {i["number"]: model.snapshot_digest(i) for i in issues}
    found.add(estimates_decide.unscored(sorted(found.open), stored, digests), sel.UNSCORED)
    found.add(sub_issue_of_closed(issues, {c["number"] for c in closed}), sel.SUB_ISSUE)
    if depth == model.REVIEW:
        found.add(found.open, sel.ALL)
        found.add(in_closed_milestones(issues, closed), sel.CLOSED_MILESTONE)
    if since_ref:
        git = Git(cwd)
        found.add(commit_refs(git, since_ref), sel.COMMIT_SUBJECT)
        found.add(path_mentions(issues, git.changed_files(since_ref, "HEAD")), sel.DIFF_PATH)
        found.add(identifier_mentions(git, issues, since_ref), sel.DIFF_IDENTIFIER)
    return found
