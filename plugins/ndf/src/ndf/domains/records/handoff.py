"""引継ぎ文書を確かめる口と、テンプレートに無いセクションを退避する口（N8a・#1785・#1835）。

本文に書いたパスは、リポジトリの根からの相対パス（絶対パスはそのまま）で実在を確かめる。退避は退避先を先に
書き、書けてから本体を書き換える（退避先を書けなければ本体を変えない）。
"""

from __future__ import annotations

from pathlib import Path

from ndf.contracts import limits as limits_mod
from ndf.domains.records import handoff_decide as hd


def body_limits() -> hd.Limits:
    """本体の上限（コントラクトの limits の handoff.lines・handoff.bytes）。"""
    return hd.Limits(int(limits_mod.value("handoff.lines", "count")), int(limits_mod.value("handoff.bytes", "count")))


def check_file(path: Path, root: Path, limits: hd.Limits) -> hd.CheckReport:
    """引継ぎ文書 1 本を確かめる。読めなければ `OSError`。"""
    text = path.read_text(encoding="utf-8")

    def present(p: str) -> bool:
        target = Path(p).expanduser()
        return (target if target.is_absolute() else root / target).exists()

    return hd.check_body(text, present, limits)


def trim_file(path: Path, history: Path) -> list[str]:
    """テンプレートに無いセクションを退避先の末尾へ移し、移したセクションの見出しを返す。移すセクションが無ければ何も書かない。

    退避先へ書けなければ `OSError`（本体は書き換えない）。退避先が本体と同じファイル（相対・絶対・リンク経由を
    問わない）なら、本体の書き換えが退避したセクションを消すため、何も書かずに `OSError` で止まる。
    """
    if history.resolve() == path.resolve():
        raise OSError(f"退避先が引継ぎ文書と同じファイル: {history}")
    text = path.read_text(encoding="utf-8")
    kept, moved = hd.trim(text)
    if not moved:
        return []
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as f:
        f.write("\n\n".join(moved) + "\n\n")
    path.write_text(kept, encoding="utf-8")
    return [block.splitlines()[0] for block in moved]
