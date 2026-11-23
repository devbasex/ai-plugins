"""空かもしれない変数の `rm` を止める guard（#3877 の決定 4）。

Claude Code は「Dangerous rm operation on possibly-empty variable path」の確認を端末の画面に出し、
その画面はモデルへ届かないのでレーンが止まる。`PreToolUse` の hook はその確認より先に働く（v2.1.330 で実測）ため、
同じ形をここで止めて、書き直しの形をツールの結果として返す。

- 止めるのは、`rm` の削除の対象に、展開が効く位置（引用の外か二重引用の中）の空を返しうる名前付きの変数の展開を含む語があるときだけ。
  ラッパー（`command`・`timeout` など）の後ろと `bash -c` 系・`eval` の中の `rm` も見る。`--` より前の、名前が字面で決まるオプションは対象でない
- 空を返さない形（`${NAME:?…}`・空でない既定値の `${NAME:-…}`・`${NAME:=…}`・`${#NAME}`）は通す。既定値・代替値の中の展開も同じ規則で見る
- 単引用・ANSI-C 引用（`$'…'`）・エスケープで文字のまま渡るドル記号と、特殊パラメータ（`$$`・`$1` など）は通す
- 値は見ない。字面の形だけで判定し、シェルを起動しない
"""

from __future__ import annotations

import re

from ndf.domains.hooks.verdict import Verdict, deny

# 展開の名前の 1 字目と 2 字目以降
_HEAD = re.compile(r"[A-Za-z_]")
_TAIL = re.compile(r"[A-Za-z0-9_]")
# `${…}` の中身の先頭の名前付きの変数と、パラメータ（名前付きの変数・位置パラメータ・特殊パラメータ）
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PARAMETER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-]")
# 値が空にならない特殊パラメータ（PID・引数の数・終了コード）
_NEVER_EMPTY = "$#?"
# 二重引用の中で `\` が文字にする字
_ESCAPABLE = '$`"\\'
# `$` の後の 1 字で決まる特殊パラメータ（名前付きの変数でない）
_SPECIAL = "$#?!-@*0123456789"
# `${…}` の中身のうち、既定値・代替値として評価される部分を持つ形（名前付きの変数・位置パラメータ・特殊パラメータのどれでも）
_OPERAND = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?$!-]):?[-=+](.*)", re.DOTALL)
# 二重引用の中の `${…}` の既定値・代替値を読む状態。`'` は文字のまま残り、`"` は展開の効き方を変えない
_IN_DOUBLE = '${"'
_HELP = (
    "報告の保存先の用意は ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前> で打つ。"
    'ほかの削除は "${NAME:?}" の形か固定のパスにする'
)


def expanded_variables(word: str, quote: str = "") -> list[str]:
    """語の字面の、展開が効く位置にある名前付きの変数の展開（`$NAME`）と `${…}` の字面。

    `${…:-…}`・`${…:+…}` などの既定値・代替値の中で評価される展開も、外側の字面の後ろに続けて返す。
    単引用・ANSI-C 引用（`$'…'`）の中・`\\$` で逃がした `$`・特殊パラメータ（`$$`・`$1` など）・閉じていない `${` は返さない。
    状態は「引用の外」「単引用の中」「ANSI-C 引用の中」「二重引用の中」「二重引用の中の `${…}` の既定値」の 5 つ。
    `quote` は読み始めの状態。
    """
    return [text for text, _ in _scan(word, quote)]


def _scan(word: str, quote: str = "") -> list[tuple[str, bool]]:
    """`expanded_variables` の展開の字面と、その展開が二重引用の中か（既定値の空の判定が引用の状態で変わる）。"""
    found: list[tuple[str, bool]] = []
    i = 0
    while i < len(word):
        char = word[i]
        if quote in ("'", "$'"):
            if quote == "$'" and char == "\\":
                i += 2
                continue
            quote = "" if char == "'" else quote
            i += 1
        elif char == "\\" and (quote in ("", _IN_DOUBLE) or word[i + 1 : i + 2] in _ESCAPABLE):
            i += 2
        elif quote == _IN_DOUBLE and char in "'\"":
            i += 1
        elif char == "$" and quote == "" and word[i + 1 : i + 2] == "'":
            quote = "$'"
            i += 2
        elif char in "'\"" and (quote == "" or quote == char):
            quote = char if quote == "" else ""
            i += 1
        elif char == "$":
            i = _expansion(word, i, found, quote in ('"', _IN_DOUBLE))
        else:
            i += 1
    return found


def _expansion(word: str, start: int, found: list[tuple[str, bool]], in_double: bool) -> int:
    """`start` の `$` から展開を読み、名前付きの変数と `${…}` の字面を `found` へ足す。次に読む位置を返す。

    `in_double` は二重引用の中か。`${…}` の終端の探索と、既定値・代替値の読み始めの状態に引き継ぐ。
    """
    nxt = word[start + 1 : start + 2]
    if nxt == "{":
        end = _closing_brace(word, start + 2, in_double)
        if end < 0:
            return start + 2
        found.append((word[start : end + 1], in_double))
        operand = _OPERAND.fullmatch(word[start + 2 : end])
        if operand:
            found.extend(_scan(operand.group(1), _IN_DOUBLE if in_double else ""))
        return end + 1
    if nxt and nxt in _SPECIAL:
        return start + 2
    if not _HEAD.fullmatch(nxt):
        return start + 1
    end = start + 2
    while end < len(word) and _TAIL.fullmatch(word[end]):
        end += 1
    found.append((word[start:end], in_double))
    return end


def _closing_brace(word: str, start: int, in_double: bool) -> int:
    """`${` の中身が始まる `start` から、対応する `}` の位置。無ければ -1。

    入れ子の `${…}` を数え、引用の中の `}` は終端にしない。二重引用の中（`in_double`）では `'` は引用を始めない。
    """
    depth = 1
    quote = ""
    opened = 0  # 中の二重引用を始めたときの入れ子の深さ
    i = start
    while i < len(word):
        char = word[i]
        if quote in ("'", "$'"):
            if quote == "$'" and char == "\\":
                i += 2
                continue
            quote = "" if char == "'" else quote
        elif char == "\\":
            i += 1
        elif char == '"':
            quote, opened = ('"', depth) if quote == "" else ("", 0)
        elif quote == "" and not in_double and char == "'":
            quote = "$'" if word[i - 1 : i] == "$" else "'"
        elif word.startswith("${", i):
            depth += 1
            i += 1
        elif char == "}" and (quote == "" or depth > opened):
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def can_be_empty(expansion: str, in_double: bool = False) -> bool:
    """`expanded_variables` が返した 1 つの展開が、空を返しうる名前付きの変数の展開か（報告の対象か）。

    `$NAME`・`${NAME}`・`${NAME:+…}`・`${NAME-…}`・`${NAME%…}` と、空を返しうる既定値の `${NAME:-…}`（`${NAME:-}`・
    `${NAME:-${1}}`・引用の外の `${NAME:-''}`）は真。`${NAME:?…}`・必ず字が残る既定値の `${NAME:-…}`・`${NAME:=…}`・
    `${#NAME}` は偽。位置パラメータ・特殊パラメータ・間接参照（`${1}`・`${@}`・`${!NAME}`）は名前付きの変数でないので偽
    （既定値の中の展開は `expanded_variables` が別に返す）。`in_double` は展開が二重引用の中か。
    """
    named = not expansion.startswith("${") or _NAME.match(expansion, 2) is not None
    return named and _may_be_empty(expansion, in_double)


def _may_be_empty(expansion: str, in_double: bool) -> bool:
    """1 つの展開が空を返しうるか。パラメータの種類によらない（`${1}` は真、`$$`・`${#…}` は偽）。"""
    if not expansion.startswith("${"):
        return expansion[1:2] not in _NEVER_EMPTY
    body = expansion[2:-1]
    if body.startswith("#"):
        return False
    param = _PARAMETER.match(body)
    if param is None or body.startswith("!"):
        return True
    rest = body[param.end() :]
    if rest.startswith(":?"):
        return False
    if rest[:2] in (":-", ":="):
        return not _has_text(rest[2:], _IN_DOUBLE if in_double else "")
    return rest != "" or param.group() not in _NEVER_EMPTY


def _has_text(word: str, quote: str) -> bool:
    """既定値の字面 `word` を `quote` の状態から読み、展開の後に必ず 1 字以上が残るか。

    引用とエスケープを外した後の字（二重引用の中の `'` は字）か、空を返さない展開があれば真。行の継続（`\\` と改行）は
    字を残さない。コマンド置換（`$(…)`・`` `…` ``）は値が分からないので、含めば偽にする。
    """
    if "$(" in word or "`" in word:
        return False
    i = 0
    while i < len(word):
        char, nxt = word[i], word[i + 1 : i + 2]
        if quote in ("'", "$'"):
            if char != "'":
                return True
            quote = ""
            i += 1
        elif char == "\\":
            if nxt != "\n":
                return True
            i += 2
        elif char == "$" and nxt == "'" and quote == "":
            quote = "$'"
            i += 2
        elif char == '"' and quote in ("", '"', _IN_DOUBLE):
            quote = {"": '"', '"': ""}.get(quote, quote)
            i += 1
        elif char == "'" and quote == "":
            quote = "'"
            i += 1
        elif char == "$" and (nxt == "{" or _HEAD.fullmatch(nxt) or (nxt and nxt in _SPECIAL)):
            found: list[tuple[str, bool]] = []
            end = _expansion(word, i, found, quote in ('"', _IN_DOUBLE))
            if end == i + 2 and nxt in _SPECIAL:
                found = [("$" + nxt, False)]
            if found and not _may_be_empty(*found[0]):
                return True
            i = end
        else:
            return True
    return False


def _is_option(plain: str, text: str) -> bool:
    """`-` で始まる語で、オプションの名前（`--name=値` の `=` より前、短いオプションは語全体）が字面で決まるか。"""
    if not plain.startswith("-") or plain == "-":
        return False
    name = text.split("=", 1)[0] if plain.startswith("--") else text
    return "$" not in name and "`" not in name


def _operands(argv: tuple[str, ...], source: tuple[str, ...]) -> list[str]:
    """`rm` の引数のうち削除の対象の字面。`--` より前の、名前が字面で決まるオプションは外す（`-"$NAME"` は対象）。"""
    found: list[str] = []
    options = True
    for plain, text in zip(argv, source):
        if options and plain == "--":
            options = False
        elif not (options and _is_option(plain, text)):
            found.append(text)
    return found


def _risky(word: str) -> list[str]:
    return [text for text, in_double in _scan(word) if can_be_empty(text, in_double)]


def risky_removal(script) -> str | None:
    """`rm` の削除の対象にある、空になりうる変数を含む語の字面。無ければ None。

    ラッパー（`command`・`timeout` など）を外した後の `rm` と、`bash -c` 系・`eval` の中の `rm` も見る（`ShellScript.waits`）。
    """
    for command in script.waits:
        if command.body_name != "rm":
            continue
        for word in _operands(command.body_argv[1:], command.body_source[1:]):
            if _risky(word):
                return word
    return None


def check(ctx, svc) -> Verdict | None:
    word = risky_removal(ctx.shell)
    if word is None:
        return None
    names = "・".join(_risky(word))
    return deny(
        f"空になりうる変数（{names}）をパスに含む rm を止めた。"
        f"変数が空だと別のパスを消すため、Claude Code が端末の画面で確認を出してレーンが止まる。{_HELP}"
    )
