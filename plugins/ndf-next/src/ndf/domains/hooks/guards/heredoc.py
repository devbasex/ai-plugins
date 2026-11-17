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
    """区切りを引用符で囲まず、本文にバッククォートか `$(` があるヒアドキュメントの区切りの語。無ければ None。"""
    lines = command.split("\n")
    for i, line in enumerate(lines):
        for m in _OPEN.finditer(line):
            if m.group(1):
                continue
            word = m.group(2)
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == word), len(lines))
            body = "\n".join(lines[i + 1 : end])
            if "`" in body or "$(" in body:
                return word
    return None


def check(ctx, svc) -> Verdict | None:
    word = unquoted_substitution(ctx.command)
    if word is None:
        return None
    return deny(
        f"区切りを引用符で囲まないヒアドキュメント（<<{word}）の本文にバッククォートか $( があり、"
        f"シェルが本文をコマンドとして実行するので止めた。{_HELP}"
    )
