"""new sprint が計画を書く前に、--design に無い課題の本文が受け入れ条件を持つかを確かめる（#1767）。

設計を付けない課題は、実装のプランの最初の worker が本文の `## 受け入れ条件` を入力に読む。節が無ければ
worker は判断待ちで止まるため、承認ゲート 1 より前（計画を書く前）に番号と次の手を返して止める。
判定は `## 受け入れ条件` の見出しだけで行い（`## 何をするか` は見ない）、節に空でない行が 1 行以上あれば満たす。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, NamedTuple

import gh_rest
import gh_sections
from step_result import result

HEADING = "## 受け入れ条件"
NO_SECTION = "受け入れ条件の節が無い"
EMPTY_SECTION = "受け入れ条件の節が空"
UNREADABLE = "本文を取れない"
_GH_TAIL = 500


class Lack(NamedTuple):
    """受け入れ条件を確かめられなかった・持たない課題の 1 件。"""

    issue: int
    reason: str
    gh: dict | None = None

    def row(self) -> dict:
        return {"issue": self.issue, "reason": self.reason, **({"gh": self.gh} if self.gh else {})}


def criteria_targets(issues: list[int], design: list[int]) -> list[int]:
    """確かめる課題（--issue から --design を除いたもの。順序を保ち、重複を除く）。"""
    seen = set(design)
    out = []
    for n in issues:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def body_lack(n: int, body: str) -> Lack | None:
    section = gh_sections.get_section(body, HEADING)
    if section is None:
        return Lack(n, NO_SECTION)
    if not any(line.strip() for line in section.splitlines()):
        return Lack(n, EMPTY_SECTION)
    return None


def read_body(n: int, cwd: str | None) -> tuple[str | None, dict | None]:
    """課題の本文を 1 回だけ読む。読めなければ (None, gh の終了コードと標準エラーの末尾)。"""
    r = gh_rest.view_json("issue", n, "body", cwd=cwd)
    if r.returncode == 0:
        try:
            d = json.loads(r.stdout)
        except ValueError:
            d = None
        if isinstance(d, dict) and isinstance(d.get("body", ""), (str, type(None))):
            return str(d.get("body") or ""), None
        return None, {"returncode": 0, "stderr": "応答を読めない: " + r.stdout.strip()[-_GH_TAIL:]}
    return None, {"returncode": r.returncode, "stderr": (r.stderr or r.stdout).strip()[-_GH_TAIL:]}


def lacking(issues: list[int], design: list[int], cwd: str | None = None) -> list[Lack]:
    """受け入れ条件を持たない課題と、本文を取れない課題を全部返す（無ければ空）。"""
    out = []
    for n in criteria_targets(issues, design):
        body, err = read_body(n, cwd)
        lack = Lack(n, UNREADABLE, err) if body is None else body_lack(n, body)
        if lack:
            out.append(lack)
    return out


def lack_next_text(lacks: list[Lack], design_command: str, same_command: str) -> str:
    """止まったときの次の手（--design へ入れて打ち直す・本文を書いて打ち直す・gh を確かめて打ち直す）。"""
    nums = lambda rows: " ".join(f"#{x.issue}" for x in rows)  # noqa: E731
    missing = [x for x in lacks if x.reason != UNREADABLE]
    unreadable = [x for x in lacks if x.reason == UNREADABLE]
    steps = [f"1. {nums(lacks)} を `--design` へ入れて打ち直す: {design_command}"]
    if missing:
        steps.append(f"{len(steps) + 1}. {nums(missing)} の本文に `{HEADING}` を書いてから、同じコマンドを打ち直す: {same_command}")
    if unreadable:
        steps.append(
            f"{len(steps) + 1}. "
            + " ".join(f"`gh issue view {x.issue}`" for x in unreadable)
            + f" が通ることを確かめてから打ち直す: {same_command}"
        )
    return " ".join(steps)


def criteria_refusal(a, command: Callable[..., str]) -> dict | None:
    """--design に無い課題の本文が受け入れ条件を持たない・読めないとき、計画を書かずに止める結果。
    command(design=[番号]) は今の引数を写して --design へ番号を足した起動の形、command() は今の起動の形。"""
    lacks = lacking(list(a.issue), list(getattr(a, "design", None) or []), cwd=str(Path(a.worktree).resolve()))
    if not lacks:
        return None
    nums = " ".join(f"#{x.issue}" for x in lacks)
    reasons = "・".join(sorted({x.reason for x in lacks}))
    return result(
        "supervise-new",
        "stopped",
        f"実装の入力が足りない課題がある（{nums}: {reasons}）。計画を書かない",
        [x.row() for x in lacks],
        {"issues": [x.issue for x in lacks]},
        next=lack_next_text(lacks, command(design=[x.issue for x in lacks]), command()),
    )
