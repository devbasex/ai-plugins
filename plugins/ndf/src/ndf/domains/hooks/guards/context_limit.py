"""文脈の量の guard（SF12・#2595・#2789）。

会話の記録の末尾の最後の応答の文脈の量（token）が運用上限（`context_thresholds.thresholds` の `limit`。上限の設定の
`context.limit` と `NDF_CONTEXT_LIMIT`。#3995）以上なら、引継ぎに要る呼び出し（メインディレクトリの引継ぎ文書の Read・Write・Edit、
`params.pass_tools`、`params.pass_ndf` の `ndf` のコマンドだけのフォアグラウンド Bash）を除いて止める。`pass_ndf` だけの連結は
`script-chain` の guard も `handoff_bash` で通す。拒否の文は、注入の上限で切られないよう先頭に `--cwd <メインディレクトリ>` での
引継ぎ文書の確かめ方と終え方（中継の下は次のセグメントのブロック、外は ndf-next）を、後に背景の監視の止め方を書き、文脈の量を入れない。拒否は数えず、
案内へ下げずに止め続ける（表の `deny_key` が `none`。#3995 の決定 6）。

PostToolUse では、文脈の量が判断開始の値（`decide_from`）以上・運用上限未満なら、超えたことと役ごとの切りどころを
案内する（利用者の入力ごとに 1 度。数え手の `NOTICE_KEY`）。知らせ済みの入力と数え手が使えないときは会話の記録を読まない。
文脈の量は同じ呼び出し（`tool_use_id`）の PreToolUse が数え手の `memo` に覚えた値を使い、読み直さない（#3995 の決定 3）。

計測のマーカー `under_relay` は判定したときだけ書く（判定しなかった行と中継の外を見分ける。#2789）。

サブエージェント（入力の `agent_id`）と、メインディレクトリに `.ndf` の無い会話は判定せず、会話の記録も読まない（#3995 の 3995:15）。
メインディレクトリは `.git` と管理ディレクトリの `commondir` だけから求め、git のコマンドを起動しない。
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Optional

from ndf import identity
from ndf.domains.hooks import tables
from ndf.domains.hooks.context import context_tokens
from ndf.domains.hooks.guards.shell import background_call
from ndf.domains.hooks.strict import _repo_root
from ndf.domains.relay.context_thresholds import Thresholds, thresholds
from ndf.domains.relay.signals import relay_dir
from ndf.domains.hooks.verdict import Verdict, advise, deny, passing

# 運用上限で通す Bash に許さない形: 変数の展開・コマンド置換（`$`・バッククォート）とプロセス置換
_SUBSTITUTION = re.compile(r"[$`]|[<>]\(")
# 引用の外で展開や別の構文になる字（チルダ・グロブ・ブレース・extglob の括弧）。運用上限で通す Bash の語に許さない
_UNQUOTED_SPECIAL = frozenset("~*?[{(")
# 二重引用の中でバックスラッシュが外す字（ほかの字の前のバックスラッシュは残る）
_DQUOTE_ESCAPES = frozenset('$`"\\\n')
# この guard の名前（`guards.json` の行）
_NAME = "context-limit"
# 判断開始の値を知らせた回数を数える数え手の `counts` のキー（利用者の入力ごとに数え直す）
NOTICE_KEY = "context-limit:notice"
# PreToolUse が読んだ文脈の量を `tool_use_id` ごとに覚える数え手の `memo` のキー
MEMO_KEY = "context-limit:tokens"
# 同じ入力の中で覚えておく `tool_use_id` の数の上限（1 つの応答の並列の呼び出しの数より大きく取る）
MEMO_KEEP = 16
# ファイルへ書かないリダイレクト（演算子と先を空白でつないだ字面）: 記述子の複製（先が数字か -）と、/dev/null へ向ける形
_SAFE_REDIRECT = re.compile(r"[<>]& (?:-|[0-9]+)|&?>>? /dev/null")
# Bash が引用の外で語を区切る空白（字句の blank。ほかの Unicode の空白は語の一部になる）
_BLANKS = frozenset(" \t")
# 引用の外で語を区切る字（Bash のメタ文字）。この後の `#` は語の先頭なのでコメントになる
_METACHARS = frozenset(" \t\n;&|()<>")


def _gitdir(dot: Path) -> Optional[Path]:
    """`gitdir: <X>` のファイルが指す管理ディレクトリの実パス（`X` は `.git` を持つディレクトリから解く）。"""
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:  # noqa: NDF401 ファイル: .git を読めなければメインディレクトリは求められない
        return None
    if not line.startswith("gitdir:"):
        return None
    return Path(os.path.realpath(dot.parent / line[len("gitdir:") :].strip()))


def _main_of(root: Path) -> Optional[Path]:
    """`.git` を持つ `root` のメインディレクトリ。共通の git のディレクトリの親はメインディレクトリとみなさない。"""
    dot = root / ".git"
    if dot.is_dir():
        return root
    gitdir = _gitdir(dot)
    if gitdir is None:
        return None
    try:
        text = (gitdir / "commondir").read_text(encoding="utf-8")
    except FileNotFoundError:  # commondir が無いのは管理ディレクトリを外に保存するメインディレクトリ（--separate-git-dir）
        return root
    except OSError:  # noqa: NDF401 ファイル: commondir を読めなければメインディレクトリは求められない
        return None
    common = Path(os.path.realpath(gitdir / text.strip()))
    parent = common.parent
    candidate = parent / ".git"
    if candidate.is_dir():
        return parent if Path(os.path.realpath(candidate)) == common else None
    return parent if candidate.is_file() and _gitdir(candidate) == common else None


def _main(cwd: str) -> Optional[str]:
    """`cwd` を含むリポジトリのメインディレクトリの実パス。求められないか `.ndf` のディレクトリが無ければ None。"""
    root = _repo_root(Path(os.path.realpath(cwd)))
    main = _main_of(root) if root is not None else None
    return str(main) if main is not None and (main / identity.FIXED_WORDS["ndf_settings_dir"]).is_dir() else None


def _in_handoff(path: str, cwd: str, handoff_dir: str) -> bool:
    """実際の読み書きの先（リンクと `..` を解いた先）が、`cwd` を含むリポジトリのメインディレクトリの引継ぎディレクトリの直下か。"""
    main = _main(cwd)
    if main is None:
        return False
    real = os.path.realpath(os.path.join(cwd, path))
    return os.path.dirname(real) == os.path.join(main, handoff_dir.strip("/")) and bool(os.path.basename(real))


def _split_cwd(words: list[str]) -> Optional[tuple[list[str], list[str]]]:
    """`--cwd <値>`・`--cwd=<値>` を除いた語と、`--cwd` の値の列。値の無い `--cwd` なら None。"""
    rest: list[str] = []
    values: list[str] = []
    it = iter(words)
    for word in it:
        if word == "--cwd":
            value = next(it, None)
            if value is None:
                return None
            values.append(value)
        elif word.startswith("--cwd="):
            values.append(word[len("--cwd=") :])
        else:
            rest.append(word)
    return rest, values


def _path_args(words: list[str], spec: dict) -> Optional[list[str]]:
    """`spec`（`pass_ndf` の 1 行）の引数だけなら、`_in_handoff` で照らす値の列。ほかの語・数の違う位置引数なら None。"""
    positional: list[str] = []
    paths: list[str] = []
    it = iter(words)
    for word in it:
        name, eq, value = word.partition("=")
        if word in spec["flags"]:
            continue
        if name not in spec["options"]:
            if word.startswith("-"):
                return None
            positional.append(word)
            continue
        got = value if eq else next(it, None)
        if got is None:
            return None
        paths += [got] if name in spec["paths"] else []
    if len(positional) != spec["positional"]:
        return None
    return paths + (positional if "positional" in spec["paths"] else [])


def _ndf_args_pass(words: list[str], cwd: str, params: dict) -> bool:
    """`ndf` に続く語が通すコマンドの形で、`handoff check` が読み書きする先がメインディレクトリの引継ぎディレクトリの中か。

    パスは CLI（`records/cli.py` の `_root` と `_handoff`）と同じく、先頭のチルダを展開してから、相対なら `--cwd`
    （無ければ `cwd`）の実パスを含む worktree の根から解く。
    """
    split = _split_cwd(words)
    if split is None or len(split[1]) > 1:
        return False
    rest, values = split
    here = os.path.realpath(cwd)
    start = os.path.realpath(os.path.join(cwd, values[0])) if values else here
    main = _main(here)
    base = _repo_root(Path(start))
    if main is None or _main(start) != main or base is None:
        return False
    for name, spec in params["pass_ndf"].items():
        head = name.split()
        if rest[: len(head)] == head:
            paths = _path_args(rest[len(head) :], spec)
            return paths is not None and all(_in_handoff(os.path.expanduser(p), str(base), params["handoff_dir"]) for p in paths)
    return False


def _static_word(text: str) -> Optional[str]:
    """語の字面（引用とエスケープを残したもの）を、Bash が渡す値に読み直す。展開・グロブを含み値が静的に決まらなければ None。"""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        piece, i = _static_piece(text, i)
        if piece is None:
            return None
        out.append(piece)
    return "".join(out)


def _static_piece(text: str, i: int) -> tuple[Optional[str], int]:
    """`i` から始まる 1 つの区切り（エスケープ・引用・1 字）の値と次の位置。値が静的に決まらなければ値は None。"""
    c = text[i]
    if c == "\\":
        return text[i + 1 : i + 2], i + 2
    if c == "'":
        end = text.find("'", i + 1)
        return (None, i) if end < 0 else (text[i + 1 : end], end + 1)
    if c == '"':
        return _double_quoted(text, i)
    if c in "$`" or c in _UNQUOTED_SPECIAL:
        return None, i
    return c, i + 1


def _double_quoted(text: str, start: int) -> tuple[Optional[str], int]:
    """`start` の二重引用の値と閉じた後の位置。展開を含むか閉じなければ値は None。"""
    out: list[str] = []
    i, n = start + 1, len(text)
    while i < n and text[i] != '"':
        if text[i] in "$`":
            return None, i
        if text[i] == "\\" and text[i + 1 : i + 2] in _DQUOTE_ESCAPES:
            i += 1
        out.append(text[i])
        i += 1
    if i >= n:
        return None, i
    return "".join(out), i + 1


def _continuations_between_words(command: str) -> bool:
    """`command` の行の継続（`\\<改行>`）がどれも、Bash の引用・エスケープの規則で引数の間にあるか。

    引用の外で、直前の字がエスケープされていない空白かタブ（字句の blank）のときだけ引数の間とみなす。Bash は継続を消して
    前後を 1 語につなぐので、それ以外（語の途中・引用の中・行の先頭・演算子の後）は構文木の語の切れ目と合う保証が無く偽。
    コメントの中の字は継続にならないので読み飛ばす。引用が閉じなければ偽。`$`・バッククォートを含まないこと（呼び手が先に止める）を前提にする。
    """
    blank = False  # 直前の字が引用の外のエスケープされていない blank か
    start = True  # 次の字が語の先頭か（行の先頭か、直前の字が引用の外のエスケープされていないメタ文字）
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            continuation = command[i + 1 : i + 2] == "\n"
            if continuation and not blank:
                return False
            if not continuation:
                blank = start = False
            i += 2
            continue
        if c in "'\"":
            end = _closing_quote(command, i)
            if end is None:
                return False
            i, blank, start = end + 1, False, False
            continue
        if c == "#" and start:
            nl = command.find("\n", i)
            i = n if nl < 0 else nl
            continue
        blank, start = c in _BLANKS, c in _METACHARS
        i += 1
    return True


def _closing_quote(command: str, start: int) -> Optional[int]:
    """`start` の引用符を閉じる位置。引用の中の行の継続（語の途中）があるか、閉じなければ None。"""
    if command[start] == "'":
        end = command.find("'", start + 1)
        return None if end < 0 or "\\\n" in command[start:end] else end
    i = start + 1
    while i < len(command):
        c = command[i]
        if c == '"':
            return i
        if c == "\\":
            if command[i + 1 : i + 2] == "\n":
                return None
            i += 2 if command[i + 1 : i + 2] in _DQUOTE_ESCAPES else 1
            continue
        i += 1
    return None


def _redirect_ok(r) -> bool:
    """ファイルへ書かないリダイレクト（記述子の複製か /dev/null）か。"""
    return not r.dynamic and _SAFE_REDIRECT.fullmatch(f"{r.op} {r.target}") is not None


def _command_ok(cmd, cwd: str, params: dict) -> bool:
    """背景でなく、語が Bash と同じ値に静的に決まり、先頭の語が `ndf`（ラッパーを持たない）の通すコマンドか。"""
    if any("\\\n" in w for w in cmd.source):  # 引用の中の行の継続（二重引用では Bash が外す）
        return False
    words = [_static_word(w) for w in cmd.source]
    if cmd.background or None in words or words[:1] != [identity.FIXED_WORDS["entrypoint"]]:
        return False
    return _ndf_args_pass([w for w in words[1:] if w is not None], cwd, params)


def params() -> dict:
    """この guard の `guards.json` の `params`（`script-chain` の guard が引継ぎの連結を見分けるのに使う）。"""
    return next(g for g in tables.guards() if g["name"] == _NAME)["params"]


def handoff_bash(ctx, params: dict) -> bool:
    """`pass_ndf` のコマンドだけを前景で打つ Bash か（連結は単純なコマンドを 1 つずつ照らす）。

    Tool の背景起動（`run_in_background`）と、Bash の引用・エスケープの規則で引数の間と確かめられない行の継続（構文木の語の
    切れ目が Bash と違い得る）は止める。
    判定は `ctx.memo` に置き、同じ Tool の呼び出しの `script-chain` の guard と分け合う。
    """
    if _NAME not in ctx.memo:
        ctx.memo[_NAME] = _handoff_bash(ctx, params)
    return ctx.memo[_NAME]


def _handoff_bash(ctx, params: dict) -> bool:
    shell = ctx.shell
    if background_call(ctx.tool_input):
        return False
    if not shell.commands or shell.assignments or shell.heredocs or len(shell.waits) > len(shell.commands):
        return False
    if _SUBSTITUTION.search(ctx.command) or not _continuations_between_words(ctx.command):
        return False
    if not all(_redirect_ok(r) for r in shell.redirects):
        return False
    return all(_command_ok(c, ctx.cwd, params) for c in shell.commands)


def _passes(ctx, params: dict) -> bool:
    """運用上限以上でも通す呼び出し（引継ぎ文書の Read・Write・Edit、`pass_tools`、`pass_ndf` だけの Bash）か。"""
    if ctx.tool in ("Read", "Write", "Edit"):
        path = ctx.tool_input.get("file_path")
        return isinstance(path, str) and _in_handoff(path, ctx.cwd, params["handoff_dir"])
    if ctx.tool in params["pass_tools"]:
        return True
    return ctx.tool == "Bash" and handoff_bash(ctx, params)


def _reason(t: Thresholds, under_relay: bool, main: str, handoff_dir: str) -> str:
    """運用上限の拒否の文（背景の監視の扱い・メインディレクトリ `main` の引継ぎ文書と確かめ方・終え方）。文脈の量は入れない。

    注入の上限で切られても検査のコマンドと終え方が残るよう、それらを先頭に置き、メインディレクトリは `--cwd` の 1 回だけ書く
    （文書はその根から解く相対パス。CLI と `_ndf_args_pass` は相対パスを `--cwd` の worktree の根から解く）。
    `--cwd` の値と文書は、空白を含んでも Bash の 1 引数になるよう引用する（引用の 2 字を見込み、中継の外で空白を含む 243 字の
    メインディレクトリでも全文が注入の上限に収まるよう詰める）。保存か検査が失敗したら理由を伝えてブロックを出さない
    （今回の引継ぎが渡らないままセグメントを終えない）。
    """
    end = "応答の最後に次のセグメントのブロックを書いて終える" if under_relay else "ndf-next のブロックを出して新しいセッションで続ける"
    doc = shlex.quote(f"{handoff_dir.strip('/')}/<文書>.md")
    return (
        f"文脈の量が運用上限（{t.limit} token）を超えた。引継ぎ文書を書き、"
        f"前景で ndf records --cwd {shlex.quote(main)} handoff check {doc} で確かめ、保存と検査が通れば{end}。"
        "通らなければ理由を伝え、ブロックを出さない。"
        "文書は --cwd の下へ絶対パスで書く（オーガナイザーは <文書> を parallel-<計画>）。"
        "書く前に背景の監視（ndf lanes wait・Monitor）を TaskStop で止め、完了通知で確かめる。"
        "supervisor・ndf plan queue は止めずに報告を待つ。止める前に wait が戻ったら結果を処理せず、"
        "「現在地」の wait の行を残す。"
    )


def _notice(tokens: int, t: Thresholds) -> str:
    """判断開始の値を超えたことと、役ごとの切りどころを書いた案内の文。"""
    return (
        f"文脈の量が判断開始の値（{t.decide_from} token）を超えた（今 {tokens} token。運用上限は {t.limit} token）。"
        "工程の途中の Tool は止めないので、次の切りどころで引継ぎ文書を書いて切り替える。"
        "レーンの conductor は次のカットポイント（ドキュメントレビューのマージの後・リファクタリングとコードレビューの前後・"
        "スプリントの Pull Request を出した後・マージのフェーズを終えた後）で、"
        "オーガナイザーはこの知らせを処理し終えて次の wait を張る前か、利用者への報告を終えた時点で切る。"
        "運用上限に達すると、引継ぎに要る呼び出しのほかは止まる。"
    )


def _tool_use_id(ctx) -> Optional[str]:
    value = ctx.payload.get("tool_use_id")
    return value if isinstance(value, str) and value else None


def _tokens(ctx, svc, notified: bool) -> Optional[int]:
    """文脈の量。PostToolUse は同じ呼び出しの PreToolUse が覚えた値を取り出して使い、無ければ会話の記録の末尾を読む。

    PreToolUse は読んだ値を、この入力の後にまだ知らせていなければ `tool_use_id` ごとに覚える（知らせ済みなら PostToolUse は
    値を使わずに終わる）。同じ呼び出しの PreToolUse と PostToolUse の間に新しい応答（`usage`）は足されないので値は同じ。
    """
    item = _tool_use_id(ctx)
    post = ctx.event == "PostToolUse"
    got = svc.counter.take(MEMO_KEY, item) if post and item is not None else None
    if got is not None and isinstance(got.get("tokens"), int):
        return got["tokens"]
    tokens = context_tokens(ctx.transcript_tail)
    if not post and tokens is not None and item is not None and not notified:
        svc.counter.remember(MEMO_KEY, item, {"tokens": tokens}, MEMO_KEEP)
    return tokens


def check(ctx, svc) -> Verdict | None:
    main = None if "agent_id" in ctx.payload else _main(ctx.cwd)
    if main is None:
        return None
    post = ctx.event == "PostToolUse"
    notified = svc.counter.data["counts"].get(NOTICE_KEY, 0) > 0  # noqa: NDF502 ファイル: 知らせる前は 0 回
    if post and (not svc.counter.usable or notified):  # 知らせない呼び出しは会話の記録を読まずに終える
        return None
    tokens = _tokens(ctx, svc, notified)
    if tokens is None:
        return None
    t = thresholds(ctx.env, tables.policy_value)
    if post:
        if t.decide_from <= tokens < t.limit and svc.counter.bump(NOTICE_KEY) == 1:
            return advise(_notice(tokens, t))
        return passing(context_tokens=tokens)
    if tokens < t.limit or _passes(ctx, svc.params):
        return passing(context_tokens=tokens)
    under = relay_dir(ctx.env) is not None
    # 引継ぎの手順はほかの guard の拒否より先に従い、連結で末尾（wait の行の扱い）が切れないよう単独で出す
    reason = _reason(t, under, main, svc.params["handoff_dir"])
    return deny(reason, notes={"context_tokens": tokens, "under_relay": under}, sole=True)
