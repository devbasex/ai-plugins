"""`.ndf/` の設定ファイルを開く場所の解決（#2336 の決定 9）。設定を読む関数はすべてここを通して開く。

置き場は NDF と一緒に配る契約の一覧（このファイルから見て `../../contracts/settings/<名前>.json` の `place`）が決める。

- `worktree`（チームの設定。git が追跡する）: 呼ばれた木の根 → 無ければメインディレクトリ
- `main`（個人の設定。`.ndf/.gitignore` に載る）: メインディレクトリ → 無ければ呼ばれた木の根

メインディレクトリは、共通ディレクトリの `core.worktree`（サブモジュールでは `--git-common-dir` の親が
`.git/modules/<名前>` を指すため。#2054）→ `--git-common-dir` の親の順に決める。標準ライブラリと `proc` だけで書く。
"""

from __future__ import annotations

import json
from pathlib import Path

import proc

SETTINGS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "settings"
_PLACES: dict[str, str] | None = None


def places() -> dict[str, str]:
    """設定ファイルの名前 → 置き場（`worktree` / `main`）。プロセスの中で 1 回だけ読む。"""
    global _PLACES
    if _PLACES is None:
        out = {}
        for f in sorted(SETTINGS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and isinstance(d.get("file"), str):
                out[d["file"]] = d.get("place", "worktree")
        _PLACES = out
    return _PLACES


def main_dir(root) -> Path | None:
    """`root` の worktree が属するメインディレクトリ。git でなければ `None`。例外を上げない。"""
    if not root:
        return None
    common = proc.git_out(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    wt = proc.git_out(root, "config", "--file", str(Path(common) / "config"), "--get", "core.worktree")
    if wt:
        p = (Path(common) / wt).resolve()
        if p.is_dir():
            return p
    return Path(common).parent


def _candidates(root, name: str):
    """`.ndf/<name>` を探す順（重なりを除く）に返す。メインディレクトリは要るときだけ解く（git の起動を減らす）。

    契約に無い名前は `KeyError`。
    """
    place = places()[name]
    here = Path(root) if root else None
    seen: set[Path] = set()
    for which in ("here", "main") if place == "worktree" else ("main", "here"):
        base = here if which == "here" else main_dir(root)
        if base and base not in seen:
            seen.add(base)
            yield base / ".ndf" / name


def setting_path(root, name: str) -> Path | None:
    """`.ndf/<name>` の開くパス。どちらの木にも無ければ `None`。契約に無い名前は `KeyError`。"""
    places()[name]  # 契約に無い名前は探す前に KeyError
    return next((p for p in _candidates(root, name) if p.is_file()), None)
