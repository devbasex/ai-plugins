"""引継ぎ文書の本体の検査と退避の判断（純粋な関数。N8a・#1785・#1835）。

引継ぎ文書はセグメントの頭で毎回読まれるため、行数とバイト数の上限で大きさを抑える。テンプレートに無い節（「セッション 95 の
記録」など）と「前の会話の進み」は退避先へ移し、本体にはテンプレートの節だけを残す。本文に書いたパス（「作成済み」と
書いたファイル）が無ければ、書いた直後と読む時点の両方で気付けるよう違反にする。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# テンプレートの節（この順で本体に残す）
TEMPLATE_SECTIONS = (
    "進め方（利用者の指示）",
    "課題の順",
    "現在地",
    "次にやること",
    "今の会話の進み",
    "運用の知見",
    "次に実行するコマンド",
)
# 行内のコードのうち、パスとして実在を確かめる候補（/ を含むか、拡張子を持つ。空白・URL・置き換えるマーカーを含まない）。
# 候補はファイルを作ったと書いた行（_CREATED のどれかを含む行）からだけ取る。ほかの行の版数（`10.17.x`）・
# ブランチ（`origin/develop`）・属性（`facts.value`）はパスでないため数えない
_CREATED = ("作成済み", "作成した")
_CODE_SPAN = re.compile(r"`([^`\s]+)`")
_PATH_LIKE = re.compile(r"^[\w.~/-][\w./~-]*(?:/[\w.-]+|\.[A-Za-z0-9]{1,5})$")
_NOT_PATH = re.compile(r"://|[<>*?{}$|]|^-|^\.\.?$")
_H2 = re.compile(r"^## (.+?)\s*$")


@dataclass(frozen=True)
class Limits:
    """本体の上限（コントラクトの limits の handoff.lines・handoff.bytes）。"""

    max_lines: int
    max_bytes: int


@dataclass(frozen=True)
class CheckReport:
    """検査の結果。どれか 1 つでも当たれば通らない。"""

    violations: list[str] = field(default_factory=list)
    lines: int = 0
    bytes: int = 0
    missing_paths: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations and not self.missing_paths


def sections(text: str) -> list[tuple[str | None, list[str]]]:
    """本文を `## ` の節に分ける。先頭の見出し前の行は名前 None の節。コードブロックの中の `## ` は見出しにしない。"""
    out: list[tuple[str | None, list[str]]] = [(None, [])]
    fenced = False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = None if fenced else _H2.match(line)
        if m:
            out.append((m.group(1), [line]))
        else:
            out[-1][1].append(line)
    return out


def path_candidates(text: str) -> list[str]:
    """ファイルを作ったと書いた行の行内のコードから、パスの候補を出た順に重ねずに取り出す。"""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        if not any(word in line for word in _CREATED):
            continue
        for m in _CODE_SPAN.finditer(line):
            token = m.group(1).rstrip(".,、。")
            if _PATH_LIKE.match(token) and not _NOT_PATH.search(token):
                seen.setdefault(token, None)
    return list(seen)


def check_body(text: str, present: Callable[[str], bool], limits: Limits) -> CheckReport:
    """本体の節の形・行数・バイト数・本文に書いたパスの実在を確かめる。`present` はパスが実在するかを回答する。"""
    names = [name for name, _ in sections(text) if name is not None]
    violations = [f"テンプレートに無い節: {n}" for n in names if n not in TEMPLATE_SECTIONS]
    violations += [f"節が重なる: {n}" for n in sorted({n for n in names if names.count(n) > 1})]
    lines = len(text.splitlines())
    size = len(text.encode("utf-8"))
    if lines > limits.max_lines:
        violations.append(f"行数が上限を超える: {lines} > {limits.max_lines}")
    if size > limits.max_bytes:
        violations.append(f"バイト数が上限を超える: {size} > {limits.max_bytes}")
    missing = [p for p in path_candidates(text) if not present(p)]
    return CheckReport(violations, lines, size, missing)


def trim(text: str) -> tuple[str, list[str]]:
    """本体に残す本文と、退避先へ移す節（見出しを含む本文）の一覧を返す。移す節が無ければ本文をそのまま返す。

    テンプレートに無い節（「前の会話の進み」もテンプレートに無い）を移す。返す本文の `## ` の節はテンプレートの節だけになる。
    """
    kept: list[str] = []
    moved: list[str] = []
    for name, body in sections(text):
        if name is not None and name not in TEMPLATE_SECTIONS:
            moved.append("\n".join(body).rstrip("\n"))
        else:
            kept += body
    if not moved:
        return text, []
    out = "\n".join(kept).rstrip("\n") + "\n"
    return out, moved
