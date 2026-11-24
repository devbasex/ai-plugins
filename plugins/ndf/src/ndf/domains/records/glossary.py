"""用語集の文書を書く口（`render`・`render --check`）と、意味の説明の検査の口（N8a・#1616・fix:3190）。

保存先は用語集の設定（表 ndf-files の glossary の行）から読む。設定の `document` は索引のパス、`document_dir` は
コンテキストごとのファイルの保存先、`max_lines` は 1 ファイルの上限の行数。無ければ既定を使う。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ndf.contracts import limits
from ndf.domains.records import files, jsonfile
from ndf.domains.records import glossary_decide as gd

# 設定に max_lines が無いときの 1 ファイルの上限の行数の方針（contracts/limits/）
MAX_LINES = "glossary.max-lines"


class GlossaryError(ValueError):
    """設定か SSOT が読めない。"""


def _read_json(path: Path) -> Any:
    try:
        return jsonfile.read(path)
    except jsonfile.JsonFileError as exc:
        raise GlossaryError(str(exc)) from exc


def layout(root: Path) -> gd.Layout:
    """設定から保存先を求める。設定が無い・source か document が無ければ `GlossaryError`。"""
    raw = _read_json(files.path(root, "glossary"))
    if not isinstance(raw, dict) or not isinstance(raw.get("source"), str) or not isinstance(raw.get("document"), str):
        raise GlossaryError("用語集の設定に source と document が無い")
    document = raw["document"]
    # document_dir が無ければ索引のパスから拡張子を除いたディレクトリ
    default_dir = str(Path(document).with_suffix(""))
    document_dir = raw["document_dir"] if isinstance(raw.get("document_dir"), str) else default_dir
    max_lines = raw["max_lines"] if isinstance(raw.get("max_lines"), int) else int(limits.value(MAX_LINES, "count"))
    return gd.Layout(raw["source"], document, document_dir, max_lines)


def load(root: Path, lay: gd.Layout) -> dict[str, Any]:
    """SSOT を読む。contexts と terms の配列が無ければ `GlossaryError`。"""
    g = _read_json(root / lay.source)
    if not isinstance(g, dict) or not isinstance(g.get("contexts"), list) or not isinstance(g.get("terms"), list):
        raise GlossaryError(f"{lay.source} に contexts と terms の配列が無い")
    return g


def _current(root: Path, paths: list[str]) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for p in paths:
        f = root / p
        out[p] = f.read_text(encoding="utf-8") if f.is_file() else None
    return out


def render(root: Path, check: bool) -> list[dict[str, str]]:
    """文書を作る。`check` なら書かずに、今と違うファイルを返す。書いたときは書いたファイルを返す。

    上限を超えるコンテキストがあれば `glossary_decide.RenderError`（ファイルを書かない）。
    """
    lay = layout(root)
    rendered = gd.render_files(load(root, lay), lay)
    differ = gd.stale(rendered, _current(root, sorted(rendered)))
    if not check:
        for p in differ:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_text(rendered[p], encoding="utf-8")
    return [{"path": p} for p in differ]


def check_meanings(root: Path) -> list[dict[str, str]]:
    """意味の説明に残った廃止した語。"""
    lay = layout(root)
    return gd.deprecated_in_meanings(load(root, lay))
