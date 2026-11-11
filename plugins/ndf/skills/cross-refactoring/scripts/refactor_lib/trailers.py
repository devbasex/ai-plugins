"""コミットのトレーラーを git から読む（#2988）。

台帳（`ledger`）が `gitfacts` を読まずにトレーラーを読めるよう、`gitfacts` から分けた。`gitfacts` は同じ名前を
再エクスポートする。
"""

from __future__ import annotations

import re
import subprocess

from .paths import git_out


def commit_trailers(work: str, sha: str) -> dict[str, str]:
    """コミットメッセージのトレーラーを git から読む。

    **結果ファイルの `trailers` は使わない。** JSON 上は仕様どおりでも、実際の
    `git commit` でトレーラーを書き忘れていれば集計に使えない。

    **末尾の段落から前へ 1 段落ずつ読む**（#553）。実行環境が帰属の段落を後ろへ
    足すと、git の標準の読み方は最後の段落しか見ないため必須の記名が読めなくなる。
    トレーラーの段落と判定しなかった段落で止めるので、散文の中にある記名の形の行は
    拾わない。同じ鍵が 2 つの段落にあれば、末尾に近い段落の値を採る。

    **1 段落目（題名）は掛けない。** 掛けると `Round: 本文の題名` の形の題名を
    トレーラーとして読む。
    """
    body = git_out(work, ["log", "-1", "--format=%B", sha], strip=False)
    paragraphs = re.split(r"\n[ \t]*\n", (body or "").strip("\n"))
    trailers: dict[str, str] = {}
    for paragraph in reversed(paragraphs[1:]):
        parsed = _parse_trailer_paragraph(paragraph)
        if not parsed:
            break
        for key, value in parsed.items():
            trailers.setdefault(key, value)
    return trailers


def _parse_trailer_paragraph(paragraph: str) -> dict[str, str]:
    """1 つの段落を git の判定に掛け、トレーラーの段落なら鍵と値を返す。

    **題名の行を補って渡す。** git はメッセージの 1 行目を題名として読むため、
    段落だけを渡すと何も返らない（git 2.53.0 で実測）。判定そのものは git に委ね、
    「何行以上なら記名の段落か」といった規則をこちら側に持たない。
    """
    if not paragraph.strip():
        return {}
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=f"subject\n\n{paragraph}\n",
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            parsed[key.strip()] = value.strip()
    return parsed
