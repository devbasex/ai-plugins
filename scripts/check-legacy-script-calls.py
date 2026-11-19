#!/usr/bin/env python3
"""Skill とエージェントの本文に残る、10 系のスクリプトの呼び出しを数える（#3799）。

11.0.0 でプラグインの `scripts/` は無くなり、処理は `bin/ndf` のサブコマンドへ移った。Skill は起動の先頭で
`$SCRIPTS` を空にする（`development-workflow/references/scripts-lookup.md`）ため、`"$SCRIPTS/pr-steps.py"` の
ような行は存在しないパスを打つ。11.0.0 はこの行を 217 行残したまま配られ、手順どおりに進めない Skill が
26 個あった。**増えたときに気づく手段が無かったため、同じことが起きないよう数を固定する。**

## 数えるもの

`plugins/ndf/skills/` と `plugins/ndf/agents/` の `.md`・`.sh`・`.py` の行のうち、次のどれかを含むもの。

| 形 | 例 |
| --- | --- |
| `$SCRIPTS/`・`${SCRIPTS}/` | `python3 "$SCRIPTS/pr-steps.py" plan` |
| `$NDF_SCRIPTS/`・`${NDF_SCRIPTS}/` | `python3 "$NDF_SCRIPTS/parallel.py" start p1` |
| プラグインの `scripts/` の下のパス | `python3 <プラグイン>/scripts/sprint-state.py init ...` |
| プラグインの根（`$CLAUDE_PLUGIN_ROOT`）の `scripts/` の下のパス。同じ名前の実体が別の場所にあっても数える | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" check <名>` |
| プラグインの中に実体の無いスクリプト名（`<名前>.py`・`<名前>.sh`） | `` `parallel.py check` で検査する ``・`` `pr-review-steps.py` の collect `` |
| NDF の置き場から始まるパスで、実体の無いスクリプト名 | `python3 skills/skill-stats/scripts/skill-stats.py` |

最後の形は、`$SCRIPTS/` を付けずに名前だけを書いた行を拾う（11.0.0 にはこの形が 481 行あった）。実体があるかは、プラグインの
根（`plugins/ndf/`）とこのリポジトリの根の `scripts/` の下で git が追跡するファイル名で見る。利用者のリポジトリのファイルを例に書いた名前（`inference.py` など）は、一覧の
`examples` に理由と組で並べて数えない。

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
import subprocess
import sys
from pathlib import Path

SCANNED = ("plugins/ndf/skills", "plugins/ndf/agents")
SUFFIXES = (".md", ".sh", ".py")
IGNORED_DIRS = {"__pycache__", ".pytest_cache", "tests"}
ALLOW = "scripts/legacy-script-calls.json"
# プラグインの scripts/ の下のパス。根の検査（check-plugin-refs.py）がパスとして読まないよう、文字列を組み立てる
LEGACY = re.compile(r"\$\{?(?:NDF_)?SCRIPTS\}?/|\$\{?CLAUDE_PLUGIN_ROOT\}?/scripts/|plugins/ndf/(?:scripts)/")
PLUGIN = "plugins/ndf"
ROOT_SCRIPTS = "scripts"
SCRIPT_NAME = re.compile(r"(?<![\w/.$-])([A-Za-z][\w-]*\.(?:py|sh))\b")
# NDF 自身の置き場から始まるパス（`skills/skill-stats/scripts/skill-stats.py` など）。利用者のリポジトリの例のパス
# （`tests/test_auth.py`・`api/orders.py`）は NDF の置き場から始まらないため拾わない
NDF_PATH = re.compile(r"(?<![\w/.$-])(?:plugins/ndf/|skills/|scripts/|lib/|hooks/|bin/)(?:[\w.-]+/)*([A-Za-z][\w-]*\.(?:py|sh))\b")
GUARD = '[ -n "${SCRIPTS:-}" ]'


def _is_call(line: str, existing: set[str]) -> bool:
    if GUARD in line:
        return False
    names = SCRIPT_NAME.findall(line) + NDF_PATH.findall(line)
    return bool(LEGACY.search(line)) or any(name not in existing for name in names)


def _files(root: Path, rel: str) -> list[str]:
    """`rel` の下で git が追跡するファイル（根からの相対パス）。git の木でなければ、ディレクトリの中のファイルすべて。

    実体の有無を、追跡していないファイル（`.venv` の中のライブラリなど）で決めない。手元と CI で数が変わる。
    """
    got = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", rel], capture_output=True, text=True, check=False)
    if got.returncode == 0:
        return [f for f in got.stdout.split("\0") if f]
    base = root / rel
    return [p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()] if base.is_dir() else []


def count_calls(root: Path, examples: frozenset[str] = frozenset()) -> dict[str, int]:
    """ファイル（根からの相対パス）ごとの、守っていない呼び出しの行の数。0 のファイルは入れない。

    `examples` は、利用者のリポジトリのファイルを例に書いた名前（数えない）。
    """
    # 実体は、プラグインの中と、このリポジトリの根の scripts/（開発者向けの検査。AUTHORING.md などが指す）で git が追跡するもの
    tracked = _files(root, PLUGIN) + _files(root, ROOT_SCRIPTS)
    existing = {Path(rel).name for rel in tracked if rel.endswith((".py", ".sh"))} | set(examples)
    found: dict[str, int] = {}
    for rel in SCANNED:
        for name in sorted(_files(root, rel)):
            path = root / name
            if path.suffix not in SUFFIXES or not path.is_file() or IGNORED_DIRS & set(Path(name).parts):
                continue
            n = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if _is_call(line, existing))
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
        examples = frozenset(str(e["name"]) for e in data.get("examples", []))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"一覧を読めない: {ALLOW}（{exc}）", file=sys.stderr)
        return 2
    found = count_calls(root, examples)
    errors = compare(found, allowed)
    for line in errors:
        print(line)
    print(f"10 系のスクリプトの呼び出し: {sum(found.values())} 行（{len(found)} ファイル）・一覧との差 {len(errors)} 件")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
