"""hook の用意済みの環境の python の置き場（#1142 の決定 20）。標準ライブラリだけで書き、`deps.require()` は呼ばない。

SessionStart（`worktree-session.sh` が起動する `hook-env.py`）が `~/.cache/ndf/venv/<版>` を `uv sync --frozen` で用意し、
プラグインの根ごとの目印 `~/.cache/ndf/roots<プラグインの根>` をその環境のディレクトリへ張る（symlink）。hook の command は
シェルの字面だけで python のパスを組み立てて起動する（版を読まない。`hooks/claude.json` の形）:

    sh -c '[ -x "$0" ] || exit 0; exec "$0" "$@"' "$HOME/.cache/ndf/roots${CLAUDE_PLUGIN_ROOT}/bin/python" <hook.py> ...

目印を python の実行ファイルへ張らないのは、python が実行ファイルの symlink をたどった先で環境を探し、環境の外の
python として動くためである（環境の `pyvenv.cfg` は `bin/` の 1 つ上で探す）。

目印が無い（SessionStart の前・uv を入れられない）ときは `[ -x ]` が偽になり、hook は判定をせずに通る。
`NDF_HOOK_PYTHON` があれば、古いエントリポイント（`worktree-guard.sh` ほか）はその python を使う（テストが使う）。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
# hook の経路が使う外部パッケージのグループ（pyproject.toml の extra と deps.GROUPS の名前）
GROUPS = ("shparse", "locks", "notify")
MODULES = ("tree_sitter", "tree_sitter_bash", "filelock", "dotenv", "slack_sdk", "httpx")
OVERRIDE_ENV = "NDF_HOOK_PYTHON"
REEXEC_ENV = "NDF_HOOK_REEXEC"


def roots_dir() -> Path:
    return Path.home() / ".cache" / "ndf" / "roots"


def pointer(root: os.PathLike[str] | str) -> Path:
    """プラグインの根 `root`（絶対パス）の目印（環境のディレクトリへの symlink）。"""
    return roots_dir() / str(root).lstrip("/")


def pointer_python(root: os.PathLike[str] | str) -> Path:
    """hook の command の `$HOME/.cache/ndf/roots${ROOT}/bin/python` と同じパス。"""
    return pointer(root) / "bin" / "python"


def hook_packages_importable() -> bool:
    try:
        return all(importlib.util.find_spec(m) is not None for m in MODULES)
    except (ImportError, ValueError):
        return False


def hook_python(root: os.PathLike[str] | str = PLUGIN_ROOT) -> str | None:
    """hook を起動する python（`NDF_HOOK_PYTHON` か目印）。無ければ None。"""
    for cand in (os.environ.get(OVERRIDE_ENV), str(pointer_python(os.path.realpath(root)))):
        if cand and os.access(cand, os.X_OK):
            return cand
    return None


def exec_hook_python(script: str, args: list[str]) -> None:
    """hook の環境の python で `script` を起動し直す（戻らない）。環境が無い・起動し直した後なら何もせずに戻る。"""
    py = hook_python()
    if py is None or os.environ.get(REEXEC_ENV):
        return
    os.execve(py, [py, script, *args], dict(os.environ, **{REEXEC_ENV: "1"}))
