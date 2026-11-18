"""`ndf relay run` に渡った claude の引数をパースする（relay.md の「ラッパーを挟まない起動」・「セッションをまたいで設定を保つ」）。

- ラッパーを挟まない起動（非対話・help とバージョン・サブコマンド・端末でない・`NDF_RELAY=0`・ラッパーが扱えない会話の選び方）は
  `passthrough` に理由を入れて返す。呼ぶ側は本物の claude を同じ引数で exec する
- 挟むときは、すべてのセッションに付ける引数（`carried`）と、最初のセッションだけのもの（最初のプロンプト・
  `--session-id`・`--resume <ID>`・`-n`/`--name`）に分ける。会話ごとの引数を次のセッションへ持ち越さない

オプションが値を取るかの表は `contracts/data/claude-cli.json` が持つ。表に無いオプションは、次の語が `-` で
始まらなければ値ごと引き継ぐ。`--名前=値` の形は 1 語で読む。
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from ndf import contracts

TABLE = "data/claude-cli.json"


@functools.lru_cache(maxsize=1)
def _table() -> dict[str, frozenset[str]]:
    data = contracts.load_json(TABLE)
    return {k: frozenset(v) for k, v in data.items() if isinstance(v, list)}


@dataclass(frozen=True)
class Parsed:
    """パースの結果。`passthrough` は挟まない理由（挟むなら None）、`notice` は挟まないことを 1 行で知らせるか。"""

    passthrough: Optional[str] = None
    notice: bool = False
    carried: tuple[str, ...] = ()
    first: tuple[str, ...] = ()
    prompt: Optional[str] = None
    session_id: Optional[str] = None
    resume: Optional[str] = None


@dataclass(frozen=True)
class _Option:
    name: str
    words: tuple[str, ...]  # オプションの語と値（`--名前=値` は 1 語）
    value: Optional[str]


@dataclass
class _Acc:
    """読み進める間の状態。"""

    carried: list[str] = field(default_factory=list)
    first: list[str] = field(default_factory=list)
    positionals: list[str] = field(default_factory=list)
    session_id: Optional[str] = None
    resume: Optional[str] = None
    unsupported: Optional[str] = None


def _take(args: Sequence[str], i: int) -> tuple[_Option, int]:
    """`args[i]` から 1 つのオプションを読み、次の位置を返す。"""
    t = _table()
    word = args[i]
    name, eq, inline = word.partition("=")
    if eq and name.startswith("--"):
        return _Option(name, (word,), inline), i + 1
    nxt = args[i + 1] if i + 1 < len(args) else None
    has_value = nxt is not None and not nxt.startswith("-")
    if word in t["flags"]:
        return _Option(word, (word,), None), i + 1
    if word in t["variadic"]:
        j = i + 1
        while j < len(args) and not args[j].startswith("-"):
            j += 1
        return _Option(word, tuple(args[i:j]), None), j
    if nxt is not None and (has_value or word in t["value"]):
        return _Option(word, (word, nxt), nxt), i + 2
    return _Option(word, (word,), None), i + 1


def _place(acc: _Acc, opt: _Option) -> None:
    """読んだオプションを、どのセッションに付けるかで振り分ける。"""
    t = _table()
    if opt.name in t["unsupported"] or (opt.name in ("-r", "--resume") and opt.value is None):
        acc.unsupported = acc.unsupported or opt.name  # 後ろの非対話の起動（`-c --version`）を先に拾うため、読み終えてから返す
    elif opt.name in ("-r", "--resume"):
        acc.resume = opt.value
    elif opt.name == "--session-id":
        acc.session_id = opt.value
    elif opt.name in t["first_only"]:
        acc.first += opt.words
    else:
        acc.carried += opt.words


def _result(acc: _Acc) -> Parsed:
    if acc.unsupported:
        return Parsed(f"ラッパーが続けられない会話の選び方 {acc.unsupported}", notice=True)
    if len(acc.positionals) > 1:
        return Parsed("プロンプトの語が 2 つ以上ある", notice=True)
    if acc.resume and acc.session_id:
        return Parsed("--resume と --session-id を同時に渡した", notice=True)
    prompt = acc.positionals[0] if acc.positionals else None
    return Parsed(carried=tuple(acc.carried), first=tuple(acc.first), prompt=prompt, session_id=acc.session_id, resume=acc.resume)


def parse(args: Sequence[str], environ: Mapping[str, str], tty: bool) -> Parsed:
    """claude の引数を、ラッパーを挟むか・どのセッションに付けるかでパースする。"""
    if environ.get("NDF_RELAY") == "0":
        return Parsed("NDF_RELAY=0")
    if not tty:
        return Parsed("端末でない")
    t = _table()
    acc = _Acc()
    i = 0
    while i < len(args):
        word = args[i]
        if word == "--":
            acc.positionals += args[i + 1 :]
            break
        if not word.startswith("-") or word == "-":
            if not acc.positionals and word in t["subcommands"]:  # 前に付いたオプション（alias の分）があっても最初の語で読む
                return Parsed(f"サブコマンド {word}")
            acc.positionals.append(word)
            i += 1
            continue
        opt, i = _take(args, i)
        if opt.name in t["non_interactive"]:
            return Parsed(f"非対話の起動 {opt.name}")
        _place(acc, opt)
    return _result(acc)
