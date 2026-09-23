"""前景の `sleep` で待つ Bash を見分ける（#829）。`token-guard.sh` から呼ぶ。

標準入力にコマンドの文字列を受け、第 1 引数に秒数の上限を受ける。拒否するなら終了コード 1、
通すなら 0 で終わる。**読めないコマンドは通す**（hook の失敗でツールを止めない）。

拒否するのは、コマンドの位置にある `sleep <引数>` が次のどちらかに当たるときだけである。
`&` で終わる（バックグラウンドで動く）`sleep` は前景を待たせないため見ない。

- `while` / `until` のループの本体（`do` と対応する `done` の間）にある（秒数が変数でも止める）
- 秒数が数で、上限を超える

コメント・引用の中・ヒアドキュメントの本文は見ない。`bash -c` / `sh -c` / `zsh -c` / `eval` の
実行される引数は、取り出して同じ規則で見る。
"""
from __future__ import annotations

import re
import shlex
import sys

SHELLS = {"bash", "sh", "zsh", "dash"}
# コマンドの位置を作る語。この後ろの語はコマンドとして読む
OPENERS = {"do", "then", "else", "elif", "if", "while", "until", "{", "!", "time"}
UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# 引数を取る shell のオプション。引数を読み飛ばして `-c` を探す
SHELL_OPTS_WITH_ARG = {"-o", "+o", "-O", "+O"}
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")


def strip_heredocs(text: str) -> str:
    """ヒアドキュメントの本文を取り除く。開始の行は残す。"""
    out, pending = [], []
    for line in text.split("\n"):
        if pending:
            dash, word = pending[0]
            if (line.lstrip("\t") if dash else line) == word:
                pending.pop(0)
            continue
        out.append(line)
        for m in HEREDOC.finditer(line.replace("<<<", "   ")):
            pending.append((m.group(1) == "-", m.group(3)))
    return "\n".join(out)


def tokens(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.commenters = "#"
    lex.wordchars += "$:@%+,[]{}!^=-/.~*?"
    return list(lex)


def seconds(word: str) -> float | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", word)
    return float(m.group(1)) * UNIT[m.group(2)] if m else None


def is_separator(tok: str) -> bool:
    return bool(tok) and all(c in ";&|()\n" for c in tok)


def should_deny(text: str, limit: float, in_loop: bool = False, depth: int = 0) -> bool:
    if depth > 5:
        return False
    toks = tokens(strip_heredocs(text))
    # 各要素は "cond"（while/until の条件）/ "body"（while/until の本体）/ "for" / "forbody"
    stack: list[str] = []
    cmd_pos = True
    i = 0
    while i < len(toks):
        tok = toks[i]
        looping = in_loop or "body" in stack
        if is_separator(tok):
            cmd_pos = True
            i += 1
            continue
        if cmd_pos:
            if tok in ("while", "until"):
                stack.append("cond")
            elif tok in ("for", "select"):
                stack.append("for")
            elif tok == "do" and stack:
                stack[-1] = "body" if stack[-1] == "cond" else "forbody"
            elif tok == "done" and stack:
                stack.pop()
            elif tok == "sleep" and i + 1 < len(toks) and not is_separator(toks[i + 1]):
                background = i + 2 < len(toks) and toks[i + 2] == "&"
                sec = seconds(toks[i + 1])
                if not background and (looping or (sec is not None and sec > limit)):
                    return True
            # 先頭の代入語（`X=1 sleep 30`）の後ろもコマンドの位置のまま
            cmd_pos = tok in OPENERS or bool(ASSIGN.match(tok))
        # 実行される引数を取り出して同じ規則で見る
        if tok in SHELLS:
            j = i + 1
            while j < len(toks) and toks[j][:1] in "-+" and not is_separator(toks[j]):
                if toks[j] in SHELL_OPTS_WITH_ARG:
                    j += 2
                    continue
                if toks[j].startswith("-") and "c" in toks[j].lstrip("-") and not toks[j].startswith("--"):
                    if j + 1 < len(toks) and should_deny(toks[j + 1], limit, looping, depth + 1):
                        return True
                    break
                j += 1
        elif tok == "eval":
            j, words = i + 1, []
            while j < len(toks) and not is_separator(toks[j]):
                words.append(toks[j])
                j += 1
            if should_deny(" ".join(words), limit, looping, depth + 1):
                return True
        i += 1
    return False


def main() -> int:
    try:
        limit = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
        return 1 if should_deny(sys.stdin.read(), limit) else 0
    except Exception:  # 読めないコマンドは通す
        return 0


if __name__ == "__main__":
    sys.exit(main())
