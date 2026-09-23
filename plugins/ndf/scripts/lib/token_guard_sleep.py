"""前景の `sleep` で待つ Bash を見分ける（#829）。`token-guard.sh` から呼ぶ。

標準入力にコマンドの文字列を受け、第 1 引数に秒数の上限を受ける。拒否するなら終了コード 1、
通すなら 0 で終わる。**読めないコマンドは通す**（hook の失敗でツールを止めない）。

拒否するのは、コマンドの位置にある `sleep <引数>` が次のどちらかに当たるときだけである。
`&` で終わる（バックグラウンドで動く）`sleep` は前景を待たせないため見ない。`&` の判定は
sleep の引数の後ろから、sleep を含むリスト（`&&` / `||` / `|` でつながる範囲）の終わりまでを見る
（`sleep 30 >/tmp/x &` も背景）。sleep を囲む `( )` / `{ }` / ループ / `if` があれば、その閉じの
後ろの `&` まで見る（`(sleep 30) &`・`while ...; do sleep 1; done &` も背景）。リダイレクトの
`>&` / `2>&1` / `&>` の `&` は背景と読まない。字句による近似で、`case` の囲みは数えない。

- `while` / `until` のループの本体（`do` と対応する `done` の間）にある（秒数が変数でも止める）
- 秒数が数で、上限を超える

コメント・引用の中・ヒアドキュメントの本文は見ない。コマンドの位置にある `bash -c` / `sh -c` /
`zsh -c` / `dash -c` / `eval` の実行される引数は、取り出して同じ規則で見る（`echo bash -c ...` の
ような引数の中の語は見ない）。`timeout 590` / `nohup` / `env` などの前置きの後ろもコマンドの位置とする。
"""
from __future__ import annotations

import re
import shlex
import sys

SHELLS = {"bash", "sh", "zsh", "dash"}
# コマンドの位置を作る語。この後ろの語はコマンドとして読む
OPENERS = {"do", "then", "else", "elif", "if", "while", "until", "{", "!", "time"}
UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
# 後ろの語をコマンドとして実行する前置き。オプションと数の引数（`timeout 590` / `nice -n 10`）を読み飛ばす
WRAPPERS = {"exec", "command", "nohup", "env", "nice", "timeout"}
# 引数を取る shell のオプション（`-o` / `+O` と、末尾が o / O の結合形 `-euo`）。引数を読み飛ばして `-c` を探す
SHELL_OPT_WITH_ARG = re.compile(r"[-+][A-Za-bd-z]*[oO]")
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
# 複合コマンドを開く語・閉じる語（コマンドの位置にあるときだけ）。`( )` は区切りの文字で数える
GROUP_OPEN = {"{", "while", "until", "for", "select", "if"}
GROUP_CLOSE = {"}", "done", "fi"}
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


def is_background(toks: list[str], j: int, enclosing: int) -> bool:
    """toks[j] から sleep を含むリストの終わりまでに、背景実行の `&` があるか。

    `enclosing` は sleep を囲む複合コマンド（`( )` / `{ }` / ループ / `if`）の数。囲みの中では
    `;` で終わっても囲みの閉じまで進み、閉じの後ろの `&` を見る（`(sleep 30) &` も背景）。
    """
    depth, ended, cmd_pos = 0, False, False
    while j < len(toks):
        tok = toks[j]
        if tok in ("&&", "||", "|", "|&"):
            cmd_pos = True
        elif is_separator(tok):
            cmd_pos = True
            redirect = tok == "&" and (toks[j - 1] in (">", "<") or (j + 1 < len(toks) and toks[j + 1] == ">"))
            for c in tok:
                if c == "(":
                    depth += 1
                elif c == ")":
                    if depth:
                        depth -= 1
                    elif enclosing:
                        enclosing, ended = enclosing - 1, False
                    else:
                        return False
                elif depth:
                    continue
                elif c in ";\n":
                    if not enclosing:
                        return False
                    ended = True
                elif c == "&" and not redirect:
                    if not ended:
                        return True
                    ended = True
        else:
            if cmd_pos and tok in GROUP_OPEN:
                depth += 1
            elif cmd_pos and tok in GROUP_CLOSE:
                if depth:
                    depth -= 1
                elif enclosing:
                    enclosing, ended = enclosing - 1, False
                else:
                    return False
            cmd_pos = tok in OPENERS
        j += 1
    return False


def should_deny(text: str, limit: float, in_loop: bool = False, depth: int = 0) -> bool:
    if depth > 5:
        return False
    toks = tokens(strip_heredocs(text))
    # 各要素は "cond"（while/until の条件）/ "body"（while/until の本体）/ "for" / "forbody"
    stack: list[str] = []
    groups = 0  # いまの位置を囲む複合コマンドの数
    cmd_pos = True
    i = 0
    while i < len(toks):
        tok = toks[i]
        at_cmd = cmd_pos
        looping = in_loop or "body" in stack
        if is_separator(tok):
            groups = max(0, groups + tok.count("(") - tok.count(")"))
            cmd_pos = True
            i += 1
            continue
        if cmd_pos:
            if tok in GROUP_OPEN:
                groups += 1
            elif tok in GROUP_CLOSE:
                groups = max(0, groups - 1)
            if tok in ("while", "until"):
                stack.append("cond")
            elif tok in ("for", "select"):
                stack.append("for")
            elif tok == "do" and stack:
                stack[-1] = "body" if stack[-1] == "cond" else "forbody"
            elif tok == "done" and stack:
                stack.pop()
            elif tok == "sleep" and i + 1 < len(toks) and not is_separator(toks[i + 1]):
                sec = seconds(toks[i + 1])
                if (looping or (sec is not None and sec > limit)) and not is_background(toks, i + 2, groups):
                    return True
            elif tok in WRAPPERS:
                while i + 1 < len(toks) and (toks[i + 1][:1] == "-" or seconds(toks[i + 1]) is not None):
                    i += 1
                i += 1
                continue
            # 先頭の代入語（`X=1 sleep 30`）の後ろもコマンドの位置のまま
            cmd_pos = tok in OPENERS or bool(ASSIGN.match(tok))
        # 実行される引数を取り出して同じ規則で見る
        if at_cmd and tok in SHELLS:
            j = i + 1
            while j < len(toks) and toks[j][:1] in "-+" and not is_separator(toks[j]):
                if SHELL_OPT_WITH_ARG.fullmatch(toks[j]):
                    j += 2
                    continue
                if toks[j].startswith("-") and "c" in toks[j].lstrip("-") and not toks[j].startswith("--"):
                    if j + 1 < len(toks) and should_deny(toks[j + 1], limit, looping, depth + 1):
                        return True
                    break
                j += 1
        elif at_cmd and tok == "eval":
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
