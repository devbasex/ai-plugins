from __future__ import annotations

import textwrap

from ndf.checks import lint
from ndf.checks.model import Source


def sources(plugin, files: dict[str, str]) -> list[Source]:
    return [Source(plugin.write(rel, textwrap.dedent(text)), rel) for rel, text in files.items()]


def test_ruff_rules_use_thresholds_from_rules(plugin, rules):
    srcs = sources(
        plugin,
        {
            "src/ndf/a.py": """
            def f(a, b, c, d, e, g, h):
                try:
                    x()
                except Exception:  # noqa: BLE001 ファイル: 印は ruff に読ませない
                    pass

            class C:
                def m(self, a, b, c, d, e, g):
                    pass
        """,
            "src/ndf/b.py": """
            def deep(a):
                if a:
                    for b in a:
                        with b:
                            while b:
                                pass
        """,
        },
    )
    hits = lint.scan(srcs, rules)
    assert sorted(hits[srcs[0].path.resolve()]) == [(2, "PLR0913"), (5, "BLE001"), (5, "S110")]
    assert hits[srcs[1].path.resolve()] == [(3, "PLR1702")]


def test_no_sources_does_not_start_ruff(rules):
    assert lint.scan([], rules) == {}


def test_except_rules_land_on_the_except_line(plugin, rules):
    srcs = sources(
        plugin,
        {
            "src/ndf/a.py": """
            def f():
                try:
                    x()
                except (
                    Exception,
                ):
                    recover()
                try:
                    x()
                except:
                    recover()
        """,
        },
    )
    assert sorted(lint.scan(srcs, rules)[srcs[0].path.resolve()]) == [(5, "BLE001"), (11, "E722")]
