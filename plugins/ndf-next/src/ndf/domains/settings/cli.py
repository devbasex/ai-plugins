"""サブコマンド `ndf settings`: 利用者の設定（`.ndf/`）を改名表に従って新しいキーへ書き換える（#3613）。

    ndf settings migrate --root <リポジトリ>                       差分と差分のハッシュを示す（書かない）
    ndf settings migrate --root <リポジトリ> --apply <差分のハッシュ>  示した差分を承認した後に書く

承認は対話の確認で受けない。差分を示す実行と書く実行に分け、書く実行は今の差分を作り直して、渡したハッシュと一致し、
変換した中身が新しいスキーマに通るときだけ書く（利用者が見ていない差分を書かない）。差分は設定ファイルごとの
JSON Patch（RFC 6902）の操作の列で、`move` と `replace` の直前に元の位置の今の値を持つ `test` を置く。

終了コード: 0 = 書き換えるものが無い・差分を示した（gate）・書いた / 1 = ハッシュが一致しない・スキーマに通らない
（種類 `refused`）/ 2 = 設定を読めない・書けない・引数の誤り（種類 `invalid`）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf import cli, contracts
from ndf.contracts import result
from ndf.domains.records import jsonfile
from ndf.domains.settings import renames

TOOL = cli.tool_of(__name__)
# --apply の値の形（plan が返す sha256 の 16 進）
DIGEST = re.compile(r"[0-9a-f]{64}")
# 書くときのインデント（人が書いた .ndf/ の実物と同じ）
INDENT = 2
# 書き換えの対象の設定のスキーマの置き場（contracts/schemas/ からの相対）
SCHEMA_DIR = "ndf-settings"


@dataclass(frozen=True)
class Target:
    """改名表に当たる設定ファイル 1 つの差分。"""

    file: str
    path: Path
    rel: str
    text: str
    migrated: dict[str, Any]
    patch: list[dict[str, Any]]
    legacy: int


def _dump(data: Any) -> str:
    return json.dumps(data, indent=INDENT, ensure_ascii=False) + "\n"


def _write(path: Path, text: str) -> None:
    """同じディレクトリの一時ファイルへ書いてから付け替える（書きかけのファイルを残さない）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _targets(root: Path) -> list[Target]:
    """改名表に当たり、実在する設定ファイルごとの差分（ファイルの ID の順）。読めなければ `JsonFileError`・`OSError`。"""
    out: list[Target] = []
    for file in sorted({r.file for r in renames.load()}):
        rel = renames.relpath(file)
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        data = jsonfile.read_object(path)
        migrated, patch = renames.migrate(file, data)
        out.append(Target(file, path, rel, text, migrated, patch, len(renames.legacy_keys(file, data))))
    return out


def _digest(targets: list[Target]) -> str:
    """差分を並べた JSON の sha256。根の絶対パスを入れない（同じ中身なら別の clone でも同じハッシュ）。"""
    rows = [{"file": t.file, "path": t.rel, "patch": t.patch} for t in targets if t.patch]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _read_stop(root: Path, exc: Exception) -> dict[str, Any]:
    return result.stopped(TOOL, "invalid", f"設定を読めない（{root}）: {exc}")


def plan(root: Path) -> dict[str, Any]:
    """改名表に当たる設定ファイルごとの差分と差分のハッシュを結果 JSON にする。ファイルを書かない。"""
    try:
        targets = _targets(root)
    except (jsonfile.JsonFileError, OSError, UnicodeDecodeError) as exc:
        return _read_stop(root, exc)
    changed = [t for t in targets if t.patch]
    if not changed:
        return result.result(TOOL, result.OK, "書き換えるものが無い", metrics={"files": 0})
    items = [
        {"kind": "settings-file", "file": t.file, "path": t.rel, "legacy_keys": t.legacy, "patch": t.patch, "reindent": _dump(json.loads(t.text)) != t.text}
        for t in changed
    ]
    digest = _digest(targets)
    summary = f"{len(changed)} 件の設定に旧いキーがある。差分を示して承認を取ってから --apply で書き換える"
    routes = {"approved": result.command(TOOL, "migrate", "--root", str(root), "--apply", digest)}
    return result.result(TOOL, result.GATE, summary, items, {"files": len(changed), "digest": digest}, routes=routes)


def _schema_errors(targets: list[Target]) -> list[str]:
    errs: list[str] = []
    for t in targets:
        errs += [f"{t.rel}: {e}" for e in contracts.schema_errors(t.migrated, contracts.schema_path(f"{SCHEMA_DIR}/{t.file}"))]
        errs += [f"{t.rel}: 旧いキー {k.old} と新しいキー {k.new} の両方がある" for k in renames.legacy_keys(t.file, t.migrated)]
    return errs


def apply(root: Path, digest: str) -> dict[str, Any]:
    """今の差分のハッシュが `digest` と一致し、変換した中身が新しいスキーマに通るときだけ書く。"""
    try:
        targets = _targets(root)
    except (jsonfile.JsonFileError, OSError, UnicodeDecodeError) as exc:
        return _read_stop(root, exc)
    changed = [t for t in targets if t.patch]
    if not changed or _digest(targets) != digest:
        detail = f"示した差分（{digest[:12]}…）と今の差分が違う。ndf settings migrate --root {root} で差分を示し直し、承認を取り直す"
        return result.stopped(TOOL, "refused", detail)
    errs = _schema_errors(changed)
    if errs:
        return result.stopped(TOOL, "refused", "書き換えた設定がスキーマに通らないため書かない: " + "; ".join(errs))
    written: list[dict[str, Any]] = []
    for t in changed:
        try:
            _write(t.path, _dump(t.migrated))
        except OSError as exc:
            done = "、".join(w["path"] for w in written) or "無し"
            return result.stopped(TOOL, "invalid", f"{t.rel} を書けない: {exc}。書き終えたファイル: {done}", items=written)
        written.append({"kind": "settings-file", "file": t.file, "path": t.rel, "operations": len(t.patch)})
    return result.result(TOOL, result.OK, f"{len(written)} 件の設定を新しいキーへ書き換えた", written, {"files": len(written)})


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="利用者の設定を新しいキーへ書き換える")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("migrate", help="改名表に従い、旧いキーを持つ設定の差分を示す（--apply で承認した差分を書く）")
    m.add_argument("--root", required=True, help="リポジトリの根")
    m.add_argument("--apply", help="承認した差分のハッシュ（migrate が示した 64 桁の 16 進）")
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root)
    if args.apply is None:
        return result.emit(plan(root))
    if not DIGEST.fullmatch(args.apply):
        return result.emit(result.stopped(TOOL, "invalid", f"--apply の値は 64 桁の小文字の 16 進: {args.apply[:16]}"))
    return result.emit(apply(root, args.apply))
