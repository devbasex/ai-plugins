#!/usr/bin/env python3
"""版上げの後に、説明文書とプラグインの定義の description に残った古い版数をそろえる。

リリースの設定（`.ndf/release.json`）の手順として、版上げの PR の組み立て（`ndf release-steps assemble`）が
`plugin.json` の `version` を上げた後に打つ。

- 各プラグインの `.claude-plugin/plugin.json`・`.codex-plugin/plugin.json`・ルートの `plugin.json`（`version` も）と、`.claude-plugin/marketplace.json` の
  そのプラグインの description の `(vX.Y.Z)` を、Claude 版の `plugin.json` の `version` にそろえる
  （`scripts/lib/validate_manifests.py` が確かめる規則）
- `scripts/check-doc-staleness.py` が「記載: <古い版>（L<行>） / <正本>: <新しい版>」と報告した行の版数だけを置き換える。
  どこに版数を書くかの知識は検査の側にだけ置く（ここで行の形を二重に持たない）

最後に 2 つの検査を打ち直し、残りがあれば 1 で終わる（版上げの PR を壊れたまま作らない）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DESCRIPTION_VERSION = re.compile(r"\(v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\)")
STALE_LINE = re.compile(
    r"^ERROR: (?P<path>[^:]+): .*?記載: (?P<old>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)（L(?P<line>\d+)） / [^:]+: "
    r"(?P<new>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)）"
)
MAX_ROUNDS = 3


def sync_descriptions(root: Path) -> list[str]:
    """description の版数を Claude 版の plugin.json の version にそろえ、書き換えたファイルを返す。"""
    changed = set()
    versions = {}
    for claude in sorted(root.glob("plugins/**/.claude-plugin/plugin.json")):
        manifest = json.loads(claude.read_text(encoding="utf-8"))
        version = manifest.get("version")
        if not isinstance(version, str):
            continue
        if isinstance(manifest.get("name"), str):
            versions[manifest["name"]] = version
        for path in (claude, claude.parent.parent / ".codex-plugin" / "plugin.json"):
            if path.is_file() and replace_description_version(path, version, json.loads(path.read_text(encoding="utf-8"))):
                changed.add(path.relative_to(root).as_posix())
        package = claude.parent.parent / "plugin.json"  # Agent Plugins package（ルートの plugin.json）は version も追う
        if package.is_file() and sync_package(package, version):
            changed.add(package.relative_to(root).as_posix())
    market = root / ".claude-plugin" / "marketplace.json"
    if market.is_file():
        for entry in json.loads(market.read_text(encoding="utf-8")).get("plugins", []):
            version = versions.get(entry.get("name")) if isinstance(entry, dict) else None
            if version is not None and replace_description_version(market, version, entry):
                changed.add(market.relative_to(root).as_posix())
    return sorted(changed)


def sync_package(path: Path, version: str) -> bool:
    """ルートの plugin.json の `version` と description の版数を `version` にそろえる（整形はそのまま保つ）。"""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    old = manifest.get("version")
    bumped = False
    if isinstance(old, str) and old != version:
        text = path.read_text(encoding="utf-8")
        literal = f'"version": {json.dumps(old)}'
        if literal not in text:
            raise SystemExit(f"{path}: version の値を本文から見つけられない（整形の形が違う）")
        path.write_text(text.replace(literal, f'"version": {json.dumps(version)}', 1), encoding="utf-8")
        bumped = True
    return replace_description_version(path, version, json.loads(path.read_text(encoding="utf-8"))) or bumped


def replace_description_version(path: Path, version: str, owner: dict) -> bool:
    """`owner`（`path` の中のオブジェクト）の `description` の `(vX.Y.Z)` だけを置き換える（ファイルの整形はそのまま保つ）。"""
    text = path.read_text(encoding="utf-8")
    description = owner.get("description")
    if not isinstance(description, str):
        return False
    found = DESCRIPTION_VERSION.search(description)
    if found is None or found.group(1) == version:
        return False
    fixed = description.replace(found.group(0), f"(v{version})", 1)
    old_literal = json.dumps(description, ensure_ascii=False)
    if old_literal not in text:
        raise SystemExit(f"{path}: description の値を本文から見つけられない（エスケープの形が違う）")
    path.write_text(text.replace(old_literal, json.dumps(fixed, ensure_ascii=False), 1), encoding="utf-8")
    return True


def stale_lines(output: str) -> list[tuple[str, int, str, str]]:
    """検査の出力から (パス, 行番号, 古い版, 新しい版) を拾う。"""
    return [(m["path"], int(m["line"]), m["old"], m["new"]) for m in (STALE_LINE.match(line) for line in output.splitlines()) if m]


def replace_on_line(path: Path, number: int, old: str, new: str) -> bool:
    """`number` 行目（1 始まり）の版数 `old` を 1 つだけ `new` にする。前後が版数の続きなら置き換えない。"""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not 1 <= number <= len(lines):
        return False
    pattern = re.compile(rf"(?<![0-9.]){re.escape(old)}(?![0-9])")
    fixed, count = pattern.subn(new, lines[number - 1], count=1)
    if count == 0:
        return False
    lines[number - 1] = fixed
    path.write_text("".join(lines), encoding="utf-8")
    return True


def run(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)


def doc_check(root: Path) -> subprocess.CompletedProcess:
    return run(root, sys.executable, str(root / "scripts" / "check-doc-staleness.py"), "--root", str(root))


def sync_documents(root: Path) -> list[str]:
    changed: list[str] = []
    for _ in range(MAX_ROUNDS):
        checked = doc_check(root)
        found = stale_lines(checked.stdout + checked.stderr)
        hits = [rel for rel, number, old, new in found if replace_on_line(root / rel, number, old, new)]
        if not hits:
            break
        changed.extend(hits)
    return sorted(set(changed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="リポジトリの根")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    changed = sync_descriptions(root) + sync_documents(root)
    for rel in changed:
        print(f"そろえた: {rel}")
    remaining = []
    docs = doc_check(root)
    if docs.returncode != 0:
        remaining.append(docs.stdout + docs.stderr)
    manifests = run(root, sys.executable, str(root / "scripts" / "lib" / "validate_manifests.py"), str(root), *families(root))
    if manifests.returncode != 0:
        remaining.append(manifests.stdout + manifests.stderr)
    if remaining:
        print("そろえた後も検査が落ちる:\n" + "\n".join(remaining), file=sys.stderr)
        return 1
    return 0


def families(root: Path) -> list[str]:
    got = run(root, sys.executable, str(root / "scripts" / "lib" / "plugin_families.py"), str(root))
    if got.returncode != 0:
        raise SystemExit(f"プラグインの一覧を読めない: {got.stderr.strip()}")
    return got.stdout.split()


if __name__ == "__main__":
    sys.exit(main())
