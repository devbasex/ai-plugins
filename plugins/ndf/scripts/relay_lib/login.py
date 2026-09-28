"""`relay.py account add <名前>`: OAuth のアカウントの登録（#1389・#1468）。

端末では 1 回の `claude auth login` を対話で通す。端末でなければ 2 回に分ける: 1 回目は `claude auth login` を新しい
セッションで起動して生かしておき（待機中のログイン。標準入力は FIFO）、認可の URL と期限を返す。2 回目は認可コードの
1 行を FIFO へ書き、終了を待ち、1 回の登録と同じ確かめ（`auth status`・同じメールアドレスと組織の重複。I3）を通して
登録する（#1468 の決定 2）。認可コードは FIFO だけを通し、引数・環境変数・ファイル・画面へ書かない（I9）。
置き場を書くのは `lib/claude_accounts.py` である。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from . import claude as cl
from .ask import Fail
from .common import PKG_ROOT  # noqa: F401  lib/ を sys.path に置く

import claude_accounts as ca  # noqa: E402,I001
from claude_usage import iso_utc  # noqa: E402

AUTH_ENV = ca.AUTH_ENV  # 専用の設定ディレクトリで claude を起動するときに外す変数（正本は claude_accounts）
URL_WAIT = 30.0  # 1 回目が認可の URL を待つ秒数
EXIT_WAIT = 60.0  # 2 回目が待機中のログインの終了を待つ秒数
URL_RE = re.compile(r"https://[^\s\x1b\"'<>]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _env(config_dir: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in AUTH_ENV and k not in cl.DROP_ENV and k != "NDF_RELAY_DIR"}
    env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def _auth(claude: str, config_dir: str, *args: str) -> subprocess.CompletedProcess | None:
    """専用の設定ディレクトリで `claude auth <副命令>` を起動する。起動できなければ None。"""
    try:
        return subprocess.run(
            [claude, "auth", *args], env=_env(config_dir), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _identity(claude: str, config_dir: str) -> tuple[str, str, str] | None:
    """専用の設定ディレクトリの `claude auth status`（JSON）からメールアドレス・組織の ID・組織名を読む。

    組織の 2 つは無ければ空。1 つのメールアドレスで組織ごとに別のアカウントになるため、組織まで読む（I3）。
    """
    p = _auth(claude, config_dir, "status", "--json")
    if p is None:
        return None
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("loggedIn") or not isinstance(d.get("email"), str):
        return None
    org_id, org_name = d.get("orgId"), d.get("orgName")
    return d["email"], org_id if isinstance(org_id, str) else "", org_name if isinstance(org_name, str) else ""


def _backfill_org(claude: str, acc: ca.Account) -> str:
    """組織を記録する前の登録の組織を、その設定ディレクトリの `auth status` から読み直して書き戻す。読めなければ空。"""
    ident = _identity(claude, ca.account_dir(acc.name))
    if ident is None or not ident[1] or ident[0].lower() != acc.email.lower():
        return ""
    ca.set_org(acc.name, ident[1], ident[2])
    return ident[1]


def owner_of(email: str, org_id: str = "", other_than: str = "", claude: str | None = None) -> str | None:
    """同じメールアドレスと組織で登録済みのアカウントの名前（I3）。

    1 つのメールアドレスで複数の組織（個人と Team など）に属せ、組織ごとに利用上限が別になる。既存の登録の
    組織が分からない（組織を記録する前の登録）ときは、`claude` があれば読み直して書き戻す。それでもどちらかの
    組織が分からなければ、違うと言い切れないので同じとみなす。
    """
    for n in ca.names():
        acc = ca.load_account(n)
        if n == other_than or not acc or not acc.email or acc.email.lower() != email.lower():
            continue
        known = acc.org_id or (_backfill_org(claude, acc) if claude and org_id else "")
        if not known or not org_id or known == org_id:
            return n
    return None


def who(email: str, org_name: str) -> str:
    return f"{email}・{org_name}" if org_name else email


def check_name(name: str) -> None:
    """名前・OS・登録済みを確かめる。通らなければ Fail。"""
    if not ca.valid_name(name):
        raise Fail("invalid_name", f"名前は英小文字・数字・- と _ の 32 字まで（{ca.METERED} は使えない）: {name}", 2)
    if sys.platform == "darwin":
        # macOS の claude は資格情報を Keychain に置き、設定ディレクトリの .credentials.json を書かない
        raise Fail("platform", "macOS では登録できない（claude が資格情報を Keychain に置き、.credentials.json を書かない）。Linux で使う", 2)
    old = ca.load_account(name)
    if old is not None and not old.needs_relogin:
        raise Fail("already_registered", f"登録済み: {name}（{old.email}）。置き直すなら先に account remove {name}")


def _claude() -> str:
    claude = cl.resolve_claude()
    if claude is None:
        raise Fail("claude_missing", "本物の claude が見つからない")
    return claude


def _register(claude: str, name: str, config_dir: str, fail_reason: str) -> dict:
    """ログインの済んだ `config_dir` を確かめて `name` として置く（I3）。"""
    ident = _identity(claude, config_dir)
    if ident is None or not os.path.isfile(os.path.join(config_dir, ca.CRED_FILE)):
        raise Fail(fail_reason, "ログインが通らなかった。登録しない")
    email, org_id, org_name = ident
    owner = owner_of(email, org_id, other_than=name, claude=claude)
    if owner:
        acc = ca.load_account(owner)
        raise Fail("already_registered", f"登録済み: {owner}（{who(acc.email, acc.org_name) if acc else email}）")
    ca.register(name, config_dir, email, org_id, org_name)
    return {"stage": "registered", "email": email, "org_name": org_name or None, "text": f"登録した: {name}（{who(email, org_name)}）"}


def add_once(name: str, quiet_stdout: bool = False) -> dict:
    """端末での 1 回の登録（今までと同じ）。`quiet_stdout` なら claude auth login の標準出力を標準エラーへ回す（`--json`）。"""
    check_name(name)
    claude = _claude()
    staging = ca.staging_dir(name)
    try:
        try:
            subprocess.run([claude, "auth", "login"], env=_env(staging), stdout=sys.stderr if quiet_stdout else None)
        except (OSError, subprocess.SubprocessError) as e:
            raise Fail("claude_missing", f"claude auth login を起動できない（{e}）") from e
        return _register(claude, name, staging, "bad_code")
    finally:
        ca.discard(staging)


def _read_url(out: str, proc: subprocess.Popen) -> str | None:
    deadline = time.time() + URL_WAIT
    while time.time() < deadline:
        try:
            with open(out, encoding="utf-8", errors="replace") as f:
                text = ANSI_RE.sub("", f.read())
        except OSError:
            text = ""
        urls = URL_RE.findall(text)
        if urls:
            return next((u for u in urls if "oauth" in u), urls[0])
        if proc.poll() is not None:
            return None
        time.sleep(0.1)
    return None


def start(name: str) -> dict:
    """1 回目: 待機中のログインを起動し、認可の URL と期限を返す（E8）。同じ名前の途中の状態は作り直す。"""
    check_name(name)
    claude = _claude()
    d = ca.make_pending(name)
    ok = False
    try:
        rfd = os.open(os.path.join(d, "code.fifo"), os.O_RDWR)  # 読み手を先に持つ（2 回目の書き込みを待たせない）
        ofd = os.open(os.path.join(d, "login.out"), os.O_WRONLY | os.O_APPEND)
        try:
            proc = subprocess.Popen(
                [claude, "auth", "login"],
                env=_env(os.path.join(d, "config")),
                stdin=rfd,
                stdout=ofd,
                stderr=ofd,
                start_new_session=True,
            )
        except OSError as e:
            raise Fail("claude_missing", f"claude auth login を起動できない（{e}）") from e
        finally:
            os.close(rfd)
            os.close(ofd)
        p = ca.save_pending(name, proc.pid)
        url = _read_url(os.path.join(d, "login.out"), proc)
        if url is None:
            raise Fail("claude_missing", "claude auth login が認可の URL を出さなかった")
        ok = True
    finally:
        if not ok:
            ca.discard_pending(name)
    expires = iso_utc(p.expires_at)
    text = (
        f"認可の URL: {url}\n"
        f"ブラウザで認可し、画面に出た認可コードを {expires} までに渡す: "
        f"printf '%s\\n' '<コード>' | relay.py account add {name} --code -"
    )
    return {"stage": "awaiting_code", "url": url, "expires_at": expires, "text": text}


def _wait_exit(p: ca.Pending) -> bool:
    deadline = time.time() + EXIT_WAIT
    while time.time() < deadline:
        if not p.alive():
            return True
        time.sleep(0.1)
    return False


def finish(name: str, code: str, swept: set[str]) -> dict:
    """2 回目: 認可コードを待機中のログインへ渡して登録する（E9）。どの失敗でも登録の途中の状態を捨てる。"""
    if name in swept:
        raise Fail("expired", f"登録の途中の状態が期限切れで捨てられた。account add {name} からやり直す")
    check_name(name)
    p = ca.read_pending(name)
    if p is None:
        ca.discard_pending(name)
        raise Fail("no_pending", f"登録の途中の状態が無い。先に account add {name} を --code なしで打つ")
    try:
        if p.expired(time.time()):
            raise Fail("expired", f"登録の途中の状態が期限切れ。account add {name} からやり直す")
        code = code.strip()
        if not code or "\n" in code:
            raise Fail("bad_code", "認可コードが空か 2 行以上")
        try:
            fd = os.open(os.path.join(ca.pending_dir(name), "code.fifo"), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as e:
            raise Fail("no_pending", f"待機中のログインが終わっていた。account add {name} からやり直す") from e
        try:
            os.write(fd, (code + "\n").encode())
        finally:
            os.close(fd)
        if not _wait_exit(p):
            raise Fail("bad_code", "claude auth login が終わらなかった。登録しない")
        return _register(_claude(), name, os.path.join(ca.pending_dir(name), "config"), "bad_code")
    finally:
        ca.discard_pending(name)


def read_code(arg: str) -> str:
    """`--code` の値。`-` は標準入力の 1 行（引数と Bash の記録に残さない。決定 13）。"""
    return sys.stdin.readline() if arg == "-" else arg
