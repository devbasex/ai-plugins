"""Skill の frontmatter（`SKILL.md` の先頭の `---` で囲んだ YAML）の読み書き。YAML は ruamel-yaml だけが扱う。"""

from __future__ import annotations

import io
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

FENCE = "---"
# 書くときに長い文字列を折り返さない幅
NO_WRAP = 4096


class FrontMatterError(Exception):
    """frontmatter が無い・閉じていない・YAML のマッピングでない。"""


def read(text: str, where: str) -> dict[str, Any]:
    """frontmatter の中身。`where` は誤りの文に添える置き場。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise FrontMatterError(f"frontmatter が無い: {where}")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == FENCE)
    except StopIteration:
        raise FrontMatterError(f"frontmatter が閉じていない: {where}") from None
    try:
        data = YAML(typ="safe", pure=True).load("\n".join(lines[1:end]))
    except YAMLError as exc:
        raise FrontMatterError(f"frontmatter の YAML を読めない: {where}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontMatterError(f"frontmatter が YAML のマッピングでない: {where}")
    return data


def dump(data: dict[str, Any]) -> str:
    """YAML の文字（文字列の値は二重引用符・折り返さない）。"""
    yaml = YAML()
    yaml.width = NO_WRAP
    out = io.StringIO()
    yaml.dump(_quoted(data), out)
    return out.getvalue()


def _quoted(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _quoted(v) for k, v in value.items()}
    if isinstance(value, str):
        return DoubleQuotedScalarString(value)
    return value
