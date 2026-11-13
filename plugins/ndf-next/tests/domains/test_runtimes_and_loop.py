"""ランタイム・収束ループのフェーズ・指摘の重さ・CI の結論の表（D2）を、読み手が頼る性質で確かめる。"""

from __future__ import annotations

from ndf.contracts import facts, limits

LOOP = "loop-phases"


def test_every_domain_table_has_its_owner_and_valid_references():
    policy_names = set(limits.table())
    domain_tables = [n for n in facts.names() if facts.place(n).startswith(f"{facts.DOMAINS_DIR}/")]
    assert len(domain_tables) == 13
    for name in domain_tables:
        assert facts.table(name).owner.startswith("D2")
        assert facts.ref_errors(name, policy_names) == []


def test_runtimes_keep_the_rotation_order_and_seat_defaults():
    assert facts.ids("runtimes") == ("claude", "codex", "agy", "kiro")
    assert facts.ids("runtimes", default_seat=True) == ("claude", "codex", "kiro")
    assert facts.value("runtimes", "kiro", "executable") == "kiro-cli"


def test_runtime_columns_point_at_declared_facts():
    env = set(facts.ids("env"))
    for rid, names in facts.column("runtimes", "host_env").items():
        assert set(names) <= env, rid
    # 使用量を測れるランタイムは hook の事象も渡す（使用量の記録は hook の停止で書く）
    assert set(facts.ids("runtimes", usage_observable=True)) <= set(facts.ids("runtimes", hook_events=True))
    # 委ねる先は既定の席と別の問い。agy は既定の席に無くても委ねられる
    assert "agy" in facts.ids("runtimes", delegate=True)


def test_probe_arguments_do_not_repeat_the_executable():
    for rid, executable in facts.column("runtimes", "executable").items():
        row = facts.row("runtimes", rid)
        for col in ("auth_probe", "model_probe"):
            assert executable not in row.get(col, []), (rid, col)


def test_loop_phases_follow_the_monitor_policies():
    phases = facts.ids(LOOP)
    assert {f"monitor.phase.{p}" for p in phases} == {n for n in limits.table() if n.startswith("monitor.phase.")}


def test_loop_phase_columns_nest():
    ids = {col: set(facts.ids(LOOP, **{col: True})) for col in ("launch", "coding", "fix", "alive", "reassign", "relaunch_in_place", "undo")}
    assert ids["fix"] <= ids["coding"] <= ids["launch"]
    assert ids["undo"] <= ids["relaunch_in_place"] <= ids["reassign"] <= ids["launch"]
    assert ids["alive"] <= ids["launch"]
    assert ids["launch"] <= set(facts.ids(LOOP, skill="cross-refactoring"))
    assert set(facts.column(LOOP, "resume_as")) == ids["fix"]


def test_severities_rank_in_row_order_and_blocking_is_the_heavy_end():
    ranks = list(facts.column("severities", "rank").values())
    assert ranks == sorted(ranks, reverse=True) and len(set(ranks)) == len(ranks)
    blocking = facts.ids("severities", blocking=True)
    assert facts.ids("severities")[: len(blocking)] == blocking
    assert set(blocking) <= set(facts.ids("severities", refactor=True))


def test_ci_outcomes_have_one_failed_set():
    # 取り消しは Runner の有無で失敗か基盤待ちに分かれるため、失敗の集合に入れず別の結果にする
    assert set(facts.ids("ci-outcomes", outcome="failed")) == {"failure", "error", "timed_out", "action_required", "startup_failure", "stale"}
    assert facts.ids("ci-outcomes", outcome="cancelled") == ("cancelled",)
    assert set(facts.column("ci-outcomes", "outcome").values()) == {"passed", "failed", "pending", "cancelled"}
