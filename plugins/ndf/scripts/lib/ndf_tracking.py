"""ndf_tracking.py: 共有が前提の宣言（`.ndf/` の下）が `.gitignore` の対象になっていないかを確かめる（#2157）。

`.ndf/` は追跡するのが標準である（`skills/development-workflow/references/project-analysis.md` の「`.ndf/` の追跡」）。
ルートの `.gitignore` が `.ndf/` を外していると、書いた宣言はコミットできず、書いた人の手元でだけ効く。
宣言を書く・確かめるコマンドは `ignored_notes()` の行を結果の `items` へ足し、`summary_note()` を要約へ添える。

    notes = ndf_tracking.ignored_notes(root, [".ndf/mvv.md", ".ndf/mvv.json"])
    # → [{"kind": "note", "key": "gitignore", "name": ".ndf/mvv.md", "result": "ignored",
    #     "source": ".gitignore:3", "pattern": ".ndf/", "text": "..."}, ...]

除外していないとき・git の外・git が無いときは空を返す（確かめられないことを除外と読ませない）。
標準ライブラリだけで書く。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _rel(root: Path, path) -> str:
    p = Path(path)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)


def ignored(root, paths) -> list[dict]:
    """paths のうち `.gitignore` の対象のもの。行ごとに `{path, source, pattern}`（source は `<ファイル>:<行>`）。

    追跡済みのファイルは対象にならない（git check-ignore の既定）。否定の行（`!` で始まる）に当たったものは外さない。
    """
    root = Path(root)
    rels = [_rel(root, p) for p in paths]
    if not rels:
        return []
    try:
        p = subprocess.run(["git", "check-ignore", "-v", "--", *rels], cwd=root, capture_output=True, text=True)
    except OSError:
        return []
    if p.returncode not in (0, 1):
        return []
    out = []
    for line in p.stdout.splitlines():
        head, sep, path = line.partition("\t")
        if not sep:
            continue
        src, _, rest = head.partition(":")
        num, _, pattern = rest.partition(":")
        if pattern.startswith("!"):
            continue
        out.append({"path": path, "source": f"{src}:{num}", "pattern": pattern})
    return out


def ignored_notes(root, paths) -> list[dict]:
    """`ignored()` を結果の `items` の行（kind note・key gitignore）にする。"""
    return [
        {
            "kind": "note",
            "key": "gitignore",
            "name": h["path"],
            "result": "ignored",
            "source": h["source"],
            "pattern": h["pattern"],
            "text": f"{h['path']} は {h['source']} の `{h['pattern']}` が除外している（コミットできない。.gitignore は変えない）",
        }
        for h in ignored(root, paths)
    ]


def summary_note(notes: list[dict]) -> str:
    """要約へ添える 1 文（除外が無ければ空）。"""
    hits = [n for n in notes if n.get("key") == "gitignore"]
    if not hits:
        return ""
    names = "・".join(n["name"] for n in hits)
    rules = "・".join(dict.fromkeys(f"{n['source']} の `{n['pattern']}`" for n in hits))
    return f"。宣言をコミットできない（{rules} が除外している: {names}）"
