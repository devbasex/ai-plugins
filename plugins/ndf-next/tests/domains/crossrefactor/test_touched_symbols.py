"""#622: --scope がファイル単位のため、課題と関係の無い関数まで整理する不具合の再現と直し。

課題が触った関数だけを提案の対象にし、同じファイルの別の関数への提案は範囲外として見送る（devbasex/devbase#191 の
cmd_scale の形）。
"""

from __future__ import annotations

import pytest

from ndf.domains.crossrefactor import diff_decide, scope, symbols_decide
from ndf.domains.crossrefactor.symbols_decide import OUT_OF_SCOPE, Touched

CLI = """import os


def cmd_scale(n):
    return n * 2


def cmd_status():
    return "ok"


class Runner:
    def start(self):
        return 1

    def stop(self):
        return 0
"""


def test_body_change_touches_only_that_function(gitwork):
    base = gitwork.make({"lib/cli.py": CLI})
    work = gitwork.work
    gitwork.commit({"lib/cli.py": CLI.replace("return n * 2", "return n * 3")})
    got = scope.touched(work, base, "HEAD", ["lib"])
    assert got.symbols == {"lib/cli.py": frozenset({"cmd_scale"})}


def test_proposal_to_another_function_in_the_same_file_is_out_of_scope(gitwork):
    # devbasex/devbase#191: 課題は cmd_scale だけを触ったのに、cmd_status まで整理していた
    base = gitwork.make({"lib/cli.py": CLI})
    work = gitwork.work
    gitwork.commit({"lib/cli.py": CLI.replace("return n * 2", "return n * 3")})
    touched = scope.touched(work, base, "HEAD", ["lib"])
    proposals = [
        {"path": "lib/cli.py", "symbol": "cmd_scale"},
        {"path": "lib/cli.py", "symbol": "cmd_status"},
    ]
    kept, skipped = symbols_decide.split_by_touched(proposals, touched)
    assert [p["symbol"] for p in kept] == ["cmd_scale"]
    assert skipped == [({"path": "lib/cli.py", "symbol": "cmd_status"}, OUT_OF_SCOPE)]


def test_method_names_match_with_and_without_the_class():
    source = CLI
    changes = {"lib/cli.py": diff_decide.FileChange("lib/cli.py", frozenset({14}))}
    touched = symbols_decide.touched_symbols(changes, {"lib/cli.py": source}, ("lib",))
    assert touched.symbols == {"lib/cli.py": frozenset({"Runner.start"})}
    assert symbols_decide.in_touched({"path": "lib/cli.py", "symbol": "Runner.start"}, touched)
    assert symbols_decide.in_touched({"path": "lib/cli.py", "symbol": "start"}, touched)
    assert symbols_decide.in_touched({"path": "lib/cli.py", "symbol": "Runner"}, touched)
    assert not symbols_decide.in_touched({"path": "lib/cli.py", "symbol": "Runner.stop"}, touched)


def test_import_only_change_gives_an_empty_set(gitwork):
    base = gitwork.make({"lib/cli.py": CLI})
    work = gitwork.work
    gitwork.commit({"lib/cli.py": CLI.replace("import os", "import os\nimport sys")})
    got = scope.touched(work, base, "HEAD", ["lib"])
    assert got.symbols == {"lib/cli.py": frozenset()}
    kept, skipped = symbols_decide.split_by_touched([{"path": "lib/cli.py", "symbol": "cmd_scale"}], got)
    assert kept == [] and len(skipped) == 1


def test_no_touched_symbols_makes_every_proposal_out_of_scope():
    kept, skipped = symbols_decide.split_by_touched([{"path": "a.py", "symbol": "f"}], Touched({}))
    assert kept == [] and skipped[0][1] == OUT_OF_SCOPE


def test_removed_lines_count_for_the_function_that_held_them(gitwork):
    base = gitwork.make({"lib/cli.py": CLI.replace("    return n * 2", "    n += 1\n    return n * 2")})
    work = gitwork.work
    gitwork.commit({"lib/cli.py": CLI})
    assert scope.touched(work, base, "HEAD", ["lib"]).symbols == {"lib/cli.py": frozenset({"cmd_scale"})}


def test_syntax_error_stops_without_falling_back_to_the_file(gitwork):
    base = gitwork.make({"lib/cli.py": CLI})
    work = gitwork.work
    gitwork.commit({"lib/cli.py": CLI + "\ndef broken(:\n"})
    with pytest.raises(scope.ScopeError, match="lib/cli.py"):
        scope.touched(work, base, "HEAD", ["lib"])


def test_files_outside_the_scope_and_other_languages(gitwork):
    base = gitwork.make({"lib/cli.py": CLI, "web/app.ts": "export const a = 1\n", "other/x.py": "def f():\n    return 1\n"})
    work = gitwork.work
    gitwork.commit({"web/app.ts": "export const a = 2\n", "other/x.py": "def f():\n    return 2\n"})
    got = scope.touched(work, base, "HEAD", ["lib", "web"])
    assert got.symbols == {} and got.whole_files == ("web/app.ts",)
    assert symbols_decide.in_touched({"path": "web/app.ts", "symbol": "a"}, got)


def test_head_must_be_the_work_tree_head(gitwork):
    base = gitwork.make({"lib/cli.py": CLI})
    work = gitwork.work
    gitwork.commit({"lib/cli.py": CLI.replace("return 1", "return 2")})
    with pytest.raises(scope.ScopeError, match="HEAD でない"):
        scope.touched(work, "HEAD~1", base, ["lib"])


def test_explicit_symbols():
    got = symbols_decide.explicit(["lib/cli.py:cmd_scale", "lib/cli.py:Runner.stop"])
    assert got.symbols == {"lib/cli.py": frozenset({"cmd_scale", "Runner.stop"})}
    with pytest.raises(ValueError, match="<ファイル>:<名前>"):
        symbols_decide.explicit(["lib/cli.py"])


# 現状固定: drive が状態ファイルの touched（Touched.as_json の形）から Touched を組み直す振る舞い
def _restored(saved):
    from types import SimpleNamespace

    from ndf.domains.crossrefactor import drive

    return drive._touched(SimpleNamespace(state={"touched": saved}))  # type: ignore[arg-type]


def test_touched_restored_from_the_state_round_trips():
    original = Touched({"lib/cli.py": frozenset({"cmd_scale", "Runner.start"}), "lib/b.py": frozenset()}, ("web/app.ts",))
    got = _restored(original.as_json())
    assert got == original
    assert got.symbols == {"lib/b.py": frozenset(), "lib/cli.py": frozenset({"Runner.start", "cmd_scale"})}
    assert isinstance(got.symbols["lib/cli.py"], frozenset)
    assert got.whole_files == ("web/app.ts",) and isinstance(got.whole_files, tuple)
    assert symbols_decide.in_touched({"path": "lib/cli.py", "symbol": "start"}, got)
    assert symbols_decide.in_touched({"path": "web/app.ts", "symbol": "a"}, got)


def test_touched_restored_from_an_empty_state():
    got = _restored({"symbols": {}, "whole_files": []})
    assert got == Touched({}, ())


def test_touched_restored_keeps_the_saved_order_of_files():
    got = _restored({"symbols": {"z.py": ["b", "a"], "a.py": ["f"]}, "whole_files": ["y.ts", "x.ts"]})
    assert list(got.symbols) == ["z.py", "a.py"]
    assert got.symbols["z.py"] == frozenset({"a", "b"})
    assert got.whole_files == ("y.ts", "x.ts")


def test_touched_restored_requires_both_keys():
    with pytest.raises(KeyError):
        _restored({"symbols": {}})
    with pytest.raises(KeyError):
        _restored({"whole_files": []})


def test_touched_from_json_round_trips_as_json():
    original = Touched({"lib/cli.py": frozenset({"cmd_scale", "Runner.start"}), "lib/b.py": frozenset()}, ("web/app.ts",))
    assert Touched.from_json(original.as_json()) == original
