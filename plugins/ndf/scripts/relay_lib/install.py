"""`/ndf:install-wrapper` と SessionStart hook の本体: install・uninstall・status・startup（#928・#1142 の C6）。

シェルの設定の読み書きは `shellrc`、複製（ランチャーとバージョンディレクトリ）は `version_dir` が持つ。
"""
from __future__ import annotations

import json
import os
import sys

from . import proc
from .common import (BLOCK_CLOSE, BLOCK_OPEN, COPY_LOCK, INSTALL_LOCK, PID_FILE, LockBusy, _lock,
                     _read_bytes, _unlock, config_dir, copy_path, data_dir, read_text, state_root)
from .shellrc import (UNSAFE, _add_record, _auto_blocks, _backup, _drop_records, _has_loader,
                      _startup_notice_message, _startup_record_noticed, bash_look, block_inner,
                      has_definition, has_direct_alias, loader_body, loader_file, loader_inner, loader_line,
                      login_shadow, login_warning, rc_blocks, rc_files, rewrite_blocks, shell_rc,
                      shellrc_body, shellrc_path)
from .version_dir import (VersionDir, _self_body, _startup_copy, _startup_refresh_old_copy,
                          copy_version_path, old_copy_path, place_copy_to, plugin_version)


def out(line: str) -> None:
    print(f"ndf-relay: {line}")


def _take_both(copy_needed: bool) -> tuple[int, int | None]:
    """`install.lock` → `copy.lock` の順に 2 秒まで待つ。どちらかが取れなければ LockBusy。"""
    root = state_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    fd = _lock(os.path.join(root, INSTALL_LOCK), 2)
    if fd is None:
        raise LockBusy()
    if not copy_needed:
        return fd, None
    cfd = _lock(os.path.join(config_dir(), COPY_LOCK), 2)
    if cfd is None:
        _unlock(fd)
        raise LockBusy()
    return fd, cfd


def cmd_install() -> int:
    """利用者が明示に打つ導入（E0〜E6）。版は比べずに今の版を置く。"""
    loader = loader_file()
    for p in [copy_path(), shellrc_path()] + ([loader] if loader else []):
        if UNSAFE & set(p):
            out(f"{p} は引用できない文字を含むため置かない")
            return 1
    sh = shell_rc()
    if loader is None and sh is None:
        shell = os.path.basename(os.environ.get("SHELL", "")) or "このシェル"
        out(f"{shell} には足さない。使うなら次の 1 行を設定へ置く: {loader_line()}")
        return 1
    look = sh[2] if sh else list(dict.fromkeys(rc_files() + bash_look()))
    found = has_definition(look)
    if found:
        out(f"{found} に claude の定義があるため足さない。使うなら次の 1 行を自分で置く: {loader_line()}")
        return 1
    for rc in rc_files():
        if rc_blocks(rc)[1]:
            out(f"{rc} の囲みに閉じが無い。直してから打ち直す")
            return 1
    shadow = login_shadow() if loader is None and sh and sh[0] == "bash" and sys.platform == "darwin" \
        else None
    if shadow:
        out(f"{shadow[0]} が無く、ログインシェルは {shadow[1]} を読んでいる。{shadow[0]} を作ると "
            f"{shadow[1]} が読まれなくなるため足さない。使うなら {shadow[0]} を作り、{shadow[1]} を読む行と"
            f"次の 1 行を置く: {loader_line()}")
        return 1
    try:
        os.makedirs(config_dir(), mode=0o700, exist_ok=True)
        fd, cfd = _take_both(True)
    except LockBusy:
        out("ほかの導入が動いている。少し待ってから打ち直す")
        return 3
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    try:
        return _install_locked(loader, sh)
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    finally:
        _unlock(cfd)
        _unlock(fd)


def _install_locked(loader: str | None, sh) -> int:
    body = _self_body()
    ver = plugin_version()
    # ランチャーが読むバージョンディレクトリを先に置く
    VersionDir(config_dir()).ensure(ver or "unknown")
    place_copy_to(copy_path(), body)
    if ver:
        with open(copy_version_path(), "w") as f:
            f.write(ver + "\n")
    place_copy_to(shellrc_path(), shellrc_body().encode(), 0o644)
    root = state_root()
    added, user = os.path.join(root, "rc-added"), os.path.join(root, "rc-user")
    backups, touched = [], []
    # 残った囲み（10.17.4〜10.17.6 は alias を直に持つ）の中を今の読み込みの行へ置き換える
    for rc in rc_files():
        found, _, text = rc_blocks(rc)
        if found and any(block_inner(text, a, b) != loader_inner() for a, b in found):
            backups.append(rewrite_blocks(rc, text, loader_inner()))
            touched.append(rc)
    if loader:
        target = loader
        place_copy_to(loader, loader_body().encode(), 0o644)
    else:
        target = sh[1]
        found, _, text = rc_blocks(target)
        if not found:
            if text is not None:
                backups.append(_backup(target, text))
            with open(target, "a", encoding="utf-8") as f:
                if text and not text.endswith("\n"):
                    f.write("\n")
                f.write(("\n" if text else "") + BLOCK_OPEN + "\n" + loader_body() + BLOCK_CLOSE + "\n")
        touched.append(target)
    for rc in dict.fromkeys(touched):
        _add_record(added, rc)
        _add_record(user, rc)
    bak = f"（バックアップ {'・'.join(backups)}）" if backups else ""
    out(f"{target} から {shellrc_path()} を読むようにした{bak}。次に開くシェルから効く")
    out(f"ラッパーの本体 {copy_path()}（版 {ver or '不明'}）")
    return 0


def cmd_uninstall() -> int:
    """U1〜U6。10.17.4〜10.17.6 の自動の囲みも同じ手順で外す。"""
    for rc in rc_files():
        if rc_blocks(rc)[1]:
            out(f"{rc} の囲みに閉じが無い。何も変えていない。直してから打ち直す")
            return 1
    try:
        fd, cfd = _take_both(os.path.isdir(config_dir()))
    except LockBusy:
        out("ほかの導入が動いている。少し待ってから打ち直す")
        return 3
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    try:
        return _uninstall_locked()
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    finally:
        _unlock(cfd)
        _unlock(fd)


def _uninstall_locked() -> int:
    lines, removed_rc, direct_alias = [], [], False
    for rc in rc_files():
        found, _, text = rc_blocks(rc)
        if not found:
            continue
        direct_alias = direct_alias or has_direct_alias(text, found)
        bak = rewrite_blocks(rc, text, None)
        removed_rc.append(rc)
        lines.append(f"{rc} の囲みを外した（バックアップ {bak}）")
    loader = loader_file()
    for p in ([loader] if loader else []) + [shellrc_path(), copy_version_path(), copy_path(),
                                               old_copy_path()]:
        if os.path.lexists(p):
            os.unlink(p)
            lines.append(f"{p} を消した")
    for base in (config_dir(), data_dir()):
        lines += [f"{p} を消した" for p in VersionDir(base).remove_all()]
    root = state_root()
    for name in ("rc-skipped", "rc-noticed"):
        _drop_records(os.path.join(root, name), removed_rc)
    for rc in removed_rc:
        _add_record(os.path.join(root, "rc-added"), rc)
        _add_record(os.path.join(root, "rc-user"), rc)
    if not lines:
        out("外すものが無い")
        return 0
    for line in lines:
        out(line)
    out("開いているシェルでは " + ("unalias claude" if direct_alias else "unset -f claude") + " で外れる")
    if os.path.islink(os.path.dirname(config_dir())) or loader:
        out("同じ設定を共有する環境でも、ラッパーを通らなくなる（開いたままのシェルは素の claude へ落ちる）")
    return 0


def _same(path: str, body: bytes) -> str:
    """複製のランチャーと、それが読むバージョンディレクトリが今の版と同じか。"""
    b = _read_bytes(path)
    if b is None:
        return "無し"
    same = b == body and VersionDir(os.path.dirname(path)).is_current(plugin_version() or "unknown")
    return "今の版と同じ" if same else "今の版と違う"


def cmd_status() -> int:
    body = _self_body()
    loader = loader_file()
    root = state_root()
    auto = _auto_blocks(root)
    if loader:
        out(f"読み込み先: {loader}（{'在る' if os.path.exists(loader) else '無い'}）")
    else:
        sh = shell_rc()
        out(f"読み込み先: {sh[1] if sh else '無し（bash と zsh 以外のシェル）'} の囲み")
    for rc in rc_files():
        found, unclosed, text = rc_blocks(rc)
        if unclosed:
            out(f"{rc}: 閉じの無い囲みがある")
        elif not found:
            # `~/.bash_profile` へ足すのは macOS だけ。ほかでは無いときに行を出さない
            if rc != rc_files()[1] or sys.platform == "darwin":
                out(f"{rc}: 囲みは無い")
        else:
            direct = has_direct_alias(text, found)
            kind = "直の alias（10.17.4〜10.17.6 の形）" if direct else "読み込みの行"
            who = "。10.17.4〜10.17.6 が自動で足した" if rc in auto else ""
            out(f"{rc}: 囲みがある（{kind}{who}）")
    out(f"ラッパーの rc {shellrc_path()}: {'在る' if os.path.exists(shellrc_path()) else '無い'}")
    ver = (read_text(copy_version_path()) or "").strip() or "不明"
    out(f"複製 {copy_path()}: {_same(copy_path(), body)}（複製の版 {ver}）")
    out(f"旧い複製 {old_copy_path()}: {_same(old_copy_path(), body)}")
    out(session_line())
    warn = None if loader else login_warning()
    if warn:
        out(warn)
    return 0


def session_line() -> str:
    """今のセッションとラッパーとの位置を 1 行で示す（#1187）。

    `status` → シェル → claude と親をたどって最初に当たる claude は、hook のときと同じなので
    `relay_position()` をそのまま使う。親がたどれない環境では「判定できない」とする。
    """
    pos = proc.relay_position()
    head = "このセッション: "
    if pos == "relay":
        pid = (read_text(os.path.join(os.environ["NDF_RELAY_DIR"], PID_FILE)) or "").strip() or "不明"
        child = proc.relay_child_pid()
        return f"{head}ラッパー経由（relay PID {pid}、claude PID {child}）"
    if pos == "no-dir":
        line = f"{head}ラッパーを通らずに起動（NDF_RELAY_DIR が無い）"
        loader = loader_file()
        loaded = ([loader] if loader and os.path.exists(loader) else []) + \
            [rc for rc in rc_files() if _has_loader(rc)]
        if loaded:
            line += (f"。{'・'.join(loaded)} に読み込みの行はあるので、"
                     "このセッションは読み込みの前に開いたシェル、または IDE から起動した")
        return line
    if pos == "not-running":
        return f"{head}ラッパーは終わっている（NDF_RELAY_DIR はあるがラッパーが動いていない）"
    if proc.proc_info(os.getppid()) is None:
        return f"{head}判定できない（親のプロセスをたどれない）"
    return f"{head}ラッパーの直接の子ではない（fork・bg-pty-host・別の入口。#1016）"


def startup_once() -> str | None:
    root = state_root()
    if not (os.path.exists(os.path.join(root, "rc-added")) or os.path.exists(copy_path())
            or os.path.exists(old_copy_path())):
        return None
    os.makedirs(root, mode=0o700, exist_ok=True)
    fd = _lock(os.path.join(root, INSTALL_LOCK), 1)
    if fd is None:
        return None
    try:
        body = _self_body()
        try:
            _startup_copy(body)
        except OSError:
            pass
        _startup_refresh_old_copy(body)
        paths = _startup_record_noticed(root)
        if not paths:
            return None
        return _startup_notice_message(paths)
    finally:
        _unlock(fd)


def cmd_startup() -> int:
    """SessionStart hook の本体。シェルの設定・ラッパーの rc・読み込み先のファイルは書かない。"""
    try:
        msg = startup_once()
    except Exception:  # SessionStart を止めない
        return 0
    if msg:
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    return 0
