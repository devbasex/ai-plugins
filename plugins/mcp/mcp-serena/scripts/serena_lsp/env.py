"""mcp-serena の外部パッケージの環境（#1142 の決定 25）。標準ライブラリだけで書く。

宣言と版の固定はプラグインの根の `pyproject.toml` と `uv.lock` が持つ。環境は `~/.cache/mcp-serena/venv/<版>` に置き
（`MCP_SERENA_VENV` で変えられる）、プラグインの根ごとの目印 `~/.cache/mcp-serena/roots<根>` をその環境へ張る（symlink）。
PreToolUse の hook の command は目印の `bin/python` を `sh -c '[ -x "$0" ] || exit 0; exec "$0" "$@"'` で直に起動し、
目印が無ければ判定をせずに通る。NDF の `lib/deps.py` は import しない（mcp-serena だけを入れた手元に NDF は無い）。

- `prepare(wait)`: SessionStart の hook が呼ぶ。環境が lock と同じなら uv を起動せず目印だけを張り直す。用意は切り離した
  子で行い `wait` 秒まで待つ。用意できた環境の python を返し、できなければ None（通知を飛ばす）。`MCP_SERENA_ENV=0` なら
  何もしない（テストが使う）
- `ensure(script, args)`: hook ではないエントリポイント（`detect`・`configure`・`check`）が呼ぶ。ruamel.yaml を import
  できればそのまま戻り、できなければ環境を用意して自分を起動し直す。uv が無ければ版を固定して入れ、入れられなければ
  理由を出して終了コード 3 で終わる（NDF の `deps.require()` と同じ契約）
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
INVOKED_ROOT = Path(os.path.abspath(__file__)).parents[2]  # symlink をたどらない根（Kiro の installer が張るリンク）
UV_VERSION = "0.12.19"
INSTALL_HINT = f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | sh"
STAMP = ".mcp-serena-env"
REEXEC_ENV = "MCP_SERENA_REEXEC"
SWITCH_ENV = "MCP_SERENA_ENV"
EXIT_PRECONDITION = 3


def _say(msg: str) -> None:
    print(f"[mcp-serena env] {msg}", file=sys.stderr)


def importable() -> bool:
    try:
        return importlib.util.find_spec("ruamel.yaml") is not None
    except ModuleNotFoundError:
        return False


def version() -> str:
    try:
        v = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return "dev"
    return v if isinstance(v, str) and v else "dev"


def venv_dir() -> Path:
    return Path(os.environ.get("MCP_SERENA_VENV") or Path.home() / ".cache" / "mcp-serena" / "venv" / version())


def pointer(root: os.PathLike[str] | str) -> Path:
    """根 `root` の目印。hook の command の `$HOME/.cache/mcp-serena/roots${ROOT}` と同じパス。"""
    return Path.home() / ".cache" / "mcp-serena" / "roots" / str(root).lstrip("/")


def roots() -> list[str]:
    real = os.path.realpath(PLUGIN_ROOT)
    found = [str(PLUGIN_ROOT), str(INVOKED_ROOT)]
    for name in ("PLUGIN_ROOT", "CODEX_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        v = os.environ.get(name)
        if v and os.path.isabs(v) and os.path.realpath(v) == real:
            found.append(v.rstrip("/") or "/")
    return list(dict.fromkeys(found))


def stamp() -> str:
    return hashlib.sha256((PLUGIN_ROOT / "uv.lock").read_bytes()).hexdigest()


def ready(venv: Path) -> bool:
    try:
        return (venv / "bin" / "python").exists() and (venv / STAMP).read_text(encoding="utf-8") == stamp()
    except OSError:
        return False


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def install_uv() -> str | None:
    """版を固定した公式のインストーラで `~/.local/bin` へ入れる。curl が無ければ pip を使う。"""
    env = dict(os.environ, UV_NO_MODIFY_PATH="1")
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "env": env, "check": False}
    try:
        if shutil.which("curl"):
            subprocess.run(["sh", "-c", INSTALL_HINT], **quiet)
        else:
            subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", f"uv=={UV_VERSION}"], **quiet)
    except OSError:
        return None
    return find_uv()


def link_pointers(venv: Path) -> None:
    for root in roots():
        p = pointer(root)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.is_symlink() and os.readlink(p) == str(venv):
                continue
            tmp = p.with_name(f".{p.name}.{os.getpid()}")
            if tmp.is_symlink():
                tmp.unlink()
            tmp.symlink_to(venv, target_is_directory=True)
            os.replace(tmp, p)
        except OSError as exc:
            _say(f"目印を張れない（{p}: {exc}）")


def sync() -> int:
    """環境を lock のとおりに用意し、目印を張る。0 が用意できた、3 が用意できない。"""
    venv = venv_dir()
    if not ready(venv):
        uv = find_uv()
        if not uv:
            _say(f"uv が無いため {UV_VERSION} を ~/.local/bin へ入れる")
            uv = install_uv()
        if not uv:
            _say(f"uv を入れられない（ネットワークか権限が無い）。手で入れてから打ち直す: {INSTALL_HINT}")
            return EXIT_PRECONDITION
        p = subprocess.run([uv, "sync", "--quiet", "--frozen", "--compile-bytecode", "--project", str(PLUGIN_ROOT)],
                           env=dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv)), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        if p.returncode != 0:
            _say(f"uv sync が失敗した（終了コード {p.returncode}）: {(p.stderr or '').strip()[-300:]}")
            return EXIT_PRECONDITION
        (venv / STAMP).write_text(stamp(), encoding="utf-8")
    link_pointers(venv)
    return 0


def prepare(wait: float = 8.0) -> str | None:
    """SessionStart の用意。用意できた環境の python（できなければ None）。"""
    if os.environ.get(SWITCH_ENV) == "0":
        return None
    venv = venv_dir()
    python = str(venv / "bin" / "python")
    try:
        if ready(venv):
            link_pointers(venv)
            return python
        code = f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r}); " \
               "from serena_lsp import env; sys.exit(env.sync())"
        child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + wait
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
    except OSError:
        return None
    return python if child.returncode == 0 and ready(venv) else None


def reexec(python: str, script: str, args: list[str]) -> None:
    """環境の python で `script` を起動し直す（戻らない）。"""
    os.execve(python, [python, script, *args], dict(os.environ, **{REEXEC_ENV: "1"}))


def ensure(script: str, args: list[str]) -> None:
    """ruamel.yaml を import できる環境で動いていることを保証する。できなければ環境を用意して起動し直す。"""
    if importable():
        return
    if os.environ.get(REEXEC_ENV):
        _say(f"環境へ起動し直したが ruamel.yaml を import できない。{PLUGIN_ROOT / 'uv.lock'} に載っているかを見る")
        sys.exit(EXIT_PRECONDITION)
    if sync() != 0:
        sys.exit(EXIT_PRECONDITION)
    reexec(str(venv_dir() / "bin" / "python"), script, args)
