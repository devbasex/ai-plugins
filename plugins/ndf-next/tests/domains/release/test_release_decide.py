"""リリースの判断の関数（版・変化・CHANGELOG・含む PR・同期の手）。"""

from __future__ import annotations

import pytest

from ndf.domains.release import changelog_decide, changes_decide, included_decide, sync_decide, version_decide


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


def test_version_names_follow_one_rule() -> None:
    assert version_decide.branch_of("1.2.4") == "release/v1.2.4"
    assert version_decide.version_of_branch("release/v1.2.4-dev.1") == "1.2.4-dev.1"
    assert version_decide.tag_of("kit", "1.2.4-dev.1") == "kit--v1.2.4"
    with pytest.raises(version_decide.VersionError):
        version_decide.version_of_branch("feat/x")
    with pytest.raises(version_decide.VersionError):
        version_decide.next_version("1.2.3", "huge", "production")


def test_issue_1756_plugin_dirs_come_from_the_caller_not_from_names() -> None:
    """#1756: 置き場はマーケットプレイスの登録から渡り、`plugins/<名前>` の形でなくても変化を拾う。"""
    dirs = {"alpha": "tools/a", "beta": "plugins/mcp/b"}
    got = changes_decide.changes(["tools/a/x.py", "plugins/mcp/b/README.md", "docs/x.md"], dirs, changes_decide.DEFAULT_UNDISTRIBUTED)
    assert [(c.plugin, c.distributed) for c in got] == [("alpha", ("tools/a/x.py",)), ("beta", ("plugins/mcp/b/README.md",))]


@pytest.mark.parametrize(("flag", "want"), [(False, False), (True, True)])
def test_issue_2217_tests_only_change_bumps_only_when_declared(flag: bool, want: bool) -> None:
    """#2217: テストだけの変化は、宣言 bump_undistributed が真のときだけ版を上げる。"""
    change = changes_decide.split(["p/tests/test_a.py", "p/lib/conftest.py"], "p", "p", changes_decide.DEFAULT_UNDISTRIBUTED)
    assert change.distributed == () and len(change.undistributed) == 2
    assert changes_decide.needs_bump(change, flag) is want
    assert changes_decide.needs_bump(changes_decide.split(["p/a.py"], "p", "p", ()), False) is True


def test_issue_2601_user_facing_change_is_decided_by_files() -> None:
    """#2601: 利用者向けの変化はリリース対象のファイルの変化だけで決まる（PR 本文を読まない）。"""
    only_tests = changes_decide.changes(["p/tests/test_a.py"], {"p": "p"}, changes_decide.DEFAULT_UNDISTRIBUTED)
    shipped = changes_decide.changes(["p/skills/a.md"], {"p": "p"}, changes_decide.DEFAULT_UNDISTRIBUTED)
    assert changes_decide.distribution_changed(only_tests) is False
    assert changes_decide.distribution_changed(shipped) is True


def test_issue_3110_instruction_changes_include_declarations() -> None:
    """#3110: 指示書の変化に `.ndf/` の設定の変化が載る。"""
    got = changes_decide.instruction_changes(["CLAUDE.md", ".ndf/release.json", "src/a.py", "docs/CLAUDE.md"])
    assert got == [".ndf/release.json", "CLAUDE.md"]


def test_changelog_insert_replaces_only_the_same_section() -> None:
    text = "# 変更履歴\n\n前書き\n\n## [kit 1.2.3] - 2026-01-01\n\n- 古い\n\n## [kit 1.2.2] - 2025-12-01\n\n- 前\n"
    new = changelog_decide.block("kit", "1.2.4", "2026-02-01", [changelog_decide.Entry(9, "B"), changelog_decide.Entry(3, "A")])
    added = changelog_decide.insert(text, "kit", "1.2.4", new)
    assert added.index("## [kit 1.2.4]") < added.index("## [kit 1.2.3]") and "- A（#3）\n- B（#9）" in added
    again = changelog_decide.insert(added, "kit", "1.2.4", changelog_decide.block("kit", "1.2.4", "2026-02-02", []))
    assert again.count("## [kit 1.2.4]") == 1 and "- 古い" in again and "- 前" in again and "- A（#3）" not in again
    assert changelog_decide.notes(added, "kit", "1.2.3") == "- 古い"
    assert changelog_decide.notes(added, "kit", "9.9.9") is None


def test_included_numbers_skip_release_branches() -> None:
    subjects = [
        "Merge pull request #12 from o/feat/x",
        "Merge pull request #13 from o/release/v1.2.3",
        "直した (#14)",
        "ふつうのコミット",
        "Merge pull request #12 from o/feat/x",
    ]
    assert included_decide.numbers(subjects) == [12, 14]


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
