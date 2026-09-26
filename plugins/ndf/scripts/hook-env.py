#!/usr/bin/env python3
"""hook の環境を用意する（#1142 の決定 20）。SessionStart（`worktree-session.sh`）が 1 回起動する。

    python3 hook-env.py [--wait 秒]

`deps.require()` と同じ手順（uv を探す・無ければ版を固定して入れる）で、`~/.cache/ndf/venv/<版>` へ hook の経路の
グループ（`lib/hook_python.py` の GROUPS）を `uv sync --frozen --inexact` で入れ、プラグインの根ごとの目印
`~/.cache/ndf/roots<根>` をその環境のディレクトリへ張る（symlink）。hook はこの目印を直に起動する。

- 環境が lock と同じなら uv を起動せず、目印だけを張り直す（2 回目以降のセッションの開始を遅らせない）
- 用意は切り離した子で行い、`--wait` 秒（既定 50）まで待つ。待ちきれなければ子が続きを行い、目印を張った後の
  hook から判定が始まる（それまでの hook は判定をせずに通る）
- 入れられない（ネットワークが無い・権限が無い）ときは理由を標準エラーへ 1 行出す。**終了コードは常に 0**
  （セッションの開始を止めない）。標準出力へは何も書かない（SessionStart の標準出力はモデルへ渡る）
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import deps  # noqa: E402  uv を探す・入れる・環境の置き場（require() は呼ばない）
import hook_python  # noqa: E402

STAMP = ".ndf-hook-env"
LOG = Path.home() / ".cache" / "ndf" / "hook-env.log"


def _say(msg: str) -> None:
    print(f"[ndf hook-env] {msg}", file=sys.stderr)


def env_stamp() -> str:
    """lock と入れるグループから作る目印。環境の中の `.ndf-hook-env` と同じなら用意し直さない。"""
    h = hashlib.sha256((deps.PLUGIN_ROOT / "uv.lock").read_bytes())
    h.update(" ".join(hook_python.GROUPS).encode())
    return h.hexdigest()


def roots() -> list[str]:
    """このプラグインを指す根の字面（hook の command が組み立てる形）。環境変数の根は同じ実体を指すものだけ。"""
    real = os.path.realpath(deps.PLUGIN_ROOT)
    found = [str(deps.PLUGIN_ROOT), real]
    for name in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", "CODEX_PLUGIN_ROOT"):
        v = os.environ.get(name)
        if v and os.path.isabs(v) and os.path.realpath(v) == real:
            found.append(v.rstrip("/") or "/")
    return list(dict.fromkeys(found))


def link_pointers(venv: Path) -> None:
    for root in roots():
        p = hook_python.pointer(root)
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


def env_ready(venv: Path, want: str) -> bool:
    try:
        return (venv / "bin" / "python").exists() and (venv / STAMP).read_text(encoding="utf-8") == want
    except OSError:
        return False


def sync_env() -> int:
    """切り離した子の本体。uv を用意して環境を作り、目印を張る。"""
    venv = Path(deps.venv_dir())
    want = env_stamp()
    if not env_ready(venv, want):
        uv = deps.find_uv()
        if not uv:
            _say(f"uv が無いため {deps.UV_VERSION} を ~/.local/bin へ入れる")
            uv = deps.install_uv()
        if not uv:
            _say(f"uv を入れられない（ネットワークか権限が無い）。手で入れてから打ち直す: {deps.INSTALL_HINT}")
            return 3
        extras = [x for g in hook_python.GROUPS for x in ("--extra", g)]
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv))
        p = subprocess.run([uv, "sync", "--quiet", "--frozen", "--inexact", "--compile-bytecode", "--project", str(deps.PLUGIN_ROOT),
                            *extras], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if p.returncode != 0:
            _say(f"uv sync が失敗した（終了コード {p.returncode}）: {(p.stderr or '').strip()[-300:]}")
            return 3
        (venv / STAMP).write_text(want, encoding="utf-8")
    link_pointers(venv)
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--sync"]:
        return sync_env()
    wait = float(argv[argv.index("--wait") + 1]) if "--wait" in argv[:-1] else 50.0
    try:
        venv = Path(deps.venv_dir())
        if env_ready(venv, env_stamp()):
            link_pointers(venv)
            return 0
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "w", encoding="utf-8") as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--sync"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                     start_new_session=True)
        deadline = time.monotonic() + wait
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if child.returncode is None:
            _say(f"hook の環境の用意が {wait:g} 秒で終わらないので、続きを背景で行う（記録: {LOG}）")
        elif child.returncode != 0:
            sys.stderr.write(LOG.read_text(encoding="utf-8", errors="replace")[-600:])
    except Exception as exc:  # noqa: BLE001 — セッションの開始を止めない
        _say(f"hook の環境を用意できない: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
