#!/usr/bin/env python3
"""Skill とエージェントの本文に残る、10 系のスクリプトの呼び出しを数える（#3799）。

11.0.0 で `plugins/ndf/scripts/` は無くなり、処理は `bin/ndf` のサブコマンドへ移った。Skill は起動の先頭で
`$SCRIPTS` を空にする（`development-workflow/references/scripts-lookup.md`）ため、`"$SCRIPTS/pr-steps.py"` の
ような行は存在しないパスを打つ。11.0.0 はこの行を 217 行残したまま配られ、手順どおりに進めない Skill が
26 個あった。**増えたときに気づく手段が無かったため、同じことが起きないよう数を固定する。**

## 数えるもの

`plugins/ndf/skills/` と `plugins/ndf/agents/` の `.md`・`.sh`・`.py` の行のうち、次のどれかを含むもの。

| 形 | 例 |
| --- | --- |
| `$SCRIPTS/`・`${SCRIPTS}/` | `python3 "$SCRIPTS/pr-steps.py" plan` |
| `$NDF_SCRIPTS/`・`${NDF_SCRIPTS}/` | `python3 "$NDF_SCRIPTS/parallel.py" start p1` |
| `plugins/ndf/scripts/` | `python3 plugins/ndf/scripts/sprint-state.py init ...` |

`scripts-lookup.md` の「飛ばす」「止まる」の形で守った行（同じ行に `[ -n "${SCRIPTS:-}" ]` がある）は数えない。
スクリプトが無ければ理由を出して飛ばすか止まるため、存在しないパスを打たない。

## 一覧（`scripts/legacy-script-calls.json`）

残っている行の数をファイルごとに持つ。次のときに失敗する。

- 一覧に無いファイルに行がある・一覧の数より増えた: 新しい呼び出しを足さず、`ndf` のサブコマンドで書く
- 一覧の数より減った・行が無くなった: 一覧の数を今の数へ下げる（0 なら行を消す）。下げ忘れると、別の行が増えても気づかない

    python3 scripts/check-legacy-script-calls.py --root .
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCANNED = ("plugins/ndf/skills", "plugins/ndf/agents")
SUFFIXES = (".md", ".sh", ".py")
IGNORED_DIRS = {"__pycache__", ".pytest_cache", "tests"}
ALLOW = "scripts/legacy-script-calls.json"
LEGACY = re.compile(r"\$\{?(?:NDF_)?SCRIPTS\}?/|plugins/ndf/scripts/")
GUARD = '[ -n "${SCRIPTS:-}" ]'


def count_calls(root: Path) -> dict[str, int]:
    """ファイル（根からの相対パス）ごとの、守っていない呼び出しの行の数。0 のファイルは入れない。"""
    found: dict[str, int] = {}
    for rel in SCANNED:
        base = root / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file() or IGNORED_DIRS & set(path.relative_to(base).parts):
                continue
            n = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if LEGACY.search(line) and GUARD not in line)
            if n:
                found[path.relative_to(root).as_posix()] = n
    return found


def compare(found: dict[str, int], allowed: dict[str, int]) -> list[str]:
    """一覧との差を 1 件 1 行で返す。"""
    errors = []
    for rel in sorted(set(found) | set(allowed)):
        now, limit = found.get(rel, 0), allowed.get(rel, 0)
        if now > limit:
            errors.append(f"増えた: {rel} の 10 系のスクリプトの呼び出し {now} 行（一覧 {limit}）→ ndf のサブコマンドで書く")
        elif now < limit:
            hint = "一覧から行を消す" if now == 0 else f"一覧の数を {now} へ下げる"
            errors.append(f"減った: {rel} は {now} 行（一覧 {limit}）→ {ALLOW} で{hint}")
    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="リポジトリの根")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        data = json.loads((root / ALLOW).read_text(encoding="utf-8"))
        allowed = {str(k): int(v) for k, v in data["files"].items()}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"一覧を読めない: {ALLOW}（{exc}）", file=sys.stderr)
        return 2
    found = count_calls(root)
    errors = compare(found, allowed)
    for line in errors:
        print(line)
    print(f"10 系のスクリプトの呼び出し: {sum(found.values())} 行（{len(found)} ファイル）・一覧との差 {len(errors)} 件")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
