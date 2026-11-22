#!/usr/bin/env python3
"""版上げの後に、説明文書とプラグインの定義の description に残った古い版数をそろえる。

リリースの設定（`.ndf/release.json`）の手順として、版上げの PR の組み立て（`ndf release-steps assemble`）が
`plugin.json` の `version` を上げた後に打つ。

- 各プラグインの `.claude-plugin/plugin.json`・`.codex-plugin/plugin.json`・ルートの `plugin.json`（`version` も。
  リリースの設定のこの手順の `writes` に書いたものだけ。無いものがあれば書く前に止める）と、`.claude-plugin/marketplace.json` の
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
RELEASE_CONFIG = ".ndf/release.json"
SCRIPT_NAME = "sync-version-mentions.py"


def sync_descriptions(root: Path) -> list[str]:
    """description の版数を Claude 版の plugin.json の version にそろえ、書き換えたファイルを返す。"""
    changed = set()
    versions = {}
    claudes = sorted(root.glob("plugins/**/.claude-plugin/plugin.json"))
    packages = declared_packages(root)
    undeclared = sorted(
        rel for rel in (package_of(c).relative_to(root).as_posix() for c in claudes if package_of(c).is_file()) if rel not in packages
    )
    if undeclared:  # 書き換えた後に組み立てが writes の外として止まる前に、書く前に止める
        raise SystemExit(f"ルートの plugin.json が {RELEASE_CONFIG} のこの手順の writes に無い: {', '.join(undeclared)}")
    for claude in claudes:
        manifest = json.loads(claude.read_text(encoding="utf-8"))
        version = manifest.get("version")
        if not isinstance(version, str):
            continue
        if isinstance(manifest.get("name"), str):
            versions[manifest["name"]] = version
        for path in (claude, claude.parent.parent / ".codex-plugin" / "plugin.json"):
            if path.is_file() and sync_manifest(path, version, follow_version=False):
                changed.add(path.relative_to(root).as_posix())
        package = package_of(claude)  # Agent Plugins package（ルートの plugin.json）は version も追う
        if package.is_file() and sync_manifest(package, version, follow_version=True):
            changed.add(package.relative_to(root).as_posix())
    market = root / ".claude-plugin" / "marketplace.json"
    if market.is_file():
        text = market.read_text(encoding="utf-8")
        fixed = text
        for entry in json.loads(text).get("plugins", []):
            version = versions.get(entry.get("name")) if isinstance(entry, dict) else None
            if version is not None:
                fixed = replace_description_version(market, fixed, version, entry)
        if fixed != text:
            market.write_text(fixed, encoding="utf-8")
            changed.add(market.relative_to(root).as_posix())
    return sorted(changed)


def package_of(claude: Path) -> Path:
    return claude.parent.parent / "plugin.json"


def declared_packages(root: Path) -> set[str]:
    """リリースの設定のうちこのスクリプトを打つ手順の `writes` から、ルートの plugin.json のパスを読む。

    組み立て（`assemble`）が書き換えてよいパスとして照らすのと同じ宣言を使う（同期の対象をここで別に持たない）。"""
    path = root / RELEASE_CONFIG
    if not path.is_file():
        return set()
    steps = json.loads(path.read_text(encoding="utf-8")).get("steps", [])
    mine = [s for s in steps if isinstance(s, dict) and any(str(a).endswith(SCRIPT_NAME) for a in s.get("command", []))]
    writes = {Path(str(w)).as_posix().removeprefix("./") for s in mine for w in s.get("writes", [])}
    return {
        w
        for w in writes
        if w.startswith("plugins/") and w.endswith("/plugin.json") and "/.claude-plugin/" not in w and "/.codex-plugin/" not in w
    }


def sync_manifest(path: Path, version: str, *, follow_version: bool) -> bool:
    """1 回読んで、description の版数（`follow_version` なら `version` も）をそろえ、変われば 1 回だけ書く（整形はそのまま保つ）。"""
    text = path.read_text(encoding="utf-8")
    manifest = json.loads(text)
    fixed = text
    old = manifest.get("version")
    if follow_version and isinstance(old, str) and old != version:
        literal = f'"version": {json.dumps(old)}'
        if literal not in fixed:
            raise SystemExit(f"{path}: version の値を本文から見つけられない（整形の形が違う）")
        fixed = fixed.replace(literal, f'"version": {json.dumps(version)}', 1)
    fixed = replace_description_version(path, fixed, version, manifest)
    if fixed == text:
        return False
    path.write_text(fixed, encoding="utf-8")
    return True


def replace_description_version(path: Path, text: str, version: str, owner: dict) -> str:
    """`owner`（`text` の中のオブジェクト）の `description` の `(vX.Y.Z)` だけを置き換えた本文を返す（整形はそのまま保つ）。"""
    description = owner.get("description")
    if not isinstance(description, str):
        return text
    found = DESCRIPTION_VERSION.search(description)
    if found is None or found.group(1) == version:
        return text
    fixed = description.replace(found.group(0), f"(v{version})", 1)
    old_literal = json.dumps(description, ensure_ascii=False)
    if old_literal not in text:
        raise SystemExit(f"{path}: description の値を本文から見つけられない（エスケープの形が違う）")
    return text.replace(old_literal, json.dumps(fixed, ensure_ascii=False), 1)


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
