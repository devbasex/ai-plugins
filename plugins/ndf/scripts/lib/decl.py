"""`.ndf/` の設定ファイルを開く場所の解決（#2336 の決定 9）。設定を読む関数はすべてここを通して開く。

置き場は NDF と一緒に配る契約の一覧（このファイルから見て `../../contracts/settings/<名前>.json` の `place`）が決める。

- `worktree`（チームの設定。git が追跡する）: 呼ばれた木の根 → 無ければメインディレクトリ
- `main`（個人の設定。`.ndf/.gitignore` に載る）: メインディレクトリ → 無ければ呼ばれた木の根

認可に関わる値（本番マージの判定に使う `project.json` の `delivery`・`worktree.json` の `production_branch`・
`runtimes.json`）は、Pull Request の変更だけで承認ゲート 2 やランタイムの制限を緩められないよう `main` に置く（#2363）。
ファイルごとならファイルの `place`、キーごとなら `keys.<キー>.place` に書く。キーの値は `read_json_setting` が、`main` の
順で最初に見つかったファイル（メインディレクトリ → 呼ばれた木）の値で上書きする（そのファイルに無ければ消す）。

メインディレクトリは、共通ディレクトリの名前が `.git` ならその親、そうでなければ `core.worktree`（サブモジュールでは `--git-common-dir` の親が
`.git/modules/<名前>` を指すため。#2054）→ `--git-common-dir` の親の順に決める。標準ライブラリと `proc` だけで書く。
"""

from __future__ import annotations

import json
from pathlib import Path

import proc

SETTINGS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "settings"
_CONTRACTS: dict[str, dict] | None = None


def _contracts() -> dict[str, dict]:
    """設定ファイルの名前 → 契約の項目。プロセスの中で 1 回だけ読む。"""
    global _CONTRACTS
    if _CONTRACTS is None:
        out = {}
        for f in sorted(SETTINGS_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and isinstance(d.get("file"), str):
                out[d["file"]] = d
        _CONTRACTS = out
    return _CONTRACTS


def places() -> dict[str, str]:
    """設定ファイルの名前 → 置き場（`worktree` / `main`）。"""
    return {k: d.get("place", "worktree") for k, d in _contracts().items()}


def key_places(name: str) -> dict[str, str]:
    """`name` のキーのうち、ファイルと違う置き場を宣言したもの（キー → 置き場）。契約に無い名前は `KeyError`。"""
    d = _contracts()[name]
    keys = d.get("keys") if isinstance(d.get("keys"), dict) else {}
    own = d.get("place", "worktree")
    return {k: v["place"] for k, v in keys.items() if isinstance(v, dict) and v.get("place", own) != own}


def main_dir(root) -> Path | None:
    """`root` の worktree が属するメインディレクトリ。git でなければ `None`。例外を上げない。"""
    if not root:
        return None
    common = proc.git_out(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    if Path(common).name == ".git":  # 通常の木。core.worktree を問い合わせない（git の起動を 1 回に保つ）
        return Path(common).parent
    wt = proc.git_out(root, "config", "--file", str(Path(common) / "config"), "--get", "core.worktree")
    if wt:
        p = (Path(common) / wt).resolve()
        if p.is_dir():
            return p
    return Path(common).parent


def _setting_candidates(root, name: str, place: str | None = None):
    """`.ndf/<name>` を探す順（重なりを除く）に返す。メインディレクトリは要るときだけ解く（git の起動を減らす）。

    `place` を渡せばファイルの置き場の代わりに使う（キーの置き場）。契約に無い名前は `KeyError`。
    """
    place = place or places()[name]
    here = Path(root) if root else None
    seen: set[Path] = set()
    for which in ("here", "main") if place == "worktree" else ("main", "here"):
        base = here if which == "here" else main_dir(root)
        if base and base not in seen:
            seen.add(base)
            yield base / ".ndf" / name


def setting_path(root, name: str, place: str | None = None) -> Path | None:
    """`.ndf/<name>` の開くパス。どちらの木にも無ければ `None`。契約に無い名前は `KeyError`。"""
    places()[name]  # 契約に無い名前は探す前に KeyError
    return next((p for p in _setting_candidates(root, name, place) if p.is_file()), None)


def load_file(f: Path, name: str) -> tuple[dict, str | None]:
    """`f`（`.ndf/<name>`）の中身と、読めなかった理由。JSON でない・オブジェクトでなければ `({}, 理由)`。"""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f".ndf/{name}: JSON として読めない（{e}）"
    return (data, None) if isinstance(data, dict) else ({}, f".ndf/{name}: 最上位がオブジェクトでない")


def overlay_keys(root, name: str, data: dict, found: Path | None) -> tuple[dict, str | None]:
    """`data`（`found` から読んだ中身）のうち、ファイルと違う置き場のキーをその置き場の順で読んだ値に替える。

    その置き場で最初に見つかったファイルが `found` と同じなら何もしない。そのファイルにキーが無ければ消す（呼ばれた木の
    値で補わない）。そのファイルが読めなければ `({}, 理由)`。
    """
    out = dict(data)
    for place in sorted(set((kp := key_places(name)).values())):
        f = setting_path(root, name, place)
        if f is None or (found is not None and f.resolve() == found.resolve()):
            continue
        other, why = load_file(f, name)
        if why:
            return {}, why
        for k in (k for k, p in kp.items() if p == place):
            if k in other:
                out[k] = other[k]
            else:
                out.pop(k, None)
    return out, None


def read_json_setting(root, name: str) -> tuple[dict, str | None]:
    """`.ndf/<name>` の中身と、読めなかった理由（キーの置き場を当てたもの）。無ければ `({}, None)`。例外を上げない。"""
    if not root:
        return {}, None
    f = setting_path(root, name)
    data, why = load_file(f, name) if f else ({}, None)
    return (data, why) if why else overlay_keys(root, name, data, f)
