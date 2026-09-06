"""スモークの assertion がパイプで判定しないことを固定する（#417 の 8 の外・#354 の続き）。

**`grep -q` は最初の一致で終わる。** 書く側がまだ書いている間にパイプが閉じ、SIGPIPE で
死ぬ。`set -o pipefail` のもとでは**一致しているのにパイプライン全体が 141 を返す**。
走査するファイルが増えるほど当たりやすく、配布 Skill を 5 個足した時点で継続的統合が
落ちた（手元では再現しなかった）。

**判定はパイプの右で行わない。** 結果を変数で受けてから照合する。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASSERTIONS = REPO / "tests" / "runtime-smoke" / "assertions"

# パイプの右で早く終わる判定。`grep -q` と `head` がこれに当たる。
EARLY_EXIT_ON_THE_RIGHT = re.compile(r"\|\s*(grep\b[^|]*\s-\w*q|head\b)")


def offending_lines() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for script in sorted(ASSERTIONS.glob("*.sh")):
        for number, line in enumerate(
            script.read_text(encoding="utf-8").splitlines(), 1
        ):
            if line.lstrip().startswith("#"):
                continue
            if EARLY_EXIT_ON_THE_RIGHT.search(line):
                found.append((script.name, number, line.strip()))
    return found


def test_the_assertions_exist() -> None:
    """走査対象が 0 件のまま通ると、検査が働いていないことに気づけない。"""
    assert sorted(ASSERTIONS.glob("*.sh"))


def test_no_assertion_decides_on_the_right_of_a_pipe() -> None:
    assert offending_lines() == []
