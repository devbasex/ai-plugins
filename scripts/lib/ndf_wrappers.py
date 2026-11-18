"""根の `scripts/` が外部パッケージを使うための 1 か所（#1142 の決定 19）。

依存はリポジトリの根の `pyproject.toml`（`[project.optional-dependencies]`）と `uv.lock` で解決する（決定 19・22）。
エントリポイントは外部パッケージの import より前に呼ぶ。

    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    from ndf_wrappers import require
    require("md", "versions")   # 2 つ以上のグループは 1 回で渡す（決定 23）
    import md, versions

`python3 scripts/<名前>.py` で起動したときは、根の uv の環境（`<根>/.venv`）へ 1 回だけ起動し直す。
全体テスト（`uv run --frozen --project . --all-extras pytest`）の中では、そのまま戻る。

10 系はこの仕事をプラグインの `scripts/lib/deps.py` に任せていた。11.0.0 の入れ替え（#3304）でその置き場が
無くなったため、根の scripts が要る分（グループの表・起動し直し）だけをここへ移した。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 根の pyproject.toml の [project.optional-dependencies] と同じグループ。値はグループが入ったかを見る import の名前
GROUPS = {
    "github": ["githubkit"],
    "md": ["markdown_it", "mdit_py_plugins"],
    "mdtable": ["tabulate"],
    "schema": ["pydantic"],
    "jsonschema": ["jsonschema"],
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
    "durable": ["dbos", "filelock"],
}
# 起動し直した回の印（値は起動し直したスクリプトのパス）
REEXEC_ENV = "NDF_ROOT_DEPS_REEXEC"
# 依存が欠けて止まるときの終了コード（10 系の deps の EXIT_DEPS_MISSING と同じ値）
EXIT_DEPS_MISSING = 69


def _stop(msg: str) -> None:
    print(f"❌ [root deps] {msg}", file=sys.stderr)
    sys.exit(EXIT_DEPS_MISSING)


def importable(group: str) -> bool:
    """グループのパッケージがすべて import できるか（`ruamel.yaml` のような点の入った名前は親が無ければ偽）。"""
    try:
        return all(importlib.util.find_spec(m) is not None for m in GROUPS[group])
    except ModuleNotFoundError:
        return False


def require(group: str, *more: str) -> None:
    """渡したグループのパッケージが import できる環境で動いていることを保証する。できなければ、足りないグループを
    すべて `--extra` に並べた根の uv の環境で 1 回だけ起動し直す。"""
    groups = list(dict.fromkeys((group, *more)))
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise ValueError(f"外部パッケージのグループに無い: {', '.join(unknown)}（{' / '.join(GROUPS)}）")
    missing = [g for g in groups if not importable(g)]
    script = str(Path(sys.argv[0]).resolve())
    if not missing:
        # 起動し直した印は子のプロセス（別のグループを要るエントリポイント）へ継がせない
        os.environ.pop(REEXEC_ENV, None)
        return
    if os.environ.get(REEXEC_ENV) == script:
        _stop(f"uv の環境へ起動し直したが {' / '.join(missing)} を import できない。{REPO / 'uv.lock'} に載っているかを見る")
    uv = shutil.which("uv")
    if not uv:
        _stop("uv が無い。https://docs.astral.sh/uv/ の手順で入れてから打ち直す")
    extras = [x for g in groups for x in ("--extra", g)]
    env = dict(os.environ, **{REEXEC_ENV: script, "UV_PROJECT_ENVIRONMENT": str(REPO / ".venv")})
    argv = [uv, "run", "--quiet", "--frozen", "--project", str(REPO), *extras, "python", script, *sys.argv[1:]]
    os.execve(uv, argv, env)
