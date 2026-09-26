"""シェルの設定ファイルの管理ブロック（囲み）の読み書きと、その記録（#928・#936・#966・#1142 の C6）。

install・uninstall・status・startup のどの副命令も、ここの同じ関数でシェルの設定を読み書きする。
"""
from __future__ import annotations

import os
import re
import sys
import time

from .common import BLOCK_CLOSE, BLOCK_OPEN, _write_file, config_dir, copy_path, read_text

DEF_RE = re.compile(r"^\s*(alias\s+claude=|function\s+claude\b|claude\s*\(\s*\))")
UNSAFE = set("'\"\\$`!\n")
READS_BASHRC_RE = re.compile(r"(?:^|[\s;&|])(?:\.|source)\s+\S*\.bashrc\b")


def shellrc_path() -> str:
    return os.path.join(config_dir(), "shellrc")


def loader_file() -> str | None:
    """devbase が読み込む永続化の場所（devbasex/devbase#253）。無ければ None。"""
    d = os.environ.get("DEVBASE_SHELLRC_DIR")
    return os.path.join(d, "ndf-relay.sh") if d and os.path.isdir(d) else None


def rc_files() -> list[str]:
    """囲みを足しうるファイル（`~/.bashrc`・`~/.bash_profile`・`~/.zshrc`）。uninstall はこの全部から外す。"""
    home = os.path.expanduser("~")
    return [os.path.join(home, ".bashrc"), os.path.join(home, ".bash_profile"),
            os.path.join(os.environ.get("ZDOTDIR") or home, ".zshrc")]


def login_files() -> list[str]:
    """ログインシェルの bash が読む候補。在る最初の 1 つだけを読む（#966）。"""
    home = os.path.expanduser("~")
    return [os.path.join(home, n) for n in (".bash_profile", ".bash_login", ".profile")]


def login_file() -> str | None:
    """ログインシェルの bash が実際に読むファイル。どれも無ければ None。"""
    return next((p for p in login_files() if os.path.exists(p)), None)


def bash_look() -> list[str]:
    """bash で既存の `claude` の定義を探すファイル。ログインシェルの設定も含める（#936・#966）。"""
    home = os.path.expanduser("~")
    return [os.path.join(home, ".bashrc"), os.path.join(home, ".bash_aliases")] + login_files()


def shell_rc() -> tuple[str, str, list[str]] | None:
    """(シェルの名前, 足す先, 既存の定義を探すファイル)。bash と zsh 以外は None。

    macOS の bash は `~/.bash_profile` へ足す。macOS の端末は新しいウィンドウをログインシェルで
    開き、ログインシェルの bash は `~/.bashrc` を読まないためである（#966）。"""
    shell = os.path.basename(os.environ.get("SHELL", ""))
    bashrc, bash_profile, zshrc = rc_files()
    if shell == "bash":
        return shell, (bash_profile if sys.platform == "darwin" else bashrc), bash_look()
    if shell == "zsh":
        return shell, zshrc, [zshrc]
    return None


def reads_bashrc(path: str) -> bool:
    """囲みの外に `~/.bashrc` を読む行（`. ~/.bashrc`・`source ~/.bashrc` など）があるか。"""
    body = read_text(path)
    if body is None:
        return False
    lines = body.split("\n")
    inside = set()
    for a, b in blocks_of(lines)[0]:
        inside.update(range(a, b + 1))
    return any(READS_BASHRC_RE.search(line) for i, line in enumerate(lines)
               if i not in inside and not line.lstrip().startswith("#"))


def login_shadow() -> tuple[str, str] | None:
    """macOS の bash で `~/.bash_profile` が無く、`~/.bash_login` か `~/.profile` をログインシェルが
    読んでいるとき (作る先, 読まれている先)。作ると読まれている先が読まれなくなる。"""
    bash_profile = login_files()[0]
    login = login_file()
    if login is None or login == bash_profile:
        return None
    return bash_profile, login


def login_warning() -> str | None:
    """macOS の bash で、読み込みの行が `~/.bashrc` にしか無く、ログインシェルが読むファイルが
    `~/.bashrc` を読まないときの警告。"""
    sh = shell_rc()
    if sys.platform != "darwin" or not sh or sh[0] != "bash":
        return None
    bashrc, bash_profile = rc_files()[:2]
    if not rc_blocks(bashrc)[0]:
        return None
    login = login_file()
    if login and (rc_blocks(login)[0] or reads_bashrc(login)):
        return None
    return (f"警告: 読み込みの行は {bashrc} にしか無く、ログインシェルが読む {login or bash_profile} は "
            f"{bashrc} を読まない。macOS の端末はログインシェルで開くため、ラッパーが効かない。"
            f"/ndf:install-wrapper を打ち直すと {bash_profile} へ足す")


def sh_quote(path: str) -> str:
    """`$HOME` の下なら `"$HOME/..."`、それ以外は `"<絶対パス>"`。"""
    home = os.path.expanduser("~").rstrip("/")
    if home and path.startswith(home + "/"):
        return f'"$HOME{path[len(home):]}"'
    return f'"{path}"'


def shellrc_body() -> str:
    c = sh_quote(copy_path())
    return ("# ndf のラッパー。/ndf:install-wrapper が書き、/ndf:install-wrapper uninstall が消す\n"
            "function claude {\n"
            f"  if [ -f {c} ]; then python3 {c} run \"$@\"\n"
            "  else command claude \"$@\"; fi\n"
            "}\n")


def loader_line() -> str:
    s = sh_quote(shellrc_path())
    return f"[ -f {s} ] && . {s}"


def loader_inner() -> list[str]:
    return ["# ndf のラッパー（/ndf:install-wrapper uninstall で外れる）", loader_line()]


def loader_body() -> str:
    return "\n".join(loader_inner()) + "\n"


# ---------------------------------------------------------------- 記録（rc-added・rc-user・rc-noticed・rc-skipped）


def _records(path: str) -> list[str]:
    return (read_text(path) or "").splitlines()


def _add_record(path: str, line: str) -> None:
    if line not in _records(path):
        with open(path, "a") as f:
            f.write(line + "\n")


def _drop_records(path: str, lines: list[str]) -> None:
    rows = _records(path)
    keep = [r for r in rows if r not in lines]
    if keep != rows:
        _write_file(path, "".join(r + "\n" for r in keep).encode(), 0o600)


def _backup(path: str, body: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bak = f"{path}.ndf-bak-{stamp}"
    with open(bak, "w", encoding="utf-8") as f:
        f.write(body)
    return bak


# ---------------------------------------------------------------- 囲み


def blocks_of(lines: list[str]) -> tuple[list[tuple[int, int]], bool]:
    """囲みの (開きの行, 閉じの行) と、閉じの無い囲みがあるかを返す。`lines` は改行を落とした行。"""
    found, start = [], None
    for i, line in enumerate(lines):
        s = line.rstrip("\r")
        if start is None and s == BLOCK_OPEN:
            start = i
        elif start is not None and s == BLOCK_CLOSE:
            found.append((start, i))
            start = None
    return found, start is not None


def rc_blocks(path: str) -> tuple[list[tuple[int, int]], bool, str | None]:
    body = read_text(path)
    if body is None:
        return [], False, None
    found, unclosed = blocks_of(body.split("\n"))
    return found, unclosed, body


def has_definition(paths: list[str]) -> str | None:
    """囲みの外に行頭の `claude` の定義を持つファイル。"""
    for p in paths:
        body = read_text(p)
        if body is None:
            continue
        lines = body.split("\n")
        inside = set()
        for a, b in blocks_of(lines)[0]:
            inside.update(range(a, b + 1))
        if any(DEF_RE.match(line) for i, line in enumerate(lines) if i not in inside):
            return p
    return None


def rewrite_blocks(path: str, body: str, inner: list[str] | None) -> str:
    """囲みの中を `inner` へ置き換える（None なら囲みを行ごと外す）。囲みの外は変えない。
    バックアップのパスを返す。"""
    lines = body.split("\n")
    out, i = [], 0
    for a, b in blocks_of(lines)[0]:
        out += lines[i:a]
        if inner is not None:
            out += [lines[a]] + inner + [lines[b]]
        i = b + 1
    out += lines[i:]
    bak = _backup(path, body)
    _write_file(path, "\n".join(out).encode("utf-8"), os.stat(path).st_mode & 0o7777)
    return bak


def block_inner(body: str, a: int, b: int) -> list[str]:
    return [x.rstrip("\r") for x in body.split("\n")[a + 1:b]]


def has_direct_alias(text: str, found: list[tuple[int, int]]) -> bool:
    return any(any(x.startswith("alias claude=") for x in block_inner(text, a, b))
               for a, b in found)


def _has_loader(rc: str) -> bool:
    """`rc` に閉じた囲みがあり、中が読み込みの行（直の alias でない）か。"""
    found, unclosed, text = rc_blocks(rc)
    return bool(found) and not unclosed and not has_direct_alias(text, found)


# ---------------------------------------------------------------- 10.17.4〜10.17.6 の自動の囲み


def _auto_blocks(root: str) -> list[str]:
    """`rc-added` に載り `rc-user` に載らず、今も囲みがあるパス（10.17.4〜10.17.6 の自動の囲み）。"""
    user = _records(os.path.join(root, "rc-user"))
    return [p for p in dict.fromkeys(_records(os.path.join(root, "rc-added")))
            if p not in user and rc_blocks(p)[0]]


def _startup_record_noticed(root: str) -> list[str]:
    """まだ知らせていない自動の囲みのパスを集め、`rc-noticed` に記録して返す。"""
    noticed = _records(os.path.join(root, "rc-noticed"))
    paths = [p for p in _auto_blocks(root) if p not in noticed]
    for p in paths:
        _add_record(os.path.join(root, "rc-noticed"), p)
    return paths


def _startup_notice_message(paths: list[str]) -> str:
    """自動で足した alias を知らせる通知文を、パスの列から組み立てる。"""
    msg = (f"ndf-relay: {'・'.join(paths)} の alias claude は 10.17.4〜10.17.6 が自動で足したもの。"
           "使い続けるなら何もしなくてよい。外すなら /ndf:install-wrapper uninstall。"
           "この alias は別のファイルが定義した alias claude（devbase の "
           "--dangerously-skip-permissions など）を上書きしている。/ndf:install-wrapper で"
           "入れ直せば囲みの中が読み込みの 1 行に替わり、その alias が戻る")
    if os.environ.get("DEVBASE_SHELLRC_DIR"):
        msg += "。コンテナを作り直した後も使うなら /ndf:install-wrapper"
    return msg
