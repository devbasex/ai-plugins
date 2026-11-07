"""NDF の状態の置き場の根（#2766）。リポジトリの外に書く状態（耐久ワークフローの記録・プランの状態・relay・台帳など）は
すべてこの根の下の `<種類>/` に置く。各置き場の個別の上書き（`NDF_DBOS_DIR` など）は呼ぶ側が先に見る。

解決の順:

1. `NDF_STATE_HOME`
2. `$XDG_STATE_HOME/ndf`
3. `$HOME/.claude/ndf/state`（既定）。ただし既定が無く旧 `$HOME/.local/state/ndf` があれば旧（移行の前）

既定を `~/.claude` の下に置くのは、Claude Code のログインを保つ構成なら永続化されているためである（`~/.local` は
コンテナの書き込み層にあり、作り直しで消える）。`CLAUDE_CONFIG_DIR` は使わない（relay がアカウントごとの子へ
別の値を渡すため、置き場が割れる）。旧から新への移行は `state-home.py migrate` が 1 回だけ行う（`migrate`）。
シェルの同じ順は `skills/development-workflow/scripts/lib/workflow-common.sh` の `ndf_state_home`・`statusline.sh`・
`hooks/claude.json` にある。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, str | None]:
    if env is not None:
        return env
    return {  # 読む変数を名前で書く（契約の検査が読みを拾う）
        "NDF_STATE_HOME": os.environ.get("NDF_STATE_HOME"),
        "XDG_STATE_HOME": os.environ.get("XDG_STATE_HOME"),
        "HOME": os.environ.get("HOME"),
    }


def _home(env: Mapping[str, str | None]) -> Path:
    return Path(env.get("HOME") or Path.home())


def default_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """既定の根（移行の後の置き場）。"""
    return _home(_env(env)) / ".claude" / "ndf" / "state"


def legacy_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """旧の根（`~/.local/state/ndf`）。"""
    return _home(_env(env)) / ".local" / "state" / "ndf"


def state_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """状態の置き場の根（モジュールの docstring の順）。空の値は無いものとして次へ進む。"""
    env = _env(env)
    if env.get("NDF_STATE_HOME"):
        return Path(env["NDF_STATE_HOME"])
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "ndf"
    new, old = default_home(env), legacy_home(env)
    if not new.exists() and old.is_dir():
        return old
    return new


def holders(root: Path, proc: Path = Path("/proc")) -> list[dict]:
    """`root` の下のファイルを開いている・作業ディレクトリにしているプロセス（自分を除く）。/proc が無ければ空。"""
    root = root.resolve()
    me = os.getpid()
    out = []
    for p in proc.glob("[0-9]*"):
        if int(p.name) == me:
            continue
        links = [p / "cwd", *(p / "fd").glob("*")] if (p / "fd").is_dir() else [p / "cwd"]
        hit = None
        for link in links:
            try:
                target = Path(os.readlink(link))
            except OSError:
                continue
            if target == root or root in target.parents:
                hit = str(target)
                break
        if hit:
            try:
                cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                cmd = ""
            out.append({"pid": int(p.name), "path": hit, "cmd": cmd[:200]})
    return out


def migrate(env: Optional[Mapping[str, str]] = None, proc: Path = Path("/proc")) -> dict:
    """旧の根を既定の根へ移す。結果は `{"status", "summary", "from", "to", "holders"}`。

    status: `migrated`（移した）・`none`（移すものが無い・既に移した・上書きの指定で既定を使わない）・
    `held`（旧を開いているプロセスがある。何も変えない）・`conflict`（新と旧が両方ある。何も変えない）。
    `<新>.partial` へ写してから改名し、最後に旧を消す。途中で落ちても旧は残り、解決は旧を指したままになる。"""
    env = _env(env)
    new, old = default_home(env), legacy_home(env)
    base = {"from": str(old), "to": str(new), "holders": []}
    if env.get("NDF_STATE_HOME") or env.get("XDG_STATE_HOME"):
        return {**base, "status": "none", "summary": "NDF_STATE_HOME か XDG_STATE_HOME が指定されていて、既定の置き場を使わない"}
    if not old.is_dir():
        return {**base, "status": "none", "summary": f"旧の置き場 {old} が無い（移すものが無い）"}
    if new.exists():
        return {**base, "status": "conflict", "summary": f"{new} と {old} が両方ある。中身を確かめて片方へまとめる"}
    held = holders(old, proc)
    if held:
        return {**base, "status": "held", "holders": held, "summary": f"{len(held)} 個のプロセスが {old} を使っている。止めてから打ち直す"}
    partial = new.with_name(new.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(old, partial, symlinks=True)
    partial.rename(new)
    shutil.rmtree(old)
    return {**base, "status": "migrated", "summary": f"{old} を {new} へ移した"}
