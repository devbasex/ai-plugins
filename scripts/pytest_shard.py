#!/usr/bin/env python3
"""CI の pytest をファイル単位でジョブへ分ける割り当て。根の conftest.py（今の NDF）と ndf-next.yml が使う。

    python3 scripts/pytest_shard.py seconds <JUnit XML の置き場>      # scripts/pytest-shard-seconds.json を作り直す
    pytest --collect-only -q | python3 scripts/pytest_shard.py files --total 6 --index 0 [--seconds <JSON>]

`files` は収集した項目の id（`<ファイル>::<名前>`）を標準入力から読み、そのジョブが受け持つファイルを 1 行ずつ出す。
重さは `--seconds` の実測秒（無いファイルは中央値）、指定が無ければファイルの項目数で数える。
値が古くても割り当ては成り立ち、偏りが戻るだけである。偏ってきたら `seconds` を打ち直す。
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECONDS = ROOT / "scripts" / "pytest-shard-seconds.json"


def assign_files(files: list[str], total: int, weight: dict[str, float]) -> dict[str, int]:
    """重いファイルから順に、いちばん空いているジョブ（同じなら番号の小さい方）へ割り当てる。重さの無いファイルは中央値で数える。"""
    known = sorted(weight[f] for f in files if f in weight)
    default = known[len(known) // 2] if known else 1.0
    load = [0.0] * total
    owner = {}
    for f in sorted(files, key=lambda f: (-weight.get(f, default), f)):
        i = min(range(total), key=lambda j: (load[j], j))
        owner[f] = i
        load[i] += weight.get(f, default)
    return owner


def read_seconds(path: Path = SECONDS) -> dict[str, float]:
    """ファイルごとの実測秒。読めなければ空（重さは中央値の既定に倒れる）。"""
    try:
        return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8"))["seconds"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def _file_of(classname: str) -> str | None:
    """`a.b.test_x.TestY` を、リポジトリにある `a/b/test_x.py` へ戻す。"""
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        rel = "/".join(parts[:n]) + ".py"
        if (ROOT / rel).is_file():
            return rel
    return None


def _seconds(a) -> int:
    seconds: Counter[str] = Counter()
    for xml in sorted(Path(a.junit).rglob("*.xml")):
        for case in ET.parse(xml).getroot().iter("testcase"):
            rel = _file_of(case.get("classname", ""))
            if rel:
                seconds[rel] += float(case.get("time") or 0)
    if not seconds:
        sys.stderr.write(f"{a.junit} に JUnit XML の項目が無い\n")
        return 1
    body = {
        "summary": "テストのファイルごとの実測秒（CI の JUnit XML の合計）。根の conftest.py がジョブの割り当てに使う。"
        "作り直しは scripts/pytest_shard.py seconds",
        "seconds": {k: round(v, 1) for k, v in sorted(seconds.items())},
    }
    SECONDS.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{SECONDS.relative_to(ROOT)}: {len(seconds)} ファイル・合計 {sum(seconds.values()):.0f} 秒")
    return 0


def _files(a) -> int:
    counts = Counter(line.split("::", 1)[0] for line in sys.stdin.read().splitlines() if "::" in line)
    weight = read_seconds(Path(a.seconds)) if a.seconds else dict(counts)
    owner = assign_files(sorted(counts), a.total, weight)
    for f in sorted(f for f, i in owner.items() if i == a.index):
        print(f)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("seconds")
    s.add_argument("junit")
    f = sub.add_parser("files")
    f.add_argument("--total", type=int, required=True)
    f.add_argument("--index", type=int, required=True)
    f.add_argument("--seconds")
    a = p.parse_args(argv)
    return _seconds(a) if a.verb == "seconds" else _files(a)


if __name__ == "__main__":
    sys.exit(main())
