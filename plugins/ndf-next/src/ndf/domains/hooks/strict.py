"""本番チャネルへのマージを見る `strict` の guard（#3345 の決定 91）。

AI が Bash で直に打つ `gh pr merge` を、宛先のリポジトリの宣言（`.ndf/project.json` の `delivery`）で判定する。
自動で本番へ届く宣言のリポジトリでは止め、宛先を確かめる NDF のマージの手順へ回す。hook の中では通信しないため
Pull Request の宛先のブランチを確かめられない。

- 宛先のリポジトリは、`cd` で移った先の作業ディレクトリと `-R`・`--repo` で決める（#2446・#2838）。`-R` が
  作業ディレクトリの `origin` と違えば、宛先の宣言を読めないので止め、宛先の clone へ `cd` して打つよう案内する
- シェルの字句は `shlex` で読む。読めない・宣言を読めないときは止める（自分の失敗で止める側へ倒す）

標準ライブラリだけで書く。環境が無いときのエントリポイント（システムの `python3`）も、依存の import より先にこれを呼ぶ。
"""

from __future__ import annotations

import json
import os
import shlex
import time
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Tuple

from ndf.domains.hooks import tables
from ndf.domains.hooks.verdict import Verdict, deny

# guard の表（hooks/guards.json）でのこの guard の名前
GUARD_NAME = "merge-production"

# シェルの字句のうち、コマンドを区切るもの
_SEPARATORS = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "(", ")"})
# 前に付いても後ろのコマンドを打つ語
_WRAPPERS = frozenset({"command", "env", "exec", "nohup", "time", "sudo"})

_MERGE_HELP = "NDF のマージの手順（宛先を確かめ、本番なら承認を求めてからマージする）で打つ"


class MergeCall(NamedTuple):
    """`gh pr merge` の呼び出し。`repo` は `-R`・`--repo` の値（無ければ None）。"""

    repo: Optional[str]


def judge(payload: dict, off: frozenset) -> Tuple[Optional[Verdict], Optional[float]]:
    """エントリポイントが呼ぶ判定と所要のミリ秒。`NDF_HOOK_OFF` で止めていれば `(None, None)`。"""
    if tables.is_off(GUARD_NAME, off):
        return None, None
    began = time.monotonic()
    verdict = check(payload, str(payload.get("cwd") or os.getcwd()))
    return verdict, round((time.monotonic() - began) * 1000, 3)


def check(raw_payload: object, root: str) -> Optional[Verdict]:
    """止める答えか None。Bash の PreToolUse 以外は None。"""
    text = _command_text(raw_payload)
    if text is None or not _mentions_merge(text):
        return None
    try:
        segments = list(_segments(text))
    except ValueError:
        return deny(f"シェルの字句を読めず、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    return _first_denial(segments, Path(root))


def _mentions_merge(text: str) -> bool:
    """字句を読む前の粗い絞り込み（`gh` と `merge` を含むか）。"""
    return "gh" in text and "merge" in text


def _first_denial(segments: List[List[str]], here: Path) -> Optional[Verdict]:
    """`cd` で作業ディレクトリを移しながら、最初に止める `gh pr merge` の判定。無ければ None。"""
    for words in segments:
        if words[:1] == ["cd"]:
            here = _cd(here, words)
            continue
        verdict = _call_verdict(here, words)
        if verdict is not None:
            return verdict
    return None


def _call_verdict(here: Path, words: List[str]) -> Optional[Verdict]:
    call = _merge_call(words)
    return None if call is None else _judge(here, call)


def _command_text(raw: object) -> Optional[str]:
    if not isinstance(raw, dict) or raw.get("hook_event_name") != "PreToolUse" or raw.get("tool_name") != "Bash":
        return None
    tool_input = raw.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else None


def _segments(text: str) -> Iterator[List[str]]:
    """区切りで分けたコマンドの語の並び。引用の中の改行は次の行とつなぐ。閉じない引用は ValueError。"""
    buffer = ""
    for line in text.split("\n"):
        buffer = f"{buffer}\n{line}" if buffer else line
        if buffer.endswith("\\"):
            buffer = buffer[:-1]
            continue
        try:
            tokens = _tokens(buffer)
        except ValueError:  # noqa: S112 利用者の入力: 引用が次の行へ続くので、次の行とつないで読み直す
            continue
        buffer = ""
        yield from _split(tokens)
    if buffer:
        raise ValueError("閉じない引用")


def _tokens(line: str) -> List[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def _split(tokens: List[str]) -> Iterator[List[str]]:
    words: List[str] = []
    for token in tokens:
        if token in _SEPARATORS:
            if words:
                yield words
            words = []
        else:
            words.append(token)
    if words:
        yield words


def _cd(here: Path, words: List[str]) -> Path:
    target = Path(os.path.expanduser(words[1] if len(words) > 1 else "~"))
    return target if target.is_absolute() else here / target


def _command_words(words: List[str]) -> List[str]:
    """前に付く変数の代入と包む語を外した語。"""
    i = 0
    while i < len(words) and ("=" in words[i].split("/")[0] and not words[i].startswith("-") or words[i] in _WRAPPERS):
        i += 1
    return words[i:]


def _merge_call(words: List[str]) -> Optional[MergeCall]:
    words = _command_words(words)
    if not words or os.path.basename(words[0]) != "gh":
        return None
    repo: Optional[str] = None
    positional: List[str] = []
    it = iter(words[1:])
    for word in it:
        if word in ("-R", "--repo"):
            repo = next(it, "")
        elif word.startswith("--repo="):
            repo = word[len("--repo=") :]
        elif word.startswith("-R") and len(word) > 2:
            repo = word[2:].lstrip("=")
        elif not word.startswith("-"):
            positional.append(word)
    return MergeCall(repo) if positional[:2] == ["pr", "merge"] else None


def _judge(here: Path, call: MergeCall) -> Optional[Verdict]:
    repo_root = _repo_root(here)
    if repo_root is None:
        return deny(f"マージの宛先のリポジトリを {here} から決められない。宛先の clone へ cd してから打つか、{_MERGE_HELP}")
    if call.repo:
        origin = _origin_slug(repo_root)
        if origin is None or origin.lower() != call.repo.lower():
            return deny(
                f"-R {call.repo} は作業ディレクトリ {repo_root} のリポジトリ（{origin or '不明'}）と違い、"
                f"宛先の宣言を読めない。`cd <{call.repo} の clone> && gh pr merge ...` で打つか、{_MERGE_HELP}"
            )
    rows = _delivery(repo_root)
    if rows is None:
        return deny(f"{repo_root} の .ndf/project.json の delivery を読めず、本番へ届くかを決められない。{_MERGE_HELP}")
    if not any(_automatic_production(row) for row in rows):
        return None
    return deny(
        f"{repo_root} は本番チャネルへのマージが自動で本番へ届く（delivery）。hook の中では Pull Request の宛先を"
        f"確かめられないため、直の gh pr merge を止めた。{_MERGE_HELP}"
    )


def _automatic_production(row: object) -> bool:
    return isinstance(row, dict) and row.get("channel") == "production" and row.get("kind") != "manual"


def _repo_root(here: Path) -> Optional[Path]:
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_dir(repo_root: Path) -> Optional[Path]:
    """設定を持つ git のディレクトリ。worktree の `.git` ファイルは共通のディレクトリまでたどる。"""
    dot = repo_root / ".git"
    if dot.is_dir():
        return dot
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:  # noqa: NDF401 ファイル: .git を読めなければ git のディレクトリは無い
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = (repo_root / line[len("gitdir:") :].strip()).resolve()
    try:
        common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    return (gitdir / common).resolve()


def _origin_slug(repo_root: Path) -> Optional[str]:
    """`origin` の URL の `owner/repo`。読めなければ None。"""
    git_dir = _git_dir(repo_root)
    try:
        text = (git_dir / "config").read_text(encoding="utf-8") if git_dir else ""
    except OSError:  # noqa: NDF401 ファイル: 設定を読めなければ origin は不明
        return None
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == '[remote "origin"]' and line.replace(" ", "").startswith("url="):
            return slug_of_url(line.split("=", 1)[1].strip())
    return None


def slug_of_url(url: str) -> Optional[str]:
    """git の URL（https・ssh・scp の形）の `owner/repo`。"""
    tail = url.rstrip("/")
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    parts = tail.replace(":", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 and all(parts[-2:]) else None


def _delivery(repo_root: Path) -> Optional[list]:
    try:
        data = json.loads((repo_root / ".ndf" / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 宣言を読めないことを None で返し、呼び手が止める
        return None
    rows = data.get("delivery") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else None
