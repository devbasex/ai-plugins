"""リリースの判断の関数（バージョン・変化・CHANGELOG・含む PR・同期の手）。"""

from __future__ import annotations

import pytest

from ndf.domains.release import changelog_decide, changes_decide, included_decide, names_decide, sync_decide, version_decide

# ai-plugins の設定のタグの書式（リリースブランチと見出しは既定）
PLUGIN_NAMES = names_decide.NameFormats("{plugin}--v{version}")


@pytest.mark.parametrize(
    ("current", "level", "channel", "want"),
    [
        ("1.2.3", "patch", "production", "1.2.4"),
        ("1.2.3", "minor", "production", "1.3.0"),
        ("1.2.3", "major", "production", "2.0.0"),
        ("1.3.0-dev.2", "patch", "production", "1.3.0"),
        ("1.2.3", "minor", "dev", "1.3.0-dev.1"),
        ("1.3.0-dev.2", "minor", "dev", "1.3.0-dev.3"),
    ],
)
def test_next_version_by_channel(current: str, level: str, channel: str, want: str) -> None:
    assert version_decide.next_version(current, level, channel) == want


@pytest.mark.parametrize(
    ("planned", "shipped", "level", "channel", "want"),
    [
        ("1.2.4", None, "patch", "production", "1.2.4"),
        ("1.2.4", "1.2.3", "patch", "production", "1.2.4"),
        ("1.2.4", "1.2.4", "patch", "production", "1.2.5"),
        ("1.2.4", "1.2.6", "minor", "production", "1.3.0"),
        ("1.2.4-dev.3", "1.2.4", "patch", "dev", "1.2.5-dev.1"),
        ("1.2.5-dev.1", "1.2.4", "patch", "dev", "1.2.5-dev.1"),
    ],
)
def test_issue_2554_next_version_clears_shipped(planned: str, shipped: str | None, level: str, channel: str, want: str) -> None:
    """#2554: 出し済みのバージョンより大きくない次のバージョンは、出し済みのバージョンを同じ幅で上げたバージョンへ繰り上げる。"""
    assert version_decide.clear_of(planned, shipped, level, channel) == want


def test_issue_2554_tag_versions_are_read_per_plugin() -> None:
    assert PLUGIN_NAMES.version_of_tag("kit", "kit--v1.2.4") == "1.2.4"
    assert PLUGIN_NAMES.version_of_tag("kit", "kit2--v1.2.4") is None
    assert PLUGIN_NAMES.version_of_tag("kit", "kit--vnext") is None
    assert version_decide.highest(["1.2.4", "1.10.0-dev.1", "1.3.0"]) == "1.10.0"
    assert version_decide.highest([]) is None


def test_version_names_follow_one_rule() -> None:
    """名前の書式の設定が無いときの今の形（受け入れ条件 7）。"""
    assert PLUGIN_NAMES.branch_of("1.2.4") == "release/v1.2.4"
    assert PLUGIN_NAMES.version_of_branch("release/v1.2.4-dev.1") == "1.2.4-dev.1"
    assert PLUGIN_NAMES.tag_of("kit", "1.2.4-dev.1") == "kit--v1.2.4"
    with pytest.raises(version_decide.VersionError):
        PLUGIN_NAMES.version_of_branch("feat/x")
    with pytest.raises(version_decide.VersionError):
        version_decide.next_version("1.2.3", "huge", "production")


def test_issue_1756_plugin_dirs_come_from_the_caller_not_from_names() -> None:
    """#1756: 保存先はマーケットプレイスの登録から渡り、`plugins/<名前>` の形でなくても変化を拾う。"""
    dirs = {"alpha": "tools/a", "beta": "plugins/mcp/b"}
    paths = ["tools/a/x.py", "plugins/mcp/b/README.md", "docs/x.md"]
    got = changes_decide.changes({name: paths for name in dirs}, dirs, ())
    assert [(c.plugin, c.distributed) for c in got] == [("alpha", ("tools/a/x.py",)), ("beta", ("plugins/mcp/b/README.md",))]


@pytest.mark.parametrize(("flag", "want"), [(False, False), (True, True)])
def test_issue_2217_tests_only_change_bumps_only_when_declared(flag: bool, want: bool) -> None:
    """#2217: テストだけの変化は、設定 bump_undistributed が真のときだけバージョンを上げる。"""
    change = changes_decide.split(["p/tests/test_a.py", "p/lib/conftest.py"], "p", "p", ())
    assert change.distributed == () and len(change.undistributed) == 2
    assert changes_decide.needs_bump(change, flag) is want
    assert changes_decide.needs_bump(changes_decide.split(["p/a.py"], "p", "p", ()), False) is True


def test_issue_2601_user_facing_change_is_decided_by_files() -> None:
    """#2601: 利用者向けの変化はリリース対象のファイルの変化だけで決まる（PR 本文を読まない）。"""
    only_tests = changes_decide.changes({"p": ["p/tests/test_a.py"]}, {"p": "p"}, ())
    shipped = changes_decide.changes({"p": ["p/skills/a.md"]}, {"p": "p"}, ())
    assert changes_decide.distribution_changed(only_tests) is False
    assert changes_decide.distribution_changed(shipped) is True


def test_issue_3615_tests_of_other_languages_are_undistributed_by_the_shared_table() -> None:
    """受け入れ条件 8: 既定はテストのファイルの表から作り、TypeScript・jest・PHP のテストだけの変化はリリース対象に数えない。"""
    paths = ["src/foo.test.ts", "src/__tests__/bar.ts", "tests/Unit/FooTest.php"]
    change = changes_decide.split(paths, "web", "", ())
    assert change.distributed == () and set(change.undistributed) == set(paths)
    assert changes_decide.needs_bump(change, False) is False
    assert "conftest.py" in changes_decide.default_undistributed()


def test_issue_3615_declared_undistributed_is_added_to_the_default() -> None:
    """受け入れ条件 8: 設定の undistributed は既定へ足す（既定を置き換えない）。"""
    change = changes_decide.split(["docs/a.md", "tests/test_a.py", "src/a.ts"], "web", "", ("docs/",))
    assert change.distributed == ("src/a.ts",) and change.undistributed == ("docs/a.md", "tests/test_a.py")


def test_issue_3110_instruction_changes_include_ndf_settings() -> None:
    """#3110: 指示書の変化に `.ndf/` の設定の変化が載る。"""
    got = changes_decide.instruction_changes(["CLAUDE.md", ".ndf/release.json", "src/a.py", "docs/CLAUDE.md"])
    assert got == [".ndf/release.json", "CLAUDE.md"]


def test_changelog_insert_replaces_only_the_same_section() -> None:
    text = "# 変更履歴\n\n前書き\n\n## [kit 1.2.3] - 2026-01-01\n\n- 古い\n\n## [kit 1.2.2] - 2025-12-01\n\n- 前\n"
    names = PLUGIN_NAMES
    heading = names.heading_of("kit", "1.2.4", "2026-02-01")
    new = changelog_decide.block(heading, [changelog_decide.Entry(9, "B"), changelog_decide.Entry(3, "A")])
    added = changelog_decide.insert(text, names.heading_key("kit", "1.2.4"), new)
    assert added.index("## [kit 1.2.4]") < added.index("## [kit 1.2.3]") and "- A（#3）\n- B（#9）" in added
    again = changelog_decide.insert(
        added, names.heading_key("kit", "1.2.4"), changelog_decide.block(names.heading_of("kit", "1.2.4", "2026-02-02"), [])
    )
    assert again.count("## [kit 1.2.4]") == 1 and "- 古い" in again and "- 前" in again and "- A（#3）" not in again
    assert changelog_decide.notes(added, names.heading_key("kit", "1.2.3")) == "- 古い"
    assert changelog_decide.notes(added, names.heading_key("kit", "9.9.9")) is None


def test_issue_3615_keep_a_changelog_heading() -> None:
    """受け入れ条件 6: Keep a Changelog の見出しの書式で、その形のセクションを足し、前のセクションを読める。"""
    names = names_decide.NameFormats("v{version}", changelog_heading="## [{version}] - {date}")
    text = "# Changelog\n\n## [1.2.2] - 2026-01-01\n\n### Fixed\n\n- 前\n"
    new = changelog_decide.block(names.heading_of("app", "1.2.3", "2026-11-17"), [changelog_decide.Entry(5, "直した")])
    added = changelog_decide.insert(text, names.heading_key("app", "1.2.3"), new)
    assert "## [1.2.3] - 2026-11-17\n\n- 直した（#5）" in added and added.index("## [1.2.3]") < added.index("## [1.2.2]")
    assert changelog_decide.notes(added, names.heading_key("app", "1.2.2")) == "### Fixed\n\n- 前"


def test_included_numbers_skip_release_branches() -> None:
    subjects = [
        "Merge pull request #12 from o/feat/x",
        "Merge pull request #13 from o/release/v1.2.3",
        "直した (#14)",
        "ふつうのコミット",
        "Merge pull request #12 from o/feat/x",
        "Merge pull request #15 from o/main",
    ]
    is_release = PLUGIN_NAMES.is_release_branch
    assert included_decide.numbers(subjects, (), is_release) == [12, 14, 15]
    # 本番ブランチから戻す PR（head は本番ブランチ）は含めない
    assert included_decide.numbers(subjects, ("main",), is_release) == [12, 14]
    # リリースブランチの書式を変えれば、その書式のブランチからのマージを除く
    other = names_decide.NameFormats(None, branch="rel-{version}").is_release_branch
    assert included_decide.numbers(["Merge pull request #16 from o/rel-1.2.3", *subjects], ("main",), other) == [12, 13, 14]


@pytest.mark.parametrize(
    ("subject", "want"),
    [
        ("Merge sprint/a into release/v1.2.3", True),
        ("Merge origin/sprint/a into release/v1.2.3", True),
        ("Merge sprint/a into feat/x", False),
        ("Merge pull request #3 from o/sprint/a", False),
    ],
)
def test_issue_3615_unit_merges_are_read_with_the_branch_format(subject: str, want: bool) -> None:
    assert included_decide.is_unit_merge(subject, PLUGIN_NAMES.is_release_branch) is want


@pytest.mark.parametrize(
    ("current", "dirty", "incoming", "landed", "want"),
    [
        ("main", ["a"], ["a"], [], (sync_decide.UPDATE_REF, ())),  # #2140: 本番ブランチにいても止まらない
        (None, [], ["a"], [], (sync_decide.UPDATE_REF, ())),
        ("develop", [], ["a"], [], (sync_decide.FAST_FORWARD, ())),
        ("develop", ["b"], ["a"], [], (sync_decide.FAST_FORWARD, ())),
        ("develop", ["plugin.json"], ["plugin.json"], ["plugin.json"], (sync_decide.RESTORE_THEN_FF, ("plugin.json",))),  # #3108
        ("develop", ["a", "b"], ["a", "b"], ["a"], (sync_decide.STOP, ("b",))),
    ],
)
def test_issue_2140_3108_sync_plan(current: str | None, dirty: list[str], incoming: list[str], landed: list[str], want: tuple) -> None:
    assert tuple(sync_decide.plan(current, "develop", dirty, incoming, landed)) == want
