"""副命令 `ndf refactor`（touched・tests・collected）の結果 JSON と止まり方。

- I9: テストの枠の待ちの諦めとメモリの停止は、落ちたテストと別の止まった理由で結果 JSON を出す
"""

from __future__ import annotations

import json
import sys

from ndf.contracts import causes, result
from ndf.domains.crossrefactor import cli, collect
from ndf.testing import slots

SOURCE = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
FILES = {
    "lib/foo.py": SOURCE,
    "tests/test_foo.py": "from lib import foo\n\n\ndef test_f():\n    assert foo.f() == 1\n",
    "tests/foo_helpers.py": "def test_hidden():\n    assert True\n",
}


def run(capsys, *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out.strip().splitlines()[-1]
    got = json.loads(out)
    assert result.errors(got, code) == []
    return code, got


def test_scope_reports_touched_functions_and_scope_test_files(gitwork, capsys):
    base = gitwork.make(FILES)
    gitwork.commit({"lib/foo.py": SOURCE.replace("return 1", "return 3")})
    code, got = run(capsys, "touched", "--base", base, "--scope", "lib", "tests", "--work", str(gitwork.work))
    assert code == 0 and got["status"] == "ok"
    assert got["items"][0]["symbols"] == {"lib/foo.py": ["f"]}
    assert got["items"][1]["files"] == ["tests/foo_helpers.py", "tests/test_foo.py"]


def test_scope_with_explicit_symbols_skips_the_diff(gitwork, capsys):
    gitwork.make(FILES)
    code, got = run(capsys, "touched", "--base", "nope", "--scope", "lib", "--symbols", "lib/foo.py:g", "--work", str(gitwork.work))
    assert code == 0 and got["items"][0]["symbols"] == {"lib/foo.py": ["g"]}


def test_scope_stops_on_an_unreadable_file(gitwork, capsys):
    base = gitwork.make(FILES)
    gitwork.commit({"lib/foo.py": SOURCE + "def broken(:\n"})
    code, got = run(capsys, "touched", "--base", base, "--scope", "lib", "--work", str(gitwork.work))
    assert got["cause"]["kind"] == "invalid" and code == causes.exit_code("invalid")


def test_tests_picks_the_importing_test(gitwork, capsys):
    gitwork.make(FILES)
    code, got = run(capsys, "tests", "--changed", "lib/foo.py", "--work", str(gitwork.work))
    assert code == 0 and got["items"][0]["files"] == ["tests/test_foo.py"]


def test_collected_stops_when_an_added_test_is_not_collected(gitwork, capsys):
    project = {
        "test": {
            "suites": [
                {
                    "name": "pytest",
                    "runner": "pytest",
                    "command": "pytest tests",
                    "collect_command": f"{sys.executable} -m pytest --collect-only -q -p no:cacheprovider tests",
                    "paths": ["tests"],
                }
            ]
        }
    }
    gitwork.make({**FILES, ".ndf/project.json": json.dumps(project)})
    code, got = run(capsys, "collected", "--paths", "tests/foo_helpers.py", "tests/test_foo.py", "--work", str(gitwork.work))
    assert got["cause"]["kind"] == "test_failed" and code == causes.exit_code("test_failed")
    assert got["items"][0]["uncollected"] == ["tests/foo_helpers.py"]


def test_collected_without_a_declaration_is_undecided(gitwork, capsys):
    gitwork.make(FILES)
    code, got = run(capsys, "collected", "--paths", "tests/foo_helpers.py", "--work", str(gitwork.work))
    assert code == 0 and got["items"][0]["undecided"] == ["tests/foo_helpers.py"]


def test_collected_that_gives_up_waiting_for_a_test_slot_stops_with_its_cause(gitwork, capsys, monkeypatch):
    gitwork.make(FILES)

    def gave_up(*_args, **_kw):
        raise slots.Stopped(slots.GAVE_UP, 30.0, 100)

    monkeypatch.setattr(collect, "collected", gave_up)
    code, got = run(capsys, "collected", "--paths", "tests/test_foo.py", "--timeout", "5", "--work", str(gitwork.work))
    cause = slots.CAUSE_OF[slots.GAVE_UP]
    assert got["cause"]["kind"] == cause and code == causes.exit_code(cause) != causes.exit_code("test_failed")


def test_bad_arguments_exit_with_the_invalid_code(capsys):
    assert cli.main(["touched"]) == causes.exit_code("invalid")
