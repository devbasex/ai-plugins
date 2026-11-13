from __future__ import annotations

import ast
import textwrap

from ndf.checks import smells

CALLS = frozenset({"run"})
MODULES = frozenset({"subprocess", "github"})


def tree(src: str) -> ast.Module:
    return ast.parse(textwrap.dedent(src))


def codes(src: str, rules: dict) -> list[str]:
    hits, _ = smells.scan(tree(src), rules["smells"])
    return sorted(code for _, code in hits)


def test_loop_call_is_counted_once_at_innermost_loop():
    t = tree("""
        for a in xs:
            for b in a:
                subprocess.run(b)
                github.issue(b)
    """)
    assert smells.loop_calls(t, CALLS, MODULES) == [3]


def test_loop_iter_and_nested_function_are_not_counted():
    t = tree("""
        for a in subprocess.run(x):
            def f():
                github.issue(a)
            g = lambda: run(a)
        y = [github.pr(n) for n in subprocess.run(z)]
    """)
    assert smells.loop_calls(t, CALLS, MODULES) == [6]


def test_receiver_that_is_not_a_name_is_not_external():
    t = tree("""
        for a in xs:
            a.run()
            self.git.run()
    """)
    assert smells.loop_calls(t, CALLS, MODULES) == []


def test_elif_chain_counts_from_head_only():
    src = "if a:\n    pass\n" + "".join(f"elif b{i}:\n    pass\n" for i in range(4))
    assert smells.elif_chains(tree(src), 4) == [1]
    assert smells.elif_chains(tree(src), 5) == []


def test_except_rules(rules):
    src = """
        try:
            x()
        except Exception:
            pass
        try:
            x()
        except OSError:
            return None
        try:
            x()
        except BaseException:
            raise
    """
    hits, caught = smells.handlers(tree(src))
    assert sorted(hits) == [(4, "BLE001"), (4, "S110"), (8, "NDF401")]
    assert caught[8] == ("OSError",)


def test_bare_except_catches_base_exception():
    _, caught = smells.handlers(tree("try:\n    x()\nexcept:\n    raise\n"))
    assert caught == {3: ("BaseException",)}


def test_fallbacks_and_suppress():
    t = tree("""
        a = x or {}
        b = x or "d"
        c = d.get("k", 1)
        e = d.get("k")
        f = os.environ.get("K", "")
        with contextlib.suppress(OSError):
            pass
    """)
    hits = smells.calls_and_fallbacks(t, frozenset({"os.environ"}))
    assert sorted(hits) == [(2, "NDF501"), (4, "NDF502"), (7, "NDF402")]


def test_function_size_rules(rules):
    long_body = "\n".join(f"    x{i} = {i}" for i in range(61))
    many_args = "def g(self, a, b, c, d, e, f):\n    pass\n"
    too_many = "def h(a, b, c, d, e, f, g):\n    pass\n"
    assert codes(f"def f():\n{long_body}\n", rules) == ["NDF701"]
    assert codes(many_args, rules) == []
    assert codes(too_many, rules) == ["NDF702"]


def test_branches_and_nesting():
    flat = tree("def f(x):\n" + "".join(f"    if x == {i}:\n        pass\n" for i in range(10)))
    assert smells.branches(flat.body[0]) == 10
    deep = tree("""
        def f(x):
            if x:
                for y in x:
                    while y:
                        with y:
                            pass
    """)
    assert smells.nesting(deep.body[0]) == 4
    chain = tree("def f(x):\n    if x:\n        pass\n    elif x:\n        if x:\n            pass\n")
    assert smells.nesting(chain.body[0]) == 2
