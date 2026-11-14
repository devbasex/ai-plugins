"""配る Skill の表から、ランタイムの定義を作り、置かれたものと照らす。

    作るもの                                       中身
    .codex-plugin/plugin.json の skills              Codex へ配り、skills/ に実体がある Skill（表の並び）
    skills/<名前>/agents/openai.yaml                  Codex へ配る Skill のうち、明示の指示でだけ起動するもの
                                                    （frontmatter の disable-model-invocation が真）の暗黙の起動を止める policy と、
                                                    argument-hint があれば明示の起動の既定の文

Claude Code は `skills/` をすべて読むので、`.claude-plugin/plugin.json` に並びを書かない。`skills/` にまだ無い Skill
（作り直しで移す前のもの）は配らない。表に無い Skill の置き場と、配らなくなった Skill の `agents/openai.yaml` は
照合で違いとして出し、作り直しで消す。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ndf.domains.manifests import front_matter, table

CODEX = "codex"
CODEX_MANIFEST = Path(".codex-plugin") / "plugin.json"
SKILLS_DIR = "skills"
POLICY_REL = Path("agents") / "openai.yaml"


class RenderError(Exception):
    """表と実体が食い違う（表に無い Skill の置き場がある）・定義が読めない。"""


def present(plugin_root: Path) -> set[str]:
    """`skills/` に実体（`SKILL.md`）がある Skill の名前。"""
    base = plugin_root / SKILLS_DIR
    return {p.parent.name for p in base.glob("*/SKILL.md")} if base.is_dir() else set()


def policy(fields: dict) -> Optional[str]:
    """Skill の frontmatter から `agents/openai.yaml` の中身。明示の指示でだけ起動する Skill でなければ None。"""
    if fields.get("disable-model-invocation") is not True:
        return None
    data: dict = {"policy": {"allow_implicit_invocation": False}}
    hint = fields.get("argument-hint")
    if hint:
        data["interface"] = {"default_prompt": str(hint)}
    return front_matter.dump(data)


def expected(plugin_root: Path) -> dict[Path, str]:
    """作るファイル → 中身。表に無い Skill の置き場があれば `RenderError`。"""
    rows = table.load(plugin_root)
    have = present(plugin_root)
    unlisted = sorted(have - {r.name for r in rows})
    if unlisted:
        raise RenderError(f"配る Skill の表（{table.TABLE_REL}）に無い Skill の置き場がある: {', '.join(unlisted)}")
    codex = [n for n in table.for_runtime(rows, CODEX) if n in have]
    out = {plugin_root / CODEX_MANIFEST: _codex_manifest(plugin_root, codex)}
    for name in codex:
        skill_md = plugin_root / SKILLS_DIR / name / "SKILL.md"
        text = policy(front_matter.read(skill_md.read_text(encoding="utf-8"), str(skill_md)))
        if text is not None:
            out[plugin_root / SKILLS_DIR / name / POLICY_REL] = text
    return out


def _codex_manifest(plugin_root: Path, names: list[str]) -> str:
    path = plugin_root / CODEX_MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RenderError(f"Codex の定義を読めない: {path}: {exc}") from exc
    data["skills"] = [f"./{SKILLS_DIR}/{n}" for n in names]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _extra(plugin_root: Path, want: dict[Path, str]) -> list[Path]:
    """作るものに無い `agents/openai.yaml`（配らなくなった・明示の指示だけでなくなった Skill のもの）。"""
    base = plugin_root / SKILLS_DIR
    found = base.glob(f"*/{POLICY_REL.as_posix()}") if base.is_dir() else []
    return sorted(p for p in found if p not in want)


def stale(plugin_root: Path) -> list[Path]:
    """作り直した結果と違う・無い・余ったファイル。"""
    want = expected(plugin_root)
    differ = [p for p, text in want.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
    return sorted(differ + _extra(plugin_root, want))


def write(plugin_root: Path) -> list[Path]:
    """違うファイルを作り直し、余ったファイルを消す。書いた・消したファイルを返す。"""
    want = expected(plugin_root)
    changed = []
    for path, text in want.items():
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            changed.append(path)
    for path in _extra(plugin_root, want):
        path.unlink()
        if not any(path.parent.iterdir()):
            path.parent.rmdir()
        changed.append(path)
    return sorted(changed)
