"""承認資料（CI の数え方・資料の節）と、利用者の設定の控え。"""

from __future__ import annotations

from ndf.domains.release import changes_decide, checks_decide, materials_decide, user_config_decide, version_decide


def test_issue_3169_skipped_checks_are_not_counted_as_failures() -> None:
    """#3169: 実行されなかったチェックは分母に入れず、内訳に出す。"""
    tally = checks_decide.tally([("a", "success")] * 23 + [("b", "skipped")])
    assert (tally.passed, tally.ran, tally.skipped, tally.green) == (23, 23, 1, True)
    assert tally.label() == "CI 23/23（スキップ 1）"


def test_checks_tally_separates_failed_and_pending() -> None:
    tally = checks_decide.tally([("a", "success"), ("b", "failure"), ("c", ""), ("d", "pending"), ("e", "cancelled")])
    assert tally.failed == ("b", "e") and tally.pending == ("c", "d") and not tally.green
    assert tally.label() == "CI 1/5"
    assert checks_decide.tally([]).label() == "CI 未実行"


def _facts(pulls: tuple[materials_decide.PullFact, ...], changes: tuple, instructions: tuple[str, ...] = ()) -> materials_decide.Facts:
    planned = (version_decide.Planned("kit", "1.2.3", "1.2.4"),)
    return materials_decide.Facts("production", planned, pulls, changes, instructions, "c" * 40)


def test_issue_2655_check_results_come_from_each_pull() -> None:
    """#2655: 検査の結果は PR ごとの CI の件数から書き、「無し」の 1 行にしない（並列リリースでも同じ）。"""
    pulls = (
        materials_decide.PullFact(5, "A", checks_decide.tally([("t", "success"), ("s", "skipped")])),
        materials_decide.PullFact(6, "B", checks_decide.tally([("t", "failure")])),
    )
    text = materials_decide.render(_facts(pulls, ()))
    assert "- #5 A（CI 1/1（スキップ 1））" in text
    assert "- #6 B（CI 0/1。通らなかったチェック: t）" in text
    assert "含む PR の CI: 通っていないものがある" in text
    assert materials_decide.items(_facts(pulls, ()))[0] == {
        "kind": "pull", "number": 5, "checks_passed": 1, "checks": 1, "checks_skipped": 1, "failed": [],
    }  # fmt: skip


def test_issue_2601_3110_material_sections() -> None:
    """#2601・#3110: 利用者向けの変化はファイルから、指示書と設定の変化は `.ndf/` も載せる。"""
    tests_only = tuple(changes_decide.changes(["kit/tests/test_a.py"], {"kit": "kit"}, changes_decide.DEFAULT_UNDISTRIBUTED))
    text = materials_decide.render(_facts((), tests_only, (".ndf/release.json",)))
    assert "利用者向けの変化なし（リリース対象のファイルの変化が 0 件）" in text
    assert "- `.ndf/release.json`" in text
    shipped = tuple(changes_decide.changes(["kit/a.py"], {"kit": "kit"}, ()))
    assert "- kit: リリース対象のファイル 1 件" in materials_decide.render(_facts((), shipped))


def test_issue_2896_managed_rows_are_not_user_config_changes() -> None:
    """#2896: Claude Code が書く管理の設定の行（scope: managed）は、利用者の設定の変化と数えない。"""
    before = {"version": 2, "plugins": {"kit@m": [{"scope": "user", "version": "1.0.0", "gitCommitSha": "0123456789"}]}}
    after = {
        "version": 2,
        "plugins": {
            "kit@m": [{"scope": "user", "version": "1.0.0", "gitCommitSha": "0123456789"}],
            "security-guidance@official": [{"scope": "managed", "version": "2.0.18"}],
        },
    }
    rows_before = user_config_decide.claude_plugins(before)
    rows_after = user_config_decide.claude_plugins(after)
    assert rows_before == {"claude:plugin:kit@m": "user:1.0.0@01234567"}
    assert user_config_decide.diff(rows_before, rows_after) == []
    changed = user_config_decide.claude_plugins({"plugins": {"kit@m": [{"scope": "user", "version": "1.0.1"}]}})
    assert user_config_decide.diff(rows_before, changed) == ["claude:plugin:kit@m: user:1.0.0@01234567 → user:1.0.1"]


def test_user_config_masks_credentials_in_sources() -> None:
    got = user_config_decide.claude_marketplaces({"m": {"source": {"url": "https://u:p@example.invalid/r.git"}}})
    assert "u:p@" not in got["claude:marketplace:m"] and "***@" in got["claude:marketplace:m"]
    codex = user_config_decide.codex_entries({"plugins": {"kit@m": {"enabled": True}}}, {"kit@m": ["1.0.0"]})
    assert codex == {"codex:plugin:kit@m": "enabled=true versions=1.0.0"}
