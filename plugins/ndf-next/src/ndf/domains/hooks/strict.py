"""本番チャネルへのマージを見る `strict` の guard（#3345 の決定 91）。

AI が Bash で直に打つ `gh pr merge` を、宛先のリポジトリの宣言（`.ndf/project.json` の `delivery`）で判定する。
自動で本番へ届く宣言のリポジトリでは止め、宛先を確かめる NDF のマージの手順へ回す。hook の中では通信しないため
Pull Request の宛先のブランチを確かめられない。

- 宛先のリポジトリは、`cd` で移った先の作業ディレクトリと `-R`・`--repo`・環境変数 `GH_REPO` で決める（#2446・#2838）。
  宛先が作業ディレクトリの `origin` と違えば、宛先の宣言を読めないので止め、宛先の clone へ `cd` して打つよう案内する
- `cd` の先が展開（`$`・`` ` ``）や `-` を含むとき、`pushd`・`popd` のときは、移った先を決められないので後のマージを止める
- サブシェル（`(`…`)`）の中の `cd`・`export` は閉じた後に戻し、パイプラインと裏で走らせる（`&`）コマンドの `cd`・`export` は
  後の文へ持ち越さない（どれも別のシェルで走る）。対にならない `)` の後は作業ディレクトリを決められないので止める
- 宛先の初期値は hook の起動時の環境変数 `GH_REPO`（Bash の Tool と同じセッションの環境）
- シェルの字句は `shlex` で読む。読めない・宣言を読めないときは止める（自分の失敗で止める側へ倒す）
- 宛先は Pull Request の URL（`gh pr merge https://github.com/<owner>/<repo>/pull/<番号>`）からも決める
- 制御構文の語（`if` / `then` / `do` など）と包む語（`env -i`・`sudo -u x`・`xargs` など）の後ろのコマンドも読み、
  `bash -c` / `sh -c` / `eval` の引数とコマンド置換（`$(...)`・`` `...` ``）は中のスクリプトとして読み直す
- `-c` 無しで起動したシェル（標準入力・ヒアドキュメント・ファイルからスクリプトを読む）と `source`・`.` は中を読めないので止める

標準ライブラリだけで書く。環境が無いときのエントリポイント（システムの `python3`）も、依存の import より先にこれを呼ぶ。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple, Union

from ndf.domains.hooks import strict_shell, tables
from ndf.domains.hooks.verdict import Verdict, deny

# guard の表（hooks/guards.json）でのこの guard の名前
GUARD_NAME = "merge-production"

# 前に付いても後ろのコマンドを打つ語（後ろの語のうち gh・シェル・eval から読み直す）
_WRAPPERS = frozenset({"command", "env", "exec", "nohup", "time", "sudo", "doas", "xargs", "timeout", "nice", "ionice", "stdbuf", "setsid"})
# 後ろにコマンドが続く制御構文の語
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"})
# `-c` の引数をスクリプトとして読むシェル
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# 中のスクリプトを読み直す深さの上限（超えたら止める）
_MAX_DEPTH = 8
_URL_RE = re.compile(r"^https?://[^/]+/([^/]+/[^/]+)/pull/\d+")
# gh が `-R` の代わりに宛先として読む環境変数（https://cli.github.com/manual/gh_help_environment）
_GH_REPO = "GH_REPO"
# 作業ディレクトリを移す語のうち、移った先を字句から決められないもの
_UNKNOWN_DIR_COMMANDS = frozenset({"pushd", "popd"})
# ファイルのスクリプトを今のシェルで読む語
_SOURCES = frozenset({"source", "."})

_MERGE_HELP = "NDF のマージの手順（宛先を確かめ、本番なら承認を求めてからマージする）で打つ"


class MergeCall(NamedTuple):
    """`gh pr merge` の呼び出し。`repo` は Pull Request の URL・`-R`・`--repo`・`GH_REPO` の `owner/repo`（無ければ None）。"""

    repo: Optional[str]


def judge(payload: dict, off: frozenset) -> Tuple[Optional[Verdict], Optional[float]]:
    """エントリポイントが呼ぶ判定と所要のミリ秒。`NDF_HOOK_OFF` で止めていれば `(None, None)`。"""
    if tables.is_off(GUARD_NAME, off):
        return None, None
    began = time.monotonic()
    verdict = check(payload, str(payload.get("cwd") or os.getcwd()), os.environ.get(_GH_REPO) or None)
    return verdict, round((time.monotonic() - began) * 1000, 3)


def check(raw_payload: object, root: str, gh_repo: Optional[str] = None) -> Optional[Verdict]:
    """止める答えか None。Bash の PreToolUse 以外は None。`gh_repo` は hook の起動時の環境変数 `GH_REPO`。"""
    text = _command_text(raw_payload)
    if text is None or not _mentions_merge(text):
        return None
    return _script_denial(text, Path(root), 0, gh_repo)


def _script_denial(text: str, here: Optional[Path], depth: int, gh_repo: Optional[str] = None) -> Optional[Verdict]:
    """スクリプト 1 つの判定。読めない・深すぎるときは止める。`here` が None なら作業ディレクトリを決められない。"""
    if depth > _MAX_DEPTH:
        return deny(f"入れ子のスクリプトが深く、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    try:
        parts = list(strict_shell.parts(text))
    except ValueError:
        return deny(f"シェルの字句を読めず、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    return _first_denial(parts, here, depth, gh_repo)


def _mentions_merge(text: str) -> bool:
    """字句を読む前の粗い絞り込み（`gh` と `merge` を含むか）。"""
    return "gh" in text and "merge" in text


def _first_denial(
    parts: List[Union[List[str], str]], here: Optional[Path], depth: int = 0, gh_repo: Optional[str] = None
) -> Optional[Verdict]:
    """`cd` で作業ディレクトリを、`export GH_REPO=...` で宛先を移しながら、最初に止める `gh pr merge` の判定。無ければ None。

    サブシェルの `(` で今の値を退避して `)` で戻す。パイプラインと `&` のコマンドの移動は後の文へ持ち越さない。
    """
    saved: List[Tuple[Optional[Path], Optional[str]]] = []
    for i, raw in enumerate(parts):
        if isinstance(raw, str):
            if raw == "(":
                saved.append((here, gh_repo))
            elif raw == ")":
                here, gh_repo = saved.pop() if saved else (None, gh_repo)
            continue
        words = _command_words(raw)
        moved = _moved(here, gh_repo, raw, words)
        if moved is not None:
            if not _in_own_shell(parts, i):
                here, gh_repo = moved
            continue
        env_repo = _assigned_gh_repo(raw[: len(raw) - len(words)], gh_repo)
        verdict = (
            _unreadable_script_denial(words) or _inner_denial(here, raw, words, depth, env_repo) or _call_verdict(here, words, env_repo)
        )
        if verdict is not None:
            return verdict
    return None


def _moved(
    here: Optional[Path], gh_repo: Optional[str], raw: List[str], words: List[str]
) -> Optional[Tuple[Optional[Path], Optional[str]]]:
    """作業ディレクトリか宛先を移す文（`cd`・`pushd`・`popd`・代入だけ・`export`・`unset GH_REPO`）なら移った後の値。ほかは None。"""
    head = words[:1]
    if head == ["cd"] or head and head[0] in _UNKNOWN_DIR_COMMANDS:
        return _cd(here, words), gh_repo
    if not words or head == ["export"]:
        return here, _assigned_gh_repo(raw, gh_repo)
    if head == ["unset"] and _GH_REPO in words[1:]:
        return here, None
    return None


def _in_own_shell(parts: List[Union[List[str], str]], i: int) -> bool:
    """`i` 番目の文が別のシェルで走るか（パイプラインの一部か、`&` で裏で走る）。"""
    before = parts[i - 1] if i > 0 else None
    after = parts[i + 1] if i + 1 < len(parts) else None
    return before in strict_shell.PIPES or after in strict_shell.PIPES or after == strict_shell.BACKGROUND


def _assigned_gh_repo(words: List[str], current: Optional[str]) -> Optional[str]:
    """語の並びの中の `GH_REPO=<値>` の最後の値（無ければ `current`）。"""
    for word in words:
        if word.startswith(f"{_GH_REPO}="):
            current = word[len(_GH_REPO) + 1 :] or None
    return current


def _unreadable_script_denial(words: List[str]) -> Optional[Verdict]:
    """中を読めないスクリプトを走らせる文（`-c` 無しのシェル・`source`・`.`）なら止める。"""
    name = os.path.basename(words[0]) if words else ""
    if name in _SOURCES:
        where = f"{name} がファイル"
    elif name in _SHELLS and _shell_script(words) is None:
        where = f"{name} が -c 無しで" + ("標準入力・ヒアドキュメント" if _reads_stdin(words) else "スクリプトのファイル")
    else:
        return None
    return deny(
        f"{where}からスクリプトを読み、中の gh pr merge が本番チャネルへのマージかを確かめられない。`bash -c '...'` で渡すか、{_MERGE_HELP}"
    )


def _reads_stdin(words: List[str]) -> bool:
    """シェルがスクリプトを標準入力から読むか（スクリプトのファイルの引数が無いか、`-s` を持つ）。"""
    it = iter(_without_redirections(words[1:]))
    for word in it:
        if word in ("-o", "+o"):
            next(it, None)
        elif word.startswith("-") and not word.startswith("--") and "s" in word:
            return True
        elif not word.startswith(("-", "+")):
            return False
    return True


def _without_redirections(words: List[str]) -> List[str]:
    """リダイレクト（`<<`・`<<<`・`<`・`2>` など）とその対象の語を外した語。"""
    kept: List[str] = []
    skip = False
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        if skip:
            skip = False
        elif word and set(word) <= set("<>&"):
            skip = True
        elif not (word.isdigit() and following and set(following) <= set("<>&")):
            kept.append(word)
    return kept


def _inner_denial(here: Optional[Path], raw: List[str], words: List[str], depth: int, gh_repo: Optional[str]) -> Optional[Verdict]:
    """`bash -c`・`eval`・コマンド置換の中のスクリプトの判定。中に `gh` と `merge` が無ければ None。"""
    for inner in _inner_scripts(raw, words):
        if _mentions_merge(inner):
            verdict = _script_denial(inner, here, depth + 1, gh_repo)
            if verdict is not None:
                return verdict
    return None


def _substitutions(raw: List[str]) -> List[str]:
    """コマンド置換（`$(...)`・`` `...` ``）を区切りに変えた語。引用の無い `` ` `` が語をまたげば、閉じるまでの語をつなぐ。"""
    found: List[str] = []
    pending: Optional[List[str]] = None
    for word in raw:
        odd = word.count("`") % 2 == 1
        if pending is not None:
            pending.append(word)
            if odd:
                found.append(" ".join(pending))
                pending = None
        elif odd:
            pending = [word]
        elif "`" in word or "$(" in word:
            found.append(word)
    if pending is not None:
        found.append(" ".join(pending))
    return [re.sub(r"\$\(|[`()]", " ; ", w) for w in found]


def _inner_scripts(raw: List[str], words: List[str]) -> List[str]:
    found = _substitutions(raw)
    head = os.path.basename(words[0]) if words else ""
    if head == "eval":
        found.append(" ".join(words[1:]))
    elif head in _SHELLS:
        script = _shell_script(words)
        if script is not None:
            found.append(script)
    return found


def _shell_script(words: List[str]) -> Optional[str]:
    """シェルの `-c` の引数。`-c` が無ければ None。"""
    for i, word in enumerate(words[1:-1], start=1):
        if word.startswith("-") and not word.startswith("--") and "c" in word:
            return words[i + 1]
    return None


def _call_verdict(here: Optional[Path], words: List[str], gh_repo: Optional[str]) -> Optional[Verdict]:
    call = _merge_call(words, gh_repo)
    return None if call is None else _judge(here, call)


def _command_text(raw: object) -> Optional[str]:
    if not isinstance(raw, dict) or raw.get("hook_event_name") != "PreToolUse" or raw.get("tool_name") != "Bash":
        return None
    tool_input = raw.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else None


def _cd(here: Optional[Path], words: List[str]) -> Optional[Path]:
    """移った先の作業ディレクトリ。展開・`-`・`pushd`・`popd` で字句から決められなければ None。"""
    args = words[1:]
    if words[0] != "cd" or any(a.startswith("-") or "$" in a or "`" in a for a in args):
        return None
    target = Path(os.path.expanduser(args[0] if args else "~"))
    if target.is_absolute():
        return target
    return None if here is None else here / target


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


def _merge_call(words: List[str], gh_repo: Optional[str] = None) -> Optional[MergeCall]:
    """`gh pr merge` の呼び出し。`-R`・`--repo` が無ければ `gh_repo`（環境変数 `GH_REPO`、`[HOST/]OWNER/REPO`）を宛先にする。"""
    words = _command_words(words)
    if not words or os.path.basename(words[0]) != "gh":
        return None
    repo: Optional[str] = "/".join(gh_repo.rstrip("/").split("/")[-2:]) if gh_repo else None
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


def _judge(here: Optional[Path], call: MergeCall) -> Optional[Verdict]:
    if here is None:
        return deny(
            f"cd の先が展開・`-`・pushd・popd で、マージの宛先のリポジトリを決められない。"
            f"宛先の clone の絶対パスへ cd してから打つか、{_MERGE_HELP}"
        )
    repo_root = _repo_root(here)
    if repo_root is None:
        return deny(f"マージの宛先のリポジトリを {here} から決められない。宛先の clone へ cd してから打つか、{_MERGE_HELP}")
    if call.repo:
        origin = _origin_slug(repo_root)
        if origin is None or origin.lower() != call.repo.lower():
            return deny(
                f"宛先 {call.repo}（-R・--repo・GH_REPO か Pull Request の URL）は作業ディレクトリ {repo_root} のリポジトリ（{origin or '不明'}）と違い、"
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
