"""外部パッケージを使うエントリポイントが最初に呼ぶ `require()`（#1142 の決定 17・不足 h）。標準ライブラリだけで書く。

宣言と版の固定は `plugins/ndf/pyproject.toml` と `plugins/ndf/uv.lock` の 1 組が持つ。パッケージは用途ごとの
グループ（extra）に分け、エントリポイントは使うグループを名前で渡す。

    sys.path.insert(0, str(<lib>))
    import deps
    deps.require("github")      # 外部パッケージの import より前に呼ぶ
    import githubkit

`require()` は次の順に動く（決定 17 の表）。

1. グループのパッケージが import できる → そのまま戻る（uv の環境の中で起動されたとき）
2. 環境変数 `NDF_DEPS_REEXEC` がある → 起動し直したのに import できない。理由を出して終了コード 3
3. uv が見つかる（`PATH`・`~/.local/bin`・`~/.cargo/bin`）→ `uv run --frozen --project <プラグインの根> --extra <グループ>
   python <パス> <引数>` で自分を起動し直す（`os.execve`）。環境は `UV_PROJECT_ENVIRONMENT` で
   `~/.cache/ndf/venv/<版>` に置く（`NDF_DEPS_VENV` で変えられる）。プラグインのキャッシュの中には作らない
4. uv が無い → 版を固定した公式のインストーラで `~/.local/bin` へ入れ（`UV_NO_MODIFY_PATH=1`）、標準エラーに 1 行を
   出してから 3 へ進む。curl が無ければ `python3 -m pip install --user uv==<版>` を使う
5. 入れられない（ネットワークが無い・権限が無い）→ 何が無いかと、手で入れるコマンドを出して終了コード 3

hook とラッパーのバージョンディレクトリはこのモジュールを使わない（I13）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

UV_VERSION = "0.12.19"
# pyproject.toml の [project.optional-dependencies] と同じグループ。値はグループが入ったかを見る import の名前
GROUPS = {"github": ["githubkit"]}
EXIT_PRECONDITION = 3
REEXEC_ENV = "NDF_DEPS_REEXEC"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
INSTALL_HINT = f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | sh"


def _stop(msg: str) -> NoReturn:
    print(f"❌ [ndf deps] {msg}", file=sys.stderr)
    sys.exit(EXIT_PRECONDITION)


def importable(group: str) -> bool:
    """グループのパッケージがすべて import できるか。"""
    return all(importlib.util.find_spec(m) is not None for m in GROUPS[group])


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def install_uv() -> str | None:
    """版を固定した公式のインストーラで `~/.local/bin` へ入れる。curl が無ければ pip を使う。入れた uv のパスを返す。"""
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


def venv_version(root: Path = PLUGIN_ROOT) -> str:
    """環境の置き場所の名前に使う版（`.claude-plugin/plugin.json` の version。読めなければ `dev`）。"""
    try:
        v = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return "dev"
    return v if isinstance(v, str) and v else "dev"


def venv_dir(root: Path = PLUGIN_ROOT) -> str:
    return os.environ.get("NDF_DEPS_VENV") or str(Path.home() / ".cache" / "ndf" / "venv" / venv_version(root))


def reexec_argv(uv: str, group: str, script: str, args: list[str], root: Path = PLUGIN_ROOT) -> list[str]:
    return [uv, "run", "--quiet", "--frozen", "--project", str(root), "--extra", group,
            "python", script, *args]


def require(group: str) -> None:
    """`group` のパッケージが import できる環境で動いていることを保証する。できなければ uv の環境で起動し直す。"""
    if group not in GROUPS:
        raise ValueError(f"外部パッケージのグループに無い: {group}（{' / '.join(GROUPS)}）")
    if importable(group):
        return
    if os.environ.get(REEXEC_ENV):
        _stop(f"uv の環境へ起動し直したが {group} のパッケージ（{', '.join(GROUPS[group])}）を import できない。"
              f"{PLUGIN_ROOT / 'uv.lock'} に載っているかを見る")
    if not (PLUGIN_ROOT / "pyproject.toml").is_file() or not (PLUGIN_ROOT / "uv.lock").is_file():
        _stop(f"外部パッケージの宣言が無い: {PLUGIN_ROOT}/pyproject.toml と uv.lock")
    uv = find_uv()
    if not uv:
        print(f"[ndf deps] uv が無いため {UV_VERSION} を ~/.local/bin へ入れる", file=sys.stderr)
        uv = install_uv()
    if not uv:
        _stop(f"uv を入れられない（ネットワークか権限が無い）。手で入れてから打ち直す: {INSTALL_HINT}")
    env = dict(os.environ, **{REEXEC_ENV: "1", "UV_PROJECT_ENVIRONMENT": venv_dir()})
    script = str(Path(sys.argv[0]).resolve())
    os.execve(uv, reexec_argv(uv, group, script, sys.argv[1:]), env)
