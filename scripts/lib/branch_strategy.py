"""branch_strategy.py: ブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）を根の検査が読む部品（#3672）。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の CI の判定（`scripts/ci-heavy-skip.py`）と
契約の構造チェックの起点の ref（`contracts_lib/ratchet.py`）が使う、ベースブランチ・本番ブランチ・取り込み方の
読み取りだけを持つ。

宣言を作る・移す・スキーマで検査するのは NDF の `ndf project branch-strategy` で、ここは同じ値を読むだけである。
プラグインの内部（スキーマ・旧いキーの移す行の表）は読まず、根の判定に要る形だけを確かめる。

- セクションが無い・形が違う（取り込み方が表に無い・ブランチ名が空）ときは `StrategyError` で止まる。既定値へ倒さない
- 取り込み方が `trunk` なのにベースブランチと本番ブランチが違う（またはその逆）ときも止まる（規則 R1）

使う側は外部パッケージを要らない（標準ライブラリだけで書く）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SECTION = "branch_strategy"
DECL = "project.json"


@dataclass(frozen=True)
class Row:
    """取り込み方の表の 1 行（行の名前は `release_strategy` の値）。"""

    name: str
    selects_units: bool  # リリースの単位（スプリントブランチ）を選んで本番ブランチから組むか


ROWS: dict[str, Row] = {
    "merge_base_branch": Row("merge_base_branch", selects_units=False),
    "release_branch": Row("release_branch", selects_units=True),
    "trunk": Row("trunk", selects_units=False),
}


class StrategyError(Exception):
    """宣言を読めない・使えない。`reason` は利用者へ出す文。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Strategy:
    """形を確かめたセクション（不変の値として扱う。`section` を書き換えない）。"""

    section: dict

    @property
    def release_strategy(self) -> str:
        return self.section["release_strategy"]

    @property
    def production(self) -> str:
        return self.section["branches"]["production"]

    @property
    def base(self) -> str:
        return self.section["branches"]["base"]

    @property
    def row(self) -> Row:
        return ROWS[self.release_strategy]


def _problems(section: Any) -> list[str]:
    if not isinstance(section, dict):
        return ["セクションがオブジェクトでない"]
    out = []
    if section.get("release_strategy") not in ROWS:
        out.append(f"release_strategy が {' / '.join(ROWS)} のどれでもない")
    branches = section.get("branches")
    if not isinstance(branches, dict) or not all(isinstance(branches.get(k), str) and branches[k] for k in ("base", "production")):
        out.append("branches.base と branches.production が空でない文字列でない")
    elif section.get("release_strategy") in ROWS and (section["release_strategy"] == "trunk") != (
        branches["base"] == branches["production"]
    ):
        out.append("R1: trunk のときだけ branches.base と branches.production が同じ")
    return out


def _from_text(text: str | None, where: str) -> Strategy:
    if text is None:
        raise StrategyError(f"{where} に `.ndf/{DECL}` がありません。`ndf project branch-strategy init` で作ってください")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise StrategyError(f"{where} の `.ndf/{DECL}` を JSON として読めない（{e}）") from e
    if not isinstance(data, dict) or SECTION not in data:
        raise StrategyError(f"{where} の `.ndf/{DECL}` にセクション `{SECTION}` がありません")
    problems = _problems(data[SECTION])
    if problems:
        raise StrategyError(f"{where} の `.ndf/{DECL}` の `{SECTION}` の形が違う: {'、'.join(problems)}")
    return Strategy(data[SECTION])


def _load(read: Callable[[], str | None], where: str) -> Strategy:
    return _from_text(read(), where)


def load_strategy(root: Path) -> Strategy:
    """作業ツリーのセクションを読み、形を確かめて型にする。"""
    p = Path(root) / ".ndf" / DECL
    return _load(lambda: p.read_text(encoding="utf-8") if p.is_file() else None, str(root))


def load_at_ref(root: Path, ref: str) -> Strategy:
    """指定したブランチのセクションを `git show <ref>:.ndf/project.json` で読み、`load_strategy` と同じ形を確かめる。"""
    ok = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True, text=True)
    if ok.returncode != 0:
        raise StrategyError(f"ref `{ref}` がありません。取得してから打ち直してください")

    def read() -> str | None:
        r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:.ndf/{DECL}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    return _load(read, f"`{ref}`")
