"""ヒアドキュメントの guard（#3699）。区切りを引用符で囲まないヒアドキュメントの本文に、コマンド置換があれば止める。

区切りを引用符で囲まない（`<<EOF`・`<<-EOF`）と、シェルは本文のバッククォートと `$(` をコマンドとして実行する。
AI が文書の断片（`` `パス` ``）を本文に入れると、そのパスがコマンドとして動く。

- 止めるのは、本文（区切りの行まで）にバッククォートか `$(` がある呼び出しだけ
- 引用符で囲んだ区切り（`<<'EOF'`・`<<"EOF"`）・変数だけの本文・ヒアドキュメントの無いコマンドは通す
- here-string（`<<<`）はヒアドキュメントではないので見ない
- 止めた記録は hook の計測（guard の名前 `heredoc`・判定 `deny`）に残る
"""

from __future__ import annotations

import re

from ndf.domains.hooks.verdict import Verdict, deny

_OPEN = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_HELP = (
    "区切りを引用符で囲んで（<<'EOF'）打ち直す。"
    "本文で値を使うなら、環境変数で渡してスクリプトの中で読む（例: NAME=値 python3 - <<'EOF' の中で os.environ[\"NAME\"]）"
)


def unquoted_substitution(command: str) -> str | None:
    """区切りを引用符で囲まず、本文にバッククォートか `$(` があるヒアドキュメントの区切りの語。無ければ None。

    開きを見つけたら（引用符の有無によらず）区切りの行までの本文を読み飛ばし、その次の行から開きを探し直す。
    本文の中の `<<EOF` の文字（ヒアドキュメントを説明する文書など）は、シェルが開きとして読まないので開きとしない。
    1 行に開きが並べば、本文はその順に続く。
    """
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        start = i + 1
        for m in _OPEN.finditer(lines[i]):
            end = _end(lines, start, m.group(2))
            body = "\n".join(lines[start:end])
            if not m.group(1) and ("`" in body or "$(" in body):
                return m.group(2)
            start = end + 1
        i = start
    return None


def _end(lines: list[str], start: int, word: str) -> int:
    """`start` 行目から探した、区切りの語 `word` だけの行の番号。区切りが無ければ行の数（末尾まで本文）。"""
    return next((j for j in range(start, len(lines)) if lines[j].strip() == word), len(lines))


def check(ctx, svc) -> Verdict | None:
    word = unquoted_substitution(ctx.command)
    if word is None:
        return None
    return deny(
        f"区切りを引用符で囲まないヒアドキュメント（<<{word}）の本文にバッククォートか $( があり、"
        f"シェルが本文をコマンドとして実行するので止めた。{_HELP}"
    )
