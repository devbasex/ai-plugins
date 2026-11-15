"""JUnit の読み取り（`ndf.testing.junit`）。見本の XML の出所は `junit_samples/README.md` にある。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from ndf.testing import junit

SAMPLES = Path(__file__).parent / "junit_samples"
TRACKED = frozenset(
    {
        "web/src/sum.test.ts",
        "web/src/sum.jest.test.js",
        "e2e/login.spec.ts",
        "tests/test_calc.py",
        "tests/Unit/UserTest.php",
        "README.md",
    }
)


def _sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


@pytest.mark.parametrize(
    ("sample", "nodes"),
    [
        ("vitest.xml", ["web/src/sum.test.ts::sum > breaks"]),
        ("jest-file.xml", ["web/src/sum.jest.test.js::sum breaks"]),
        ("phpunit.xml", ["tests/Unit/UserTest.php::test_rejects_duplicate"]),
        ("pytest-xunit1.xml", ["tests/test_calc.py::test_breaks", "tests/test_calc.py::test_zero"]),
        ("playwright.xml", ["e2e/login.spec.ts::login › rejects bad password"]),
    ],
)
def test_each_runner_sample_returns_only_the_failed_tests(sample: str, nodes: list[str]) -> None:
    # 受け入れ条件 1・3: 通ったテストと <skipped> は入らず、<failure>・<error> だけが <ファイル>::<名前> で返る
    got = junit.junit_failures(_sample(sample), TRACKED)
    assert list(got.nodes) == nodes
    assert got.unresolved == ()


@pytest.mark.parametrize("sample", ["jest-default.xml", "pytest-xunit2.xml"])
def test_cases_without_a_file_attribute_are_reported_not_listed(sample: str) -> None:
    # 受け入れ条件 4: ファイルを決められない testcase は一覧へ入れず、名前と試した属性を理由に出す
    got = junit.junit_failures(_sample(sample), TRACKED)
    assert got.nodes == ()
    assert got.unresolved
    assert all("試した属性" in r and "testcase の file" in r for r in got.unresolved)
    assert any("breaks" in r for r in got.unresolved)


def test_absolute_path_resolves_to_the_longest_tracked_suffix() -> None:
    # 受け入れ条件 3: 絶対パスの末尾に当たる追跡するファイルのうち、最も長いものを選ぶ
    tracked = frozenset({"UserTest.php", "tests/Unit/UserTest.php"})
    assert junit.resolve_file(["/home/runner/work/app/app/tests/Unit/UserTest.php"], tracked) == "tests/Unit/UserTest.php"


def test_resolve_file_normalizes_and_skips_ambiguous_candidates() -> None:
    tracked = frozenset({"a/x.spec.ts", "b/x.spec.ts", "e2e/only.spec.ts", "web/y.test.ts"})
    assert junit.resolve_file([".\\web\\y.test.ts"], tracked) == "web/y.test.ts"
    # 末尾の照合で 2 つ当たる候補は決めずに次の候補へ
    assert junit.resolve_file(["x.spec.ts", "only.spec.ts"], tracked) == "e2e/only.spec.ts"
    assert junit.resolve_file(["x.spec.ts", "", "Suite title"], tracked) is None


def test_split_xmls_are_merged_without_duplicates_in_order() -> None:
    # 受け入れ条件 5（読み取りの側）: 2 本の XML の一覧は、組み立てで重ねずに昇順へ並ぶ
    first = (
        b'<testsuite><testcase file="b.py" name="t2"><failure/></testcase><testcase file="a.py" name="t1"><error/></testcase></testsuite>'
    )
    second = b'<testsuites><testsuite><testsuite file="b.py"><testcase name="t2"><failure/></testcase></testsuite></testsuite></testsuites>'
    tracked = frozenset({"a.py", "b.py"})
    merged = sorted({n for xml in (first, second) for n in junit.junit_failures(xml, tracked).nodes})
    assert merged == ["a.py::t1", "b.py::t2"]
    assert list(junit.junit_failures(first, tracked).nodes) == ["a.py::t1", "b.py::t2"]


def test_skipped_only_and_passing_cases_are_not_failures() -> None:
    xml = b'<testsuite><testcase file="a.py" name="s"><skipped/></testcase><testcase file="a.py" name="ok"/></testsuite>'
    assert junit.junit_failures(xml, frozenset({"a.py"})) == junit.JunitFailures((), ())


def test_broken_xml_raises() -> None:
    with pytest.raises(junit.JunitError):
        junit.junit_failures(b"<testsuite><testcase", TRACKED)


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_xml_members_returns_only_xml_in_name_order() -> None:
    archive = _zip({"b.XML": b"<b/>", "a.xml": b"<a/>", "log.txt": b"x"})
    assert junit.xml_members(archive) == [b"<a/>", b"<b/>"]
    assert junit.xml_members(_zip({"log.txt": b"x"})) == []


def test_xml_members_limits_each_and_total_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(junit, "MAX_XML_BYTES", 4)
    monkeypatch.setattr(junit, "MAX_EXTRACT_BYTES", 8)
    assert junit.xml_members(_zip({"a.xml": b"1234", "b.xml": b"5678"})) == [b"1234", b"5678"]
    with pytest.raises(junit.JunitError):
        junit.xml_members(_zip({"a.xml": b"12345"}))
    with pytest.raises(junit.JunitError):
        junit.xml_members(_zip({"a.xml": b"1234", "b.xml": b"5678", "c.xml": b"9"}))


def test_xml_members_does_not_trust_an_understated_file_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # 宣言した file_size より中身が大きい ZIP は、上限を超えて展開せずに断る
    monkeypatch.setattr(junit, "MAX_XML_BYTES", 16)
    archive = bytearray(_zip({"a.xml": b"<a>" + b"x" * 64 + b"</a>"}))
    small = (8).to_bytes(4, "little")
    # 展開後の大きさ: ローカルヘッダーの 22 バイト目と、中央ディレクトリの項目の 24 バイト目
    central = archive.index(b"PK\x01\x02")
    archive[22:26] = small
    archive[central + 24 : central + 28] = small
    with pytest.raises(junit.JunitError):
        junit.xml_members(bytes(archive))


def test_broken_zip_raises() -> None:
    with pytest.raises(junit.JunitError):
        junit.xml_members(b"not a zip")
