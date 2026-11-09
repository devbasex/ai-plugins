"""design_docs.py: 設計文書の名前の規則と、課題の番号から HEAD の文書を読む処理（#2519）。

`design-match.py`（完了判定の突き合わせ）と `design-survey.py`（外部の調査の検査）が共有する。
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from step_result import EXIT_UNREADABLE, StepError, git

DESIGN_DIR = "issues"
DESIGN_NAME = re.compile(r"^issue-(?P<nums>\d+(?:-\d+)*)-design(?:-[^/]+)?\.md$")
REQUIREMENTS_NAME = re.compile(r"^issue-(?P<nums>\d+(?:-\d+)*)-requirements\.md$")


def git_list(root, *args) -> list[str]:
    p = git(root, *args, check=False)
    if p.returncode != 0:
        raise StepError(f"git {' '.join(args[:3])} が読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    return [ln for ln in p.stdout.splitlines() if ln]


def require_repo(root) -> None:
    try:
        git_list(root, "rev-parse", "--verify", "HEAD")
    except OSError as e:
        raise StepError(f"git を起動できない: {e}", EXIT_UNREADABLE)


def name_numbers(rel: str, *, requirements: bool = False) -> set[str] | None:
    """ファイル名が設計文書（`requirements` なら受け入れ条件のファイルも）の形なら、名前の課題の番号を返す。"""
    name = PurePosixPath(rel).name
    m = DESIGN_NAME.match(name) or (REQUIREMENTS_NAME.match(name) if requirements else None)
    if not m:
        return None
    return {str(int(n)) for n in m.group("nums").split("-")}


def _show(root, rel: str) -> str:
    p = git(root, "show", f"HEAD:{rel}", check=False)
    if p.returncode != 0:
        raise StepError(f"設計文書を HEAD から読めない: {rel}", EXIT_UNREADABLE)
    return p.stdout


def _head_issue_files(root) -> list[str]:
    return git_list(root, "ls-tree", "-r", "--name-only", "HEAD", "--", f"{DESIGN_DIR}/")


def connected_docs(paths: list[str], issues, *, requirements: bool = False) -> list[str]:
    """`paths` のうち、名前の番号の並びに `issues` のどれかを含む設計文書を、`paths` の順で返す。

    選んだ文書の名前のほかの番号もたどり、新しい番号が出なくなるまで選び足す（`issues=[8]` でも
    `issue-8-design.md`・`issue-7-8-design.md`・`issue-7-design.md` を選ぶ）。`requirements` なら
    `issue-<番号>-requirements.md` も選ぶ。"""
    files = [(rel, nums) for rel in paths if (nums := name_numbers(rel, requirements=requirements)) is not None]
    want = {str(int(n)) for n in issues}
    picked: set[str] = set()
    grew = True
    while grew:
        grew = False
        for rel, nums in files:
            if rel not in picked and want & nums:
                picked.add(rel)
                if nums - want:
                    want |= nums
                    grew = True
    return [rel for rel, _ in files if rel in picked]


def find_designs(root, issues: list[str]) -> list[tuple[str, str]]:
    """HEAD の `issues/` から、番号の並びに `issues` のどれかを含む設計文書を `(パス, 中身)` で返す。"""
    want = {str(int(n)) for n in issues}
    return [(rel, _show(root, rel)) for rel in _head_issue_files(root) if want & (name_numbers(rel) or set())]


def find_closed(root, issues: list[str], *, requirements: bool = True) -> list[tuple[str, str]]:
    """`find_designs` と同じく HEAD から読むが、番号のつながる文書（`connected_docs`）をすべて読む。"""
    return [(rel, _show(root, rel)) for rel in connected_docs(_head_issue_files(root), issues, requirements=requirements)]


def read_files(root, paths: list[str]) -> list[tuple[str, str]]:
    """渡したパスを worktree から `(パス, 中身)` で読む。"""
    out = []
    for raw in paths:
        p = Path(raw) if Path(raw).is_absolute() else Path(root) / raw
        try:
            out.append((raw, p.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            raise StepError(f"設計文書を読めない: {raw}（{e}）", EXIT_UNREADABLE)
    return out
