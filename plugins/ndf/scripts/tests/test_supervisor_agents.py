"""3 層の supervisor の 2 つの定義が、寿命だけを違えて同じ本文を持つこと（#954 の AC1）。

寿命 1 時間（`experimental.cacheTtl: 1h`）は収束ループから始める区間の定義だけが持つ。
本文は手で 2 つ書き、ここで一致を確かめる（設計の決定 12）。
"""
from __future__ import annotations

import pathlib
import re

PLUGIN = pathlib.Path(__file__).resolve().parents[2]
SHORT = PLUGIN / "agents" / "supervisor.md"
LONG = PLUGIN / "agents" / "supervisor-waits.md"
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


def split(path: pathlib.Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    assert m, f"{path.name} に frontmatter が無い"
    return m.group(1), text[m.end():]


def top_level(fm: str) -> dict[str, str]:
    return dict(
        (k.strip(), v.strip())
        for k, v in (line.split(":", 1) for line in fm.splitlines() if ":" in line and not line.startswith(" "))
    )


def cache_ttl(fm: str) -> str | None:
    """`experimental:` の下の字下げの行から `cacheTtl` を読む。"""
    inside = False
    for line in fm.splitlines():
        if not line.startswith(" "):
            inside = line.split(":", 1)[0].strip() == "experimental"
            continue
        if inside and line.strip().startswith("cacheTtl:"):
            return line.split(":", 1)[1].strip()
    return None


def test_only_the_waits_definition_has_the_one_hour_ttl():
    short_fm, _ = split(SHORT)
    long_fm, _ = split(LONG)
    assert top_level(short_fm).get("name") == "supervisor"
    assert top_level(long_fm).get("name") == "supervisor-waits"
    assert "experimental" not in top_level(short_fm)
    assert cache_ttl(short_fm) is None
    assert cache_ttl(long_fm) == "1h"


def test_the_two_bodies_are_identical():
    assert split(SHORT)[1].encode() == split(LONG)[1].encode()


def test_the_definitions_do_not_restrict_tools():
    # supervisor は Skill と Agent を使う。general-purpose と同じ Tool を持たせる
    for path in (SHORT, LONG):
        fm = top_level(split(path)[0])
        assert "tools" not in fm
        assert "disallowedTools" not in fm
