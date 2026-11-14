"""MVV 判定の前の機械の検査（`ndf.progress.gate` の部品）。

承認済み MVV のハッシュ・承認の記録・モード・レッドラインのパス・MVV の宣言を変えない、の 5 つ（3364-3）と、出し直しの
指摘への対応表が「記載した」と書く PR 本文の節に中身があるか（#2853）。1 つでも外れれば
LLM の判定を読まずに承認ゲートで人へ戻す。判定が「従う」でも、レッドラインがあれば省かない（`may_skip`。#3017）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import re

import pathspec

from ndf.contracts import facts
from ndf.progress import model

# 承認ゲートを省かないモード（MVV 判定の前の検査 3）
EXCLUDED_MODES = ("operation", "documentation")
# 利用者の MVV の宣言のファイルの名前（拡張子を除く。表 ndf-files の宣言の行から引く。MVV 判定の前の検査 5。共通原則の C7）
MVV_STEM = "mvv"
# MVV 判定の語（判定の応答の verdict）
FOLLOW, NOT_FOLLOW, UNKNOWN = "follow", "not_follow", "unknown"
VERDICTS = (FOLLOW, NOT_FOLLOW, UNKNOWN)

# --- MVV 判定の前の機械の検査 --------------------------------------------------------------------------


@dataclass(frozen=True)
class Changed:
    """PR が変えたファイル。`previous` は名前を変えた前のパス、`renamed` は名前の変更か。"""

    path: str
    previous: Optional[str] = None
    renamed: bool = False


@dataclass(frozen=True)
class MvvFacts:
    """機械の検査が読む事実。`status` はプロジェクト MVV の状態（approved・unapproved・mismatch・unreadable・absent）。

    `current` は今の MVV のファイルの sha256、`approved` は利用者の承認の記録の sha256、`referenced` はスプリントの
    状態が持つ参照の sha256。`boundary_paths` は進め方の宣言のレッドラインのパス。`body` は PR 本文（Markdown）、`claimed` は
    出し直しの指摘への対応表が「記載した」と書く節の見出し（#2853）。
    """

    status: str
    current: Optional[str]
    approved: Optional[str]
    referenced: Optional[str]
    mode: str
    changed: tuple[Changed, ...] = ()
    boundary_paths: tuple[str, ...] = ()
    body: Optional[str] = None
    claimed: tuple[str, ...] = ()


def check_mvv_hash(f: MvvFacts) -> Optional[str]:
    """検査 1: プロジェクト MVV が承認済みで、今のファイルのハッシュが承認の記録と一致する。"""
    if f.status != "approved":
        return f"プロジェクト MVV が承認済みでない（{f.status}）"
    if not f.current or f.current != f.approved:
        return f"今の MVV のハッシュ {f.current or '無し'} が承認の記録 {f.approved or '無し'} と一致しない"
    return None


def check_approval_record(f: MvvFacts) -> Optional[str]:
    """検査 2: スプリントの状態が承認の記録を参照し、その sha256 が承認の記録と一致する。"""
    if not f.referenced:
        return "スプリントの状態に承認済み MVV の参照が無い"
    if f.referenced != f.approved:
        return f"スプリントの状態の参照 {f.referenced} が承認の記録 {f.approved or '無し'} と一致しない"
    return None


def check_mode(f: MvvFacts) -> Optional[str]:
    """検査 3: モードが承認ゲートを省かないモード（operation・documentation）でない。"""
    return f"モード {f.mode} は承認ゲートを省かない" if f.mode in EXCLUDED_MODES else None


def _matches(c: Changed, patterns: Sequence[str]) -> bool:
    """変えたファイル（名前を変えた前のパスも）に、パターン（gitignore の書き方。根からの相対）のどれかが当たるか。"""
    spec = pathspec.PathSpec.from_lines("gitignore", [p for p in patterns if p.strip()])
    return any(spec.match_file(p.removeprefix("./")) for p in (c.path, c.previous) if p)


def mvv_decls() -> tuple[str, ...]:
    """利用者の MVV の宣言のパス（根からの相対。表 ndf-files と places から引く）。"""
    decls = [r for r in facts.rows("ndf-files", kind="declaration") if Path(r["path"]).stem == MVV_STEM]
    return tuple(f"{facts.value('places', r['place'], 'path')}/{r['path']}" for r in decls)


def _hits(changed: Iterable[Changed], patterns: Sequence[str]) -> list[str]:
    out = []
    for c in changed:
        if c.renamed and not c.previous:
            out.append(f"{c.path}（名前を変えた前のパスが分からない）")
        elif _matches(c, patterns):
            out.append(f"{c.previous} → {c.path}" if c.previous else c.path)
    return out


def check_boundary(f: MvvFacts) -> Optional[str]:
    """検査 4: 変えたファイル（名前を変えた前のパスも）がレッドラインのパスに当たらない。"""
    hits = _hits(f.changed, f.boundary_paths) if f.boundary_paths else []
    return "レッドラインのパスに当たる: " + " / ".join(hits) if hits else None


def check_mvv_decl(f: MvvFacts) -> Optional[str]:
    """検査 5: 利用者の MVV の宣言（`.ndf/mvv.*`）を変えない（共通原則の C7。宣言では外せない）。"""
    decls = mvv_decls()
    hits = [c for c in f.changed if _matches(c, decls)]
    return "MVV の宣言を変える（共通原則の C7）: " + " / ".join(c.path for c in hits) if hits else None


# Markdown の節の見出し（`##` から `######`）
HEADING = re.compile(r"^#{2,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)


def sections(body: str) -> dict[str, str]:
    """Markdown の見出し → その節の中身（次の見出しまで。前後の空白を除く）。同じ見出しが 2 つあれば中身をつなぐ。"""
    heads = list(HEADING.finditer(body))
    out: dict[str, str] = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        text = body[h.end() : end].strip()
        name = h.group(1).strip()
        out[name] = f"{out[name]}\n{text}".strip() if name in out else text
    return out


def check_claimed_sections(f: MvvFacts) -> Optional[str]:
    """検査 6: 対応表が「記載した」と書く節が PR 本文にあり、中身がある（#2853。食い違えば出し直しの中で直す）。"""
    if not f.claimed:
        return None
    held = sections(f.body) if f.body is not None else {}
    empty = [name for name in f.claimed if not held.get(name)]
    return "対応表が記載したと書く節に中身が無い: " + " / ".join(empty) if empty else None


# 機械の検査を当てる順
CHECKS: tuple[Callable[[MvvFacts], Optional[str]], ...] = (
    check_mvv_hash,
    check_approval_record,
    check_mode,
    check_boundary,
    check_mvv_decl,
    check_claimed_sections,
)


def refusals(f: MvvFacts) -> list[str]:
    """外れた機械の検査の理由（空なら MVV 判定へ進める）。"""
    return [why for check in CHECKS if (why := check(f)) is not None]


def well_formed(verdict: Any) -> bool:
    """判定の応答が契約の形か（verdict・reasons・boundary がそろい、reasons と boundary が空でない文字列の配列）。

    契約の外は承認ゲートを省かない。boundary を欠く・空の文字を含む応答を、レッドラインが無いと読まない。
    """

    def strings(x: Any) -> bool:
        return isinstance(x, list) and all(isinstance(s, str) and s.strip() for s in x)

    return (
        isinstance(verdict, dict)
        and verdict.get("verdict") in VERDICTS
        and strings(verdict.get("reasons"))
        and strings(verdict.get("boundary"))
    )


def may_skip(f: MvvFacts, verdict: Any) -> tuple[bool, list[str]]:
    """承認ゲートを省けるか（省けないときの理由）。外れた検査・形の違う判定・従わない判定・レッドラインがあれば省かない。"""
    why = refusals(f)
    if why:
        return False, why
    if not well_formed(verdict):
        return False, ["MVV 判定の応答が契約の形でない"]
    if verdict["verdict"] != FOLLOW:
        return False, [f"MVV 判定が {verdict['verdict']}"]
    if verdict["boundary"]:
        return False, ["レッドラインに当たる: " + " / ".join(verdict["boundary"])]
    return True, []


def note(gate: str, verdict: Any, why: Sequence[str]) -> str:
    """MVV 判定の記録（PR のコメントと承認資料に載せる Markdown）。レッドラインの中身も載せる（#2950）。"""
    v = verdict if isinstance(verdict, dict) else {}
    reasons = [str(r) for r in v["reasons"]] if isinstance(v.get("reasons"), list) else []
    boundary = [str(b) for b in v["boundary"]] if isinstance(v.get("boundary"), list) else []
    lines = [f"## MVV 判定（{model.gate_label(gate)}）", "", f"- 判定: {v['verdict'] if 'verdict' in v else '無し'}"]
    lines += ["", "### 理由", "", *([f"- {r}" for r in reasons] or ["- （無し）"])]
    lines += ["", "### レッドライン", "", *([f"- {b}" for b in boundary] or ["- （無し）"])]
    if why:
        lines += ["", "### 承認ゲートを省かない理由", "", *[f"- {w}" for w in why]]
    return "\n".join(lines) + "\n"
