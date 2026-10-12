"""承認資料の `## 指示書の変更` の節（#1834。C7 の操作を承認する人へ見せる）。

指示書は `.ndf/instructions.json` の `files`（`Declaration.load`）。宣言が無いリポジトリでは `AGENTS.md` と
`CLAUDE.md` に絞る（#1834 の受け入れ条件 8）。前のタグから承認したコミットまでの first-parent のログで、
指示書ごとに触れたコミットを並べ、件名から PR の番号を読む（`merged_range.pr_number`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import merged_range
import proc
from instructions_lib.declaration import Declaration
from instructions_lib.model import CheckError
from proc import StepError

HEADING = "## 指示書の変更"
UNDECLARED_FILES = ("AGENTS.md", "CLAUDE.md")  # 宣言が無いときに見る指示書（受け入れ条件 8）


@dataclass(frozen=True)
class InstructionChange:
    """変わった指示書 1 つ。commits は [(短い SHA, PR 番号 または None)]（ログの順）。"""

    file: str
    commits: tuple[tuple[str, int | None], ...]


def files(root: Path) -> list[str]:
    """指示書のパス。宣言が壊れていれば StepError（2）。"""
    try:
        decl = Declaration.load(root)
    except CheckError as e:
        raise StepError(f"指示書の宣言を読めない: {e}", 2)
    return list(decl.files) if decl.present else list(UNDECLARED_FILES)


def changes(root: Path, prev: str, sha: str) -> list[InstructionChange]:
    """`prev..sha` の first-parent のログで触れた指示書（宣言の順）。git が失敗すれば StepError（2）。"""
    out = []
    for name in files(root):
        try:
            p = proc.git(root, "log", "--first-parent", "--format=%h%x09%P%x09%s", f"{prev}..{sha}", "--", name, check=False)
        except OSError:
            raise StepError("git が無い", 2)
        if p.returncode != 0:
            raise StepError(f"git log {prev}..{sha} -- {name}: {p.stderr.strip()[:300]}", 2)
        commits = []
        for line in p.stdout.splitlines():
            short, parents, subject = line.split("\t", 2)
            commits.append((short, merged_range.pr_number(parents, subject)))
        if commits:
            out.append(InstructionChange(name, tuple(commits)))
    return out


def section_lines(found: list[InstructionChange], prev: str, sha: str) -> list[str]:
    """節の中身。変わった指示書が無ければ `- 無し`。"""
    if not found:
        return ["- 無し"]
    lines = []
    for c in found:
        shas = "、".join(f"{s}（#{n}）" if n else s for s, n in c.commits)
        lines += [f"- `{c.file}`: {shas}", f"  - 差分: `git diff {prev} {sha} -- {c.file}`"]
    return lines
