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
    assert assignment.choose_implementer(["claude", "codex", "kiro"], "claude", "kiro") == ("kiro", "named")


def test_choose_implementer_uses_the_host_when_it_participates(assignment):
    assert assignment.choose_implementer(["codex", "claude", "kiro"], "claude") == ("claude", "host")


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


@pytest.mark.parametrize(
    "host, expected",
    [
        ("claude", ["claude", "codex", "kiro"]),
        ("codex", ["claude", "codex", "kiro"]),
        ("kiro", ["claude", "codex", "kiro"]),
        ("agy", ["claude", "codex", "agy", "kiro"]),
    ],
)
def test_default_pool_leaves_agy_out_unless_it_is_the_host(assignment, host, expected):
    """AC1〜AC3。"""
    assert assignment.default_pool(host) == expected


def _no_probe(names):
    return {}, True


def test_default_seats_for_a_claude_host(assignment):
    """AC1: round 1 codex+kiro / round 2 claude+kiro / round 3 claude+codex。"""
    p = assignment.resolve_participants(
        assignment.default_pool("claude"),
        host="claude",
        probe=_no_probe,
    )
    seats = [assignment.review_seats(r, p.available, []) for r in (1, 2, 3)]
    assert seats == [["codex", "kiro"], ["claude", "kiro"], ["claude", "codex"]]


def test_include_agy_restores_the_previous_rotation(assignment):
    """AC4: `--include agy` の座席は 4 者の母集合の輪番と同じ。`--exclude agy` は止めない。"""
    p = assignment.resolve_participants(
        assignment.default_pool("claude"),
        host="claude",
        include=["agy"],
        probe=_no_probe,
    )
    four = list(assignment.ALL_RUNTIMES)
    for r in range(1, 5):
        assert assignment.review_seats(r, p.available, []) == assignment.review_seats(r, four, [])

    q = assignment.resolve_participants(
        assignment.default_pool("claude"),
        host="claude",
        exclude=["agy"],
        probe=_no_probe,
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
        assignment.resolve_participants(pool, host="claude", only="agy", exclude=["agy"], probe=_no_probe)

    called = []
    with pytest.raises(assignment.AssignmentError):
        assignment.resolve_participants(pool, host="claude", only="typo", probe=lambda n: called.append(n) or ({}, True))
    assert called == []


@pytest.mark.parametrize(
    "include, only, expected",
    [
        ([], None, ["kiro", "agy"]),
        (["agy"], None, ["kiro"]),
        ([], "agy", ["kiro"]),
        (["kiro"], None, ["kiro", "agy"]),
    ],
)
def test_recorded_exclusions_restores_both_kinds_unless_newly_named(assignment, include, only, expected):
    """#786 の AC4d / 決定 12: 外した者と無視した除外を足し戻す。無視した名前は新しい指定が勝つ。

    外した者（`excluded`）は `include` に重なっても残す（矛盾は `resolve_participants` が止める）。
    """
    recorded = {"excluded": ["kiro"], "ignored_exclude": ["agy"]}
    assert assignment.recorded_exclusions(recorded, include, only) == expected


def test_recorded_exclusions_of_an_old_state_is_empty(assignment):
    """記録を持たない状態ファイル（`participants` が空）でも空で返す。"""
    assert assignment.recorded_exclusions({}, []) == []


# ---------- 結果なしの後の規則（#919） ----------

THREE = ["claude", "codex", "kiro"]


def _decide(assignment, seat, reason, *, log=(), available=THREE, host="claude", **kw):
    account = kw.pop("account", None)
    return assignment.after_no_result(
        assignment.Assignee(seat, account),
        reason,
        available=available,
        log=list(log),
        step=kw.pop("step", "review"),
        attempt=kw.pop("attempt", 1),
        host=host,
        **kw,
    )


def _entry(assignment, seat, reason, decision, *, account=None, step="review", attempt=1):
    return assignment.no_result_entry(step, attempt, assignment.Assignee(seat, account), reason, decision, "2026-10-04T00:00:00Z")


def test_first_no_result_other_than_usage_limit_relaunches_the_same_agent(assignment):
    got = _decide(assignment, "kiro", "stalled", busy=["codex"])
    assert (got.action, got.to, got.drop) == ("relaunch", assignment.Assignee("kiro"), False)


def test_usage_limit_reassigns_without_relaunching(assignment):
    got = _decide(assignment, "kiro", "usage_limit", busy=["codex"])
    assert (got.action, got.to.seat, got.drop) == ("reassign", "claude", True)


def test_no_result_after_a_relaunch_reassigns(assignment):
    first = _decide(assignment, "kiro", "stalled", busy=["codex"])
    log = [_entry(assignment, "kiro", "stalled", first)]
    got = _decide(assignment, "kiro", "stalled", log=log, busy=["codex"])
    assert (got.action, got.to.seat) == ("reassign", "claude")


def test_a_relaunch_carried_to_the_next_attempt_counts_as_the_same_relaunch(assignment):
    """修正の起動し直しは次の試行で起動される。2 度続けて結果なしなら振り替える（PR 1732 の指摘）。"""
    first = _decide(assignment, "kiro", "stalled", step="fix", attempt=1, relaunch_next_attempt=True)
    log = [_entry(assignment, "kiro", "stalled", first, step="fix", attempt=1)]
    got = _decide(assignment, "kiro", "stalled", log=log, step="fix", attempt=2, relaunch_next_attempt=True)
    assert (first.action, got.action) == ("relaunch", "reassign")
    # 間に結果を残した試行があれば連続ではない
    again = _decide(assignment, "kiro", "stalled", log=log, step="fix", attempt=3, relaunch_next_attempt=True)
    assert again.action == "relaunch"


def test_excluded_and_busy_runtimes_are_never_the_target(assignment):
    gone = _decide(assignment, "claude", "usage_limit", busy=["kiro"])
    log = [_entry(assignment, "claude", "usage_limit", gone)]
    assert gone.to.seat == "codex"
    # claude は外れ、codex は今の席にいる。kiro が落ちても候補は残らない
    got = _decide(assignment, "kiro", "usage_limit", log=log, busy=["codex"])
    assert (got.action, got.to) == ("abort", None)
    assert got.drop is True


def test_running_out_of_candidates_aborts(assignment):
    got = _decide(assignment, "kiro", "usage_limit", available=["codex", "kiro"], busy=["codex"])
    assert got.action == "abort"


def test_a_runtime_dropped_by_usage_limit_is_out_including_its_second_seat(assignment):
    d = _decide(assignment, "kiro", "usage_limit", busy=["codex"])
    log = [_entry(assignment, "kiro", "usage_limit", d)]
    assert assignment.excluded_runtimes(log) == {"kiro"}
    assert assignment.seats_pool(THREE, log) == ["claude", "codex"]
    got = _decide(assignment, "codex", "usage_limit", log=log, busy=["claude"])
    assert got.action == "abort"
    for round_no in range(1, 4):
        assert all(assignment.seat_runtime(s) != "kiro" for s in assignment.review_seats(round_no, ["claude"], []))


def test_the_target_comes_only_from_the_available_participants(assignment):
    got = _decide(assignment, "kiro", "usage_limit", available=["codex", "kiro"], host="agy")
    assert got.to.seat == "codex"
    got = _decide(assignment, "kiro", "usage_limit", available=["kiro"], host="agy")
    assert got.action == "abort"


def test_claude_usage_limit_moves_to_another_account_first(assignment):
    seen = []

    def pick(tried):
        seen.append(tried)
        return "work2"

    got = _decide(assignment, "claude", "usage_limit", account="work1", busy=["codex"], pick_account=pick)
    assert (got.action, got.to, got.drop) == ("reassign", assignment.Assignee("claude", "work2"), False)
    assert seen == [frozenset({"work1"})]
    log = [_entry(assignment, "claude", "usage_limit", got, account="work1")]
    assert assignment.excluded_runtimes(log) == set()
    assert assignment.current_account(log, "claude", "work1") == "work2"
    # 次の上限では試したアカウントがどちらも渡る
    _decide(assignment, "claude", "usage_limit", account="work2", log=log, busy=["codex"], pick_account=pick)
    assert seen[-1] == frozenset({"work1", "work2"})


def test_an_inherited_account_counts_as_tried(assignment):
    """席にアカウントが無ければ、継承した initial_account を試したアカウントに含める（同じアカウントへ振り替えない）。"""
    seen = []
    _decide(assignment, "claude", "usage_limit", busy=["codex"], initial_account="work1", pick_account=lambda t: seen.append(t) or "work2")
    assert seen == [frozenset({"work1"})]


def test_claude_without_a_spare_account_moves_to_another_runtime(assignment):
    got = _decide(assignment, "claude", "usage_limit", busy=["codex"], pick_account=lambda tried: None)
    assert (got.action, got.to.seat) == ("reassign", "kiro")


def test_accounts_are_not_tried_for_reasons_other_than_usage_limit(assignment):
    called = []
    first = _decide(assignment, "claude", "stalled", busy=["codex"])
    log = [_entry(assignment, "claude", "stalled", first)]
    got = _decide(assignment, "claude", "stalled", log=log, busy=["codex"], pick_account=lambda t: called.append(t) or "work2")
    assert called == []
    assert got.to.seat == "kiro"


def test_only_never_reassigns(assignment):
    got = _decide(assignment, "kiro", "usage_limit", available=["kiro"], only=True, pick_account=lambda t: "x")
    assert got.action == "abort"
    got = _decide(assignment, "claude", "usage_limit", available=THREE, only=True, pick_account=lambda t: "x")
    assert got.action == "abort"


def test_an_agent_on_a_new_account_may_relaunch_once_more(assignment):
    log = [
        _entry(assignment, "claude", "stalled", _decide(assignment, "claude", "stalled"), account="work1"),
    ]
    assert _decide(assignment, "claude", "stalled", log=log, account="work1").action == "reassign"
    assert _decide(assignment, "claude", "stalled", log=log, account="work2").action == "relaunch"


def test_reassignments_never_exceed_participants_plus_accounts(assignment):
    accounts = ["a1", "a2"]
    log: list = []
    failed = assignment.Assignee("claude", None)
    reassigned = 0
    for _ in range(20):
        d = assignment.after_no_result(
            failed,
            "usage_limit",
            available=THREE,
            log=log,
            step="implement",
            attempt=1,
            host="claude",
            pick_account=lambda tried: next((a for a in accounts if a not in tried), None),
        )
        log.append(assignment.no_result_entry("implement", 1, failed, "usage_limit", d, "t"))
        if d.action != "reassign":
            break
        reassigned += 1
        failed = d.to
    assert d.action == "abort"
    assert reassigned <= len(THREE) + len(accounts)


def test_the_reassignment_rule_follows_the_relaunch_set(assignment, monkeypatch):
    monkeypatch.setattr(assignment, "NO_RELAUNCH_REASONS", frozenset({"stalled"}))
    assert _decide(assignment, "kiro", "stalled", busy=["codex"]).action == "reassign"
    assert _decide(assignment, "kiro", "usage_limit", busy=["codex"]).action == "relaunch"


def test_pinned_seats_are_dropped_once_either_runtime_was_replaced(assignment):
    pinned = ["claude", "claude-2"]
    assert assignment.pinned_in_force(pinned, []) == pinned
    d = _decide(assignment, "claude", "usage_limit", pick_account=lambda t: "w2")
    assert assignment.pinned_in_force(pinned, [_entry(assignment, "claude", "usage_limit", d)]) is None
    other = _decide(assignment, "kiro", "usage_limit", busy=["codex"])
    assert assignment.pinned_in_force(pinned, [_entry(assignment, "kiro", "usage_limit", other)]) == pinned


def test_the_entry_holds_only_names(assignment):
    d = _decide(assignment, "claude", "usage_limit", account="w1", pick_account=lambda t: "w2")
    entry = _entry(assignment, "claude", "usage_limit", d, account="w1")
    assert entry == {
        "step": "review",
        "attempt": 1,
        "seat": "claude",
        "account": "w1",
        "reason": "usage_limit",
        "decision": "reassign",
        "to": "claude",
        "to_account": "w2",
        "at": "2026-10-04T00:00:00Z",
    }


def test_a_bad_seat_name_is_rejected(assignment):
    with pytest.raises(assignment.AssignmentError):
        _decide(assignment, "gpt", "usage_limit")
