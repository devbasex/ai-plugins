"""外部パッケージを使うエントリポイントが最初に呼ぶ `require()`（#1142 の決定 17・不足 h）。標準ライブラリだけで書く。

宣言と版の固定は、プラグインの根（`PLUGIN_ROOT`。このファイルの 2 つ上）の `pyproject.toml` と `uv.lock` の 1 組が持つ。パッケージは用途ごとの
グループ（extra）に分け、エントリポイントは使うグループを名前で渡す。

    sys.path.insert(0, str(<lib>))
    import deps
    deps.require("github")      # 外部パッケージの import より前に呼ぶ
    import githubkit

2 つ以上のグループが要るエントリポイントは 1 回の呼び出しで並べる（`deps.require("notify", "locks")`。決定 23）。
起動し直すのは 1 回で、`uv run` へ `--extra` を並べる。分けて呼ぶと、2 つ目は起動し直した後なので止まる。

`require()` は次の順に動く（決定 17 の表）。

1. 渡したグループのパッケージがすべて import できる → `NDF_DEPS_REEXEC` を環境から外して戻る（uv の環境の中で
   起動されたとき）。外すのは、子のプロセスが別のグループを要るときに起動し直せるようにするため
2. 環境変数 `NDF_DEPS_REEXEC` が自分のスクリプトのパス → 起動し直したのに import できない。理由を出して終了コード 3。
   印の値は起動し直したスクリプトのパスで、別のパス（印を外す前の版の親から継いだ `1` など）なら 3 へ進む
3. uv が見つかる（`PATH`・`~/.local/bin`・`~/.cargo/bin`）→ `uv run --frozen --project <プラグインの根> --extra <グループ> ...
   python <パス> <引数>` で自分を起動し直す（`os.execve`）。環境は `UV_PROJECT_ENVIRONMENT` で
   `~/.cache/ndf/venv/<版>` に置く（`NDF_DEPS_VENV` で変えられる）。プラグインのキャッシュの中には作らない
4. uv が無い → 版を固定した公式のインストーラで `~/.local/bin` へ入れ（`UV_NO_MODIFY_PATH=1`）、標準エラーに 1 行を
   出してから 3 へ進む。curl が無ければ `python3 -m pip install --user uv==<版>` を使う
5. 入れられない（ネットワークが無い・権限が無い）→ 何が無いかと、手で入れるコマンドを出して終了コード 3

hook とラッパーは `require()` を呼ばない（I13・決定 20）。ラッパー（`relay_lib/runtime.py`）は `find_uv`・`install_uv`・`venv_dir` だけを使う。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn, Sequence

UV_VERSION = "0.12.19"
# pyproject.toml の [project.optional-dependencies] と同じグループ。値はグループが入ったかを見る import の名前
GROUPS = {
    "github": ["githubkit"],
    "md": ["markdown_it", "mdit_py_plugins"],
    "mdtable": ["tabulate"],
    "schema": ["pydantic"],
    "procs": ["psutil"],
    "locks": ["filelock"],
    "shparse": ["tree_sitter", "tree_sitter_bash"],
    "versions": ["semver"],
    "bump": ["bumpversion"],
    "pathmatch": ["pathspec"],
    "textparse": ["unidiff", "pygments"],
    "yamlio": ["ruamel.yaml"],
    "waits": ["tenacity"],
    "notify": ["slack_sdk", "dotenv", "httpx"],
    "terminal": ["ptyprocess"],
}
EXIT_PRECONDITION = 3
REEXEC_ENV = "NDF_DEPS_REEXEC"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
INSTALL_HINT = f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | sh"


def _stop(msg: str) -> NoReturn:
    print(f"❌ [ndf deps] {msg}", file=sys.stderr)
    sys.exit(EXIT_PRECONDITION)


def importable(group: str) -> bool:
    """グループのパッケージがすべて import できるか（`ruamel.yaml` のような点の入った名前は親が無ければ偽）。"""
    try:
        return all(importlib.util.find_spec(m) is not None for m in GROUPS[group])
    except ModuleNotFoundError:
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


def reexec_argv(uv: str, groups: str | Sequence[str], script: str, args: list[str],
                root: Path = PLUGIN_ROOT) -> list[str]:
    extras = [x for g in ([groups] if isinstance(groups, str) else groups) for x in ("--extra", g)]
    return [uv, "run", "--quiet", "--frozen", "--project", str(root), *extras, "python", script, *args]


def require(group: str, *more: str, project: Path | None = None) -> None:
    """渡したグループのパッケージが import できる環境で動いていることを保証する。できなければ、足りないグループを
    すべて `--extra` に並べた uv の環境で 1 回だけ起動し直す。

    `project` は宣言と lock を持つ根（既定はプラグインの根）。リポジトリの根の `scripts/` は根を渡し、環境は
    `<根>/.venv`（全体テストと同じ環境）に置く。"""
    root = PLUGIN_ROOT if project is None else Path(project).resolve()
    groups = list(dict.fromkeys((group, *more)))
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise ValueError(f"外部パッケージのグループに無い: {', '.join(unknown)}（{' / '.join(GROUPS)}）")
    missing = [g for g in groups if not importable(g)]
    if not missing:
        # 起動し直した印は子のプロセス（別のグループを要るエントリポイント）へ継がせない。継ぐと子は起動し直さずに止まる
        os.environ.pop(REEXEC_ENV, None)
        return
    script = str(Path(sys.argv[0]).resolve())
    if os.environ.get(REEXEC_ENV) == script:
        mods = ", ".join(m for g in missing for m in GROUPS[g])
        _stop(f"uv の環境へ起動し直したが {' / '.join(missing)} のパッケージ（{mods}）を import できない。"
              f"{root / 'uv.lock'} に載っているかを見る")
    if not (root / "pyproject.toml").is_file() or not (root / "uv.lock").is_file():
        _stop(f"外部パッケージの宣言が無い: {root}/pyproject.toml と uv.lock")
    uv = find_uv()
    if not uv:
        print(f"[ndf deps] uv が無いため {UV_VERSION} を ~/.local/bin へ入れる", file=sys.stderr)
        uv = install_uv()
    if not uv:
        _stop(f"uv を入れられない（ネットワークか権限が無い）。手で入れてから打ち直す: {INSTALL_HINT}")
    venv = venv_dir() if root == PLUGIN_ROOT else str(root / ".venv")
    env = dict(os.environ, **{REEXEC_ENV: script, "UV_PROJECT_ENVIRONMENT": venv})
    os.execve(uv, reexec_argv(uv, groups, script, sys.argv[1:], root), env)
