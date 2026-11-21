"""`ndf relay account add <名前>`: OAuth のアカウントの登録（旧い `relay.py account add` を移した。#3569）。

結果 JSON を出すサブコマンドなので、登録は 2 回に分ける。1 回目（`--code` なし）は `claude auth login` を新しいセッションで
起動して生かしておき（待機中のログイン。標準入力は FIFO）、認可の URL と期限を返す。2 回目（`--code`）は認可コードの
1 行を FIFO へ書き、終了を待ち、`claude auth status` で本人を確かめ、同じメールアドレスと組織の重複が無ければ登録する。
認可コードは FIFO だけを通し、引数・環境変数・ファイル・結果へ書かない。

登録の途中の状態は旧いと同じ `<保存先>/.pending-<名前>/`（`config/`・`code.fifo`・`login.out`・`pending.json`）に置く。
"""

from __future__ import annotations

import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

import psutil

from ndf.adapters import claude as claude_adapter
from ndf.adapters import cli_floor
from ndf.contracts import facts, limits
from ndf.domains.relay import account_registry as reg

PENDING_TTL = 600.0  # 登録の途中の状態の期限（秒。旧いと同じ）
POLL_INTERVAL = 0.1  # 締め切りまで条件を見直す間隔（秒）
PENDING_CONFIG, PENDING_FIFO, PENDING_OUT, PENDING_FILE = "config", "code.fifo", "login.out", "pending.json"
URL_RE = re.compile(r"https://[^\s\x1b\"'<>]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# 専用の設定ディレクトリで claude を起動するときに外す変数（親の認証を子のログインへ持ち込まない）。
# 認証の値・提供元の選択・認証のプロファイルは、登録済みアカウントの子と同じく子の環境の表の account-drop の行から引く
AUTH_ENV = (
    *facts.ids("env", child="account-drop"),
    "NDF_CLAUDE_ACCOUNT",
    "NDF_RELAY_DIR",
)


class LoginError(Exception):
    """登録できない。`kind` は結果の原因の種類（`refused`・`precondition`・`invalid`）。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class Added(NamedTuple):
    """登録の結果。`needs_code` なら認可コードが要る（`url` と `expires_at` を持つ）。"""

    name: str
    needs_code: bool
    url: Optional[str] = None
    expires_at: Optional[str] = None
    email: Optional[str] = None
    org_name: Optional[str] = None


def _claude() -> str:
    """PATH から claude を探す。バージョンは確かめない（`require_claude` が起動と同じ環境で確かめる）。"""
    path = shutil.which(claude_adapter.CLAUDE)
    if path is None:
        raise LoginError("precondition", f"{claude_adapter.CLAUDE} が PATH に無い")
    return path


def require_claude(claude: str, env: Mapping[str, str]) -> None:
    """`claude` が下限より古ければ `LoginError("precondition")`（`cli_floor`。#3691）。

    `env` は認証のコマンドと同じ環境（`auth_env`）を渡す。`claude --version` にも、外している親の認証を渡さない。
    """
    cli_floor.refuse_old("claude", claude, functools.partial(LoginError, "precondition"), env)  # type: ignore[arg-type]


def auth_env(environ: Mapping[str, str], config_dir: Path) -> dict[str, str]:
    """専用の設定ディレクトリで claude を打つ環境（親の認証の変数を外し、`CLAUDE_CONFIG_DIR` を置く）。"""
    env = {k: v for k, v in environ.items() if k not in AUTH_ENV}
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def _auth(claude: str, config_dir: Path, environ: Mapping[str, str], *args: str) -> Optional[subprocess.CompletedProcess]:
    """`claude auth <args>` を打つ。起動できなければ None、claude が下限より古ければ `LoginError("precondition")`。"""
    env = auth_env(environ, config_dir)
    require_claude(claude, env)
    try:
        return subprocess.run(
            [claude, "auth", *args],
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=limits.seconds("relay.claude-auth"),
        )
    except (OSError, subprocess.SubprocessError):  # noqa: NDF401 外部コマンド: 起動できなければ None（呼び手がログインの失敗として止める）
        return None


def _identity(claude: str, config_dir: Path, environ: Mapping[str, str]) -> Optional[reg.Identity]:
    """`claude auth status --json` のメールアドレス・組織の ID・組織名。ログインしていなければ None。"""
    p = _auth(claude, config_dir, environ, "status", "--json")
    if p is None:
        return None
    try:
        d = json.loads(p.stdout)
    except ValueError:  # noqa: NDF401 外部コマンド: JSON でない出力はログインしていないとみなす
        return None
    if not isinstance(d, dict) or not d.get("loggedIn") or not isinstance(d.get("email"), str):
        return None
    org_id, org_name = d.get("orgId"), d.get("orgName")
    return reg.Identity(d["email"], org_id if isinstance(org_id, str) else "", org_name if isinstance(org_name, str) else "")


def owner_of(store: Path, email: str, org_id: str, other_than: str) -> Optional[str]:
    """同じメールアドレスと組織で登録済みのアカウントの名前。どちらかの組織が分からなければ同じとみなす。"""
    for n in reg.names(store):
        acc = reg.load(store, n)
        if n == other_than or acc is None or acc.email.lower() != email.lower():
            continue
        if not acc.org_id or not org_id or acc.org_id == org_id:
            return n
    return None


def check_name(store: Path, name: str) -> None:
    reg.check_name(name)
    if name in reg.key_names(store):
        raise LoginError("invalid", f"名前が登録済み API キーと重なる: {name}")
    if sys.platform == "darwin":
        raise LoginError("precondition", "macOS では登録できない（claude が資格情報を Keychain に置き、.credentials.json を書かない）")
    old = reg.load(store, name)
    if old is not None and not old.needs_relogin:
        raise LoginError("refused", f"登録済み: {name}。置き直すなら先に ndf relay account remove {name}")


def pending_dir(store: Path, name: str) -> Path:
    return store / f".pending-{name}"


def _start_time(pid: int) -> Optional[float]:
    try:
        p = psutil.Process(pid)
        if p.status() == psutil.STATUS_ZOMBIE:
            return None
        return round(p.create_time(), 2)
    except psutil.Error:  # noqa: NDF401 外部コマンド: 無いプロセスは開始の時刻を持たない
        return None


def _read_pending(store: Path, name: str) -> Optional[dict[str, Any]]:
    try:
        d = json.loads((pending_dir(store, name) / PENDING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない途中の状態は無いものとして作り直す
        return None
    ok = isinstance(d, dict) and isinstance(d.get("pid"), int) and isinstance(d.get("pid_start"), (int, float))
    return d if ok else None


def _alive(p: dict[str, Any]) -> bool:
    return _start_time(p["pid"]) == p["pid_start"]


def discard_pending(store: Path, name: str) -> None:
    """待機中のログインを止め（自分のセッションのグループへ SIGTERM → SIGKILL）、登録の途中の状態を消す。"""
    p = _read_pending(store, name)
    if p is not None and _alive(p):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p["pid"], sig)
            except OSError:
                break
            _wait_until(lambda: not _alive(p), limits.seconds("procs.stop-tree"))
            if not _alive(p):
                break
    reg.discard(pending_dir(store, name))


def sweep_pending(store: Path, now: float) -> set[str]:
    """期限を過ぎた登録の途中の状態を捨て、捨てた名前を返す。"""
    gone: set[str] = set()
    if not store.is_dir():
        return gone
    for e in store.iterdir():
        name = e.name.removeprefix(".pending-")
        if name == e.name:
            continue
        p = _read_pending(store, name)
        stale = now >= float(p["expires"]) if p and "expires" in p else now - e.stat().st_mtime > PENDING_TTL
        if stale:
            discard_pending(store, name)
            gone.add(name)
    return gone


def _read_url(out: Path, proc: subprocess.Popen) -> Optional[str]:
    deadline = time.monotonic() + limits.seconds("relay.login-url")
    while time.monotonic() < deadline:
        try:
            text = ANSI_RE.sub("", out.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            text = ""
        urls = URL_RE.findall(text)
        if urls:
            return next((u for u in urls if "oauth" in u), urls[0])
        if proc.poll() is not None:
            return None
        time.sleep(POLL_INTERVAL)
    return None


def _start(store: Path, name: str, environ: Mapping[str, str], now: float) -> Added:
    claude = _claude()
    d = pending_dir(store, name)
    env = auth_env(environ, d / PENDING_CONFIG)
    require_claude(claude, env)  # 古ければ登録の途中の状態を作らずに止まる
    discard_pending(store, name)
    reg.make_store(store)
    d.mkdir(mode=0o700)
    (d / PENDING_CONFIG).mkdir(mode=0o700)
    os.mkfifo(d / PENDING_FIFO, 0o600)
    os.close(os.open(d / PENDING_OUT, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    ok = False
    try:
        rfd = os.open(d / PENDING_FIFO, os.O_RDWR)  # 読み手を先に持つ（2 回目の書き込みを待たせない）
        ofd = os.open(d / PENDING_OUT, os.O_WRONLY | os.O_APPEND)
        try:
            proc = subprocess.Popen([claude, "auth", "login"], env=env, stdin=rfd, stdout=ofd, stderr=ofd, start_new_session=True)
        except OSError as exc:
            raise LoginError("precondition", f"claude auth login を起動できない（{exc}）") from exc
        finally:
            os.close(rfd)
            os.close(ofd)
        expires = now + PENDING_TTL
        started = _start_time(proc.pid) or 0.0  # noqa: NDF501 外部コマンド: 開始の時刻を読めなければ 0（生きているとみなさない）
        row = {"version": 1, "name": name, "pid": proc.pid, "pid_start": started, "created_at": reg.iso_utc(now)}
        reg.write(d / PENDING_FILE, {**row, "expires_at": reg.iso_utc(expires), "expires": expires})
        url = _read_url(d / PENDING_OUT, proc)
        if url is None:
            raise LoginError("refused", "claude auth login が認可の URL を出さなかった")
        ok = True
    finally:
        if not ok:
            discard_pending(store, name)
    return Added(name, needs_code=True, url=url, expires_at=reg.iso_utc(expires))


def _wait_until(done: Callable[[], bool], seconds: float) -> bool:
    """締め切り（今から `seconds` 秒）まで `POLL_INTERVAL` ごとに `done` を見る。成り立てば真、締め切りを越えたら偽。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(POLL_INTERVAL)
    return False


def _wait_exit(p: dict[str, Any]) -> bool:
    return _wait_until(lambda: not _alive(p), limits.seconds("relay.login-exit"))


def _register(store: Path, claude: str, name: str, config_dir: Path, environ: Mapping[str, str], now: float) -> Added:
    ident = _identity(claude, config_dir, environ)
    if ident is None or not (config_dir / reg.CRED_FILE).is_file():
        raise LoginError("refused", "ログインが通らなかった。登録しない")
    owner = owner_of(store, ident.email, ident.org_id, other_than=name)
    if owner:
        raise LoginError("refused", f"同じメールアドレスと組織で登録済み: {owner}")
    reg.register(store, name, config_dir, ident, now)
    return Added(name, needs_code=False, email=ident.email, org_name=ident.org_name or None)  # noqa: NDF501 外部コマンド: 組織名の無い本人は null


def _finish(store: Path, name: str, code: str, environ: Mapping[str, str], now: float) -> Added:
    p = _read_pending(store, name)
    try:
        if p is None:
            raise LoginError("precondition", f"登録の途中の状態が無いか期限切れ。先に ndf relay account add {name} を --code なしで打つ")
        code = code.strip()
        if not code or "\n" in code:
            raise LoginError("invalid", "認可コードが空か 2 行以上")
        try:
            fd = os.open(pending_dir(store, name) / PENDING_FIFO, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            raise LoginError("refused", f"待機中のログインが終わっていた。ndf relay account add {name} からやり直す") from exc
        try:
            os.write(fd, (code + "\n").encode())
        finally:
            os.close(fd)
        if not _wait_exit(p):
            raise LoginError("refused", "claude auth login が終わらなかった。登録しない")
        return _register(store, _claude(), name, pending_dir(store, name) / PENDING_CONFIG, environ, now)
    finally:
        discard_pending(store, name)


def add(store: Path, name: str, code: Optional[str], environ: Mapping[str, str], now: Optional[float] = None) -> Added:
    """`code` が無ければ待機中のログインを起動して認可コードを求め、あれば渡して登録する。"""
    now = time.time() if now is None else now
    sweep_pending(store, now)
    check_name(store, name)
    if code is None:
        return _start(store, name, environ, now)
    return _finish(store, name, code, environ, now)


def logout(store: Path, name: str, environ: Mapping[str, str]) -> bool:
    """アカウントの設定ディレクトリで `claude auth logout` を打つ。通らなければ偽。"""
    claude = shutil.which(claude_adapter.CLAUDE)
    if claude is None:
        return False
    p = _auth(claude, reg.account_dir(store, name), environ, "logout")
    return p is not None and p.returncode == 0
