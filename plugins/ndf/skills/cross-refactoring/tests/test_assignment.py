"""担当の決定（ホスト判定 / 母集合の既定 / 実装担当 / 席）のテスト。

cross-refactoring は提案を参加者の全員が、改修計画以降を実装担当 1 者が通し（#933 の決定 1）、
cross-review は claude / codex / kiro とホストの母集合（ホストが agy なら 4 者）から 2 席を決める。母集合の既定は Skill ごとに違う。
"""
from __future__ import annotations

import pytest


HOSTS = ["claude", "codex", "agy", "kiro"]


# ---------- ホスト判定 ----------

def test_explicit_host_wins(assignment):
    assert assignment.detect_host("codex") == ("codex", "explicit")


@pytest.mark.parametrize("host", HOSTS)
def test_every_participant_can_host(assignment, host):
    """4 者とも NDF の配布先であるため、4 者ともホストになれる。"""
    assert assignment.detect_host(host) == (host, "explicit")


def test_unknown_runtime_cannot_host(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.detect_host("gemini")


def test_host_detected_from_env(assignment):
    host, detection = assignment.detect_host(None, {"CLAUDE_PLUGIN_ROOT": "/x"})
    assert (host, detection) == ("claude", "env")
    host, detection = assignment.detect_host(None, {"KIRO_AGENT": "ndf"})
    assert (host, detection) == ("kiro", "env")


def test_agy_is_not_detected_from_env(assignment):
    """agy が子プロセスへ環境変数を渡すかを確かめていないため、推定へ入れない。

    誤った推定は提案・レビューの母集合を狂わせる。agy がホストのときは
    `--host agy` を明示する。
    """
    assert "agy" not in dict(assignment.HOST_ENV_HINTS).values()


def test_host_detection_fails_loudly_when_unknown(assignment):
    """既定値を勝手に置かない。間違ったまま一周すると成果物を見るまで気付けない。"""
    with pytest.raises(assignment.AssignmentError):
        assignment.detect_host(None, {})


# ---------- 母集合 ----------

@pytest.mark.parametrize("host", HOSTS)
def test_review_pool_is_the_default_three_and_the_host(assignment, host):
    """#786 の決定 1: claude / codex / kiro とホスト（ホストが agy のときだけ 4 者）。"""
    expected = [r for r in assignment.ALL_RUNTIMES if r in {"claude", "codex", "kiro", host}]
    assert assignment.review_pool(host) == expected


@pytest.mark.parametrize("host, expected", [
    ("claude", ["claude", "codex", "kiro"]),
    ("codex", ["codex", "kiro"]),
    ("agy", ["codex", "agy", "kiro"]),
    ("kiro", ["codex", "kiro"]),
])
def test_refactor_pool_is_codex_kiro_and_the_host(assignment, host, expected):
    """AC31 / AC32 — cross-refactoring の既定はホストを含む。並びは固定の順。"""
    assert assignment.refactor_pool(host) == expected


def test_no_runtime_is_excluded_from_applying(assignment):
    """除外を表す定数は残さない。

    空の除外一覧を残すと、理由を書かないまま値を足せる置き場所だけが残る。
    """
    assert not hasattr(assignment, "IMPL_EXCLUDED")


# ---------- 実装担当（cross-refactoring。#933 の決定 1） ----------

@pytest.mark.parametrize("host", HOSTS)
def test_the_host_implements_when_it_participates(assignment, host):
    """名指しが無ければ、参加者にいるホストが実装担当になる。"""
    participants = assignment.refactor_pool(host)
    assert assignment.choose_implementer(participants, host) == (host, "host")


def test_the_first_participant_implements_when_the_host_is_out(assignment):
    """ホストが参加者にいなければ（外した場合）、参加者の先頭が実装担当になる。"""
    assert assignment.choose_implementer(["codex", "kiro"], "claude") == ("codex", "first")


def test_a_named_implementer_wins_over_the_host(assignment):
    assert assignment.choose_implementer(
        ["claude", "codex", "kiro"], "claude", "kiro") == ("kiro", "named")


def test_a_named_implementer_outside_the_participants_is_rejected(assignment):
    """AC21 — 誰が実装したかが指定と食い違うため、代わりの者を選ばずに止める。"""
    with pytest.raises(assignment.AssignmentError):
        assignment.choose_implementer(["claude", "codex", "kiro"], "claude", "agy")


def test_no_participant_cannot_choose_an_implementer(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.choose_implementer([], "claude")


@pytest.mark.parametrize("host", HOSTS)
def test_the_implementer_is_deterministic(assignment, host):
    """再開しても担当が変わらないこと（同じ入力なら同じ結果。状態に依らない）。"""
    participants = assignment.refactor_pool(host)
    assert assignment.choose_implementer(participants, host) == \
        assignment.choose_implementer(list(participants), host)


def test_the_rotation_is_gone(assignment):
    """輪番は持たない。1 回の実行を同じ 1 者が通す（#933 の決定 1）。"""
    assert not hasattr(assignment, "impl_assign")
    assert not hasattr(assignment, "impl_for_seq")


# ---------- 固定の順（#214） ----------

# `gemini` があった位置へ `agy` を入れた（#214）。並べ替えると、母集合の並びと
# 実装担当の「参加者の先頭」、レビューの席が変わり、これまでの記録と突き合わせられなくなる。


def test_the_participant_list_keeps_the_replaced_position(assignment):
    assert assignment.ALL_RUNTIMES == ("claude", "codex", "agy", "kiro")


def test_host_runtimes_covers_every_participant(assignment):
    """「ホストになれるか」と「参加できるか」は別の問いだが、いまは同じ答えになる。"""
    assert assignment.HOST_RUNTIMES == assignment.ALL_RUNTIMES


# ---------- 席の埋め方と席の名前（#727。cross-review が使う） ----------

def _previous_review_rotation(round_no: int, pool: list[str]) -> list[str]:
    """席の埋め方より前の輪番。3 者の母集合から `(round_no - 1) % 3` の者を外した 2 者。

    関数は消えたため、式を期待値として持つ（AC8 の主張を保つ）。
    """
    dropped = (round_no - 1) % len(pool)
    return [r for i, r in enumerate(pool) if i != dropped]


def test_review_seats_match_the_previous_rotation_for_three_available(assignment):
    """AC8: 使える者が 3 者のとき、変更前の輪番と同じ値になる（4 ホスト × ラウンド 1〜12）。"""
    for host in assignment.HOST_RUNTIMES:
        # 変更の前の母集合（全ランタイム − ホスト）。`review_pool` は #892 でホストを含む形へ変わった
        pool = [r for r in assignment.ALL_RUNTIMES if r != host]
        for round_no in range(1, 13):
            assert assignment.review_seats(round_no, pool, []) == \
                _previous_review_rotation(round_no, pool), f"host={host} round={round_no}"


def test_review_seats_with_four_available_give_each_two_turns(assignment):
    """AC9: 使える者が 4 者なら毎ラウンド 2 席で、ラウンド 1〜4 で各者がちょうど 2 回。"""
    available = list(assignment.ALL_RUNTIMES)
    seats = [assignment.review_seats(r, available, []) for r in range(1, 5)]
    assert all(len(s) == 2 for s in seats)
    counts = {name: sum(name in s for s in seats) for name in available}
    assert counts == {name: 2 for name in available}


def test_review_seats_with_two_available_return_both_every_round(assignment):
    """AC10"""
    for round_no in range(1, 5):
        assert assignment.review_seats(round_no, ["codex", "kiro"], []) == ["codex", "kiro"]


def test_review_seats_with_one_available_fill_from_fallback_or_second_seat(assignment):
    """AC11"""
    assert assignment.review_seats(1, ["codex"], ["claude"]) == ["codex", "claude"]
    assert assignment.review_seats(1, ["codex"], []) == ["codex", "codex-2"]


def test_review_seats_skip_a_fallback_that_is_already_available(assignment):
    """埋め合わせの候補が使える者に含まれるときは飛ばす（同じ席の名前を 2 つ返さない）。"""
    assert assignment.review_seats(1, ["claude"], ["claude"]) == ["claude", "claude-2"]


def test_review_seats_with_none_available_use_fallback_twice(assignment):
    """AC12"""
    assert assignment.review_seats(1, [], ["claude"]) == ["claude", "claude-2"]
    with pytest.raises(assignment.AssignmentError):
        assignment.review_seats(1, [], [])


def test_review_seats_reject_a_bad_round(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.review_seats(0, ["codex", "kiro"], [])


def test_seat_runtime_strips_the_suffix(assignment):
    """AC13"""
    assert assignment.seat_runtime("kiro-2") == "kiro"
    assert assignment.seat_runtime("kiro") == "kiro"


@pytest.mark.parametrize("seat", ["gemini", "kiro-1", "kiro-10", "kiro-2-3"])
def test_seat_runtime_rejects_a_malformed_seat(assignment, seat):
    """AC13"""
    with pytest.raises(assignment.AssignmentError):
        assignment.seat_runtime(seat)


def test_seat_pattern_matches_the_documented_form(assignment):
    assert assignment.SEAT_PATTERN.pattern == r"^(claude|codex|agy|kiro)(-[2-9])?$"
