"""compose のファイルの `services:` を YAML を読まずに字下げで拾う。標準ライブラリだけで動く。"""

from __future__ import annotations


def _is_blank_or_comment(line: str) -> bool:
    return not line.strip() or line.lstrip().startswith("#")


def _services_block_lines(text: str):
    """トップレベルの `services:` の中の行を (字下げ, 行) で返す（空行とコメントは除く）。"""
    inside = False
    for line in text.splitlines():
        if _is_blank_or_comment(line):
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            inside = line.rstrip() == "services:"
        elif inside:
            yield lead, line


def _iter_service_lines(text: str):
    """サービス名の行を (名前, None)、image の行を (直前のサービス名, image) で返す。"""
    indent, cur = None, None
    for lead, line in _services_block_lines(text):
        if indent is None:
            indent = lead
        stripped = line.strip()
        if lead == indent and stripped.endswith(":"):
            cur = stripped[:-1].strip("'\"")
            yield cur, None
        elif cur and stripped.startswith("image:"):
            yield cur, line.split(":", 1)[1].strip().strip("'\"")


def compose_services(text: str) -> list[tuple[str, str]]:
    """compose のファイルの `services:` の直下の名前と image（YAML を読まずに字下げで拾う）。"""
    out: list[str] = []
    images: dict[str, str] = {}
    for name, image in _iter_service_lines(text):
        if image is None:
            out.append(name)
        else:
            images[name] = image
    return [(s, images.get(s, "")) for s in out]
