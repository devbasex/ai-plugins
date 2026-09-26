"""ラッパーを動かす python の選択と、その環境の用意（#1142 の決定 20）。標準ライブラリと `lib/deps.py` だけを使う。

ラッパーの中身は外部パッケージ（psutil・filelock・ptyprocess・markdown-it-py・semver）の包みの上にある。
ランチャーは素の `python3` から起動されるため、`main` は副命令を動かす前に `enter()` を呼び、次の順に決める。

1. 今の python で `GROUPS` のパッケージがすべて import できる → そのまま続ける
2. 環境の python（下の表）が在り、まだ起動し直していない → `REEXEC_ENV` を付けてその python で自分を起動し直す
3. 環境が無い（起動し直した先でも import できない）:
   - 環境を用意する副命令（`PREPARE`。導入と SessionStart の `startup`）→ 同じ lock から環境を作って 2 へ進む。
     作れないときは理由を出して終了コード 3
   - hook の副命令（`PASS`）→ 判定をせずに決まった終了コードで終わる（パススルー）
   - `run` → 理由を出して本物の claude をそのまま起動する（素通し）

| ラッパーの中身の置き場 | 環境の python |
| --- | --- |
| バージョンディレクトリ（複製。`MANIFEST` を持つ） | `<バージョンディレクトリ>/.venv/bin/python`（複製のときに `uv sync --frozen` で作る） |
| プラグインのキャッシュ | `lib/deps.py` の `venv_dir()`（`~/.cache/ndf/venv/<版>`）の `bin/python` |

`deps.require()` は呼ばない（`uv run` を挟まない。I13）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(PKG_ROOT, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import deps  # noqa: E402

# ラッパーが使うグループ（pyproject.toml の extra）。import できるかは deps.GROUPS の名前で見る
GROUPS = ("procs", "locks", "md", "versions", "terminal")
REEXEC_ENV = "NDF_RELAY_REEXEC"
UV_ENV = "NDF_RELAY_UV"
PREPARE = {"install", "uninstall", "status", "startup", "stop"}
# hook の副命令が環境の無いときに返す終了コード（is-child の 1 は「ラッパーの直接の子でない」）
PASS = {"mark": 0, "question": 0, "is-child": 1, "notice": 0}
MANIFEST = "MANIFEST"


class EnvUnavailable(OSError):
    """ラッパーの環境を用意できない（uv を入れられない・`uv sync` が失敗した）。文は理由と対処。"""


def ready() -> bool:
    """今の python で、ラッパーの使うパッケージがすべて import できるか。"""
    try:
        return all(importlib.util.find_spec(m) is not None for g in GROUPS for m in deps.GROUPS[g])
    except (ImportError, ValueError):
        return False


def python_of(venv: str) -> str:
    return os.path.join(venv, "bin", "python")


def is_version_dir(root: str = PKG_ROOT) -> bool:
    return os.path.isfile(os.path.join(root, MANIFEST))


def version_env(root: str) -> str:
    """バージョンディレクトリ `root` の中の環境。"""
    return os.path.join(root, ".venv")


def env_dir(root: str = PKG_ROOT) -> str:
    """ラッパーの中身の置き場 `root` が使う環境の置き場所（上の表）。"""
    return version_env(root) if is_version_dir(root) else deps.venv_dir(deps.PLUGIN_ROOT)


def find_uv() -> str:
    """`NDF_RELAY_UV`・PATH と既定の置き場の uv。無ければ版を固定して入れる。入れられなければ EnvUnavailable。"""
    forced = os.environ.get(UV_ENV)
    if forced:
        return forced
    uv = deps.find_uv()
    if not uv:
        print(f"[ndf relay] uv が無いため {deps.UV_VERSION} を ~/.local/bin へ入れる", file=sys.stderr)
        uv = deps.install_uv()
    if not uv:
        raise EnvUnavailable(f"uv を入れられない（ネットワークか権限が無い）。手で入れてから打ち直す: {deps.INSTALL_HINT}")
    return uv


def sync(project: str, venv: str, inexact: bool = False) -> str:
    """`project` の pyproject.toml と uv.lock から、`GROUPS` の入った環境を `venv` に作る。環境の python を返す。

    `inexact` は、ほかのグループも入る共有の環境（プラグインのキャッシュの環境）で、入っているものを消さない。"""
    extras = [x for g in GROUPS for x in ("--extra", g)]
    cmd = [find_uv(), "sync", "--frozen", "--quiet", "--project", project, *extras, *(["--inexact"] if inexact else [])]
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=venv)
    env.pop("VIRTUAL_ENV", None)
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise EnvUnavailable(f"uv sync を起動できない（{e}）") from None
    python = python_of(venv)
    if p.returncode != 0 or not os.path.isfile(python):
        tail = (p.stderr or p.stdout).strip().splitlines()[-1:] or ["理由は出ていない"]
        raise EnvUnavailable(f"ラッパーの環境を {venv} に作れない（uv sync の終了コード {p.returncode}: {tail[0]}）")
    return python


def _reexec(python: str, launcher: str, argv: list[str]) -> None:
    os.execve(python, [python, launcher, *argv], dict(os.environ, **{REEXEC_ENV: "1"}))


def enter(argv: list[str], launcher: str, root: str = PKG_ROOT) -> int | None:
    """副命令を動かせる python で動いていれば None（続ける）。起動し直すなら戻らない。
    環境が無くて副命令を動かさないときは、終了コードを返す（`run` は素通しして戻らない）。"""
    if ready():
        os.environ.pop(REEXEC_ENV, None)  # 子の claude と hook へ継がせない
        return None
    sub = argv[0] if argv else ""
    venv = env_dir(root)
    python = python_of(venv)
    if not os.environ.get(REEXEC_ENV) and os.path.isfile(python):
        _reexec(python, launcher, argv)
    if sub in PASS:
        return PASS[sub]
    if sub in PREPARE:
        own = is_version_dir(root)
        try:
            python = sync(root if own else str(deps.PLUGIN_ROOT), venv, inexact=not own)
        except EnvUnavailable as e:
            print(f"ndf-relay: {e}", file=sys.stderr)
            return deps.EXIT_PRECONDITION
        os.environ.pop(REEXEC_ENV, None)
        _reexec(python, launcher, argv)
    reason = f"ラッパーの環境（{venv}）が無い。/ndf:install-wrapper を打ち直す"
    if sub == "run":
        from . import claude as cl  # 外部パッケージを import しない
        claude = cl.resolve_claude()
        cl.say(f"ラッパーを始めない（{reason}）。カットポイントでは示されたコマンドを手で入力する")
        if claude is not None and cl.depth() < 2:
            cl.passthrough(claude, argv[1:])
        return 127
    print(f"ndf-relay: {reason}", file=sys.stderr)
    return deps.EXIT_PRECONDITION
