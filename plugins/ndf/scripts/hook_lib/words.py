"""コマンドの本文を、引用を解いた語の並びへ分ける（development-workflow の `workflow-guard.sh` の `wf_split`。#1142 の決定 20）。

    <hook の環境の python> hook.py words   < コマンドの本文

**展開はしない。** 判定に使うだけで、実行はしない。`$SCRIPTS` のような未展開の変数はそのままの文字列として残る。
構文木は `lib/shparse.py`（tree-sitter-bash）が作る。

出力は NUL で区切った語の並びで、**コマンドの境目は空の語**で表す（空の語は引用を解いた結果として出ないため、実在の語と
衝突しない）。1 つのコマンドの語は、名前・引数と、そのコマンドのリダイレクト（`2>&1`・`>&2` の字面）を字面の順に並べる。
置換（`$(…)`）の中のコマンドは、外のコマンドの後ろに別のコマンドとして並ぶ。ヒアドキュメントの本文はコマンドとして読まない。
"""
from __future__ import annotations

import shparse as sp

REDIRECTS = ("file_redirect", "herestring_redirect")


def _redirect_words(r: sp.Node) -> list[str]:
    """リダイレクトの語。ファイルへのものは記述子・演算子・最初の被演算子を 1 語に（`2>&1`・`>&2`）、ヒアストリングは
    `<<<` と中身の 2 語にする。後ろに並ぶ語はコマンドの引数として別に出る。"""
    if r.type == "herestring_redirect":
        body = [c for c in r.children if c.is_named and sp.field_of(r, c) != "argument"]
        return ["<<<", *(sp.unquote(c) for c in body[:1])]
    fd = next((sp.node_text(c) for c in r.children if sp.field_of(r, c) == "descriptor"), "")
    dest = sp.redirect_destination(r)
    return [fd + sp.redirect_operator(r) + (sp.unquote(dest) if dest is not None else "")]


def _command_words(n: sp.Node, extra: list[sp.Node]) -> list[str]:
    nodes, rs, _ = sp.command_parts(n, extra)
    shown = [(c.start_byte, [sp.unquote(c)]) for c in nodes]
    shown += [(r.start_byte, _redirect_words(r)) for r in rs if r.type in REDIRECTS]
    return [w for _, ws in sorted(shown, key=lambda x: x[0]) for w in ws]


def command_stream(cmd: str) -> list[str]:
    """語の並び（コマンドの境目は空の語）。"""
    out: list[str] = []

    def visit(n: sp.Node, extra: list[sp.Node]) -> None:
        if n.type in ("heredoc_body", "comment"):
            return
        if n.type == "redirected_statement":
            red = sp.split_redirects(n)
            if red.body is not None:
                visit(red.body, [*extra, *red.redirects] if red.reattach else extra)
            return
        if n.type == "command":
            if out:
                out.append("")
            out.extend(_command_words(n, extra))
            for s in sp.substitutions(n):
                visit(s, [])
            return
        kids = [c for c in n.children if c.is_named]
        for i, c in enumerate(kids):  # 並びとパイプの末尾のリダイレクトは最後のコマンドのもの
            visit(c, extra if i == len(kids) - 1 else [])

    visit(sp.parse_bash(cmd), [])
    return out
