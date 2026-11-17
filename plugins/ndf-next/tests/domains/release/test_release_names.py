"""名前の書式（タグ・リリースブランチ・CHANGELOG の見出し）を作り、読み戻す（#3615 の決定 4）。"""

from __future__ import annotations

import pytest

from ndf.domains.release import names_decide

KEEP_A_CHANGELOG = "## [{version}] - {date}"


def test_formats_of_reads_the_keys_and_fills_defaults() -> None:
    got = names_decide.formats_of({"tag": "v{version}", "branch": "rel-{version}", "changelog_heading": KEEP_A_CHANGELOG})
    assert got == names_decide.NameFormats("v{version}", "rel-{version}", KEEP_A_CHANGELOG)
    assert names_decide.formats_of({"tag": None}) == names_decide.NameFormats(
        None, names_decide.DEFAULT_BRANCH, names_decide.DEFAULT_HEADING
    )


@pytest.mark.parametrize(
    ("fmt", "unit", "version", "want"),
    [
        ("v{version}", "app", "1.2.3", "v1.2.3"),
        ("{plugin}--v{version}", "ndf", "1.2.3", "ndf--v1.2.3"),
        ("v{version}", "app", "1.2.3-dev.1", "v1.2.3"),
    ],
)
def test_tag_names_round_trip(fmt: str, unit: str, version: str, want: str) -> None:
    """受け入れ条件 5・I3: タグの名前を作り、同じ書式で読み戻すと基底のバージョンになる。"""
    names = names_decide.NameFormats(fmt)
    assert names.tag_of(unit, version) == want
    assert names.version_of_tag(unit, want) == want.rsplit("v", 1)[-1]


@pytest.mark.parametrize("name", ["xv1.2.3", "ndf2--v1.2.3", "v1.2", "release-v1.2.3", "v1.2.3 "])
def test_names_that_do_not_fit_the_format_are_not_read(name: str) -> None:
    """I3: 書式の前後に文字が付いた名前・ほかの単位のタグは読まない。"""
    assert names_decide.NameFormats("v{version}").version_of_tag("ndf", name) is None
    assert names_decide.NameFormats("{plugin}--v{version}").version_of_tag("ndf", name) is None


def test_unit_names_with_regex_characters_are_escaped() -> None:
    names = names_decide.NameFormats("{plugin}@{version}")
    assert names.version_of_tag("a.b+c", "a.b+c@1.0.0") == "1.0.0"
    assert names.version_of_tag("a.b+c", "aXb+c@1.0.0") is None


def test_tag_format_null_makes_no_tags() -> None:
    names = names_decide.NameFormats(None)
    assert names.tag_of("app", "1.2.3") is None and names.version_of_tag("app", "v1.2.3") is None
    assert names_decide.highest_tag(names, "app", ["v1.2.3"]) is None


def test_release_branch_names() -> None:
    default = names_decide.NameFormats(None)
    assert default.branch_of("1.2.3") == "release/v1.2.3" and default.version_of_branch("release/v1.2.3") == "1.2.3"
    other = names_decide.NameFormats(None, branch="rel-{version}")
    assert other.branch_of("1.2.3") == "rel-1.2.3" and other.version_of_branch("rel-1.2.3") == "1.2.3"
    assert other.is_release_branch("rel-1.2.3") and not other.is_release_branch("release/v1.2.3")
    assert not default.is_release_branch("sprint/x")
    with pytest.raises(names_decide.FormatMismatch):
        other.version_of_branch("sprint/x")


def test_headings_and_keys() -> None:
    """受け入れ条件 6・7: 既定の書式は今の見出し、Keep a Changelog の書式はその形。キーは日付から後ろを除いた先頭。"""
    default = names_decide.NameFormats(None)
    assert default.heading_of("kit", "1.2.3", "2026-11-17") == "## [kit 1.2.3] - 2026-11-17"
    assert default.heading_key("kit", "1.2.3") == "## [kit 1.2.3]"
    keep = names_decide.NameFormats(None, changelog_heading=KEEP_A_CHANGELOG)
    assert keep.heading_of("app", "1.2.3", "2026-11-17") == "## [1.2.3] - 2026-11-17"
    assert keep.heading_key("app", "1.2.3") == "## [1.2.3]"
    no_date = names_decide.NameFormats(None, changelog_heading="## {version}")
    assert no_date.heading_key("app", "1.2.3") == "## 1.2.3"


def test_highest_tag_skips_suffixes_and_other_formats() -> None:
    names = names_decide.NameFormats("v{version}")
    tags = ["v1.2.2", "v1.10.0", "v2.0.0-dev.1", "app-1.0.0", "v9"]
    assert names_decide.highest_tag(names, "app", tags) == ("v1.10.0", "1.10.0")
    assert names_decide.highest_tag(names, "app", ["other"]) is None
