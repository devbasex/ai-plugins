"""担当の決定（ホスト判定 / 母集合 / 輪番）のテスト。

**`runtimes` と `impl_capable` を同一視しない**ことがここの主題である。
前者はホストを除いた 3 者（提案・レビュー）、後者は gemini を除いた 3 者（適用）で、
重なるが一致しない。
"""
from __future__ import annotations

import pytest


HOSTS = ["claude", "codex", "kiro"]


# ---------- ホスト判定 ----------

def test_explicit_host_wins(assignment):
    assert assignment.detect_host("codex") == ("codex", "explicit")


def test_explicit_host_rejects_gemini(assignment):
    """gemini は NDF の配布先ではないため、ホストになれない。"""
    with pytest.raises(assignment.AssignmentError):
        assignment.detect_host("gemini")


def test_host_detected_from_env(assignment):
    host, detection = assignment.detect_host(None, {"CLAUDE_PLUGIN_ROOT": "/x"})
    assert (host, detection) == ("claude", "env")
    host, detection = assignment.detect_host(None, {"KIRO_AGENT": "ndf"})
    assert (host, detection) == ("kiro", "env")


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
    """適用の母集合はホストによらず常に claude / codex / kiro になる。"""
    assert assignment.impl_pool() == ["claude", "codex", "kiro"]
    assert "gemini" not in assignment.impl_pool()


# ---------- 輪番 ----------

@pytest.mark.parametrize("host", HOSTS)
def test_impl_and_reviewers_never_overlap(assignment, host):
    for round_no in range(1, 13):
        impl, reviewers = assignment.assign(round_no, host)
        assert impl not in reviewers, f"round {round_no} で実装担当がレビューにも入っている"
        assert len(reviewers) == 2, f"round {round_no} のレビュー担当が 2 者でない"
        assert host not in reviewers, f"round {round_no} でホストがレビューに入っている"


@pytest.mark.parametrize("host", HOSTS)
def test_gemini_never_implements(assignment, host):
    for round_no in range(1, 13):
        impl, _ = assignment.assign(round_no, host)
        assert impl != "gemini"


@pytest.mark.parametrize("host", HOSTS)
def test_host_takes_impl_turn_at_least_once(assignment, host):
    """ホストは適用にだけ参加する。3 ラウンド回れば必ず 1 度は担当する。"""
    impls = {assignment.assign(r, host)[0] for r in range(1, 4)}
    assert host in impls


def test_reviewers_narrow_to_two_when_impl_is_host(assignment):
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
