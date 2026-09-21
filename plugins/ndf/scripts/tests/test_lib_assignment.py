"""担当決定の現状固定テスト。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ASSIGNMENT = Path(__file__).resolve().parents[1] / "lib" / "assignment.py"

EXPECTED = {
    "claude": [
        ("codex", ["agy", "kiro"]),
        ("agy", ["codex", "kiro"]),
        ("kiro", ["codex", "agy"]),
        ("claude", ["codex", "kiro"]),
        ("codex", ["agy", "kiro"]),
        ("agy", ["codex", "kiro"]),
        ("kiro", ["codex", "agy"]),
        ("claude", ["codex", "agy"]),
    ],
    "codex": [
        ("codex", ["agy", "kiro"]),
        ("agy", ["claude", "kiro"]),
        ("kiro", ["claude", "agy"]),
        ("claude", ["agy", "kiro"]),
        ("codex", ["claude", "kiro"]),
        ("agy", ["claude", "kiro"]),
        ("kiro", ["claude", "agy"]),
        ("claude", ["agy", "kiro"]),
    ],
    "agy": [
        ("codex", ["claude", "kiro"]),
        ("agy", ["codex", "kiro"]),
        ("kiro", ["claude", "codex"]),
        ("claude", ["codex", "kiro"]),
        ("codex", ["claude", "kiro"]),
        ("agy", ["claude", "kiro"]),
        ("kiro", ["claude", "codex"]),
        ("claude", ["codex", "kiro"]),
    ],
    "kiro": [
        ("codex", ["claude", "agy"]),
        ("agy", ["claude", "codex"]),
        ("kiro", ["codex", "agy"]),
        ("claude", ["codex", "agy"]),
        ("codex", ["claude", "agy"]),
        ("agy", ["claude", "codex"]),
        ("kiro", ["claude", "agy"]),
        ("claude", ["codex", "agy"]),
    ],
}


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


@pytest.mark.parametrize("host", EXPECTED)
def test_assign_keeps_the_eight_round_rotation(assignment, host):
    actual = [assignment.assign(round_no, host) for round_no in range(1, 9)]

    assert actual == EXPECTED[host]
    assert any(impl == host for impl, _ in actual)
    assert any(impl != host for impl, _ in actual)
    assert all(len(reviewers) == 2 for _, reviewers in actual)
    assert all(impl not in reviewers for impl, reviewers in actual)


@pytest.mark.parametrize("host", ("claude", "codex", "agy", "kiro"))
def test_assign_rejects_a_bad_round(assignment, host):
    """round_no < 1 の場合に AssignmentError が送出される（R2-001）。"""
    for round_no in (0, -1):
        with pytest.raises(
            assignment.AssignmentError,
            match=r"^ラウンド番号は 1 以上です:",
        ) as excinfo:
            assignment.assign(round_no, host)
        assert "ラウンド番号は 1 以上です" in str(excinfo.value)


def test_review_assign_rejects_a_bad_round(assignment):
    """round_no < 1 の下限境界で AssignmentError が送出される（R2-003）。

    同モジュールの `assign` / `review_seats` は下限境界を固定しているが、
    `review_assign` だけ抜けていたため現状の振る舞いを固定する。
    """
    for round_no in (0, -1):
        with pytest.raises(
            assignment.AssignmentError,
            match=r"^ラウンド番号は 1 以上です:",
        ) as excinfo:
            assignment.review_assign(round_no, "claude")
        assert "ラウンド番号は 1 以上です" in str(excinfo.value)


@pytest.mark.parametrize("host", ("gemini", "unknown"))
def test_review_assign_rejects_a_host_outside_host_runtimes(assignment, host):
    """HOST_RUNTIMES に含まれないホストを拒否する現状を固定する（R2-005）。"""
    with pytest.raises(assignment.AssignmentError) as excinfo:
        assignment.review_assign(1, host)

    assert "ホストになれないランタイムです" in str(excinfo.value)


# ---------- 適用の輪番（#727。cross-refactoring が使う） ----------

def test_impl_assign_rotates_over_the_participants_starting_after_the_host(assignment):
    """AC34: `participants[round_no % len]`。ホスト claude の既定でも codex から始まる。"""
    participants = ["claude", "codex", "kiro"]
    actual = [assignment.impl_assign(r, participants) for r in range(1, 7)]
    assert actual == ["codex", "kiro", "claude", "codex", "kiro", "claude"]


def test_impl_assign_rejects_a_bad_round(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.impl_assign(0, ["claude", "codex"])


def test_impl_assign_rejects_an_empty_list(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.impl_assign(1, [])


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
