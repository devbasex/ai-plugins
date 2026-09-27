"""`lib/test_strategy.py` — 戦略の解決・`{paths}` の置き換え・時間の上限（#1334 AC1・AC2・AC4・AC5・AC9・決定 2・8・14）。"""

from __future__ import annotations

import json
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
    words = ts.scope_words(s.suites[0].scope_command, ["plugins/ndf/scripts/tests/test_x.py"])
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
    assert (s.name, s.source, s.round_command) == ("round-only", "derived:test.suites", "make test")
    assert s.whole_commands() == ["make test"]


def test_arguments_come_before_the_declaration():
    """前提 3 — `{paths}` を含む引数は雛形、含まなければそのまま走らせるコマンド。"""
    s = ts.resolve(CARMO, round_test="make test-unit")
    assert (s.name, s.source, s.round_command) == ("round-only", "args", "make test-unit")
    assert s.whole_commands() == [CARMO["test"]["suites"][0]["command"]], "全体テストは宣言の suite の command"
    assert ts.resolve({}, round_test="make test-unit").whole_commands() == ["make test-unit"], "宣言も --baseline-test も無ければ兼ねる"
    s = ts.resolve(AI_PLUGINS, round_test="pytest {paths} -q")
    assert (s.name, s.source) == ("local-full", "args")
    assert s.suites[0].scope_command == "pytest {paths} -q" and s.suites[0].command == "pytest . -q"
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
    words = ts.scope_words(s.suites[0].scope_command, ["tests/a.py", "tests/b.py"])
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
