"""frontmatter と YAML の読み書きの包み（#1142 の決定 19・種類 13）。ruamel.yaml を呼ぶのはこのモジュールだけである。

往復の読み書き（round-trip）で、書き戻しても引用符・コメント・キーの順を保つ。

- frontmatter は、文書の 1 行目の `---` から次の `---` の行まで（`...` でも閉じる）。無ければ None
- **値は YAML の型で返る。** 今の `skill-stats.py:parse_front_matter` は値をすべて文字列で返し、
  `name: true` は `"true"`、`tags: [a, b]` は `"[a, b]"` になった。この包みでは `True` と `["a", "b"]` になる。
  文字列が要る側は `front_matter_text()` で字面を読む
- 読めない YAML は `YamlError`（日本語の 1 行。行と桁を添える）

使う側は `deps.require("yamlio")` を先に呼ぶ。
"""
from __future__ import annotations

import io
from typing import Any, NamedTuple

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError, YAMLError


class YamlError(ValueError):
    """YAML として読めない。"""


class FrontMatter(NamedTuple):
    raw: str          # 区切りの行の間の YAML の字面
    body: str         # 閉じの区切りの次の行からの本文
    body_line: int    # 本文の最初の行の番号（0 始まり）


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_yaml(text: str, where: str = "") -> Any:
    """YAML の文字列を読む（往復の型。辞書は `CommentedMap`、配列は `CommentedSeq` で、`dict` / `list` として使える）。"""
    try:
        return _yaml().load(text)
    except MarkedYAMLError as exc:
        mark = exc.problem_mark
        pos = f"{mark.line + 1} 行 {mark.column + 1} 桁" if mark is not None else "位置不明"
        raise YamlError(f"{where or 'YAML'}: 読めない（{pos}: {exc.problem or exc.context}）") from None
    except YAMLError as exc:
        raise YamlError(f"{where or 'YAML'}: 読めない（{exc}）") from None


def dump_yaml(data: Any) -> str:
    """読んだ値（か普通の辞書と配列）を YAML の文字列へ書く。"""
    buf = io.StringIO()
    _yaml().dump(data, buf)
    return buf.getvalue()


def split_front_matter(text: str) -> FrontMatter | None:
    """文書の先頭の frontmatter を分ける。無い・閉じていなければ None。"""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n").rstrip() != "---":
        return None
    for k in range(1, len(lines)):
        if lines[k].rstrip("\r\n").rstrip() in ("---", "..."):
            return FrontMatter("".join(lines[1:k]), "".join(lines[k + 1:]), k + 1)
    return None


def front_matter_text(text: str) -> str | None:
    """frontmatter の YAML の字面（区切りの行を除く）。無ければ None。"""
    fm = split_front_matter(text)
    return None if fm is None else fm.raw


def read_front_matter(text: str, where: str = "") -> dict:
    """frontmatter を辞書で読む（値は YAML の型）。無い・空なら `{}`。辞書でなければ `YamlError`。"""
    fm = split_front_matter(text)
    if fm is None:
        return {}
    data = load_yaml(fm.raw, where or "frontmatter")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise YamlError(f"{where or 'frontmatter'}: キーと値の組にする（{type(data).__name__} だった）")
    return data


def update_front_matter(text: str, updates: dict[str, Any], where: str = "") -> str:
    """frontmatter のキーを書き換えた文書を返す（値に None を渡したキーは消す）。本文と、ほかのキーの字面は保つ。

    frontmatter が無ければ先頭に足す。
    """
    fm = split_front_matter(text)
    data = load_yaml(fm.raw, where or "frontmatter") if fm is not None else None
    if data is None:
        data = load_yaml("{}")
        data.fa.set_block_style()
    if not isinstance(data, dict):
        raise YamlError(f"{where or 'frontmatter'}: キーと値の組にする（{type(data).__name__} だった）")
    for key, value in updates.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    head = "---\n" + (dump_yaml(data) if len(data) else "") + "---\n"
    return head + (fm.body if fm is not None else text)
