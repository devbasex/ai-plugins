"""担当の決定（ホスト判定 / 母集合 / 輪番）のテスト。

**`runtimes` と `impl_capable` を同一視しない**ことがここの主題である。
前者はホストを除いた 3 者（提案・レビュー）、後者は参加する 4 者すべて（適用）で、
重なるが一致しない。
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
def test_review_pool_is_all_minus_host(assignment, host):
    pool = assignment.review_pool(host)
    assert len(pool) == 3
    assert host not in pool
    assert set(pool) == set(assignment.ALL_RUNTIMES) - {host}


@pytest.mark.parametrize("host", HOSTS)
def test_impl_pool_is_host_independent(assignment, host):
    """適用の母集合はホストによらず参加する 4 者すべてになる。"""
    assert assignment.impl_pool() == ["claude", "codex", "agy", "kiro"]


def test_no_runtime_is_excluded_from_applying(assignment):
    """除外を表す定数は残さない。

    空の除外一覧を残すと、理由を書かないまま値を足せる置き場所だけが残る。
    """
    assert not hasattr(assignment, "IMPL_EXCLUDED")


# ---------- 輪番 ----------

@pytest.mark.parametrize("host", HOSTS)
def test_impl_and_reviewers_never_overlap(assignment, host):
    for round_no in range(1, 17):
        impl, reviewers = assignment.assign(round_no, host)
        assert impl not in reviewers, f"round {round_no} で実装担当がレビューにも入っている"
        assert len(reviewers) == 2, f"round {round_no} のレビュー担当が 2 者でない"
        assert host not in reviewers, f"round {round_no} でホストがレビューに入っている"


@pytest.mark.parametrize("host", HOSTS)
def test_every_participant_implements_within_four_rounds(assignment, host):
    """4 ラウンドで 4 者が 1 度ずつ適用担当になる（`--max-outer-rounds` の既定と揃う）。"""
    impls = [assignment.assign(r, host)[0] for r in range(1, 5)]
    assert set(impls) == set(assignment.ALL_RUNTIMES)
    assert len(set(impls)) == len(impls), f"同じ担当が 2 度入っている: {impls}"


@pytest.mark.parametrize("host", HOSTS)
def test_host_takes_impl_turn_at_least_once(assignment, host):
    """ホストは適用にだけ参加する。4 ラウンド回れば必ず 1 度は担当する。"""
    impls = {assignment.assign(r, host)[0] for r in range(1, 5)}
    assert host in impls


def test_reviewers_narrow_to_two_when_impl_is_host(rounds, assignment):
    """実装担当がホストと同じラウンドでも、レビュー担当は 3 者にならず 2 者になる。"""
    host = "claude"
    rounds = [r for r in range(1, 13) if assignment.assign(r, host)[0] == host]
    assert rounds, "ホストが実装担当になるラウンドが無い"
    for round_no in rounds:
        _, reviewers = assignment.assign(round_no, host)
        assert len(reviewers) == 2


def test_excluded_reviewer_rotates_across_rounds(assignment):
    """余る 1 者はラウンドを跨いで順に外れ、負荷が偏らないこと。"""
    host = "claude"
    pool = set(assignment.review_pool(host))
    excluded = []
    for round_no in range(1, 13):
        impl, reviewers = assignment.assign(round_no, host)
        if impl != host:
            continue
        excluded.append((pool - {impl} - set(reviewers)).pop())
    assert len(set(excluded)) > 1, f"常に同じ 1 者だけが外れている: {excluded}"


@pytest.mark.parametrize("host", HOSTS)
def test_assignment_is_deterministic(assignment, host):
    """再開しても担当が変わらないこと（同じ入力なら同じ結果）。"""
    for round_no in range(1, 13):
        assert assignment.assign(round_no, host) == assignment.assign(round_no, host)


def test_round_number_must_be_positive(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.assign(0, "claude")


# ---------- 割り当てを直に固定する（#214 / #216） ----------

# `gemini` があった位置へ `agy` を入れた（#214）。並べ替えると同じラウンド番号でも
# 担当が変わり、これまでの記録と突き合わせられなくなる。
# 適用の母集合を 4 者にしたため（#216）、ラウンド 2 以降の担当が 1 つずつずれる。
# 適用担当は 4 ラウンドで 1 周し、レビュー担当は適用担当がホストと重なるラウンドで
# 1 者を落とすため 12 ラウンドで 1 周する。読み替えた結果を直に置く。
EXPECTED_FOR_CLAUDE = {
    1: ("codex", ["agy", "kiro"]),
    2: ("agy", ["codex", "kiro"]),
    3: ("kiro", ["codex", "agy"]),
    4: ("claude", ["codex", "kiro"]),
    5: ("codex", ["agy", "kiro"]),
    6: ("agy", ["codex", "kiro"]),
    7: ("kiro", ["codex", "agy"]),
    8: ("claude", ["codex", "agy"]),
    9: ("codex", ["agy", "kiro"]),
    10: ("agy", ["codex", "kiro"]),
    11: ("kiro", ["codex", "agy"]),
    12: ("claude", ["agy", "kiro"]),
}


def test_the_participant_list_keeps_the_replaced_position(assignment):
    assert assignment.ALL_RUNTIMES == ("claude", "codex", "agy", "kiro")


def test_host_runtimes_covers_every_participant(assignment):
    """「ホストになれるか」と「参加できるか」は別の問いだが、いまは同じ答えになる。"""
    assert assignment.HOST_RUNTIMES == assignment.ALL_RUNTIMES


@pytest.mark.parametrize("round_no", sorted(EXPECTED_FOR_CLAUDE))
def test_the_rotation_matches_the_renamed_result(assignment, round_no):
    assert assignment.assign(round_no, "claude") == EXPECTED_FOR_CLAUDE[round_no]


# ---------- レビューだけの輪番（cross-review が使う） ----------

def test_review_assign_excludes_the_host(assignment):
    """母集合はホストを除く 3 者で、返るのは常に 2 者である。"""
    for host in assignment.HOST_RUNTIMES:
        for round_no in range(1, 10):
            picked = assignment.review_assign(round_no, host)
            assert len(picked) == 2
            assert host not in picked
            assert set(picked) <= set(assignment.review_pool(host))


def test_review_assign_rotates_the_excluded_one(assignment):
    """外す 1 者はラウンドごとに回り、3 ラウンドで 1 周する。"""
    host = "claude"
    pool = assignment.review_pool(host)
    dropped = [
        set(pool) - set(assignment.review_assign(r, host)) for r in (1, 2, 3)
    ]
    assert [next(iter(d)) for d in dropped] == pool
    # 4 ラウンド目は 1 ラウンド目と同じ担当へ戻る
    assert assignment.review_assign(4, host) == assignment.review_assign(1, host)


def test_review_assign_rejects_a_bad_round(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.review_assign(0, "claude")


def test_review_assign_rejects_an_unknown_host(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.review_assign(1, "gemini")


# ---------- 席の埋め方と席の名前（#727。cross-review が使う） ----------
#
# 変更前の席の割り当て（`review_assign`）を期待値に使えるよう、同じファイルに置く。
# `review_assign` のテストは P7 で消す。

def test_review_seats_match_review_assign_for_three_available(assignment):
    """AC8: 使える者が 3 者のとき、変更前の輪番と同じ値になる（4 ホスト × ラウンド 1〜12）。"""
    for host in assignment.HOST_RUNTIMES:
        pool = assignment.review_pool(host)
        for round_no in range(1, 13):
            assert assignment.review_seats(round_no, pool, []) == \
                assignment.review_assign(round_no, host), f"host={host} round={round_no}"


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
