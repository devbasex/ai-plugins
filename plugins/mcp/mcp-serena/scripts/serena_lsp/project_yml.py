"""`.serena/project.yml` の最上位の配列のキーを読み書きする（決定 8・#1142 の決定 25）。

読み取りは ruamel.yaml（往復の読み取り。注釈と引用符の位置を保つ）が行い、書き込みは読み取った位置を使った
行の操作で行う。**対象のキーのブロックの外は 1 バイトも変えない。** 扱うのはブロックの形（`key:` の後に `- 値` の行）と
空の値（`key:`・`key: []`・`null`）で、要素は文字列だけである。それ以外の形（流れの形の非空の配列・アンカー・別名・
写像・スカラーの値）は UnsupportedShape を投げる。

ruamel.yaml は mcp-serena の環境（`pyproject.toml` と `uv.lock`。`serena_lsp/env.py` が用意する）にある。import は
読むときまで遅らせる（hook は控えが使えれば読まない。`hooks.py`）。
"""
from __future__ import annotations

from pathlib import Path


class UnsupportedShape(Exception):
    """読めない形（流れの形の非空の配列・アンカー・写像など）。"""


def _parse(text: str):
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.error import YAMLError
    try:
        data = YAML(typ="rt", pure=True).load(text)
    except YAMLError as exc:
        raise UnsupportedShape(f"YAML として読めない: {str(exc).splitlines()[0]}") from None
    if data is None:
        return CommentedMap()
    if not isinstance(data, CommentedMap):
        raise UnsupportedShape("最上位が写像でない")
    return data


def _block(data, key: str):
    """(キーの行, ブロックの終わりの次の行, 要素) を返す。キーが無ければ None。"""
    from ruamel.yaml.comments import CommentedSeq
    if key not in data:
        return None
    start = data.lc.key(key)[0]
    value = data[key]
    if value is None:
        return start, start + 1, []
    if not isinstance(value, CommentedSeq):
        raise UnsupportedShape(f"{key} の読めない値: {value!r}")
    if value.anchor.value:
        raise UnsupportedShape(f"{key} にアンカーがある")
    if value.fa.flow_style():
        if len(value):
            raise UnsupportedShape(f"{key} が流れの形の配列")
        return start, start + 1, []
    end = start + 1
    for i, item in enumerate(value):
        if not isinstance(item, str) or getattr(getattr(item, "anchor", None), "value", None):
            raise UnsupportedShape(f"{key} の読めない要素: {item!r}")
        end = value.lc.item(i)[0] + 1
    return start, end, [str(item) for item in value]


def read_list(text: str, key: str):
    """キーの値を文字列の配列で返す。キーが無ければ None。"""
    found = _block(_parse(text), key)
    return None if found is None else found[2]


def _render(key: str, values: list) -> list:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:"] + [f"- {v}" for v in values]


def _join(lines: list, text: str) -> str:
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def write_list(text: str, key: str, values: list) -> str:
    """キーのブロックを values で置き換える。キーが無ければ末尾に足す。"""
    found = _block(_parse(text), key)  # 読めない形なら書く前に止める
    if found is None:
        body = text if not text or text.endswith("\n") else text + "\n"
        return body + "\n".join(_render(key, values)) + "\n"
    start, end, _ = found
    lines = text.splitlines()
    return _join(lines[:start] + _render(key, values) + lines[end:], text)


def append_list(text: str, key: str, values: list) -> str:
    """無い要素だけをキーのブロックの末尾へ足す。**既存の行は注釈と引用符ごと残す。**

    要素の字下げは既存の最後の要素に合わせる。キーが無いか空（`key: []`）なら write_list と同じ形で書く。
    """
    found = _block(_parse(text), key)  # 読めない形なら書く前に止める
    current = found[2] if found else []
    added = [v for v in values if v not in current]
    if not added:
        return text
    if not current:
        return write_list(text, key, added)
    _, end, _ = found
    lines = text.splitlines()
    last = lines[end - 1]
    indent = last[:len(last) - len(last.lstrip())]
    return _join(lines[:end] + [f"{indent}- {v}" for v in added] + lines[end:], text)


def strip_blocks(text: str, keys) -> str:
    """keys のブロックを取り除いた残り（書き換えの前後の比較に使う）。"""
    data = _parse(text)
    spans = sorted((b[0], b[1]) for b in (_block(data, k) for k in keys) if b)
    lines = text.splitlines()
    for start, end in reversed(spans):
        lines = lines[:start] + lines[end:]
    return "\n".join(lines)


def load_state(root):
    """導入先の設定を読む。project.yml が無ければ None。読めない形なら UnsupportedShape。

    返す辞書: languages（採った言語。project.local.yml の language_servers が上書きする）・
    marked（configure の目印 mcp_serena_excluded があるか）・excluded（外した言語の名前）。
    目印と外した言語は常に project.yml から読む。
    """
    serena = Path(root) / ".serena"
    yml = serena / "project.yml"
    if not yml.is_file():
        return None
    text = yml.read_text(encoding="utf-8")
    languages = read_list(text, "language_servers") or []
    local = serena / "project.local.yml"
    if local.is_file():
        override = read_list(local.read_text(encoding="utf-8"), "language_servers")
        if override is not None:
            languages = override
    excluded = read_list(text, "mcp_serena_excluded")
    return {"languages": languages, "marked": excluded is not None,
            "excluded": [item.split()[0] for item in excluded or [] if item.split()]}
