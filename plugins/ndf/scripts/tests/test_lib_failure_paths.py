"""`lib/junit.failure_texts`・`lib/failure_paths.mentioned`・`test_triage.classify` の `caused_output`（#1649 決定 2・3）。

落ちたテストの本文から、変更したファイルのパスを拾う手がかり 1 の部品を確かめる。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import failure_paths  # noqa: E402
import junit  # noqa: E402
import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402

# #1634 の形: 行数の上限の検査が、違反したファイルのパスを本文に出す。
LINES_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuite tests="2" failures="1" errors="1">
<testcase classname="tests.test_layout" file="tests/test_layout.py" name="test_no_file_exceeds_500_lines[claude.py]">
<failure message="AssertionError: plugins/ndf/scripts/supervise_lib/claude.py has 509 lines">tests/test_layout.py:12: in test_no
    assert n &lt;= 500</failure></testcase>
<testcase classname="tests.test_other" file="tests/test_other.py" name="test_ok"/>
<testcase classname="tests.test_err" file="/ci/root/tests/test_err.py" name="test_boom"><error message="boom">src/a.py:3: ImportError</error></testcase>
</testsuite>
"""
TRACKED = ["tests/test_layout.py", "tests/test_other.py", "tests/test_err.py"]


def test_failure_texts_are_keyed_by_test_id_and_carry_message_and_body():
    texts = junit.failure_texts(LINES_XML, TRACKED)
    key = "tests/test_layout.py::tests.test_layout::test_no_file_exceeds_500_lines[claude.py]"
    assert set(texts) == {key, "tests/test_err.py::tests.test_err::test_boom"}
    assert "supervise_lib/claude.py has 509 lines" in texts[key] and "assert n <= 500" in texts[key]
    assert "src/a.py:3" in texts["tests/test_err.py::tests.test_err::test_boom"]
    assert junit.failure_texts(b"<broken", TRACKED) is None


def test_long_failure_texts_keep_the_head_and_the_tail():
    body = "head-path.py " + "x" * 20000 + " tail-path.py"
    xml = f'<testsuite><testcase classname="c" file="tests/test_other.py" name="t"><failure>{body}</failure></testcase></testsuite>'
    text = junit.failure_texts(xml.encode(), TRACKED, limit=200)["tests/test_other.py::c::t"]
    assert len(text) < 300 and "head-path.py" in text and "tail-path.py" in text


def test_mentioned_finds_relative_and_absolute_paths_but_not_longer_names():
    text = "E  /work/repo/plugins/ndf/a.py:3 failed / see src/b.py. and src/c.pyc and xsrc/d.py and src/e.py_old"
    paths = ["plugins/ndf/a.py", "src/b.py", "src/c.py", "src/d.py", "src/e.py", "./src/b.py"]
    assert failure_paths.mentioned(text, paths) == ["plugins/ndf/a.py", "src/b.py"]


def test_mentioned_leaves_out_excluded_paths():
    text = "tests/test_layout.py:12: plugins/x.py"
    assert failure_paths.mentioned(text, ["tests/test_layout.py", "plugins/x.py"], exclude=["tests/test_layout.py"]) == ["plugins/x.py"]
    assert failure_paths.mentioned("", ["plugins/x.py"]) == []


def _repo(tmp_path):
    work = tmp_path / "work"
    (work / "tests").mkdir(parents=True)
    (work / "tests" / "test_b.py").write_text("", encoding="utf-8")
    (work / ".gitignore").write_text("out/\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "t@e.st"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "i"]):
        subprocess.run(["git", *args], cwd=work, check=True)
    return work


def test_classify_returns_the_rerun_text_of_each_caused_test(tmp_path):
    work = _repo(tmp_path)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()

    def run(command, cwd, timeout, log):
        failing = str(cwd) == str(work)
        out = pathlib.Path(cwd) / "out"
        out.mkdir(exist_ok=True)
        case = '<testcase classname="c" file="tests/test_b.py" name="t"><failure message="src/z.py broke">tb</failure></testcase>'
        (out / "junit.xml").write_text(f"<testsuite>{case if failing else ''}</testsuite>", encoding="utf-8")
        return (1 if failing else 0), False

    strategy = ts.Strategy("local-full", "test.strategy", [ts.Suite("py", "pytest -q", "pytest -q {paths}", junit="out/junit.xml", paths=["."])])
    out = test_triage.classify(
        work=str(work), strategy=strategy, failed=["tests/test_b.py::c::t"], fallback_reason=None, base_sha=base, timeout=30,
        log_dir=tmp_path / "logs", run=run,
    )
    assert out["caused"] == ["tests/test_b.py::c::t"]
    assert "src/z.py broke" in out["caused_output"]["tests/test_b.py::c::t"]
