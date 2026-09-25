"""`.serena/project.yml` の最上位の配列のキーを行単位で読み書きする（決定 8）。

YAML のライブラリを使わない。扱うのはブロックの形（`key:` の後に `- 値` の行）と空の
流れの形（`key: []`）だけで、それ以外の形を見つけたら UnsupportedShape を投げる。
対象のキーのブロックの外は 1 バイトも変えない。
"""
import re


class UnsupportedShape(Exception):
    """読めない形（流れの形の非空の配列・アンカー・写像など）。"""


_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")


def _strip_comment(value: str) -> str:
    value = value.strip()
    if value.startswith("#"):
        return ""
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _find_block(lines: list, key: str):
    """(開始行, 終了行の次, キーの行の値) を返す。キーが無ければ None。"""
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if not m or m.group(1) != key:
            continue
        end = i + 1
        j = i + 1
        while j < len(lines):
            cur = lines[j]
            if cur.startswith("-") or (cur[:1] in (" ", "\t") and cur.strip()):
                j += 1
                end = j
            elif not cur.strip():
                j += 1  # 空行は、後ろにまだ要素が続くときだけブロックに含める
            else:
                break
        return i, end, m.group(2)
    return None


def _scalar(item: str) -> str:
    value = _strip_comment(item)
    if not value or value[0] in "&*{[|>!" or value.endswith(":") or ": " in value:
        raise UnsupportedShape(f"読めない要素: {item!r}")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def read_list(text: str, key: str):
    """キーの値を文字列の配列で返す。キーが無ければ None。"""
    lines = text.splitlines()
    found = _find_block(lines, key)
    if found is None:
        return None
    start, end, head = found
    head = _strip_comment(head)
    items = []
    for line in lines[start + 1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not stripped.startswith("- "):
            raise UnsupportedShape(f"{key} の読めない行: {line!r}")
        items.append(_scalar(stripped[2:]))
    if head in ("", "null", "~"):
        return items
    if head == "[]" and not items:
        return []
    raise UnsupportedShape(f"{key} の読めない値: {head!r}")


def _render(key: str, values: list) -> list:
    if not values:
        return [f"{key}: []"]
    return [f"{key}:"] + [f"- {v}" for v in values]


def write_list(text: str, key: str, values: list) -> str:
    """キーのブロックを values で置き換える。キーが無ければ末尾に足す。"""
    read_list(text, key)  # 読めない形なら書く前に止める
    lines = text.splitlines()
    found = _find_block(lines, key)
    if found is None:
        body = text if not text or text.endswith("\n") else text + "\n"
        return body + "\n".join(_render(key, values)) + "\n"
    start, end, _ = found
    new_lines = lines[:start] + _render(key, values) + lines[end:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


def append_list(text: str, key: str, values: list) -> str:
    """無い要素だけをキーのブロックの末尾へ足す。**既存の行は注釈と引用符ごと残す。**

    要素の字下げは既存の最後の要素に合わせる。キーが無いか空（`key: []`）なら
    write_list と同じ形で書く。
    """
    current = read_list(text, key)  # 読めない形なら書く前に止める
    added = [v for v in values if v not in (current or [])]
    if not added:
        return text
    if not current:
        return write_list(text, key, added)
    lines = text.splitlines()
    start, end, _ = _find_block(lines, key)
    items = [line for line in lines[start + 1:end] if line.lstrip().startswith("- ")]
    indent = items[-1][:len(items[-1]) - len(items[-1].lstrip())]
    new_lines = lines[:end] + [f"{indent}- {v}" for v in added] + lines[end:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


def strip_blocks(text: str, keys) -> str:
    """keys のブロックを取り除いた残り（書き換えの前後の比較に使う）。"""
    lines = text.splitlines()
    for key in keys:
        found = _find_block(lines, key)
        if found:
            lines = lines[:found[0]] + lines[found[1]:]
    return "\n".join(lines)


def load_state(root):
    """導入先の設定を読む。project.yml が無ければ None。読めない形なら UnsupportedShape。

    返す辞書: languages（採った言語。project.local.yml の language_servers が上書きする）・
    marked（configure の目印 mcp_serena_excluded があるか）・excluded（外した言語の名前）。
    目印と外した言語は常に project.yml から読む。
    """
    from pathlib import Path
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
