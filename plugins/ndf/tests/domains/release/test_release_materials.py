"""承認資料（CI の数え方・資料のセクション）と、利用者の設定のスナップショット。"""

from __future__ import annotations

from ndf.domains.release import changes_decide, checks_decide, forms_decide, materials_decide, user_config_decide, version_decide


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


def test_only_the_latest_run_of_a_check_is_counted() -> None:
    """同じ名前の check run が複数あれば、最後に始まった結論だけを数える（取り消した古い実行を失敗と数えない）。"""
    got = checks_decide.latest_per_name(
        [
            ("approval-check", "cancelled", "2026-11-18T21:00:00Z"),
            ("pytest", "success", "2026-11-18T21:01:00Z"),
            ("approval-check", "success", "2026-11-18T21:05:00Z"),
            ("ndf/release-approval", "success", ""),
        ]
    )
    assert got == [("approval-check", "success"), ("pytest", "success"), ("ndf/release-approval", "success")]
    assert checks_decide.tally(got).green
    # 後から始まった実行が落ちていれば、前に通っていても落ちたと数える
    assert checks_decide.latest_per_name([("t", "success", "2026-11-18T21:00:00Z"), ("t", "failure", "2026-11-18T21:09:00Z")]) == [
        ("t", "failure")
    ]


SINCE = "d" * 40


def _facts(
    pulls: tuple[materials_decide.PullFact, ...],
    changes: tuple,
    instructions: tuple[str, ...] = (),
    form: str = "package-plugin",
    versioned: bool = True,
) -> materials_decide.Facts:
    planned = (version_decide.Planned("kit", "1.2.3", "1.2.4", SINCE),) if versioned else ()
    return materials_decide.Facts("production", planned, pulls, changes, instructions, "c" * 40, forms_decide.form_of(form), versioned)


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
    # #4446: 実行されたチェックの無い PR は通ったと数えず、含む PR が 0 件ならそう書く
    unrun = (materials_decide.PullFact(7, "C", checks_decide.tally([])),)
    assert "- 含む PR の CI: 実行されていない PR がある（#7）" in materials_decide.pull_sections(unrun)
    assert "- 含む PR の CI: 対象の PR は無い" in materials_decide.pull_sections(())


def test_issue_2601_3110_material_sections() -> None:
    """#2601・#3110: 利用者向けの変化はファイルから、指示書と設定の変化は `.ndf/` も載せる。"""
    tests_only = tuple(changes_decide.changes({"kit": ["kit/tests/test_a.py"]}, {"kit": "kit"}, ()))
    text = materials_decide.render(_facts((), tests_only, (".ndf/release.json",)))
    assert "利用者向けの変化なし（リリース対象のファイルの変化が 0 件）" in text
    assert "- `.ndf/release.json`" in text
    shipped = tuple(changes_decide.changes({"kit": ["kit/a.py"]}, {"kit": "kit"}, ()))
    assert "- kit: リリース対象のファイル 1 件" in materials_decide.render(_facts((), shipped))


def test_issue_3615_materials_show_reach_versions_and_since() -> None:
    """承認資料に届き方の行と、単位ごとの「前 → 次」と範囲の起点が出る。"""
    text = materials_decide.render(_facts((), ()))
    assert forms_decide.reach_line(forms_decide.form_of("package-plugin")) in text
    assert f"- kit: 1.2.3 → 1.2.4（範囲の起点 {SINCE[:12]}）" in text


def test_issue_3615_materials_without_a_version_have_no_version_section() -> None:
    """バージョンを持たない設定（保存先 none）では「バージョン」のセクションを出さず、届き方は出す。"""
    text = materials_decide.render(_facts((), (), form="document", versioned=False))
    assert "## バージョン" not in text and "## 届き方" in text and "置き先のシステム" in text


def test_issue_3615_materials_show_the_deliveries_of_the_channel() -> None:
    """届き方に、設定の届け先のうちそのチャネルの行（`channel` が null の行を含む）を出す。"""
    rows = [
        {"target": "App Store", "kind": "manual", "trigger": "審査へ提出する", "channel": "production"},
        {"target": "TestFlight", "kind": "auto", "trigger": "CD が配る", "channel": "base"},
        {"target": "社内の共有フォルダ", "kind": "manual", "trigger": "PDF を置く"},
    ]
    production = forms_decide.deliveries_of(rows, "production")
    assert [d.target for d in production] == ["App Store", "社内の共有フォルダ"]
    assert [d.target for d in forms_decide.deliveries_of(rows, "dev")] == ["TestFlight", "社内の共有フォルダ"]
    text = materials_decide.render(_facts((), (), form="mobile")._replace(deliveries=production))
    assert "- App Store（手動）: 審査へ提出する" in text and "TestFlight" not in text


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


def test_codex_entries_current_behavior() -> None:
    """現状固定（codex_entries）: 取得元は設定の順に 3 つのキーを写し、プラグインは設定と保存先の和を名前の順に並べる。"""
    conf = {
        "marketplaces": {
            "m": {"source_type": "git", "source": "https://u:p@example.invalid/r.git", "ref": "main", "extra": 1},
            "bare": "notdict",
            "partial": {"source": "x"},
        },
        "plugins": {"b@m": {"enabled": False}, "a@m": {}, "c@m": "notdict", "z@m": {"enabled": "Yes"}},
    }
    cached = {"a@m": ["2.0.0", "1.0.0"], "d@m": [], "e@m": ["1.0.0"]}
    got = user_config_decide.codex_entries(conf, cached)
    assert list(got.items()) == [
        ("codex:marketplace:m", '{"ref": "main", "source": "https://***@example.invalid/r.git", "source_type": "git"}'),
        ("codex:marketplace:bare", '{"ref": null, "source": null, "source_type": null}'),
        ("codex:marketplace:partial", '{"ref": null, "source": "x", "source_type": null}'),
        ("codex:plugin:a@m", "enabled=- versions=1.0.0,2.0.0"),
        ("codex:plugin:b@m", "enabled=false versions="),
        ("codex:plugin:c@m", "enabled=- versions="),
        ("codex:plugin:d@m", "enabled=- versions="),
        ("codex:plugin:e@m", "enabled=- versions=1.0.0"),
        ("codex:plugin:z@m", "enabled=yes versions="),
    ]


def test_codex_entries_ignores_tables_of_wrong_shape() -> None:
    """現状固定（codex_entries）: 空の設定は空、表でない marketplaces・plugins は無いものとして扱う。"""
    assert user_config_decide.codex_entries({}, {}) == {}
    got = user_config_decide.codex_entries({"marketplaces": [], "plugins": "x"}, {"k@m": ["1"]})
    assert got == {"codex:plugin:k@m": "enabled=- versions=1"}
