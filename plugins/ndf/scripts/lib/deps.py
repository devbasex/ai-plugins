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
   起動されたとき）。外すのは、子のプロセスが別のグループを要るときに起動し直せるようにするため。
   `UV_PROJECT_ENVIRONMENT` も `release_project_env` で起動し直す前の利用者の値へ戻し、共有の環境を指す値は外す
   （子の `uv run --project <別のプロジェクト>` が共有の環境を作り直さないため。#2509）
2. 環境変数 `NDF_DEPS_REEXEC` が自分のスクリプトのパス → 起動し直したのに import できない。理由を出して終了コード 69。
   印の値は起動し直したスクリプトのパスで、別のパス（印を外す前の版の親から継いだ `1` など）なら 3 へ進む
3. uv が見つかる（`PATH`・`~/.local/bin`・`~/.cargo/bin`）→ `uv run --frozen --project <プラグインの根> --extra <グループ> ...
   python <パス> <引数>` で自分を起動し直す（`os.execve`）。環境は `UV_PROJECT_ENVIRONMENT` で
   `~/.cache/ndf/venv/<版>` に置く（`NDF_DEPS_VENV` で変えられる）。プラグインのキャッシュの中には作らない
4. uv が無い → 版を固定した公式のインストーラで `~/.local/bin` へ入れ（`UV_NO_MODIFY_PATH=1`）、標準エラーに 1 行を
   出してから 3 へ進む。curl が無ければ `python3 -m pip install --user uv==<版>` を使う
5. 入れられない（ネットワークが無い・権限が無い）→ 何が無いかと、手で入れるコマンドを出して終了コード 69

依存の欠け（2・5 と、`pyproject.toml` と `uv.lock` が無いとき）の終了コードは `EXIT_DEPS_MISSING`（69。`sysexits.h` の
`EX_UNAVAILABLE`）で、値の持ち主はこのファイルだけである（#1654）。止まるときは実行の核の早い出口（`run_core.exit_early`）を
通し、例外の跡を出さずに `stopped` と足りないグループ（`gaps` の `kind` が `deps`）の結果を返す（#2337）。共通の契約の 3（前提が無い・飛ばしてよい）とは
分け、supervise の `skip_code` に読ませない。`uv run` 自身が環境を作れないときは `os.execve` の後なので uv の終了コード
（1）で終わる。

hook とラッパーは `require()` を呼ばない（I13・決定 20）。ラッパー（`relay_lib/runtime.py`）は `ensure_uv`・`venv_dir` だけを使う。
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
    "durable": ["dbos", "filelock"],
}
EXIT_DEPS_MISSING = 69  # 依存の欠け（sysexits.h の EX_UNAVAILABLE）。共通の契約の 0〜3・10〜29 と重ねない
REEXEC_ENV = "NDF_DEPS_REEXEC"
PROJECT_ENV = "UV_PROJECT_ENVIRONMENT"
# 起動し直す前の利用者の UV_PROJECT_ENVIRONMENT。無かったときは置かない（#2509 の決定 2）
SAVED_ENV = "NDF_DEPS_SAVED_UV_PROJECT_ENVIRONMENT"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
INSTALL_HINT = f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | sh"


def _stop(msg: str, groups: Sequence[str] = ()) -> NoReturn:
    """依存の欠けで止める。結果は実行の核の早い出口（`run_core.exit_early`）が出す（#2337 の決定 11）。"""
    print(f"❌ [ndf deps] {msg}", file=sys.stderr)
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_core  # 循環を避けて止まるときだけ読む（run_core → step_result → deps）

    gaps = [{"kind": "deps", "name": g, "reason": msg} for g in groups] or [{"kind": "deps", "name": "uv", "reason": msg}]
    run_core.exit_early(msg, EXIT_DEPS_MISSING, gaps)


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


def ensure_uv(label: str) -> str | None:
    """`find_uv` で探し、無ければ `label` 付きの 1 行を標準エラーへ出して `install_uv` で入れる。入れられなければ None。"""
    uv = find_uv()
    if not uv:
        print(f"{label} uv が無いため {UV_VERSION} を ~/.local/bin へ入れる", file=sys.stderr)
        uv = install_uv()
    return uv


def venv_version(root: Path = PLUGIN_ROOT) -> str:
    """環境の置き場所の名前に使う版（`.claude-plugin/plugin.json` の version。読めなければ `dev`）。"""
    try:
        v = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return "dev"
    return v if isinstance(v, str) and v else "dev"


def venv_dir(root: Path = PLUGIN_ROOT) -> str:
    return os.environ.get("NDF_DEPS_VENV") or str(Path.home() / ".cache" / "ndf" / "venv" / venv_version(root))


def _real(path: str | Path) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def is_shared_venv(path: str | None) -> bool:
    """`path` が共有の環境の置き場か（`NDF_DEPS_VENV` と同じか、親が `~/.cache/ndf/venv`）。版の名前には依らない（#2509 の決定 3）。"""
    if not path:
        return False
    p, forced = _real(path), os.environ.get("NDF_DEPS_VENV")
    return (bool(forced) and p == _real(forced)) or os.path.dirname(p) == _real(Path.home() / ".cache" / "ndf" / "venv")


def release_project_env(script: str | None = None) -> None:
    """NDF が起動し直すために設定した `UV_PROJECT_ENVIRONMENT` を子プロセスへ継がせない（#2509 の I5〜I7）。

    自分を起動し直した回（`NDF_DEPS_REEXEC` が `script`）なら退避した利用者の値を戻し、無ければ外す。続けて、値が共有の
    環境を指していれば外す（古い版の親から継いだ値も含む）。退避の変数も外す。"""
    saved = os.environ.pop(SAVED_ENV, None)
    if script is not None and os.environ.get(REEXEC_ENV) == script:
        if saved is None:
            os.environ.pop(PROJECT_ENV, None)
        else:
            os.environ[PROJECT_ENV] = saved
    if is_shared_venv(os.environ.get(PROJECT_ENV)):
        os.environ.pop(PROJECT_ENV, None)


def reexec_argv(uv: str, groups: str | Sequence[str], script: str, args: list[str], root: Path = PLUGIN_ROOT) -> list[str]:
    extras = [x for g in ([groups] if isinstance(groups, str) else groups) for x in ("--extra", g)]
    return [uv, "run", "--quiet", "--frozen", "--project", str(root), *extras, "python", script, *args]


def _normalize_groups(group: str, more: tuple[str, ...]) -> list[str]:
    """重複を除いたグループの並び。外部パッケージのグループに無いものがあれば ValueError。"""
    groups = list(dict.fromkeys((group, *more)))
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise ValueError(f"外部パッケージのグループに無い: {', '.join(unknown)}（{' / '.join(GROUPS)}）")
    return groups


def _resolve_uv_or_stop(groups: Sequence[str] = ()) -> str:
    """uv を探し、無ければ入れる。入れられなければ止める。"""
    uv = ensure_uv("[ndf deps]")
    if not uv:
        _stop(f"uv を入れられない（ネットワークか権限が無い）。手で入れてから打ち直す: {INSTALL_HINT}", groups)
    return uv


def require(group: str, *more: str, project: Path | None = None) -> None:
    """渡したグループのパッケージが import できる環境で動いていることを保証する。できなければ、足りないグループを
    すべて `--extra` に並べた uv の環境で 1 回だけ起動し直す。

    `project` は宣言と lock を持つ根（既定はプラグインの根）。リポジトリの根の `scripts/` は根を渡し、環境は
    `<根>/.venv`（全体テストと同じ環境）に置く。"""
    root = PLUGIN_ROOT if project is None else Path(project).resolve()
    groups = _normalize_groups(group, more)
    missing = [g for g in groups if not importable(g)]
    script = str(Path(sys.argv[0]).resolve())
    if not missing:
        release_project_env(script)  # 印を外す前に呼ぶ（起動し直した回かを印で決める）
        # 起動し直した印は子のプロセス（別のグループを要るエントリポイント）へ継がせない。継ぐと子は起動し直さずに止まる
        os.environ.pop(REEXEC_ENV, None)
        return
    if os.environ.get(REEXEC_ENV) == script:
        mods = ", ".join(m for g in missing for m in GROUPS[g])
        _stop(
            f"uv の環境へ起動し直したが {' / '.join(missing)} のパッケージ（{mods}）を import できない。"
            f"{root / 'uv.lock'} に載っているかを見る",
            missing,
        )
    if not (root / "pyproject.toml").is_file() or not (root / "uv.lock").is_file():
        _stop(f"外部パッケージの宣言が無い: {root}/pyproject.toml と uv.lock", missing)
    uv = _resolve_uv_or_stop(missing)
    venv = venv_dir() if root == PLUGIN_ROOT else str(root / ".venv")
    env = {k: v for k, v in os.environ.items() if k != SAVED_ENV}
    if PROJECT_ENV in os.environ:  # 利用者の値は起動し直した後に戻す（release_project_env）
        env[SAVED_ENV] = os.environ[PROJECT_ENV]
    env.update({REEXEC_ENV: script, PROJECT_ENV: venv})
    os.execve(uv, reexec_argv(uv, groups, script, sys.argv[1:], root), env)
