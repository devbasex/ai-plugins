"""本番チャネルへのマージを見る `strict` の guard（#3345 の決定 91）。

AI が Bash で直に打つ `gh pr merge` を、宛先のリポジトリの宣言（`.ndf/project.json` の `delivery`）で判定する。
自動で本番へ届く宣言のリポジトリでは止め、宛先を確かめる NDF のマージの手順へ回す。hook の中では通信しないため
Pull Request の宛先のブランチを確かめられない。

- 宛先のリポジトリは、`cd` で移った先の作業ディレクトリと `-R`・`--repo` で決める（#2446・#2838）。`-R` が
  作業ディレクトリの `origin` と違えば、宛先の宣言を読めないので止め、宛先の clone へ `cd` して打つよう案内する
- シェルの字句は `shlex` で読む。読めない・宣言を読めないときは止める（自分の失敗で止める側へ倒す）
- 宛先は Pull Request の URL（`gh pr merge https://github.com/<owner>/<repo>/pull/<番号>`）からも決める
- 制御構文の語（`if` / `then` / `do` など）と包む語（`env -i`・`sudo -u x`・`xargs` など）の後ろのコマンドも読み、
  `bash -c` / `sh -c` / `eval` の引数とコマンド置換（`$(...)`・`` `...` ``）は中のスクリプトとして読み直す

標準ライブラリだけで書く。環境が無いときのエントリポイント（システムの `python3`）も、依存の import より先にこれを呼ぶ。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

from ndf.domains.hooks import strict_shell, tables
from ndf.domains.hooks.verdict import Verdict, deny

# guard の表（hooks/guards.json）でのこの guard の名前
GUARD_NAME = "merge-production"

# 前に付いても後ろのコマンドを打つ語（後ろの語のうち gh・シェル・eval から読み直す）
_WRAPPERS = frozenset(
    {"command", "env", "exec", "nohup", "time", "sudo", "doas", "xargs", "timeout", "nice", "ionice", "stdbuf", "setsid"}
)
# 後ろにコマンドが続く制御構文の語
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"})
# `-c` の引数をスクリプトとして読むシェル
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# 中のスクリプトを読み直す深さの上限（超えたら止める）
_MAX_DEPTH = 8
_URL_RE = re.compile(r"^https?://[^/]+/([^/]+/[^/]+)/pull/\d+")

_MERGE_HELP = "NDF のマージの手順（宛先を確かめ、本番なら承認を求めてからマージする）で打つ"


class MergeCall(NamedTuple):
    """`gh pr merge` の呼び出し。`repo` は Pull Request の URL か `-R`・`--repo` の `owner/repo`（無ければ None）。"""

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
    return _script_denial(text, Path(root), 0)


def _script_denial(text: str, here: Path, depth: int) -> Optional[Verdict]:
    """スクリプト 1 つの判定。読めない・深すぎるときは止める。"""
    if depth > _MAX_DEPTH:
        return deny(f"入れ子のスクリプトが深く、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    try:
        segments = list(strict_shell.segments(text))
    except ValueError:
        return deny(f"シェルの字句を読めず、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    return _first_denial(segments, here, depth)


def _mentions_merge(text: str) -> bool:
    """字句を読む前の粗い絞り込み（`gh` と `merge` を含むか）。"""
    return "gh" in text and "merge" in text


def _first_denial(segments: List[List[str]], here: Path, depth: int = 0) -> Optional[Verdict]:
    """`cd` で作業ディレクトリを移しながら、最初に止める `gh pr merge` の判定。無ければ None。"""
    for raw in segments:
        words = _command_words(raw)
        if words[:1] == ["cd"]:
            here = _cd(here, words)
            continue
        verdict = _inner_denial(here, raw, words, depth) or _call_verdict(here, words)
        if verdict is not None:
            return verdict
    return None


def _inner_denial(here: Path, raw: List[str], words: List[str], depth: int) -> Optional[Verdict]:
    """`bash -c`・`eval`・コマンド置換の中のスクリプトの判定。中に `gh` と `merge` が無ければ None。"""
    for inner in _inner_scripts(raw, words):
        if _mentions_merge(inner):
            verdict = _script_denial(inner, here, depth + 1)
            if verdict is not None:
                return verdict
    return None


def _inner_scripts(raw: List[str], words: List[str]) -> List[str]:
    found = [re.sub(r"\$\(|[`()]", " ; ", w) for w in raw if "`" in w or "$(" in w]
    head = os.path.basename(words[0]) if words else ""
    if head == "eval":
        found.append(" ".join(words[1:]))
    elif head in _SHELLS:
        for i, word in enumerate(words[1:-1], start=1):
            if word.startswith("-") and not word.startswith("--") and "c" in word:
                found.append(words[i + 1])
                break
    return found


def _call_verdict(here: Path, words: List[str]) -> Optional[Verdict]:
    call = _merge_call(words)
    return None if call is None else _judge(here, call)


def _command_text(raw: object) -> Optional[str]:
    if not isinstance(raw, dict) or raw.get("hook_event_name") != "PreToolUse" or raw.get("tool_name") != "Bash":
        return None
    tool_input = raw.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else None


def _cd(here: Path, words: List[str]) -> Path:
    target = Path(os.path.expanduser(words[1] if len(words) > 1 else "~"))
    return target if target.is_absolute() else here / target


def _command_words(words: List[str]) -> List[str]:
    """前に付く変数の代入・制御構文の語・包む語を外した、実際に打つコマンドの語。"""
    i = 0
    while i < len(words):
        word = words[i]
        if word in _KEYWORDS or "=" in word.split("/")[0] and not word.startswith("-"):
            i += 1
        elif os.path.basename(word) in _WRAPPERS:
            i = _after_wrapper(words, i + 1)
        else:
            break
    return words[i:]


def _after_wrapper(words: List[str], start: int) -> int:
    """包む語の後ろで、打つコマンド（`gh`・シェル・`eval`）の位置。無ければ包む語の直後の語の位置。"""
    for j in range(start, len(words)):
        if os.path.basename(words[j]) in _SHELLS | {"gh", "eval"}:
            return j
    return start


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
    if positional[:2] != ["pr", "merge"]:
        return None
    urls = [m.group(1) for m in map(_URL_RE.match, positional[2:]) if m]
    return MergeCall(urls[0] if urls else repo)


def _judge(here: Path, call: MergeCall) -> Optional[Verdict]:
    repo_root = _repo_root(here)
    if repo_root is None:
        return deny(f"マージの宛先のリポジトリを {here} から決められない。宛先の clone へ cd してから打つか、{_MERGE_HELP}")
    if call.repo:
        origin = _origin_slug(repo_root)
        if origin is None or origin.lower() != call.repo.lower():
            return deny(
                f"宛先 {call.repo}（-R・--repo か Pull Request の URL）は作業ディレクトリ {repo_root} のリポジトリ（{origin or '不明'}）と違い、"
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
