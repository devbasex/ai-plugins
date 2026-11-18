"""`ndf relay run` に渡った claude の引数を読み分ける（純粋な関数。relay.md の「ラッパーを挟まない起動」・「セッションをまたいで設定を保つ」）。

- ラッパーを挟まない起動（非対話・help と版・サブコマンド・端末でない・`NDF_RELAY=0`・ラッパーが扱えない会話の選び方）は
  `passthrough` に理由を入れて返す。呼ぶ側は本物の claude を同じ引数で exec する
- 挟むときは、すべてのセッションに付ける引数（`carried`）と、最初のセッションだけのもの（最初のプロンプト・
  `--session-id`・`--resume <ID>`・`-n`/`--name`）に分ける。会話ごとの引数を次のセッションへ持ち越さない

オプションが値を取るかの表は claude 2.1 の `claude --help` から作った。表に無いオプションは、次の語が `-` で
始まらなければ値ごと引き継ぐ。`--名前=値` の形は 1 語で読む。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

# 値を 1 つ取る
VALUE = frozenset(
    {
        "--agent", "--agents", "--append-system-prompt", "--autocompact", "--debug-file", "--effort", "--environment",
        "--fallback-model", "--input-format", "--json-schema", "--max-budget-usd", "--model", "-n", "--name",
        "--output-format", "--permission-mode", "--permission-prompts", "--plugin-dir", "--plugin-url",
        "--remote-control-session-name-prefix", "--session-id", "--setting-sources", "--settings", "--system-prompt",
        "--system-prompt-snapshot",
    }
)  # fmt: skip
# 値を省ける（次の語が `-` で始まらなければ値）
OPTIONAL = frozenset(
    {
        "--cloud",
        "-d",
        "--debug",
        "--from-pr",
        "--prompt-suggestions",
        "--remote-control",
        "-r",
        "--resume",
        "--teleport",
        "-w",
        "--worktree",
    }
)
# 値を複数取る（次の `-` で始まる語まで）
VARIADIC = frozenset(
    {
        "--add-dir",
        "--allowedTools",
        "--allowed-tools",
        "--betas",
        "--disallowedTools",
        "--disallowed-tools",
        "--file",
        "--mcp-config",
        "--tools",
    }
)
FLAGS = frozenset(
    {
        "--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background", "--bare", "--brief", "--chrome",
        "-c", "--continue", "--dangerously-skip-permissions", "--desktop", "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections", "--fork-session", "--forward-subagent-text", "-h", "--help", "--ide",
        "--include-hook-events", "--include-partial-messages", "--no-chrome", "--no-session-persistence", "-p", "--print",
        "--replay-user-messages", "--restricted", "--safe-mode", "--strict-mcp-config", "--tmux", "--verbose", "-v", "--version",
    }
)  # fmt: skip
SUBCOMMANDS = frozenset(
    {
        "agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install", "logs", "mcp", "plugin", "plugins",
        "purge", "respawn", "rm", "setup-token", "stop", "kill", "ultrareview", "update", "upgrade",
    }
)  # fmt: skip
# 非対話・すぐ終わる起動（黙ってパススルーする）
NON_INTERACTIVE = frozenset({"-p", "--print", "-h", "--help", "-v", "--version"})
# ラッパーが続けられない会話の選び方（1 行出してパススルーする）
UNSUPPORTED = frozenset(
    {
        "-c",
        "--continue",
        "--fork-session",
        "--from-pr",
        "--teleport",
        "--cloud",
        "--bg",
        "--background",
        "--tmux",
        "-w",
        "--worktree",
        "--desktop",
    }
)
# 最初のセッションだけに付ける
FIRST_ONLY = frozenset({"-n", "--name"})


@dataclass(frozen=True)
class Parsed:
    """読み分けた結果。`passthrough` は挟まない理由（挟むなら None）、`notice` は挟まないことを 1 行で知らせるか。"""

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


def _take(args: Sequence[str], i: int) -> tuple[_Option, int]:
    """`args[i]` から 1 つのオプションを読み、次の位置を返す。"""
    word = args[i]
    name, eq, inline = word.partition("=")
    if eq and name.startswith("--"):
        return _Option(name, (word,), inline), i + 1
    nxt = args[i + 1] if i + 1 < len(args) else None
    has_value = nxt is not None and not nxt.startswith("-")
    if word in FLAGS:
        return _Option(word, (word,), None), i + 1
    if word in VARIADIC:
        j = i + 1
        while j < len(args) and not args[j].startswith("-"):
            j += 1
        return _Option(word, tuple(args[i:j]), None), j
    if word in VALUE or (word not in OPTIONAL and has_value):
        if nxt is None:
            return _Option(word, (word,), None), i + 1
        return _Option(word, (word, nxt), nxt), i + 2
    if has_value:
        return _Option(word, (word, nxt), nxt), i + 2
    return _Option(word, (word,), None), i + 1


def parse(args: Sequence[str], environ: Mapping[str, str], tty: bool) -> Parsed:
    """claude の引数を、ラッパーを挟むか・どのセッションに付けるかで読み分ける。"""
    if environ.get("NDF_RELAY") == "0":
        return Parsed("NDF_RELAY=0")
    if not tty:
        return Parsed("端末でない")
    carried: list[str] = []
    first: list[str] = []
    positionals: list[str] = []
    session_id = resume = unsupported = None
    i = 0
    while i < len(args):
        word = args[i]
        if word == "--":
            positionals += args[i + 1 :]
            break
        if not word.startswith("-") or word == "-":
            if not positionals and word in SUBCOMMANDS:  # 前に付いたオプション（alias の分）があっても最初の語で読む
                return Parsed(f"サブコマンド {word}")
            positionals.append(word)
            i += 1
            continue
        opt, i = _take(args, i)
        if opt.name in NON_INTERACTIVE:
            return Parsed(f"非対話の起動 {opt.name}")
        if opt.name in UNSUPPORTED or (opt.name in ("-r", "--resume") and opt.value is None):
            unsupported = unsupported or opt.name  # 後ろの非対話の起動（`-c --version`）を先に拾うため、読み終えてから返す
        elif opt.name in ("-r", "--resume"):
            resume = opt.value
        elif opt.name == "--session-id":
            session_id = opt.value
        elif opt.name in FIRST_ONLY:
            first += opt.words
        else:
            carried += opt.words
    if unsupported:
        return Parsed(f"ラッパーが続けられない会話の選び方 {unsupported}", notice=True)
    if len(positionals) > 1:
        return Parsed("プロンプトの語が 2 つ以上ある", notice=True)
    if resume and session_id:
        return Parsed("--resume と --session-id を同時に渡した", notice=True)
    prompt = positionals[0] if positionals else None
    return Parsed(carried=tuple(carried), first=tuple(first), prompt=prompt, session_id=session_id, resume=resume)
