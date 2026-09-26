"""担当決定の現状固定テスト。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ASSIGNMENT = Path(__file__).resolve().parents[1] / "lib" / "assignment.py"

@pytest.fixture(scope="module")
def assignment():
    spec = importlib.util.spec_from_file_location("ndf_lib_assignment", ASSIGNMENT)
    mod = importlib.util.module_from_spec(spec)
    # `@dataclass` は `sys.modules[cls.__module__]` を見るため、登録してから実行する
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("host", ("claude", "codex", "agy", "kiro"))
def test_detect_host_accepts_an_explicit_host(assignment, host):
    assert assignment.detect_host(host, {}) == (host, "explicit")


def test_detect_host_rejects_an_unknown_explicit_host(assignment):
    with pytest.raises(
        assignment.AssignmentError,
        match=r"^--host には claude/codex/agy/kiro .+ gemini$",
    ):
        assignment.detect_host("gemini", {})


@pytest.mark.parametrize(
    ("env", "expected_host"),
    [
        ({"CLAUDE_PLUGIN_ROOT": "/plugins/claude"}, "claude"),
        ({"CODEX_HOME": "/home/codex"}, "codex"),
        ({"KIRO_AGENT": "ndf"}, "kiro"),
    ],
)
def test_detect_host_uses_environment_hints(assignment, env, expected_host):
    assert assignment.detect_host(None, env) == (expected_host, "env")


def test_detect_host_uses_the_first_environment_hint(assignment):
    env = {
        "KIRO_AGENT": "ndf",
        "CODEX_HOME": "/home/codex",
        "CLAUDE_PLUGIN_ROOT": "/plugins/claude",
    }

    assert assignment.detect_host(None, env) == ("claude", "env")


def test_detect_host_rejects_an_environment_without_hints(assignment):
    with pytest.raises(
        assignment.AssignmentError,
        match=r"^ホストを推定できませんでした。.*--host claude\|codex\|agy\|kiro.*$",
    ):
        assignment.detect_host(None, {})


@pytest.mark.parametrize("host", ("gemini", "unknown"))
def test_the_default_pool_rejects_a_host_outside_host_runtimes(assignment, host):
    """HOST_RUNTIMES に含まれないホストを、母集合の既定が拒否する。"""
    with pytest.raises(assignment.AssignmentError) as excinfo:
        assignment.default_pool(host)

    assert "ホストになれないランタイムです" in str(excinfo.value)


# ---------- 実装担当の選び方（#933 の決定 1。cross-refactoring が使う） ----------

def test_choose_implementer_prefers_the_named_participant(assignment):
    assert assignment.choose_implementer(["claude", "codex", "kiro"], "claude", "kiro") \
        == ("kiro", "named")


def test_choose_implementer_uses_the_host_when_it_participates(assignment):
    assert assignment.choose_implementer(["codex", "claude", "kiro"], "claude") \
        == ("claude", "host")


def test_choose_implementer_falls_back_to_the_first_participant(assignment):
    """ホストを --exclude で外した実行では、参加者の先頭が担う。"""
    assert assignment.choose_implementer(["codex", "kiro"], "claude") == ("codex", "first")


def test_choose_implementer_rejects_a_name_outside_the_participants(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.choose_implementer(["codex", "kiro"], "claude", "agy")


def test_choose_implementer_rejects_an_empty_list(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.choose_implementer([], "claude")


# ---------- 席名からランタイム名への変換（#727） ----------

def test_seat_runtime_extracts_runtime_name(assignment):
    """現状固定: 基底席と副席から同じランタイム名を返す。"""
    for runtime in assignment.ALL_RUNTIMES:
        assert assignment.seat_runtime(runtime) == runtime

    seats = {
        "kiro-2": "kiro",
        "agy-3": "agy",
        "codex-5": "codex",
        "claude-9": "claude",
    }
    for seat, runtime in seats.items():
        assert assignment.seat_runtime(seat) == runtime


# ---------- レビュー席の割り当て（#727。cross-review が使う） ----------

def test_review_seats_with_three_or_more_available_rotates_in_available_order(assignment):
    """3者以上: available の順序を保った2席が輪番で選ばれる。"""
    available = ["codex", "agy", "kiro"]
    assert assignment.review_seats(1, available, []) == ["agy", "kiro"]
    assert assignment.review_seats(2, available, []) == ["codex", "kiro"]
    assert assignment.review_seats(3, available, []) == ["codex", "agy"]


def test_review_seats_with_two_available_returns_both_every_round(assignment):
    """2者: ラウンド番号によらず常にその2者が返る。"""
    available = ["codex", "kiro"]
    for round_no in range(1, 5):
        assert assignment.review_seats(round_no, available, []) == ["codex", "kiro"]


def test_review_seats_with_one_available_fills_from_fallback_or_second_seat(assignment):
    """1者: fallback から補填、または fallback が空・重複時は <name>-2 補填。"""
    assert assignment.review_seats(1, ["codex"], ["claude"]) == ["codex", "claude"]
    assert assignment.review_seats(1, ["codex"], []) == ["codex", "codex-2"]
    assert assignment.review_seats(1, ["codex"], ["codex"]) == ["codex", "codex-2"]


def test_review_seats_with_zero_available_fills_from_fallback_or_raises(assignment):
    """0者: fallback から2席割当、fallback も空の場合は AssignmentError。"""
    assert assignment.review_seats(1, [], ["claude"]) == ["claude", "claude-2"]
    with pytest.raises(
        assignment.AssignmentError,
        match=r"^使える者も席の埋め合わせに使える者もいません$",
    ):
        assignment.review_seats(1, [], [])


def test_review_seats_raises_when_both_available_and_fallback_are_empty(assignment):
    """0者かつ fallback も空: 公開入口が AssignmentError を送出する（R1-003）。

    使える者と席の埋め合わせ候補がともに無いことを、利用者向けの理由が示す。
    """
    with pytest.raises(assignment.AssignmentError) as excinfo:
        assignment.review_seats(1, [], [])

    message = str(excinfo.value)
    assert "使える者" in message
    assert "席の埋め合わせに使える者" in message


def test_review_seats_rejects_a_bad_round(assignment):
    """round_no < 1 の場合に AssignmentError が送出される。"""
    with pytest.raises(
        assignment.AssignmentError,
        match=r"^ラウンド番号は 1 以上です: 0$",
    ):
        assignment.review_seats(0, ["codex", "kiro"], [])
    with pytest.raises(
        assignment.AssignmentError,
        match=r"^ラウンド番号は 1 以上です: -1$",
    ):
        assignment.review_seats(-1, ["codex", "kiro"], [])


# ---------- 既定の母集合（cross-review と cross-refactoring で共通）と座席 ----------

@pytest.mark.parametrize("host, expected", [
    ("claude", ["claude", "codex", "kiro"]),
    ("codex", ["claude", "codex", "kiro"]),
    ("kiro", ["claude", "codex", "kiro"]),
    ("agy", ["claude", "codex", "agy", "kiro"]),
])
def test_default_pool_leaves_agy_out_unless_it_is_the_host(assignment, host, expected):
    """AC1〜AC3。"""
    assert assignment.default_pool(host) == expected


def _no_probe(names):
    return {}, True


def test_default_seats_for_a_claude_host(assignment):
    """AC1: round 1 codex+kiro / round 2 claude+kiro / round 3 claude+codex。"""
    p = assignment.resolve_participants(
        assignment.default_pool("claude"), host="claude", probe=_no_probe,
    )
    seats = [assignment.review_seats(r, p.available, []) for r in (1, 2, 3)]
    assert seats == [["codex", "kiro"], ["claude", "kiro"], ["claude", "codex"]]


def test_include_agy_restores_the_previous_rotation(assignment):
    """AC4: `--include agy` の座席は 4 者の母集合の輪番と同じ。`--exclude agy` は止めない。"""
    p = assignment.resolve_participants(
        assignment.default_pool("claude"), host="claude", include=["agy"], probe=_no_probe,
    )
    four = list(assignment.ALL_RUNTIMES)
    for r in range(1, 5):
        assert assignment.review_seats(r, p.available, []) == assignment.review_seats(r, four, [])

    q = assignment.resolve_participants(
        assignment.default_pool("claude"), host="claude", exclude=["agy"], probe=_no_probe,
    )
    assert q.ignored_exclude == ["agy"]
    assert "agy" not in q.available


def test_only_agy_without_include_and_its_conflicts(assignment):
    """AC4b: `--only agy` は agy 1 者。`--exclude agy` と重ねると止まる。綴りの誤りは確認の前に止まる。"""
    pool = assignment.default_pool("claude")
    p = assignment.resolve_participants(pool, host="claude", only="agy", probe=_no_probe)
    assert p.available == ["agy"]
    assert p.included == []

    with pytest.raises(assignment.AssignmentError, match="矛盾"):
        assignment.resolve_participants(
            pool, host="claude", only="agy", exclude=["agy"], probe=_no_probe)

    called = []
    with pytest.raises(assignment.AssignmentError):
        assignment.resolve_participants(
            pool, host="claude", only="typo", probe=lambda n: called.append(n) or ({}, True))
    assert called == []


@pytest.mark.parametrize("include, only, expected", [
    ([], None, ["kiro", "agy"]),
    (["agy"], None, ["kiro"]),
    ([], "agy", ["kiro"]),
    (["kiro"], None, ["kiro", "agy"]),
])
def test_recorded_exclusions_restores_both_kinds_unless_newly_named(
        assignment, include, only, expected):
    """#786 の AC4d / 決定 12: 外した者と無視した除外を足し戻す。無視した名前は新しい指定が勝つ。

    外した者（`excluded`）は `include` に重なっても残す（矛盾は `resolve_participants` が止める）。
    """
    recorded = {"excluded": ["kiro"], "ignored_exclude": ["agy"]}
    assert assignment.recorded_exclusions(recorded, include, only) == expected


def test_recorded_exclusions_of_an_old_state_is_empty(assignment):
    """記録を持たない状態ファイル（`participants` が空）でも空で返す。"""
    assert assignment.recorded_exclusions({}, []) == []
