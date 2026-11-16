#!/usr/bin/env python3
"""CI の pytest の JUnit XML から、テストのファイルごとの実測時間を集めて `pytest-shard-durations.json` へ書く。

    gh run download <pytest.yml の run> -D <置き場>
    python3 scripts/pytest-shard-durations.py <置き場>

根の `conftest.py` がこの値でファイルをジョブへ割り振る（重いファイルから順に、いちばん空いているジョブへ）。
値が古くても割り当ては成り立ち、偏りが戻るだけである。偏ってきたら打ち直す。
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "pytest-shard-durations.json"


def file_of(classname: str) -> str | None:
    """`a.b.test_x.TestY` を、リポジトリにある `a/b/test_x.py` へ戻す。"""
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        rel = "/".join(parts[:n]) + ".py"
        if (ROOT / rel).is_file():
            return rel
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        sys.stderr.write(__doc__)
        return 2
    seconds: Counter[str] = Counter()
    for xml in sorted(Path(argv[0]).rglob("*.xml")):
        for case in ET.parse(xml).getroot().iter("testcase"):
            rel = file_of(case.get("classname", ""))
            if rel:
                seconds[rel] += float(case.get("time") or 0)
    if not seconds:
        sys.stderr.write(f"{argv[0]} に JUnit XML の項目が無い\n")
        return 1
    body = {
        "summary": "テストのファイルごとの実測秒（CI の JUnit XML の合計）。根の conftest.py がジョブの割り当てに使う。"
        "作り直しは scripts/pytest-shard-durations.py",
        "seconds": {k: round(v, 1) for k, v in sorted(seconds.items())},
    }
    OUT.write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(seconds)} ファイル・合計 {sum(seconds.values()):.0f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
