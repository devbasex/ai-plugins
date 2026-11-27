"""本番チャネルへのマージを見る `strict` の guard（docs/specifications/ndf-rebuild.md の決定 91）。

AI が Bash で直に打つ `gh pr merge` を、マージ先のリポジトリの設定（`.ndf/project.json` の `branch_strategy` の
`release.deliveries`）で判定する。
自動で本番へ届く設定のリポジトリでは止め、マージ先を確かめる NDF のマージの手順へ回す。hook の中では通信しないため
Pull Request のマージ先のブランチを確かめられない。

- マージ先のリポジトリは、`cd` で移った先の作業ディレクトリと `-R`・`--repo`・環境変数 `GH_REPO` で決める（#2446・#2838）。
  マージ先が作業ディレクトリの `origin` と違えば、マージ先の設定を読めないので止め、マージ先の clone へ `cd` して打つよう案内する
- `cd` の先が展開（`$`・`` ` ``）や `-` を含むとき、`pushd`・`popd` のときは、移った先を決められないので後のマージを止める
- サブシェル（`(`…`)`）の中の `cd`・`export` は閉じた後に戻し、パイプラインと裏で走らせる（`&`）コマンドの `cd`・`export` は
  後の文へ持ち越さない（どれも別のシェルで走る）。対にならない `)` の後は作業ディレクトリを決められないので止める
- マージ先の初期値は hook の起動時の環境変数 `GH_REPO`（Bash の Tool と同じセッションの環境）
- シェルの字句は `shlex` で読む。読めない・設定を読めないときは止める（自分の失敗で止める側へ倒す）
- マージ先は Pull Request の URL（`gh pr merge https://github.com/<owner>/<repo>/pull/<番号>`）からも決める
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

from ndf import identity
from ndf.adapters import git as git_mod
from ndf.core import production_merge
from ndf.domains.hooks import strict_shell, tables
from ndf.domains.hooks.verdict import Verdict, deny
from ndf.testing import weight

# マージ先のリポジトリの設定（ブランチ戦略の設定の届け先の行を読む）
_PROJECT_FILE = production_merge.PROJECT_FILE
# ブランチ戦略の設定の保存先（`core.branch_strategy` は jsonschema に依るので、ここでは標準ライブラリで読む）
_SECTION = identity.BRANCH_STRATEGY_SECTION
_DELIVERIES_KEY = f"{_SECTION}.release.deliveries"
# マージの承認の証跡のマーカーの語（`core.merge_approval.MERGE_MARK` の中身）
MERGE_MARK_WORD = identity.MERGE_MARK_WORD
# コメントの本文をファイルから読む引数（`gh pr comment`・`gh issue comment` の `--body-file`・`-F`、`gh api` の `--input`）
_BODY_FILE_FLAGS = ("--body-file", "-F", "--input")

# guard の表（hooks/guards.json）でのこの guard の名前
GUARD_NAME = "merge-production"

# 後ろにコマンドが続く制御構文の語
_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}"})
# `-c` の引数をスクリプトとして読むシェル
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# 中のスクリプトを読み直す深さの上限（超えたら止める）
_MAX_DEPTH = 8
_URL_RE = re.compile(r"^https?://[^/]+/([^/]+/[^/]+)/pull/\d+")
# gh が `-R` の代わりに対象のリポジトリとして読む環境変数（https://cli.github.com/manual/gh_help_environment）
_GH_REPO = "GH_REPO"
# hook の入力の、Tool の引数のキー（`context.HookContext.tool_input` も読む）
TOOL_INPUT = "tool_input"
# 作業ディレクトリを移す語のうち、移った先を字句から決められないもの
_UNKNOWN_DIR_COMMANDS = frozenset({"pushd", "popd"})
# ファイルのスクリプトを今のシェルで読む語
_SOURCES = frozenset({"source", "."})
# 前に付いても後ろのコマンドを打つ語
# 重い処理の判定のラッパー（`weight.WRAPPERS`）に、権限と端末を変えて後ろを打つ語を足す
_WRAPPERS = weight.WRAPPERS | {"sudo", "doas", "setsid"}
# 包む語の後ろで読み直す、打つコマンドの語
_WRAPPED_TARGETS = _SHELLS | {"gh", "eval"}

_MERGE_HELP = "NDF のマージの手順（マージ先を確かめ、本番なら承認を求めてからマージする）で打つ"


class MergeCall(NamedTuple):
    """`gh pr merge` の呼び出し。`repo` は Pull Request の URL・`-R`・`--repo`・`GH_REPO` の `owner/repo`（無ければ None）。"""

    repo: Optional[str]


class ShellContext(NamedTuple):
    """文を打つシェルの環境。作業ディレクトリ `here`（決められなければ None）とマージ先 `gh_repo`（`GH_REPO` の値、無ければ None）。
    `script` は Bash の Tool に渡したコマンドの全文（ヒアドキュメントの本文を含む。証跡のマーカーを探す）。"""

    here: Optional[Path]
    gh_repo: Optional[str]
    script: str = ""


def judge(payload: dict, off: frozenset) -> Tuple[Optional[Verdict], Optional[float]]:
    """エントリポイントが呼ぶ判定と所要のミリ秒。`NDF_HOOK_OFF` で止めていれば `(None, None)`。"""
    if tables.is_off(GUARD_NAME, off):
        return None, None
    began = time.monotonic()
    verdict = check(payload, str(payload.get("cwd") or os.getcwd()), os.environ.get(_GH_REPO) or None)  # noqa: NDF501 利用者の入力: 空の GH_REPO は未設定と同じ
    return verdict, round((time.monotonic() - began) * 1000, 3)


def check(raw_payload: object, root: str, gh_repo: Optional[str] = None) -> Optional[Verdict]:
    """止める回答か None。Bash の PreToolUse 以外は None。`gh_repo` は hook の起動時の環境変数 `GH_REPO`。"""
    text = _command_text(raw_payload)
    if text is None or not _mentions_merge(text):
        return None
    return _script_denial(text, ShellContext(Path(root), gh_repo, text), 0)


def _script_denial(text: str, ctx: ShellContext, depth: int) -> Optional[Verdict]:
    """スクリプト 1 つの判定。読めない・深すぎるときは止める。`ctx.here` が None なら作業ディレクトリを決められない。"""
    if depth > _MAX_DEPTH:
        return deny(f"入れ子のスクリプトが深く、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    try:
        parts = list(strict_shell.parts(text))
    except ValueError:
        return deny(f"シェルの字句を読めず、本番チャネルへのマージかを確かめられない。{_MERGE_HELP}")
    return _first_denial(parts, ctx, depth)


def _mentions_merge(text: str) -> bool:
    """字句を読む前の粗い絞り込み（`gh` と、`merge` か本文をファイルから読む引数を含むか）。"""
    return "gh" in text and ("merge" in text or any(flag in text for flag in _BODY_FILE_FLAGS))


def _first_denial(parts: List[Union[List[str], str]], ctx: ShellContext, depth: int = 0) -> Optional[Verdict]:
    """`cd` で作業ディレクトリを、`export GH_REPO=...` でマージ先を移しながら、最初に止める `gh pr merge` の判定。無ければ None。

    サブシェルの `(` で今の値を退避して `)` で戻す。パイプラインと `&` のコマンドの移動は後の文へ持ち越さない。
    """
    saved: List[ShellContext] = []
    for i, raw in enumerate(parts):
        if isinstance(raw, str):
            ctx = _subshell_step(raw, saved, ctx)
            continue
        words = _command_words(raw)
        moved = _moved(ctx, raw, words)
        if moved is not None:
            if not _in_own_shell(parts, i):
                ctx = moved
            continue
        # 文の前の一時的な代入（`GH_REPO=x gh ...`）はその文だけの環境にし、後の文へ持ち越さない
        call_ctx = ctx._replace(gh_repo=_assigned_gh_repo(raw[: len(raw) - len(words)], ctx.gh_repo))
        verdict = _statement_denial(call_ctx, raw, words, depth)
        if verdict is not None:
            return verdict
    return None


def _subshell_step(raw: str, saved: List[ShellContext], ctx: ShellContext) -> ShellContext:
    """区切りの字句 1 つの処理。`(` で今の値を `saved` へ退避し、`)` で戻す（退避が無ければ作業ディレクトリを決められない）。"""
    if raw == "(":
        saved.append(ctx)
    elif raw == ")":
        return saved.pop() if saved else ctx._replace(here=None)
    return ctx


def _statement_denial(ctx: ShellContext, raw: List[str], words: List[str], depth: int) -> Optional[Verdict]:
    """文 1 つの判定（読めないスクリプト → 中のスクリプト → `gh pr merge` の呼び出しの順）。止めなければ None。"""
    return _unreadable_script_denial(words) or _inner_denial(ctx, raw, words, depth) or _call_verdict(ctx, words)


def _moved(ctx: ShellContext, raw: List[str], words: List[str]) -> Optional[ShellContext]:
    """作業ディレクトリかマージ先を移す文（`cd`・`pushd`・`popd`・代入だけ・`export`・`unset GH_REPO`）なら移った後の値。ほかは None。"""
    head = words[:1]
    if head == ["cd"] or head and head[0] in _UNKNOWN_DIR_COMMANDS:
        return ctx._replace(here=_cd(ctx.here, words))
    if not words or head == ["export"]:
        return ctx._replace(gh_repo=_assigned_gh_repo(raw, ctx.gh_repo))
    if head == ["unset"] and _GH_REPO in words[1:]:
        return ctx._replace(gh_repo=None)
    return None


def _in_own_shell(parts: List[Union[List[str], str]], i: int) -> bool:
    """`i` 番目の文が別のシェルで走るか（パイプラインの一部か、`&` で裏で走る）。

    改行で並んだ文は区切りの字句を挟まずに続くので、前後が語の並び（list）のときは区切りではない。
    """
    before = parts[i - 1] if i > 0 and isinstance(parts[i - 1], str) else None
    after = parts[i + 1] if i + 1 < len(parts) and isinstance(parts[i + 1], str) else None
    return before in strict_shell.PIPES or after in strict_shell.PIPES or after == strict_shell.BACKGROUND


def _assigned_gh_repo(words: List[str], current: Optional[str]) -> Optional[str]:
    """語の並びの中の `GH_REPO=<値>` の最後の値（無ければ `current`）。"""
    for word in words:
        if word.startswith(f"{_GH_REPO}="):
            current = word[len(_GH_REPO) + 1 :] or None  # noqa: NDF501 利用者の入力: 空の GH_REPO= はマージ先を外す
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


def _inner_denial(ctx: ShellContext, raw: List[str], words: List[str], depth: int) -> Optional[Verdict]:
    """`bash -c`・`eval`・コマンド置換の中のスクリプトの判定。中に `gh` と `merge` が無ければ None。"""
    for inner in _inner_scripts(raw, words):
        if _mentions_merge(inner):
            verdict = _script_denial(inner, ctx, depth + 1)
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


def _call_verdict(ctx: ShellContext, words: List[str]) -> Optional[Verdict]:
    call = _merge_call(words, ctx.gh_repo)
    if call is not None:
        return _judge(ctx.here, call)
    return _writes_merge_mark(ctx.here, words, ctx.script)


def _comment_call(words: List[str]) -> Optional[List[str]]:
    """`gh pr comment`・`gh issue comment`・`gh api` の呼び出しなら `gh` の後ろの語。ほかは None。"""
    words = _command_words(words)
    if not words or os.path.basename(words[0]) != "gh":
        return None
    positional = [w for w in words[1:] if not w.startswith("-")]
    if positional[:2] in (["pr", "comment"], ["issue", "comment"]) or positional[:1] == ["api"]:
        return words[1:]
    return None


def _writes_merge_mark(here: Optional[Path], words: List[str], text: str) -> Optional[Verdict]:
    """コメントを書く `gh` の呼び出しで、コマンドの全文か本文のファイルにマージの承認の証跡のマーカーがあれば止める（#4409 の決定 7）。

    証跡は NDF の回答の経路（工程の承認ゲート・MVV 判定・`ndf ci answer`）だけが書く。本文のファイルを読めない
    （標準入力 `-`・無いパス）ときは通す。防ぐ相手はうっかりで、読めないコメントをすべて止めると通常のコメントも止まる。
    """
    args = _comment_call(words)
    if args is None:
        return None
    if MERGE_MARK_WORD in text or any(MERGE_MARK_WORD in _body_file(here, path) for path in _body_files(args)):
        return deny(
            f"本文にマージの承認の証跡のマーカー（{MERGE_MARK_WORD}）がある。証跡は承認ゲートへの回答（`ndf answer`・"
            "`ndf plan next --answer <回答> --by human`）だけが書く。Bash のコメントで書かない"
        )
    return None


def _body_files(args: List[str]) -> List[str]:
    """本文をファイルから読む引数の値（`--body-file x`・`--body-file=x`・`-F x`・`--input x`）。"""
    found: List[str] = []
    it = iter(args)
    for word in it:
        if word in _BODY_FILE_FLAGS:
            found.append(next(it, ""))
        elif word.startswith(tuple(f"{flag}=" for flag in _BODY_FILE_FLAGS if flag.startswith("--"))):
            found.append(word.split("=", 1)[1])
    return found


def _body_file(here: Optional[Path], path: str) -> str:
    """本文のファイルの中身（`gh api -F <キー>=@<パス>` の `@` の後ろも読む）。標準入力 `-`・読めないパスは空。"""
    path = path.split("=@", 1)[1] if "=@" in path else path.removeprefix("@")
    if not path or path == "-":
        return ""
    target = Path(os.path.expanduser(path))
    if not target.is_absolute():
        if here is None:
            return ""
        target = here / target
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 本文のファイルを読めなければ通す（うっかりだけを防ぐ）
        return ""


def _command_text(raw: object) -> Optional[str]:
    if not isinstance(raw, dict) or raw.get("hook_event_name") != "PreToolUse" or raw.get("tool_name") != "Bash":
        return None
    tool_input = raw.get(TOOL_INPUT)
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
        elif _is_wrapper(word):
            i = _after_wrapper(words, i + 1)
        else:
            break
    return words[i:]


def _is_wrapper(word: str) -> bool:
    """前に付いても後ろのコマンドを打つ語か（後ろの語のうち gh・シェル・eval から読み直す）。"""
    return os.path.basename(word) in _WRAPPERS


def _after_wrapper(words: List[str], start: int) -> int:
    """包む語の後ろで、打つコマンド（`gh`・シェル・`eval`）の位置。無ければ包む語の直後の語の位置。"""
    for j in range(start, len(words)):
        if os.path.basename(words[j]) in _WRAPPED_TARGETS:
            return j
    return start


def _merge_call(words: List[str], gh_repo: Optional[str] = None) -> Optional[MergeCall]:
    """`gh pr merge` の呼び出し。`-R`・`--repo` が無ければ `gh_repo`（環境変数 `GH_REPO`、`[HOST/]OWNER/REPO`）をマージ先にする。"""
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
            f"cd の先が展開・`-`・pushd・popd で、マージ先のリポジトリを決められない。"
            f"マージ先の clone の絶対パスへ cd してから打つか、{_MERGE_HELP}"
        )
    repo_root = _repo_root(here)
    if repo_root is None:
        return deny(f"マージ先のリポジトリを {here} から決められない。マージ先の clone へ cd してから打つか、{_MERGE_HELP}")
    if call.repo:
        origin = _remote_slug(repo_root)
        if origin is None or origin.lower() != call.repo.lower():
            return deny(
                f"マージ先 {call.repo}（-R・--repo・GH_REPO か Pull Request の URL）は作業ディレクトリ {repo_root} のリポジトリ（{origin or '不明'}）と違い、"
                f"マージ先の設定を読めない。`cd <{call.repo} の clone> && gh pr merge ...` で打つか、{_MERGE_HELP}"
            )
    verdict = production_merge.automatic_rows(_deliveries(repo_root))
    if verdict is production_merge.Verdict.UNKNOWN:
        return deny(f"{repo_root} の {_PROJECT_FILE} の {_DELIVERIES_KEY} を読めず、本番へ届くかを決められない。{_MERGE_HELP}")
    if verdict is not production_merge.Verdict.AUTOMATIC:
        return None
    return deny(
        f"{repo_root} は本番チャネルへのマージが自動で本番へ届く（{_DELIVERIES_KEY}）。hook の中では Pull Request のマージ先を"
        f"確かめられないため、直の gh pr merge を止めた。{_MERGE_HELP}"
    )


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


def _remote_slug(repo_root: Path) -> Optional[str]:
    """リモート名（メインディレクトリの `.ndf/worktree.json` の `remote`、無ければ既定）の URL の `owner/repo`。読めなければ None。

    linked worktree からもメインディレクトリ（共通の git ディレクトリの親）の設定を読む（`Git.remote()` と同じ設定）。
    """
    git_dir = _git_dir(repo_root)
    return git_mod.configured_remote_slug(git_dir) if git_dir is not None else None


def _deliveries(repo_root: Path) -> object:
    """ブランチ戦略の設定（`.ndf/project.json` の `branch_strategy`）。ファイルを読めなければ None。

    届け先の規則（`release.deliveries` と `release.cd` の読み方）は判定の部品 `core.production_merge` が持つ（#4409 の決定 2）。
    """
    try:
        data = json.loads((repo_root / _PROJECT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 設定を読めないことを None で返し、判定の部品が unknown にする
        return None
    return data.get(_SECTION) if isinstance(data, dict) else None
