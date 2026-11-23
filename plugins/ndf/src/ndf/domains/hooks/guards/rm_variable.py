"""空かもしれない変数の `rm` を止める guard（#3877 の決定 4）。

Claude Code は「Dangerous rm operation on possibly-empty variable path」の確認を端末の画面に出し、
その画面はモデルへ届かないのでレーンが止まる。`PreToolUse` の hook はその確認より先に働く（v2.1.330 で実測）ため、
同じ形をここで止めて、書き直しの形をツールの結果として返す。

- 止めるのは、`rm` の削除の対象に、展開が効く位置（引用の外か二重引用の中）の `$NAME`・`${NAME}` を含む語があるときだけ
- `${NAME:?}`・`${NAME:-…}`・`${NAME:+…}` と、引用やエスケープで文字のまま渡るドル記号は通す（空にならないか、展開されない）
- 値は見ない。字面の形だけで判定し、シェルを起動しない
"""

from __future__ import annotations

import re

from ndf.domains.hooks.verdict import Verdict, deny

# 1 つの展開が空になりうるか。`$NAME`・`${NAME}` に当たり、`${NAME:?…}`・`${NAME:-…}`・`${NAME:+…}` に当たらない
_VARIABLE = re.compile(r"\A\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})\Z")
# 展開の名前の 1 字目と 2 字目以降
_HEAD = re.compile(r"[A-Za-z_]")
_TAIL = re.compile(r"[A-Za-z0-9_]")
# 二重引用の中で `\` が文字にする字
_ESCAPABLE = '$`"\\'
_HELP = (
    "報告の保存先の用意は ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前> で打つ。"
    'ほかの削除は "${NAME:?}" の形か固定のパスにする'
)


def expanded_variables(word: str) -> list[str]:
    """語の字面の、展開が効く位置にある `$NAME`・`${…}` の字面。

    単引用の中・`\\$` で逃がした `$`・閉じていない `${` は返さない。状態は「引用の外」「単引用の中」「二重引用の中」の 3 つ。
    """
    found: list[str] = []
    quote = ""
    i = 0
    while i < len(word):
        char = word[i]
        if quote == "'":
            quote = "" if char == "'" else quote
            i += 1
        elif char == "\\" and (quote == "" or (i + 1 < len(word) and word[i + 1] in _ESCAPABLE)):
            i += 2
        elif char in "'\"" and (quote == "" or quote == char):
            quote = char if quote == "" else ""
            i += 1
        elif char == "$":
            expansion, i = _expansion(word, i)
            if expansion:
                found.append(expansion)
        else:
            i += 1
    return found


def _expansion(word: str, start: int) -> tuple[str, int]:
    """`start` の `$` から読んだ展開の字面と、次に読む位置。展開でなければ字面は空。"""
    nxt = word[start + 1 : start + 2]
    if nxt == "{":
        end = word.find("}", start + 2)
        return ("", start + 2) if end < 0 else (word[start : end + 1], end + 1)
    if not _HEAD.fullmatch(nxt):
        return "", start + 1
    end = start + 2
    while end < len(word) and _TAIL.fullmatch(word[end]):
        end += 1
    return word[start:end], end


def risky_removal(script) -> str | None:
    """`rm` の削除の対象にある、空になりうる変数を含む語の字面。無ければ None。"""
    for command in script.commands:
        if not command.argv or command.argv[0].rsplit("/", 1)[-1] != "rm":
            continue
        for word in command.source[1:]:
            if any(_VARIABLE.fullmatch(found) for found in expanded_variables(word)):
                return word
    return None


def check(ctx, svc) -> Verdict | None:
    word = risky_removal(ctx.shell)
    if word is None:
        return None
    names = "・".join(found for found in expanded_variables(word) if _VARIABLE.fullmatch(found))
    return deny(
        f"空になりうる変数（{names}）をパスに含む rm を止めた。"
        f"変数が空だと別のパスを消すため、Claude Code が端末の画面で確認を出してレーンが止まる。{_HELP}"
    )
