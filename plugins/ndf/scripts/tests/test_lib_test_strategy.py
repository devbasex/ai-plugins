"""`lib/test_strategy.py` — 戦略の解決・`{paths}` の置き換え・時間の上限（#1334 AC1・AC2・AC4・AC5・AC9・決定 2・8・14）。"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import test_strategy as ts  # noqa: E402

CARMO = {
    "version": 1,
    "test": {
        "strategy": "local-scoped-ci-whole",
        "ci": {"check": "test-results", "junit_artifacts": "junit-*"},
        "suites": [
            {
                "name": "phpunit",
                "runner": "phpunit",
                "command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml",
                "scope_command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml {paths}",
                "junit": "build/ndf/junit.xml",
                "container": {"service": "app"},
                "paths": ["tests"],
            }
        ],
    },
    "test_duration": {"measured": [{"seconds": 3827.0, "source": "ci-junit", "detail": "run"}]},
    "ci": {
        "provider": "github-actions",
        "workflows": [{"path": ".github/workflows/test-results.yml", "jobs": 24, "wall_seconds": 360.0}],
        "required_checks": ["test-results", "codex-review"],
    },
}

AI_PLUGINS = {
    "version": 1,
    "test": {
        "suites": [
            {
                "name": "pytest",
                "runner": "pytest",
                "command": "uv run --frozen --project . --all-extras pytest . -q -n 4",
                "scope_command": "uv run --frozen --project . --all-extras pytest {paths} -q -n 4",
                "paths": ["."],
            }
        ]
    },
    "test_duration": {"measured": [{"seconds": 424.0, "source": "ci-steps", "detail": "run"}]},
    "ci": {"provider": "github-actions", "workflows": [{"path": ".github/workflows/pytest.yml", "jobs": 4, "wall_seconds": 296.0}]},
}


# ---------- 決定 2: 解き方の表 ----------


def test_the_declared_strategy_wins(tmp_path):
    s = ts.resolve(CARMO)
    assert (s.name, s.source) == ("local-scoped-ci-whole", "test.strategy")
    assert s.ci == {"check": "test-results", "checks": ["test-results"], "junit_artifacts": "junit-*"}
    assert s.suites[0].junit == "build/ndf/junit.xml" and s.whole_on_ci


def test_ai_plugins_derives_local_full_from_the_duration():
    """AC2 — 宣言に `test.strategy` が無ければ所要から導く。ai-plugins（424 秒 ≤ 600）は local-full。"""
    s = ts.resolve(AI_PLUGINS)
    assert (s.name, s.source) == ("local-full", "derived:test_duration")
    words = shlex.split(ts.fill(s.suites[0].scope_command, ["plugins/ndf/scripts/tests/test_x.py"]))
    assert words == [
        "uv",
        "run",
        "--frozen",
        "--project",
        ".",
        "--all-extras",
        "pytest",
        "plugins/ndf/scripts/tests/test_x.py",
        "-q",
        "-n",
        "4",
    ]


def test_a_long_suite_with_a_readable_ci_derives_ci_whole():
    decl = json.loads(json.dumps(CARMO))
    del decl["test"]["strategy"]
    assert ts.propose(decl) == ("local-scoped-ci-whole", "derived:test_duration")
    del decl["ci"]
    assert ts.propose(decl) == ("local-full", "derived:test_duration")


def test_the_duration_source_order_is_record_then_junit_then_steps():
    decl = {
        "test_duration": {
            "measured": [
                {"seconds": 3.0, "source": "ci-steps"},
                {"seconds": 2.0, "source": "ci-junit"},
                {"seconds": 1.0, "source": "ndf-record"},
            ]
        }
    }
    assert ts.whole_seconds(decl) == (1.0, "ndf-record")
    decl["test_duration"]["measured"].pop()
    assert ts.whole_seconds(decl) == (2.0, "ci-junit")


def test_suites_without_a_scope_template_derive_round_only():
    decl = {"test": {"suites": [{"name": "make", "runner": "make", "command": "make test"}]}}
    s = ts.resolve(decl)
    assert (s.name, s.source, s.round_command) == ("round-only", "derived:test.suites", None)
    assert s.whole_commands() == ["make test"] and s.round_commands() == ["make test"]


def test_arguments_come_before_the_declaration():
    """前提 3 — `{paths}` を含む引数は雛形、含まなければそのまま走らせるコマンド。"""
    s = ts.resolve(CARMO, round_test="make test-unit")
    assert (s.name, s.source, s.round_command) == ("round-only", "args", "make test-unit")
    assert s.whole_commands() == [CARMO["test"]["suites"][0]["command"]], "全体テストは宣言の suite の command"
    assert ts.resolve({}, round_test="make test-unit").whole_commands() == ["make test-unit"], "宣言も --baseline-test も無ければ兼ねる"
    s = ts.resolve(AI_PLUGINS, round_test="pytest {paths} -q")
    assert (s.name, s.source) == ("local-full", "args")
    assert [x.scope_command for x in s.scoped_suites()] == ["pytest {paths} -q"]
    assert s.whole_commands() == [AI_PLUGINS["test"]["suites"][0]["command"]], "全体テストは宣言の command（#1483 I11）"
    assert ts.resolve({}, round_test="pytest {paths} -q").whole_commands() == ["pytest . -q"]
    s = ts.resolve({}, baseline_test="pytest -q")
    assert (s.name, s.round_command) == ("round-only", "pytest -q") and s.notes


def test_a_declared_ci_whole_strategy_keeps_its_name_with_a_template_argument():
    s = ts.resolve(CARMO, baseline_test="docker compose exec -T app ./vendor/bin/phpunit {paths}")
    assert s.name == "local-scoped-ci-whole" and s.source == "args" and s.ci["check"] == "test-results"


def test_missing_or_unknown_test_stops_without_defaults():
    """AC4・I3 — 宣言に `test` が無い・不明で引数も無ければ、ai-plugins の値で埋めずに止める。"""
    with pytest.raises(ts.StrategyError) as e:
        ts.resolve({})
    assert ".ndf/project.json の test" in str(e.value) and "--round-test" in str(e.value)
    with pytest.raises(ts.StrategyError) as e:
        ts.resolve({"test": {"unknown": "指示書に走らせ方が無い"}})
    assert "指示書に走らせ方が無い" in str(e.value)
    s = ts.resolve({"test": {"unknown": "x"}}, round_test="make test")
    assert s.name == "round-only"


def test_a_ci_whole_strategy_needs_a_scope_template():
    decl = {"test": {"strategy": "local-scoped-ci-whole", "suites": [{"name": "x", "runner": "x", "command": "make test"}]}}
    with pytest.raises(ts.StrategyError) as e:
        ts.resolve(decl)
    assert "scope_command" in str(e.value)


# ---------- I1・AC5: 前置きを見ない ----------


@pytest.mark.parametrize("prefix", ["", "env X=1 ", "uv run --frozen ", "docker compose exec -T app "])
def test_the_prefix_changes_neither_the_strategy_nor_the_targets(prefix):
    decl = json.loads(json.dumps(AI_PLUGINS))
    decl["test"]["suites"][0]["scope_command"] = f"{prefix}pytest {{paths}} -q"
    s = ts.resolve(decl)
    assert (s.name, s.source) == ("local-full", "derived:test_duration")
    words = shlex.split(ts.fill(s.suites[0].scope_command, ["tests/a.py", "tests/b.py"]))
    assert words[-3:] == ["tests/a.py", "tests/b.py", "-q"]
    assert words[: len(words) - 3] == prefix.split() + ["pytest"]


# ---------- 決定 14: `{paths}` は 1 語 ----------


@pytest.mark.parametrize(
    "template, fragment",
    [
        ("pytest -q", "{paths} が無い"),
        ("phpunit --filter={paths}", "1 語"),
        ('pytest "{paths}', "語に分けられない"),
    ],
)
def test_a_bad_template_is_rejected(template, fragment):
    assert fragment in ts.template_problem(template, "test.suites[0].scope_command")
    decl = {"test": {"suites": [{"name": "x", "runner": "x", "command": "x", "scope_command": template}]}}
    with pytest.raises(ts.StrategyError):
        ts.resolve(decl)
    if "{paths}" in template:
        # 引数の雛形も同じ規則で止める。`{paths}` の無い引数は雛形でなく、そのまま走らせるコマンド（round-only）。
        with pytest.raises(ts.StrategyError):
            ts.resolve({}, round_test=template)
    else:
        assert ts.resolve({}, round_test=template).name == "round-only"


def test_the_whole_command_of_a_template_puts_a_dot_at_paths():
    assert ts.whole_command_of("pytest {paths} -q") == "pytest . -q"


def test_suite_for_picks_the_longest_matching_paths():
    a = ts.Suite("a", "a", "a {paths}", paths=["tests"])
    b = ts.Suite("b", "b", "b {paths}", paths=["tests/e2e"])
    s = ts.Strategy("local-full", "x", [a, b])
    assert ts.suite_for(s, "tests/e2e/x.py") is b
    assert ts.suite_for(s, "tests/unit/x.py") is a
    assert ts.suite_for(s, "src/x.py") is None


# ---------- 決定 8・AC9: 時間の上限 ----------


def test_carmo_limits_with_a_30_minute_budget():
    """AC9 — 全体テストを CI に任せる戦略は着手前の上限を 0.10·B にし、CI の待ちは max(3·c, 0.05·B)。"""
    s = ts.resolve(CARMO)
    w, source = ts.whole_seconds(CARMO)
    c = ts.ci_wall_seconds(CARMO, "test-results")
    got = ts.limits(s, 30, whole_seconds_value=w, whole_source=source, ci_seconds=c, measured_seconds=12.0)
    assert got["init_test_timeout"] == 180
    assert got["test_timeout"] == 36  # 3 × 12
    assert got["ci_wait_timeout"] == 1080  # 3 × 360
    assert got["basis"] == {
        "budget_minutes": 30,
        "w": 3827.0,
        "w_source": "ci-junit",
        "c": 360.0,
        "x": 12.0,
        "strategy": "local-scoped-ci-whole",
        "unknown_duration": False,
    }
    assert ts.reserve_seconds(s, w, c, True) == (0.0, 360.0), "バッファに w を入れない"


def test_local_full_limits_grow_with_the_declared_duration():
    s = ts.resolve(AI_PLUGINS)
    got = ts.limits(s, 30, whole_seconds_value=424.0, whole_source="ci-steps", ci_seconds=296.0)
    assert got["init_test_timeout"] == 1272  # max(180, 3 × 424)
    assert got["whole_timeout"] == 1272
    assert got["test_timeout"] == 1272  # x が無ければ着手前の上限
    assert got["ci_wait_timeout"] == 888
    assert ts.reserve_seconds(s, 424.0, 296.0, False) == (424.0, 424.0)
    assert ts.reserve_seconds(s, 424.0, 296.0, True) == (424.0, 296.0)


def test_limits_without_a_budget_use_three_times_the_duration_or_the_unknown_values():
    """supervise は予算を持たない。所要があれば 3·w と 3·c、無ければ「所要が不明のときの値」。"""
    s = ts.resolve(CARMO)
    got = ts.limits(s, None, whole_seconds_value=3827.0, ci_seconds=360.0)
    assert (got["test_timeout"], got["whole_timeout"], got["ci_wait_timeout"]) == (11481, 11481, 1080)
    assert got["basis"]["unknown_duration"] is False
    got = ts.limits(ts.resolve(AI_PLUGINS), None)
    assert (got["test_timeout"], got["whole_timeout"], got["ci_wait_timeout"]) == (900, 1800, 3600)
    assert got["basis"]["unknown_duration"] is True


def test_ci_wall_seconds_prefers_the_named_check_then_the_largest():
    assert ts.ci_wall_seconds(CARMO, "test-results") == 360.0
    decl = {"ci": {"workflows": [{"path": "a.yml", "wall_seconds": 10.0}, {"path": "b.yml", "wall_seconds": 90.0}]}}
    assert ts.ci_wall_seconds(decl, "zzz") == 90.0
    assert ts.ci_wall_seconds({"ci": {"unknown": "x"}}, None) is None


# ---------- 決定 3: 宣言の読み取りと supervise.json の移行 ----------


def test_decl_of_reads_project_json_and_falls_back_to_supervise_json(tmp_path):
    (tmp_path / ".ndf").mkdir()
    decl, note = ts.decl_of(tmp_path, {"test": {"command": "pytest {paths} -q", "all": "."}})
    assert note and "supervise.json" in note
    assert decl["test"]["suites"][0]["scope_command"] == "pytest {paths} -q"
    assert decl["test"]["suites"][0]["command"] == "pytest . -q"
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps(AI_PLUGINS), encoding="utf-8")
    decl, note = ts.decl_of(tmp_path, {"test": {"command": "ignored {paths}"}})
    assert note is None and decl["test"]["suites"][0]["name"] == "pytest"


def test_state_round_trip_keeps_the_resolved_strategy():
    s = ts.resolve(CARMO)
    again = ts.Strategy.from_state(json.loads(json.dumps(s.as_state())))
    assert again.as_state() == s.as_state()


# ---------- #1483: suite の種別・全体テスト・範囲テスト・起動の失敗 ----------

LINT_ONLY = {
    "test": {
        "suites": [{"name": "sc", "runner": "shellcheck", "kind": "lint", "scope_command": "shellcheck -s bash {paths}", "paths": ["*.sh"]}]
    }
}


def _without_kind(state: dict) -> dict:
    return {**state, "suites": [{k: v for k, v in s.items() if k != "kind"} for s in state["suites"]]}


def test_a_declaration_without_kind_resolves_as_before():
    """AC3・I3 — 種別を書かない宣言の戦略・全体テスト・範囲テストが変わらない。"""
    for decl in (AI_PLUGINS, CARMO):
        s = ts.resolve(decl)
        suite = decl["test"]["suites"][0]
        state = _without_kind(s.as_state())
        assert state["suites"][0] == {k: suite.get(k) for k in ("name", "command", "scope_command", "junit")} | {
            "paths": suite.get("paths") or []
        }
        assert all(x.kind == "test" for x in s.suites)
        assert s.whole_commands() == [suite["command"]]
        runs = ts.scope_runs(s, ["tests/a.py"], ["x.sh"])
        assert [shlex.split(r.command) for r in runs] == [
            [w if w != "{paths}" else "tests/a.py" for w in shlex.split(suite["scope_command"])]
        ]


def test_the_kind_survives_the_state_and_an_old_state_reads_as_test():
    """AC4 — `as_state` に種別が残り、種別の無い状態を `from_state` で読むとテスト。"""
    s = ts.resolve(LINT_ONLY)
    assert s.as_state()["suites"][0]["kind"] == "lint"
    assert ts.Strategy.from_state(s.as_state()).suites[0].kind == "lint"
    old = _without_kind(ts.resolve(AI_PLUGINS).as_state())
    assert ts.Strategy.from_state(old).suites[0].kind == "test"


def test_a_lint_only_strategy_never_puts_a_dot_at_paths():
    """AC5・I4 — 静的解析の suite だけで `command` が無ければ、全体テストに `{paths}` を `.` にしたものが入らない。"""
    s = ts.resolve(LINT_ONLY)
    assert "shellcheck -s bash ." not in s.whole_commands()
    s = ts.resolve({}, baseline_test="shellcheck -s bash {paths}", template_kind="lint")
    assert s.whole_commands() == [] and ts.NO_LINT_WHOLE in s.notes


def test_the_declared_command_wins_over_the_argument_template():
    """AC6・I11 — 引数の雛形と宣言の `command` の両方があれば、全体テストは宣言の `command`。"""
    decl = {"test": {"suites": [{"name": "sc", "runner": "sc", "kind": "lint", "command": "bash scripts/check-lint.sh"}]}}
    s = ts.resolve(decl, baseline_test="shellcheck {paths}", template_kind="lint", scope_paths=["a.sh"])
    assert s.whole_commands("lint") == ["bash scripts/check-lint.sh"]
    s = ts.resolve(AI_PLUGINS, baseline_test="pytest {paths}")
    assert s.whole_commands("test") == [AI_PLUGINS["test"]["suites"][0]["command"]]


def test_a_lint_template_fills_paths_with_the_scope():
    """AC7 — 静的解析の雛形は `{paths}` を範囲のパスで埋める。範囲が無ければ全体テストは無く、`notes` に理由。"""
    s = ts.resolve({}, baseline_test="shellcheck -s bash {paths}", template_kind="lint", scope_paths=["scripts/a.sh", "b c.sh"])
    assert s.whole_commands() == ["shellcheck -s bash scripts/a.sh 'b c.sh'"]
    assert s.notes == []
    s = ts.resolve({}, baseline_test="shellcheck -s bash {paths}", template_kind="lint")
    assert s.whole_commands() == [] and s.notes == [ts.NO_LINT_WHOLE]


def test_scope_runs_give_targets_to_tests_and_changed_files_to_lint():
    """AC8・I10 — テストの suite には対象、静的解析の suite には変更したファイルのうち `paths` に当たるもの。"""
    decl = {
        "test": {
            "suites": [
                {"name": "py", "runner": "pytest", "command": "pytest .", "scope_command": "pytest {paths}"},
                {
                    "name": "fmt",
                    "runner": "ruff",
                    "kind": "lint",
                    "command": "ruff format --check .",
                    "scope_command": "ruff format --check {paths}",
                    "paths": ["*.py"],
                },
                {"name": "sc", "runner": "sc", "kind": "lint", "scope_command": "shellcheck {paths}", "paths": ["scripts"]},
            ]
        }
    }
    s = ts.resolve(decl)
    runs = ts.scope_runs(s, ["tests/test_a.py"], ["src/a.py", "scripts/x.sh", "README.md"])
    assert [(r.suite, r.kind, r.command) for r in runs] == [
        ("py", "test", "pytest tests/test_a.py"),
        ("fmt", "lint", "ruff format --check src/a.py"),
        ("sc", "lint", "shellcheck scripts/x.sh"),
    ]
    assert ts.scope_runs(s, [], ["README.md"]) == []


def test_the_kind_comes_only_from_the_declaration_or_the_argument():
    """I1 — 同じ雛形でも種別の引数で種別が変わり、雛形の語を変えても種別は変わらない。"""
    for template in ("shellcheck {paths}", "ruff check {paths}", "pytest {paths}"):
        assert ts.resolve({}, baseline_test=template).scoped_suites()[0].kind == "test"
        assert ts.resolve({}, baseline_test=template, template_kind="lint").scoped_suites()[0].kind == "lint"
    with pytest.raises(ts.StrategyError):
        ts.resolve({}, baseline_test="pytest {paths}", template_kind="unit")


def test_round_only_runs_each_suite_in_its_own_shell(tmp_path):
    """AC10・I6 — 宣言から導いた round-only は suite ごとに 1 本で、前の suite の `cd` が後に効かない。"""
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
    decl = {
        "test": {
            "suites": [
                {"name": "a", "runner": "x", "command": "cd a && touch ran"},
                {"name": "b", "runner": "x", "command": "cd b && touch ran"},
            ]
        }
    }
    s = ts.resolve(decl)
    assert s.name == "round-only" and s.round_command is None
    for command in s.round_commands():
        assert subprocess.run(command, shell=True, cwd=tmp_path).returncode == 0
    assert (tmp_path / "a" / "ran").exists() and (tmp_path / "b" / "ran").exists()


@pytest.mark.parametrize(
    "code, timed_out, status",
    [
        (0, False, "passed"),
        (1, False, "failed"),
        (2, False, "failed"),
        (126, False, "launch_failed"),
        (127, False, "launch_failed"),
        (None, True, "timed_out"),
    ],
)
def test_outcome_separates_launch_failures(code, timed_out, status):
    """AC11・I7 — 終了コードと打ち切りから「通った / 落ちた / 起動の失敗 / 打ち切った」を返す。"""
    got = ts.outcome(code, timed_out)
    assert got.status == status and got.launch_failed == (status == "launch_failed")
    if status == "launch_failed":
        assert str(code) in got.reason


def test_fill_quotes_every_path_as_one_argument(tmp_path):
    """AC22・I2 — 空白・`$`・`;`・`(` を含むパスも 1 つの引数としてシェルへ渡る。"""
    paths = ["a b.py", "$x;(y).sh", "c.py"]
    command = ts.fill("printf '%s\\n' {paths}", paths)
    out = subprocess.run(command, shell=True, cwd=tmp_path, capture_output=True, text=True).stdout
    assert out.splitlines() == paths


@pytest.mark.parametrize("template", ["pytest '{paths}'", 'pytest "{paths}"', "pytest a{paths}", "x {paths} '{paths}'"])
def test_a_quoted_or_glued_paths_is_rejected(template):
    """AC22・決定 2 — 引用の中や語に付いた `{paths}` は雛形として拒む。"""
    assert ts.template_problem(template, "k") is not None


@pytest.mark.parametrize("template", ["(cd sub && pytest -q {paths})", "pytest {paths};echo done", "env X=$HOME pytest {paths}"])
def test_shell_templates_are_accepted(template):
    assert ts.template_problem(template, "k") is None


def test_covers_matches_globs_and_prefixes():
    suite = ts.Suite("s", "", "x {paths}", paths=["*.sh", "tools"])
    assert suite.covers("a.sh") and suite.covers("scripts/b.sh") and suite.covers("tools/x.py")
    assert not suite.covers("a.py") and not suite.covers("toolsx/y")


def test_verify_commands_list_both_kinds_or_none():
    """AC19・AC20 — 宣言の全体テスト（両方の種別）を返し、宣言が無ければ `None`。"""
    decl = {
        "test": {
            "suites": [
                {"name": "py", "runner": "pytest", "command": "pytest .", "scope_command": "pytest {paths}"},
                {"name": "lint", "runner": "sh", "kind": "lint", "command": "bash scripts/check-lint.sh"},
            ]
        }
    }
    assert ts.verify_commands(decl) == ["pytest .", "bash scripts/check-lint.sh"]
    assert ts.verify_commands({}) is None
    assert ts.verify_commands({"test": {"unknown": "x"}}) is None


def test_verify_commands_stops_on_an_invalid_declaration():
    """`test` があって解けない宣言は、宣言が無いときと分けて、不正なキーを示して止める。"""
    decl = {"test": {"suites": [{"name": "py", "kind": "bogus", "command": "pytest ."}]}}
    with pytest.raises(ts.StrategyError, match=r"test\.suites\[0\]\.kind"):
        ts.verify_commands(decl)


# ---------- #1437: 雛形から組んだ全体テストの注記 ----------

PYTEST_DECL = {"test": {"suites": [{"name": "py", "runner": "pytest", "command": "pytest -q"}]}}
LINT_ONLY_DECL = {"test": {"suites": [{"name": "sc", "runner": "shellcheck", "kind": "lint", "command": "shellcheck -s bash a.sh"}]}}


@pytest.mark.parametrize("decl", [{}, LINT_ONLY_DECL], ids=["no-decl", "lint-only-decl"])
def test_a_whole_test_built_from_the_template_carries_one_note(decl):
    """#1437 AC1・I1 — 宣言にテストの `command` が無く種別 test の雛形なら、`{paths}` を `.` にした全体テストと注記 1 件。"""
    s = ts.resolve(decl, baseline_test="shellcheck -s bash {paths}", scope_paths=["a.sh"])
    assert "shellcheck -s bash ." in s.whole_commands()
    assert s.notes == [ts.WHOLE_FROM_TEMPLATE]


def test_the_note_does_not_depend_on_the_command_words():
    """#1437 I4 — 同じ形の雛形は、コマンドの語に依らず同じ注記を持つ。"""
    a = ts.resolve({}, baseline_test="shellcheck -s bash {paths}")
    b = ts.resolve({}, baseline_test="pytest {paths} -q")
    assert a.notes == b.notes == [ts.WHOLE_FROM_TEMPLATE]


def test_a_template_without_declaration_still_runs_the_whole_directory():
    """#1437 AC7・I3 — 注記を足しても、全体テストは `pytest . -q` のまま、suite の並びと名前も変わらない。"""
    s = ts.resolve({}, baseline_test="pytest {paths} -q")
    assert s.whole_commands() == ["pytest . -q"]
    assert [(x.name, x.scope_command, x.kind) for x in s.suites] == [("args", "pytest {paths} -q", "test")]


@pytest.mark.parametrize("kind", ["test", "lint"])
def test_a_declared_whole_test_wins_over_the_template(kind):
    """#1437 AC4・I2 — 宣言にテストの `command` があれば、全体テスト（テスト）は宣言だけで、注記は付かない。"""
    template = "uvx --from shellcheck-py shellcheck -s bash {paths}"
    s = ts.resolve(PYTEST_DECL, baseline_test=template, template_kind=kind, scope_paths=["images/redmine7/postresync.sh"])
    tests = [x.command for x in s.suites if x.kind == "test" and x.command]
    assert tests == ["pytest -q"]
    assert all(" ." not in c for c in s.whole_commands())
    assert ts.WHOLE_FROM_TEMPLATE not in s.notes
    if kind == "lint":
        lints = [x.command for x in s.suites if x.kind == "lint" and x.command]
        assert lints == ["uvx --from shellcheck-py shellcheck -s bash images/redmine7/postresync.sh"]
