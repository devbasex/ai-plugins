"""ラッパーの入れ替えの判定と、入れ替えの申し送り（`handover.json`）（#1587）。

セッションの切り替えで子の claude が居ない間に、動いているラッパーを更新後の版のバージョンディレクトリの
コードへ `os.execve` で入れ替える。PID・端末・作業ディレクトリは変わらない。

1. `running_dir`: 動いているバージョンディレクトリの名前。プラグインのキャッシュから起動したものは None（判定しない）
2. `expected_dir`: 導入先のファイルから計算した、更新後に使うべき名前。動いている名前と同じなら何もしない
3. `prepare`: 違えば、更新後の版の `relay.py startup` を子プロセスで打ってバージョンディレクトリと
   `relay.current` を置かせ、動いているランチャーの置き場の `relay.current` を読み直す
4. `write` / `exec_argv`: 申し送りを 0600 で書き、入れ替え先の python でランチャーを起動し直す。
   新しい版は環境変数 `NDF_RELAY_HANDOVER` で申し送りのパスを受け、`read` で読んで消す

ラッパーは複製を書き換えない（置くのは更新後の版の `startup`）。申し送りは認証情報を持たない。
標準ライブラリと relay_lib の中だけを import する。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from . import runtime
from .claude import DROP_ENV
from .common import PKG_ROOT, env_num, read_text
from .version_dir import VersionDir, plugin_version

SCHEMA = 1
HANDOVER_FILE = "handover.json"
HANDOVER_ENV = "NDF_RELAY_HANDOVER"
LAUNCHER = "relay.py"
# `reexec_skipped` の `reason` の集合（設計の入出力の契約）
STARTUP_FAILED = "startup-failed"
NOT_PLACED = "not-placed"
UNREADABLE = "unreadable"
NO_ENV = "no-env"
NO_HANDOVER = "no-handover"
HANDOVER_WRITE = "handover-write"
EXEC_FAILED = "exec-failed"
REASONS = (STARTUP_FAILED, NOT_PLACED, UNREADABLE, NO_ENV, NO_HANDOVER, HANDOVER_WRITE, EXEC_FAILED)
# 読む側が無いと次のセッションを起動できないキー
REQUIRED = ("shown", "from_dir", "to_dir", "written_at", "claude", "marketplace", "version", "first_args", "state", "next")
STATE_KEYS = ("section", "account", "multi", "auth_section")
NEXT_KEYS = ("args", "cwd", "command", "from_session", "cwd_fallback", "carried", "plan")
# 環境の用意を含む startup の上限の秒（決定 10。中の uv sync と同じ値）
PREPARE_TIMEOUT = 600


class Skip(Exception):
    """入れ替えない。`reason` は REASONS の 1 つ、`detail` は 1 行。"""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def running_dir(root: str = PKG_ROOT) -> str | None:
    """動いているバージョンディレクトリの名前。プラグインのキャッシュから起動した（または隣にランチャーが無い）ときは None。"""
    if not runtime.is_version_dir(root):
        return None
    if not os.path.isfile(os.path.join(os.path.dirname(root), LAUNCHER)):
        return None
    return os.path.basename(root)


def own_dir(root: str = PKG_ROOT) -> str | None:
    """動いているバージョンディレクトリの名前（`start` の行の `relay_version_dir`）。キャッシュから起動したら None。"""
    return os.path.basename(root) if runtime.is_version_dir(root) else None


def copy_base(root: str = PKG_ROOT) -> str:
    """動いているランチャーの置き場（バージョンディレクトリの親）。読み直す `relay.current` はここのもの（I10）。"""
    return os.path.dirname(root)


def expected_dir(install_path: str | None, version: str | None) -> str | None:
    """導入先 `install_path` のファイルから計算した、更新後に使うべきバージョンディレクトリの名前。読めなければ None。

    版は導入先の `plugin.json`（`startup` が使うもの）を先に、無ければ `version` を使う。"""
    if not install_path:
        return None
    try:
        ver = plugin_version(install_path) or version
        if not ver:
            return None
        return VersionDir("", source=os.path.join(install_path, "scripts")).name_for(ver)
    except OSError:
        return None


def startup_env() -> dict:
    """`startup` の子プロセスの環境。`plugin_cli` と同じく、子の claude の印と作業ディレクトリを除く。"""
    return {k: v for k, v in os.environ.items() if k not in DROP_ENV and k not in ("NDF_RELAY_DIR", HANDOVER_ENV)}


def prepare(install_path: str, running: str, base: str) -> str:
    """更新後の版の `startup` を打ち、`base` の `relay.current` が指す入れ替え先の名前を返す。入れ替えないなら Skip。"""
    launcher = os.path.join(install_path, "scripts", LAUNCHER)
    timeout = env_num("NDF_RELAY_PREPARE_TIMEOUT", PREPARE_TIMEOUT)
    try:
        p = subprocess.run(
            [sys.executable, launcher, "startup"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=startup_env(),
        )
    except subprocess.TimeoutExpired:
        raise Skip(STARTUP_FAILED, f"startup が {int(timeout)} 秒で終わらない") from None
    except (OSError, subprocess.SubprocessError) as e:
        raise Skip(STARTUP_FAILED, f"startup を起動できない（{type(e).__name__}）") from None
    if p.returncode != 0:
        raise Skip(STARTUP_FAILED, f"startup の終了コード {p.returncode}")
    to = VersionDir(base).current()
    if to is None:
        raise Skip(UNREADABLE, "relay.current が無いか壊れている")
    if to == running:
        raise Skip(NOT_PLACED, "startup の後も relay.current が動いているものを指す")
    root = os.path.join(base, to)
    if not os.path.isfile(runtime.python_of(runtime.version_env(root))):
        raise Skip(NO_ENV, f"{to} に環境の python が無い")
    if not os.path.isfile(os.path.join(root, "relay_lib", "handover.py")):
        raise Skip(NO_HANDOVER, f"{to} は申し送りを読む仕組みを持たない")
    return to


def write(relay_dir: str, data: dict) -> str:
    """申し送りを作業ディレクトリへ 0600 で書き、パスを返す。書けなければ Skip（handover-write）。"""
    path = os.path.join(relay_dir, HANDOVER_FILE)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"schema": SCHEMA, **data}, f, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError) as e:
        discard(tmp)
        raise Skip(HANDOVER_WRITE, f"申し送りを書けない（{type(e).__name__}）") from None
    return path


def discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def read(path: str) -> tuple[dict | None, str | None]:
    """申し送りを読んで消す。(中身か None, 利用者が手で打つ次のコマンドか None)。

    無い・JSON でない・知らない `schema`・必須のキーが無いときは中身を None にする。`shown` は読めれば返す。"""
    text = read_text(path)
    discard(path)
    try:
        data = json.loads(text) if text is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, None
    shown = data.get("shown") if isinstance(data.get("shown"), str) and data.get("shown") else None
    schema = data.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or not 1 <= schema <= SCHEMA:
        return None, shown
    state, nxt = data.get("state"), data.get("next")
    if (
        any(k not in data for k in REQUIRED)
        or not isinstance(state, dict)
        or not isinstance(nxt, dict)
        or not isinstance(state.get("section"), int)
        or any(k not in state for k in STATE_KEYS)
        or any(k not in nxt for k in NEXT_KEYS)
        or not isinstance(data.get("first_args"), list)
    ):
        return None, shown
    return data, shown


def exec_argv(base: str, to: str, first_args: list[str]) -> tuple[str, list[str]]:
    """入れ替え先の python と、ランチャーを `run 最初の引数` で起動する argv。"""
    python = runtime.python_of(runtime.version_env(os.path.join(base, to)))
    return python, [python, os.path.join(base, LAUNCHER), "run", *first_args]


def exec_env(path: str) -> dict:
    """ラッパーの環境に申し送りのパスだけを足したもの（中身は環境変数に入れない）。"""
    env = dict(os.environ)
    env.pop(runtime.REEXEC_ENV, None)
    env[HANDOVER_ENV] = path
    return env


def elapsed(since: float) -> float:
    return round(time.time() - since, 3)
