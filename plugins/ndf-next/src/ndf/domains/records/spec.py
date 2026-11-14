"""確定仕様化の口: 設計を確定仕様へ溶かした後の用語集の付け替え・文書の作り直し・設計の削除（N8a・#1715）。

正本を決められない語があれば、何も書かずに `spec_decide.SourceError` で止まる（書く前に判定する）。
コミットに入れるパスは、渡した確定仕様のすべて・用語集の正本と書き直した文書・消した設計である。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from ndf.adapters.git import Git
from ndf.domains.records import files, glossary
from ndf.domains.records import glossary_decide as gd
from ndf.domains.records import spec_decide as sd


class SpecError(ValueError):
    """渡した確定仕様か設計が読めない。"""


def _texts(root: Path, specs: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in specs:
        try:
            out[p] = (root / p).read_text(encoding="utf-8")
        except OSError as exc:
            raise SpecError(f"確定仕様 {p} を読めない: {exc}") from exc
    return out


def _rel(root: Path, p: str) -> str:
    """渡したパスをリポジトリの根からの相対パス（`/` 区切り）にそろえる。根の外なら `SpecError`。

    `./issues/d.md` と `issues/d.md` を同じ設計として用語集の `pending_source` と照合するためにそろえる。
    """
    base = os.path.normpath(os.path.abspath(root))
    target = os.path.normpath(os.path.join(base, os.path.expanduser(p)))
    rel = os.path.relpath(target, base)
    if rel == os.curdir or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise SpecError(f"リポジトリの中のファイルでない: {p}")
    return Path(rel).as_posix()


def finalize(root: Path, specs: Sequence[str], removed: Sequence[str], overrides: Mapping[str, str]) -> list[str]:
    """用語集の正本を付け替え、文書を作り直し、設計を消して、コミットに入れるパスを返す。

    渡したパス（確定仕様・消す設計・`overrides` のファイル）は、リポジトリの根からの相対パスにそろえてから使う。
    """
    if not specs:
        raise SpecError("確定仕様（--spec）が 1 つも無い")
    specs = list(dict.fromkeys(_rel(root, p) for p in specs))
    removed = list(dict.fromkeys(_rel(root, p) for p in removed))
    overrides = {term: _rel(root, p) for term, p in overrides.items()}
    texts = _texts(root, specs)
    missing = [p for p in removed if not (root / p).is_file()]
    if missing:
        raise SpecError("消す設計が無い: " + "、".join(missing))
    # 用語集の設定が無いプロジェクトは、用語集の処理だけを省く（plan-to-spec と同じ）
    touched = _glossary(root, set(removed), texts, overrides) if files.path(root, "glossary").is_file() else []
    for p in removed:
        (root / p).unlink()
    return sd.commit_paths(specs, touched, removed)


def _glossary(root: Path, removed: set[str], texts: Mapping[str, str], overrides: Mapping[str, str]) -> list[str]:
    """用語集の正本を付け替えて文書を作り直し、書いたパス（正本と文書）を返す。"""
    lay = glossary.layout(root)
    g = glossary.load(root, lay)
    plan = sd.plan_glossary(g, removed, texts, overrides)
    applied = sd.apply_plan(g, plan) if plan is not None else g
    # 上限を超えるコンテキストがあれば、正本を書く前に止まる
    gd.render_files(applied, lay)
    if plan is not None:
        (root / lay.source).write_text(json.dumps(applied, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written = [row["path"] for row in glossary.render(root, check=False)]
    return [lay.source, *written]


def commit(root: Path, paths: Sequence[str], message: str) -> str:
    """渡したパスだけをコミットし、コミットの sha を返す。git が失敗すれば `GitError`。"""
    git = Git(root)
    git.run("add", "-A", "--", *paths)
    # パスを渡して、ほかにステージ済みの変更があってもコミットへ入れない
    git.run("commit", "-q", "-m", message, "--", *paths)
    return git.rev_parse("HEAD")
