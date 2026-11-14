"""配る Skill の表から、ランタイムの定義を作り、置かれたものと照らす。

ランタイムごとに、表の `runtimes` の行が次を決める。

- `definition` があるランタイム: その定義（`plugin.json`）の `skills` に、そのランタイムへ配り `skills/` に実体がある
  Skill を表の並びで書く。null のランタイム（`skills/` をすべて読む）の定義は書かない
- `policy` があるランタイム: そのランタイムへ配る Skill のうち、明示の指示でだけ起動するもの（frontmatter の
  `disable-model-invocation` が真）の置き場に、暗黙の起動を止める policy と、`argument-hint` があれば明示の起動の
  既定の文を書く

`skills/` にまだ無い Skill（作り直しで移す前のもの）は配らない。表に無い Skill の置き場は誤りにし、要らなくなった
policy のファイルは照合で違いとして出し、作り直しで消す。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ndf.core.worker import SKILL_FILE, SKILLS_DIR
from ndf.domains.manifests import front_matter, table


class RenderError(Exception):
    """表と実体が食い違う（表に無い Skill の置き場がある）・定義が読めない。"""


def present(plugin_root: Path) -> set[str]:
    """`skills/` に実体（`SKILL.md`）がある Skill の名前。"""
    base = plugin_root / SKILLS_DIR
    return {p.parent.name for p in base.glob(f"*/{SKILL_FILE}")} if base.is_dir() else set()


def policy(fields: dict) -> Optional[str]:
    """Skill の frontmatter から policy のファイルの中身。明示の指示でだけ起動する Skill でなければ None。"""
    if fields.get("disable-model-invocation") is not True:
        return None
    data: dict = {"policy": {"allow_implicit_invocation": False}}
    hint = fields.get("argument-hint")
    if hint:
        data["interface"] = {"default_prompt": str(hint)}
    return front_matter.dump(data)


def expected(plugin_root: Path) -> dict[Path, str]:
    """作るファイル → 中身。表に無い Skill の置き場があれば `RenderError`。"""
    return _expected(plugin_root)[1]


def _expected(plugin_root: Path) -> tuple[table.Table, dict[Path, str]]:
    """読んだ表と、作るファイル → 中身。"""
    found = table.load(plugin_root)
    have = present(plugin_root)
    unlisted = sorted(have - {s.name for s in found.skills})
    if unlisted:
        raise RenderError(f"配る Skill の表（{table.TABLE_REL}）に無い Skill の置き場がある: {', '.join(unlisted)}")
    out: dict[Path, str] = {}
    for runtime in found.runtimes:
        names = [n for n in found.for_runtime(runtime.name) if n in have]
        if runtime.definition is not None:
            path = plugin_root / runtime.definition
            out[path] = _definition(path, names)
        if runtime.policy is not None:
            out.update(_policies(plugin_root, names, runtime.policy))
    return found, out


def _definition(path: Path, names: list[str]) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RenderError(f"ランタイムの定義を読めない: {path}: {exc}") from exc
    data["skills"] = [f"./{SKILLS_DIR}/{n}" for n in names]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _policies(plugin_root: Path, names: list[str], rel: str) -> dict[Path, str]:
    out = {}
    for name in names:
        skill_md = plugin_root / SKILLS_DIR / name / SKILL_FILE
        text = policy(front_matter.read(skill_md.read_text(encoding="utf-8"), str(skill_md)))
        if text is not None:
            out[plugin_root / SKILLS_DIR / name / rel] = text
    return out


def _extra(plugin_root: Path, found_table: table.Table, want: dict[Path, str]) -> list[Path]:
    """作るものに無い policy のファイル（配らなくなった・明示の指示だけでなくなった Skill のもの）。"""
    base = plugin_root / SKILLS_DIR
    rels = {r.policy for r in found_table.runtimes if r.policy is not None}
    found = [p for rel in sorted(rels) for p in base.glob(f"*/{rel}")] if base.is_dir() else []
    return sorted(p for p in found if p not in want)


def _differ(want: dict[Path, str]) -> list[Path]:
    """作るファイルのうち、無いか中身が違うもの（`want` の並び）。"""
    return [p for p, text in want.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]


def stale(plugin_root: Path) -> list[Path]:
    """作り直した結果と違う・無い・余ったファイル。"""
    found, want = _expected(plugin_root)
    return sorted(_differ(want) + _extra(plugin_root, found, want))


def write(plugin_root: Path) -> list[Path]:
    """違うファイルを作り直し、余ったファイルを消す。書いた・消したファイルを返す。"""
    found, want = _expected(plugin_root)
    written = _write_changed(want)
    removed = [_remove(path) for path in _extra(plugin_root, found, want)]
    return sorted(written + removed)


def _write_changed(want: dict[Path, str]) -> list[Path]:
    """無いか中身が違うファイルを書き、書いたファイルを返す。"""
    written = []
    for path in _differ(want):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(want[path], encoding="utf-8")
        written.append(path)
    return written


def _remove(path: Path) -> Path:
    """ファイルを消し、置き場が空になれば置き場も消す。消したファイルを返す。"""
    path.unlink()
    if not any(path.parent.iterdir()):
        path.parent.rmdir()
    return path
