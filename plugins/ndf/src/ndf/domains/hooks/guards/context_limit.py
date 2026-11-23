"""文脈の量の guard（SF12・#2595・#2789）。

会話の記録の末尾の最後の応答の文脈の量（token）が運用上限（`context_thresholds.thresholds` の `limit`。上限の設定の
`context.limit` と `NDF_CONTEXT_LIMIT`。#3995）以上なら、引継ぎに要る呼び出し（メインディレクトリの引継ぎ文書の Read・Write・Edit、
`params.pass_tools`、`params.pass_ndf` の `ndf` のコマンドだけの Bash）を除いて止める。拒否の文は背景の監視の止め方・引継ぎ文書・
終え方（中継の下は次のセグメントのブロック、外は ndf-next）を書き、文脈の量を入れない。拒否は同じ理由で数えられ、続けば案内へ下がる
（#3345 の決定 92）。

計測のマーカー `under_relay` は判定したときだけ書く（判定しなかった行と中継の外を見分ける。#2789）。

サブエージェント（入力の `agent_id`）と、メインディレクトリに `.ndf` の無い会話は判定せず、会話の記録も読まない（#3995 の 3995:15）。
メインディレクトリは `.git` と管理ディレクトリの `commondir` だけから求め、git のコマンドを起動しない。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from ndf import identity
from ndf.domains.hooks import tables
from ndf.domains.hooks.context import context_tokens
from ndf.domains.hooks.strict import _repo_root
from ndf.domains.relay.context_thresholds import Thresholds, thresholds
from ndf.domains.relay.signals import relay_dir
from ndf.domains.hooks.verdict import Verdict, deny, passing

# 運用上限で通す Bash に許さない形: 変数の展開・コマンド置換（`$`・バッククォート）とプロセス置換
_SUBSTITUTION = re.compile(r"[$`]|[<>]\(")
# 運用上限で通す Bash の語に許さない形: チルダ・グロブ・ブレースの展開
_GLOB = re.compile(r"^~|[*?\[{]")
# ファイルへ書かないリダイレクト（演算子と先を空白でつないだ字面）: 記述子の複製（先が数字か -）と、/dev/null へ向ける形
_SAFE_REDIRECT = re.compile(r"[<>]& (?:-|[0-9]+)|&?>>? /dev/null")


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

    相対のパスは CLI（`records/cli.py` の `_root`）と同じく、`--cwd`（無ければ `cwd`）の実パスを含む worktree の根から解く。
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
            return paths is not None and all(_in_handoff(p, str(base), params["handoff_dir"]) for p in paths)
    return False


def _redirect_ok(r) -> bool:
    """ファイルへ書かないリダイレクト（記述子の複製か /dev/null）か。"""
    return not r.dynamic and _SAFE_REDIRECT.fullmatch(f"{r.op} {r.target}") is not None


def _command_ok(cmd, cwd: str, params: dict) -> bool:
    """ラッパーを持たず、展開の無い語で、背景でない `ndf` の通すコマンドか。"""
    return (
        not cmd.background
        and cmd.argv == cmd.body_argv
        and cmd.argv[:1] == (identity.FIXED_WORDS["entrypoint"],)
        and not any(_GLOB.search(w) for w in cmd.argv)
        and _ndf_args_pass(list(cmd.argv[1:]), cwd, params)
    )


def _passes(ctx, params: dict) -> bool:
    """運用上限以上でも通す呼び出し（引継ぎ文書の Read・Write・Edit、`pass_tools`、`pass_ndf` だけの Bash）か。"""
    if ctx.tool in ("Read", "Write", "Edit"):
        path = ctx.tool_input.get("file_path")
        return isinstance(path, str) and _in_handoff(path, ctx.cwd, params["handoff_dir"])
    if ctx.tool in params["pass_tools"]:
        return True
    if ctx.tool != "Bash":
        return False
    shell = ctx.shell
    if not shell.commands or shell.assignments or shell.heredocs or len(shell.waits) > len(shell.commands):
        return False
    if _SUBSTITUTION.search(ctx.command) or not all(_redirect_ok(r) for r in shell.redirects):
        return False
    return all(_command_ok(c, ctx.cwd, params) for c in shell.commands)


def _reason(t: Thresholds, under_relay: bool, handoff_dir: str) -> str:
    """運用上限の拒否の文（背景の監視の扱い・引継ぎ文書・終え方）。文脈の量は入れない。"""
    end = "応答の最後に次のセグメントのブロックを書いて終える" if under_relay else "ndf-next のブロックを出して新しいセッションで続ける"
    return (
        f"文脈の量が運用上限（{t.limit} token）を超えた。次の手: 背景の監視（ndf lanes wait・Monitor）があれば TaskStop で止めて"
        "完了通知で確かめる。supervisor・ndf plan queue は止めずに報告を待つ。止める前に wait が戻っていたら結果を処理せず、"
        "引継ぎ文書の「現在地」の wait の行をそのまま残す。"
        f"引継ぎ文書（オーガナイザーは {handoff_dir}parallel-<計画>.md）を書いて ndf records handoff check で確かめ、{end}"
    )


def check(ctx, svc) -> Verdict | None:
    if "agent_id" in ctx.payload or _main(ctx.cwd) is None:
        return None
    tokens = context_tokens(ctx.transcript_tail)
    if tokens is None:
        return None
    t = thresholds(ctx.env, tables.policy_value)
    if tokens < t.limit or _passes(ctx, svc.params):
        return passing(context_tokens=tokens)
    under = relay_dir(ctx.env) is not None
    return deny(_reason(t, under, svc.params["handoff_dir"]), notes={"context_tokens": tokens, "under_relay": under})
