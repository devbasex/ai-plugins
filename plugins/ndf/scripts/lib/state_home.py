"""NDF の状態の置き場（#2766）。リポジトリの外に書く状態を、持ち主ごとに 3 つの根へ分ける。

| 持ち主 | 根（既定） | 置くもの | 理由 |
| --- | --- | --- | --- |
| チェックアウト | `<git の共通ディレクトリ>/ndf/state`（`checkout_home`） | 耐久の記録 `dbos/`・プランの状態 `sv/` | 絶対パスから鍵を作る。永続で、チェックアウトごとに別になる（同じパスの別のチェックアウトと混ざらない） |
| 利用者 | `~/.claude/ndf/state`（`user_home`） | 追記だけの台帳（`ask.jsonl`・MVV の記録・`metrics/`・`usage/`・`checks/`・`stages/` など。`USER_KINDS`） | Claude Code のログインと同じ永続の木。複数のコンテナで共有して集計に使う |
| コンテナのプロセス | `~/.local/state/ndf`（`container_home`） | relay・待ちの通知・guard・context（pid とセッションを持つもの） | pid の名前空間はコンテナごとに別。プロセスはコンテナとともに消える |

`NDF_STATE_HOME` か `$XDG_STATE_HOME/ndf` を指定すると、3 つとも その 1 つの根になる（テストと明示の指定）。
各置き場の個別の上書き（`NDF_DBOS_DIR` など）は呼ぶ側が先に見る。`CLAUDE_CONFIG_DIR` は使わない（relay が
アカウントごとの子へ別の値を渡すため、置き場が割れる）。

旧の置き場（`~/.local/state/ndf`）からの移り方:

- チェックアウトの記録は、新に無く旧にある記録を旧のまま使う（`legacy_entry`。動いている耐久ワークフローを続ける）
- 利用者の台帳は `state-home.py migrate`（`migrate`）が旧から足し合わせる。何度打ってもよい

シェルの同じ順は `skills/development-workflow/scripts/lib/workflow-common.sh` の `ndf_user_state_home` にある。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

# 利用者の根に置く台帳（ファイルかディレクトリの名前）。ほかはコンテナの根に残る
USER_KINDS = (
    "ask.jsonl",
    "mvv-gate.jsonl",
    "project-mvv.jsonl",
    "project-mvv-signals.jsonl",
    "gate-rejections.jsonl",
    "project-mvv",
    "metrics",
    "usage",
    "checks",
    "stages",
    "estimate",
    "test-run",
)


def _env(env: Optional[Mapping[str, str]]) -> Mapping[str, Optional[str]]:
    if env is not None:
        return env
    return {  # 読む変数を名前で書く（契約の検査が読みを拾う）
        "NDF_STATE_HOME": os.environ.get("NDF_STATE_HOME"),
        "XDG_STATE_HOME": os.environ.get("XDG_STATE_HOME"),
        "HOME": os.environ.get("HOME"),
    }


def explicit_home(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    """明示した根（`NDF_STATE_HOME` → `$XDG_STATE_HOME/ndf`）。無ければ None。空の値は無いものとする。"""
    env = _env(env)
    if env.get("NDF_STATE_HOME"):
        return Path(env["NDF_STATE_HOME"])
    if env.get("XDG_STATE_HOME"):
        return Path(env["XDG_STATE_HOME"]) / "ndf"
    return None


def _home(env: Mapping[str, Optional[str]]) -> Path:
    return Path(env.get("HOME") or Path.home())


def legacy_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """旧の根（`~/.local/state/ndf`）。コンテナの根の既定と同じ場所である。"""
    return _home(_env(env)) / ".local" / "state" / "ndf"


def container_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """コンテナのプロセスの状態の根。"""
    return explicit_home(env) or legacy_home(env)


def user_home(env: Optional[Mapping[str, str]] = None) -> Path:
    """利用者の台帳の根。"""
    return explicit_home(env) or _home(_env(env)) / ".claude" / "ndf" / "state"


def find_git_dir(start: Path) -> tuple[Path, Path] | None:
    """(リポジトリの根, git の共通ディレクトリ)。`.git` を上へ探して読む（`git` を起動しない）。git でなければ None。"""
    start = Path(start).resolve()
    for d in (start, *start.parents):
        dot = d / ".git"
        if dot.is_dir():
            return d, _common_of(dot)
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            gitdir = Path(text[len("gitdir:") :].strip())
            gitdir = gitdir if gitdir.is_absolute() else (d / gitdir)
            return d, _common_of(gitdir.resolve())
    return None


def _common_of(gitdir: Path) -> Path:
    f = gitdir / "commondir"
    if f.is_file():
        try:
            rel = f.read_text(encoding="utf-8").strip()
        except OSError:
            return gitdir
        p = Path(rel)
        return (p if p.is_absolute() else gitdir / p).resolve()
    return gitdir


def checkout_home(start: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> Path:
    """チェックアウトの記録の根。`start`（省けば作業ディレクトリ）の git の共通ディレクトリの下。git の外はコンテナの根。"""
    explicit = explicit_home(env)
    if explicit is not None:
        return explicit
    try:
        got = find_git_dir(Path(start) if start is not None else Path.cwd())
    except OSError:
        got = None
    return got[1] / "ndf" / "state" if got else container_home(env)


def legacy_entry(new: Path, kind: str, name: str, env: Optional[Mapping[str, str]] = None) -> Path:
    """チェックアウトの記録 `new`（`<根>/<kind>/<name>`）が無く、旧の根の同じ記録があれば旧を返す（移行の前の記録を続ける）。"""
    if explicit_home(env) is not None or new.exists():
        return new
    old = legacy_home(env) / kind / name
    return old if old.exists() else new


def _merge(src: Path, dst: Path, moved: list, kept: list) -> None:
    """`src` を `dst` へ足し合わせる。`.jsonl` は追記、無いものは移し、同じ名前の他のファイルは旧に残す。"""
    if src.is_dir() and not src.is_symlink():
        for child in sorted(src.iterdir()):
            _merge(child, dst / child.name, moved, kept)
        try:
            src.rmdir()
        except OSError:
            pass  # 残したものがある
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.move(str(src), str(dst))
        moved.append(str(dst))
    elif src.suffix == ".jsonl":
        data = src.read_bytes()
        if data:
            with dst.open("ab") as f:
                f.write(data if data.endswith(b"\n") else data + b"\n")
        src.unlink()
        moved.append(str(dst))
    else:
        kept.append(str(src))


def migrate(env: Optional[Mapping[str, str]] = None) -> dict:
    """旧の根の利用者の台帳（`USER_KINDS`）を利用者の根へ足し合わせる。結果は `{"status", "summary", "from", "to", "moved", "kept"}`。

    status: `migrated`（移した）・`none`（移すものが無い・明示の根を使う）・`kept`（同じ名前のファイルが両方にあり、
    旧に残したものがある）。何度打ってもよい（旧の版のプロセスが旧へ書いた分も、次に打てば移る）。"""
    env = _env(env)
    old, new = legacy_home(env), user_home(env)
    base = {"from": str(old), "to": str(new), "moved": [], "kept": []}
    if explicit_home(env) is not None:
        return {**base, "status": "none", "summary": "NDF_STATE_HOME か XDG_STATE_HOME が指定されていて、既定の置き場を使わない"}
    moved: list = []
    kept: list = []
    for kind in USER_KINDS:
        if (old / kind).exists():
            _merge(old / kind, new / kind, moved, kept)
    out = {**base, "moved": moved, "kept": kept}
    if kept:
        return {**out, "status": "kept", "summary": f"{len(moved)} 件を移し、{len(kept)} 件は移し先に同じ名前があるため旧に残した"}
    if moved:
        return {**out, "status": "migrated", "summary": f"{len(moved)} 件を {new} へ移した"}
    return {**out, "status": "none", "summary": f"{old} に移す台帳が無い"}
