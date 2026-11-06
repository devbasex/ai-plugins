"""禁止語の宣言（`.ndf/glossary.json` の `check.banned`）を読み、照らす（#2620 の決定 8）。

足した行を集める処理と置き換え先を引く処理は `glossary.py` が持つ。ここは宣言の形・検査する範囲・行の照合だけを持ち、
特定のプロジェクトの語を持たない。形は `skills/requirements-design/references/glossary-format.md` にある。
"""

from __future__ import annotations

from pathlib import Path

import pathmatch


def parse_banned(raw, label: str, unreadable) -> dict | None:
    """宣言を確かめて語・検査する範囲・外す範囲の辞書を返す。宣言が無ければ None。誤りは `unreadable(文)` を上げる。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise unreadable(f"{label} の check.banned はオブジェクトで書く")
    words, paths, exclude = raw.get("words"), raw.get("paths"), raw.get("exclude", [])
    for name, value in (("words", words), ("paths", paths), ("exclude", exclude)):
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise unreadable(f"{label} の check.banned.{name} は文字列の配列で書く")
    if not words or not paths:
        raise unreadable(f"{label} の check.banned は words と paths を持つ")
    return {"words": words, "paths": paths, "exclude": exclude}


def banned_in_term(term: dict, banned: dict | None) -> list[str]:
    """語と意味の欄にある禁止語。廃止した語の欄は置き換え先を引くために持つので見ない。"""
    return [w for w in (banned or {}).get("words", []) if w in term["term"] or w in term["meaning"]]


def banned_files(files, added: dict[str, set[int]] | None, root: Path, banned: dict | None) -> list[tuple[str, str, set[int] | None]]:
    """照らす (根からの相対パス, 本文, 見る行) の並び。`files`（--file）はすべての行、`added`（--diff）は足した行を見る。"""
    if not banned or not (files or added):
        return []
    if files:
        base = root.resolve()
        full = [Path(f).resolve() for f in files]
        pairs = [(p.relative_to(base).as_posix(), p, None) for p in full if p.is_relative_to(base)]
    else:
        pairs = [(rel, root / rel, lines) for rel, lines in sorted(added.items())]
    return [
        (rel, p.read_text(encoding="utf-8", errors="replace"), lines)
        for rel, p, lines in pairs
        if pathmatch.path_matches(rel, banned["paths"]) and not pathmatch.path_matches(rel, banned["exclude"]) and p.is_file()
    ]


def banned_findings(args, root: Path, banned: dict | None, added_lines, replacement) -> list[dict]:
    """禁止語の行の指摘（`args` は `check` の引数）。ファイルの種類を問わず、コードのコメント・文字列・コードブロックも照らす。"""
    items = []
    added = added_lines(root, args.diff) if banned and args.diff else None
    for rel, text, wanted in banned_files(args.file, added, root, banned):
        for n, line in enumerate(text.splitlines(), 1):
            if wanted is None or n in wanted:
                hits = [w for w in banned["words"] if w in line]
                items += [{"rule": "banned", "path": rel, "line": n, "term": w, "detail": f"禁止語。{replacement(w)} と書く"} for w in hits]
    return items
