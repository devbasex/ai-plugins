#!/usr/bin/env python3
"""Skill の境界をまたぐ**実行の参照**を数え、例外の一覧に無いものを失敗として返す。

Skill は配布の基準（`plugins/ndf/manifests/*-skills.txt`）で 1 つずつ配るかを決める。
**配る Skill を絞る配布先（agy）では、基準に無い Skill が配布した先から消える。**
別の Skill の `scripts/` を読み込む・起動する・`sys.path` へ入れる参照は、その相手を
配らない配布先で解決できない。

`cross-refactoring` が `cross-review` の共通層を相対で読んでいたのがこの形で、共通層を
プラグインルート直下へ移して解消した（#285）。**増えたときに気づく手段が無ければ、
同じことが繰り返される。** ここでは残る参照を一覧として固定し、増えた分だけを返す。

## 数え方

**文書のリンクは対象にしない。** Skill をまたぐリンクは読み手を案内するもので、配布した
先で解決できなくても手順は動く。数えるのは次の 2 つの形だけである。

| 形 | 例 |
| --- | --- |
| 相対パス | `"$SKILL_DIR/../cross-review/scripts/state.py"` |
| Python のパス連結 | `... / "fix" / "scripts" / "fetch-pr-comments.sh"` |

Skill 名だけを手がかりにすると、`gh pr view` のような別の用途まで入る。直後に
`scripts` が続く形へ絞る。

    python3 scripts/check-cross-skill-refs.py --root .
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILLS_REL = "plugins/ndf/skills"
SUFFIXES = (".py", ".sh", ".md")
IGNORED_DIRS = {"__pycache__", ".pytest_cache"}

# 既に知っている参照。**共通層ではなく Skill の本体どうしの参照**であるため、置き場所を
# 移すだけでは解けない。解くには双方の Skill の設計が要る（#344）。
EXCEPTIONS: dict[tuple[str, str], str] = {
    ("plugins/ndf/skills/fix/SKILL.md", "cross-review"): "#344",
    ("plugins/ndf/skills/cross-review/scripts/state.py", "fix"): "#344",
}

# Markdown の行内リンクの飛び先。読み手への案内であるため走査から外す。
_MD_INLINE_LINK = re.compile(r"\]\([^)]*\)")
# Markdown の参照定義（`[名前]: ../skill/...`）。同じく案内である。
_MD_LINK_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+")


def _skill_names(root: Path) -> list[str]:
    skills = root / SKILLS_REL
    return sorted(p.name for p in skills.iterdir() if p.is_dir())


def _patterns(names: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """Skill ごとに、相対パスと Python のパス連結の 2 つの形を持つ 1 本の式を作る。"""
    built = []
    for name in names:
        escaped = re.escape(name)
        built.append((
            name,
            re.compile(
                rf"\.\./{escaped}/"                                  # 相対パス
                rf"|\"{escaped}\"\s*/\s*\"scripts\""                 # Python のパス連結
            ),
        ))
    return built


def _files(root: Path):
    for path in sorted((root / SKILLS_REL).rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if IGNORED_DIRS & set(path.relative_to(root).parts):
            continue
        yield path


def _strip_links(line: str, is_markdown: bool) -> str:
    if not is_markdown:
        return line
    if _MD_LINK_DEF.match(line):
        return ""
    return _MD_INLINE_LINK.sub("]()", line)


def find_references(root: Path) -> list[tuple[str, int, str, str]]:
    """`(ファイル, 行番号, 指している Skill, 行の中身)` を返す。"""
    patterns = _patterns(_skill_names(root))
    found: list[tuple[str, int, str, str]] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        owner = rel.split("/")[3]        # plugins/ndf/skills/<所有する Skill>/...
        is_markdown = path.suffix == ".md"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            body = _strip_links(line, is_markdown)
            if not body:
                continue
            for name, pattern in patterns:
                # 自分自身への参照は境界をまたがない。
                if name != owner and pattern.search(body):
                    found.append((rel, number, name, line.strip()))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="リポジトリの根")
    parser.add_argument("--list", action="store_true", help="見つかった参照をすべて出す")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    found = find_references(root)

    if args.list:
        for rel, number, name, line in found:
            mark = EXCEPTIONS.get((rel, name), "")
            print(f"{rel}:{number}: -> {name} {mark}\n    {line}")

    seen = {(rel, name) for rel, _, name, _ in found}
    new = [item for item in found if (item[0], item[2]) not in EXCEPTIONS]
    stale = sorted(key for key in EXCEPTIONS if key not in seen)

    if not new and not stale:
        print(f"Skill の境界をまたぐ実行の参照は例外の {len(EXCEPTIONS)} 件だけです")
        return 0

    if new:
        print("例外の一覧に無い、Skill の境界をまたぐ実行の参照があります:", file=sys.stderr)
        for rel, number, name, line in new:
            print(f"- {rel}:{number}: {name} を指しています\n    {line}", file=sys.stderr)
        print(
            "配る Skill を絞る配布先では、相手を配らないと解決できません。共通層は\n"
            "plugins/ndf/scripts/lib/ へ置いてください。解けない場合は起票し、この\n"
            "スクリプトの EXCEPTIONS へ番号とともに書いてください。",
            file=sys.stderr,
        )
    if stale:
        print("例外の一覧に、実体の無い項目があります（消してください）:", file=sys.stderr)
        for rel, name in stale:
            print(f"- {rel} -> {name}（{EXCEPTIONS[(rel, name)]}）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
