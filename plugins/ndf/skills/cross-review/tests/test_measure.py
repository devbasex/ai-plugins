"""効果の測定（`scripts/measure.py`）のテスト（#156 の 4 本目）。

設計は `issues/issue-156-pr4-design.md`、計画は `issues/issue-156-pr4-plan.md` にある。
**測定は状態ファイルを読むだけで、GitHub へ問い合わせない。**
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest


_MEASURE = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure.py"


def _state(**overrides) -> dict:
    """最小の状態ファイル。必要な項目だけを上書きして使う。"""
    state = {
        "started_at": "2026-05-23T00:00:00+00:00",
        "ended_at": "2026-05-23T01:10:00+00:00",
        "current_pr": 123,
        "pr_history": [{"pr": 123, "opened_at": "...", "closed_at": None, "rounds": 1}],
        "rounds": [],
        "review_findings": [],
        "evidence_rounds": [],
        "final": "approved",
    }
    state.update(overrides)
    return state


def _round(round_no: int, pr: int = 123, **overrides) -> dict:
    rec = {"round": round_no, "pr": pr, "reviewers": ["codex", "agy"]}
    rec.update(overrides)
    return rec


def _finding(finding_id: str, round_no: int, path: str, line: int,
             pr: int = 123, **overrides) -> dict:
    rec = {
        "finding_id": finding_id, "pr": pr, "round": round_no, "agent": "codex",
        "path": path, "line": line, "severity": "major", "body": finding_id,
    }
    rec.update(overrides)
    return rec


def _fix(*positions: dict) -> dict:
    """修正の記録。位置は `resolved_thread_positions` が持つ（#156 の Task 1）。"""
    return {
        "commit": "abc1234", "fixed": len(positions),
        "resolved_threads": len(positions),
        "resolved_thread_ids": [p["thread_id"] for p in positions if p.get("thread_id")],
        "resolved_thread_positions": list(positions),
    }


def _position(thread_id: str, path: str | None, line: int | None) -> dict:
    return {"thread_id": thread_id, "path": path, "line": line}


# ---------- 受け入れ条件 13: 費用（ラウンド数・起動回数・実時間）が並ぶ ----------


def test_cost_counts_rounds_launches_and_wall_clock(measure_mod):
    """費用はローテーション全体の合計である。"""
    st = _state(rounds=[_round(1), _round(2), _round(3)])

    cost = measure_mod.measure(st)["cost"]

    assert cost["rounds"] == 3
    assert cost["reviewer_launches"] == 6
    # 00:00:00 → 01:10:00 は 4200 秒。
    assert cost["wall_clock_seconds"] == 4200


def test_reviewer_launches_falls_back_to_recorded_agents(measure_mod):
    """`reviewers` を持たない古い記録では、結果を残した担当の数を数える。"""
    st = _state(rounds=[
        {"round": 1, "pr": 123,
         "codex": {"intent": "REQUEST_CHANGES"}, "agy": {"intent": "APPROVE"}},
        {"round": 2, "pr": 123, "codex": {"intent": "APPROVE"}},
    ])

    assert measure_mod.measure(st)["cost"]["reviewer_launches"] == 3


def test_identity_keys_report_state_file_key_and_all_prs(measure_mod):
    """`pr` は状態ファイルの鍵で、`prs` は `pr_history[]` の順に全件を持つ。"""
    st = _state(
        current_pr=124,
        pr_history=[{"pr": 123, "rounds": 2}, {"pr": 124, "rounds": 1}],
        rounds=[_round(1, pr=123), _round(2, pr=123), _round(3, pr=124)],
    )

    result = measure_mod.measure(st)

    assert result["pr"] == 123
    assert result["prs"] == [123, 124]
    assert result["rounds"] == 3


# ---------- 受け入れ条件 14: 収束の様子（終わり方・振動・上限の到達） ----------


@pytest.mark.parametrize(
    "final,oscillation,max_rounds",
    [
        ("approved", 0, 0),
        ("max_rounds", 0, 1),
        ("oscillation", 1, 0),
        ("error", 0, 0),
    ],
)
def test_convergence_reports_final_and_flags(measure_mod, final, oscillation, max_rounds):
    """**0 か 1 で出す。** 複数を比べるときに測る側が足せるようにするため。"""
    st = _state(final=final, rounds=[_round(1)])

    convergence = measure_mod.measure(st)["convergence"]

    assert convergence["final"] == final
    assert convergence["oscillation"] == oscillation
    assert convergence["max_rounds"] == max_rounds


# ---------- 受け入れ条件 15: 記録が無いときに落ちない ----------


def test_empty_state_does_not_crash(measure_mod):
    """空の状態ファイルでもキーが欠けない。"""
    result = measure_mod.measure({})

    assert result["pr"] is None
    assert result["prs"] == []
    assert result["rounds"] == 0
    assert result["cost"] == {
        "rounds": 0, "reviewer_launches": 0, "wall_clock_seconds": None}
    assert result["convergence"] == {"final": None, "oscillation": 0, "max_rounds": 0}
    assert "methods" in result


def test_wall_clock_is_null_while_the_run_has_not_ended(measure_mod):
    """終わっていない実行では実時間を出さない。**0 で埋めない。**"""
    st = _state(rounds=[_round(1)])
    del st["ended_at"]

    assert measure_mod.measure(st)["cost"]["wall_clock_seconds"] is None


# ---------- 呼び出し方 ----------


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_MEASURE), *args],
        capture_output=True, text=True, check=False,
    )


def test_cli_writes_json_to_stdout(tmp_path):
    state_file = tmp_path / "cross-review-pr123-state.json"
    state_file.write_text(json.dumps(_state(rounds=[_round(1)])), encoding="utf-8")

    proc = _run([str(state_file)])

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["pr"] == 123


def test_cli_writes_json_to_output_file(tmp_path):
    state_file = tmp_path / "cross-review-pr123-state.json"
    state_file.write_text(json.dumps(_state(rounds=[_round(1)])), encoding="utf-8")
    out = tmp_path / "measure.json"

    proc = _run([str(state_file), "--output", str(out)])

    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["cost"]["rounds"] == 1


def test_cli_fails_when_the_state_file_is_missing(tmp_path):
    proc = _run([str(tmp_path / "absent.json")])

    assert proc.returncode != 0
    assert "absent.json" in proc.stderr


# ---------- 受け入れ条件 5 / 6 / 8 / 9 / 11: 上限の方式（`oracle`） ----------


def test_oracle_counts_only_the_findings_that_were_fixed(measure_mod):
    """受け入れ条件 5。解決したスレッドと位置が結べた指摘だけを数える。"""
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10),
            _finding("codex-r1-1", 1, "b.py", 20),
        ],
    )

    result = measure_mod.measure(st)

    assert result["methods"]["oracle"] == {"found": 1, "unmatched": 0, "ambiguous": 0}
    assert measure_mod._oracle(st).finding_ids == {"codex-r1-0"}


def test_oracle_does_not_pick_up_a_finding_from_a_later_round(measure_mod):
    """受け入れ条件 6。round 1 の解決が、round 2 の同じ位置の指摘へ結ばれない。

    限らないと、**解決した時点でまだ存在しない指摘**を上限へ算入する。
    """
    st = _state(
        rounds=[
            _round(1, fix=_fix(_position("T1", "a.py", 10))),
            _round(2),
        ],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10),
            _finding("agy-r2-0", 2, "a.py", 10, agent="agy"),
        ],
    )

    oracle = measure_mod._oracle(st)

    assert oracle.finding_ids == {"codex-r1-0"}
    assert measure_mod.measure(st)["methods"]["oracle"]["found"] == 1


def test_oracle_takes_the_newest_round_when_several_match(measure_mod):
    """絞り込んだ後になお複数が一致するときは、ラウンドが最も新しいものを採る。

    行番号は修正で動くため、古い側へ結ぶと別の指摘を数える。
    """
    st = _state(
        rounds=[
            _round(1),
            _round(2, fix=_fix(_position("T1", "a.py", 10))),
        ],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10),
            _finding("agy-r2-0", 2, "a.py", 10, agent="agy"),
        ],
    )

    assert measure_mod._oracle(st).finding_ids == {"agy-r2-0"}


def test_oracle_reports_unmatched_threads(measure_mod):
    """受け入れ条件 8。どの指摘とも一致しないスレッドを `unmatched` に出す。

    落としたことが出力から見えないと、再現率が実際より高く出ていることに
    気づけない。
    """
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "z.py", 99)))],
        review_findings=[_finding("codex-r1-0", 1, "a.py", 10)],
    )

    assert measure_mod.measure(st)["methods"]["oracle"] == {
        "found": 0, "unmatched": 1, "ambiguous": 0}


def test_oracle_counts_a_thread_without_a_position_as_unmatched(measure_mod):
    """位置の欠けた要素は結べない。**落とさずに `unmatched` へ数える。**"""
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", None, None)))],
        review_findings=[_finding("codex-r1-0", 1, "a.py", 10)],
    )

    assert measure_mod.measure(st)["methods"]["oracle"] == {
        "found": 0, "unmatched": 1, "ambiguous": 0}


def test_oracle_reports_two_findings_at_one_position_as_ambiguous(measure_mod):
    """受け入れ条件 9。同じラウンドの同じ位置に別の本文の指摘が 2 件あるとき。

    **どちらを解決したかが記録から決まらない。** 片方を採ると、未修正の
    もう片方を上限へ算入しうる。
    """
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, body="別の本文 A"),
            _finding("agy-r1-0", 1, "a.py", 10, agent="agy", body="別の本文 B"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["oracle"] == {
        "found": 0, "unmatched": 0, "ambiguous": 1}


def test_oracle_ignores_merged_elements(measure_mod):
    """母集合は代表だけである。統合された側を数えると同じ指摘が 2 件になる。"""
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, origin_runtimes=["codex", "agy"],
                     merged_from=["agy-r1-0"]),
            _finding("agy-r1-0", 1, "a.py", 10, agent="agy",
                     merged_into="codex-r1-0"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["oracle"] == {
        "found": 1, "unmatched": 0, "ambiguous": 0}
    assert measure_mod._oracle(st).finding_ids == {"codex-r1-0"}


def test_oracle_does_not_link_across_pull_requests(measure_mod):
    """受け入れ条件 11。ローテーション済みの記録で Pull Request をまたいで結ばない。

    解決済みスレッドはその Pull Request のものである。ラウンドだけで絞ると、
    ローテーション前の Pull Request の指摘へ結ばれる。
    """
    st = _state(
        current_pr=124,
        pr_history=[{"pr": 123, "rounds": 1}, {"pr": 124, "rounds": 1}],
        rounds=[
            _round(1, pr=123),
            _round(2, pr=124, fix=_fix(_position("T1", "a.py", 10))),
        ],
        review_findings=[_finding("codex-r1-0", 1, "a.py", 10, pr=123)],
    )

    result = measure_mod.measure(st)

    assert result["methods"]["oracle"] == {"found": 0, "unmatched": 1, "ambiguous": 0}
    assert result["prs"] == [123, 124]
    assert result["cost"]["rounds"] == 2
    assert result["cost"]["reviewer_launches"] == 4


def test_oracle_counts_one_finding_once_for_two_resolutions(measure_mod):
    """同じ指摘を 2 度解決しても、上限の件数は 1 である。"""
    st = _state(
        rounds=[
            _round(1, fix=_fix(_position("T1", "a.py", 10))),
            _round(2, fix=_fix(_position("T2", "a.py", 10))),
        ],
        review_findings=[_finding("codex-r1-0", 1, "a.py", 10)],
    )

    assert measure_mod.measure(st)["methods"]["oracle"]["found"] == 1


# ---------- 受け入れ条件 1 / 2 / 3 / 4 / 10: 1 者だけの方式と多数決の方式 ----------


def test_single_reports_one_result_per_agent(measure_mod):
    """受け入れ条件 2。**担当ごとに 1 通り出す。**

    1 者だけの結果は誰を選ぶかで変わる。1 つの数字にまとめると、選び方が結果に混ざる。
    """
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, origin_runtimes=["codex"]),
            _finding("agy-r1-0", 1, "b.py", 20, agent="agy", origin_runtimes=["agy"]),
        ],
    )

    single = measure_mod.measure(st)["methods"]["single"]

    assert single["codex"] == {"found": 1, "matched": 1, "of_oracle": 1.0}
    assert single["agy"] == {"found": 1, "matched": 0, "of_oracle": 0.0}


def test_single_reads_the_agent_when_origin_runtimes_is_absent(measure_mod):
    """受け入れ条件 3。`origin_runtimes` を持たない指摘は `[agent]` として読む。

    **無いものを「0 者」として読むと、比較対象の過去の記録の `single` が全件
    0 になる。** 変更の前後を比べるのがこの測定の目的である。
    """
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10),
            _finding("agy-r1-0", 1, "b.py", 20, agent="agy"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert methods["single"]["codex"]["found"] == 1
    assert methods["single"]["agy"]["found"] == 1
    # 補った値は 1 者であるため、多数決には入らない。
    assert methods["majority"]["found"] == 0


def test_methods_do_not_count_a_merged_finding_twice(measure_mod):
    """受け入れ条件 4。母集合は代表だけである。

    統合された側を一緒に数えると、同じ指摘が 2 件になる。統合の前後で
    `single` の値が変わらないよう、代表の `origin_runtimes` で判定する。
    """
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, origin_runtimes=["codex", "agy"],
                     merged_from=["agy-r1-0"]),
            _finding("agy-r1-0", 1, "a.py", 10, agent="agy", merged_into="codex-r1-0"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert methods["single"]["codex"]["found"] == 1
    assert methods["single"]["agy"]["found"] == 1
    assert methods["majority"] == {"found": 1, "matched": 1, "of_oracle": 1.0}


def test_majority_takes_findings_with_two_or_more_origins(measure_mod):
    """多数決は `origin_runtimes` が 2 者以上の指摘を採る。"""
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, origin_runtimes=["codex", "agy"]),
            _finding("codex-r1-1", 1, "b.py", 20, origin_runtimes=["codex"]),
        ],
    )

    assert measure_mod.measure(st)["methods"]["majority"] == {
        "found": 1, "matched": 1, "of_oracle": 1.0}


def test_of_oracle_does_not_exceed_one_when_a_finding_was_not_fixed(measure_mod):
    """受け入れ条件 10。**分子は `found` ではない。**

    採用集合には修正されなかった指摘も却下された指摘も入る。`found` をそのまま
    割ると 1.0 を超える。
    """
    st = _state(
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10),
            _finding("codex-r1-1", 1, "b.py", 20, classification="rejected"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["single"]["codex"] == {
        "found": 2, "matched": 1, "of_oracle": 1.0}


# ---------- 受け入れ条件 1 / 7: この変更の方式（`proposed`） ----------


def test_four_methods_come_from_one_record(measure_mod):
    """受け入れ条件 1。4 つの方式を同じ記録から計算する。"""
    st = _state(
        evidence_rounds=[1],
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, origin_runtimes=["codex", "agy"],
                     classification="verified_blocking"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert set(methods) == {"single", "majority", "proposed", "oracle"}
    assert methods["single"]["codex"]["found"] == 1
    assert methods["majority"]["found"] == 1
    assert methods["proposed"]["found"] == 1
    assert methods["oracle"]["found"] == 1


def test_proposed_limits_the_denominator_to_marked_rounds(measure_mod):
    """受け入れ条件 7。**分母も印のあるラウンドに限る。**

    分子だけを絞ると、印の混ざった記録で再現率が過小に出る。全ラウンドの上限
    （2 件）で割ると、**拾えるものを全部拾っても 0.5 にしかならない。**
    """
    st = _state(
        evidence_rounds=[2],
        rounds=[
            _round(1, fix=_fix(_position("T1", "a.py", 10))),
            _round(2, fix=_fix(_position("T2", "b.py", 20))),
        ],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10),
            _finding("codex-r2-0", 2, "b.py", 20, classification="verified_blocking"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert methods["oracle"]["found"] == 2
    assert methods["proposed"] == {
        "found": 1, "matched": 1, "of_oracle": 1.0,
        "oracle_scope": "evidence_rounds", "oracle_base": 1}


def test_proposed_reports_all_rounds_when_every_round_is_marked(measure_mod):
    """印が全ラウンドに付いていれば、分母は他の 3 つと同じである。

    添えないと、読む側が `proposed` の再現率を他の 3 つと同じ分母の値として読む。
    """
    st = _state(
        evidence_rounds=[1, 2],
        rounds=[
            _round(1, fix=_fix(_position("T1", "a.py", 10))),
            _round(2, fix=_fix(_position("T2", "b.py", 20))),
        ],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
            _finding("codex-r2-0", 2, "b.py", 20, classification="needs_human_judgment"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["proposed"] == {
        "found": 2, "matched": 2, "of_oracle": 1.0,
        "oracle_scope": "all_rounds", "oracle_base": 2}


def test_proposed_takes_only_the_two_counted_classifications(measure_mod):
    """採るのは `verified_blocking` と `needs_human_judgment` の 2 つだけである。

    棄却した指摘と立証できなかった指摘は採らない。
    """
    st = _state(
        evidence_rounds=[1],
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
            _finding("codex-r1-1", 1, "b.py", 20, classification="rejected"),
            _finding("codex-r1-2", 1, "c.py", 30,
                     classification="insufficient_evidence"),
            _finding("agy-r1-0", 1, "d.py", 40, agent="agy",
                     classification="needs_human_judgment"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["proposed"]["found"] == 2


def test_proposed_ignores_findings_from_unmarked_rounds(measure_mod):
    """印の無いラウンドの指摘を母集合へ入れない。

    入れると、区分の付かない指摘が `insufficient_evidence` として落ち、方式の
    再現率が実際より低く出る。
    """
    st = _state(
        evidence_rounds=[2],
        rounds=[
            _round(1, fix=_fix(_position("T1", "a.py", 10))),
            _round(2, fix=_fix(_position("T2", "b.py", 20))),
        ],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
            _finding("codex-r2-0", 2, "b.py", 20, classification="verified_blocking"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["proposed"]["found"] == 1


# ---------- 受け入れ条件 12: 計算できない値が理由つきの `null` で出る ----------


def test_proposed_is_null_when_no_round_carries_the_evidence_mark(measure_mod):
    """印を持つラウンドが無ければ、この変更の方式は計算できない。

    **キーは常に置く。** 省くと、読む側が「0 件」と「計算できない」を区別できず、
    欠けたキーを読んで落ちる。
    """
    st = _state(
        evidence_rounds=[],
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
        ],
    )

    assert measure_mod.measure(st)["methods"]["proposed"] == {
        "found": None, "matched": None, "of_oracle": None,
        "oracle_scope": None, "oracle_base": None,
        "reason": "no_evidence_rounds"}


def test_oracle_is_null_without_any_recorded_position(measure_mod):
    """位置の記録を持つラウンドが無ければ、上限の方式は計算できない。

    **値を 0 で埋めない。** 埋めると、この変更より前に取った記録が「修正が
    1 件も無かった実行」として比較へ混ざる。
    """
    st = _state(
        evidence_rounds=[1],
        rounds=[_round(1, fix={"commit": "abc1234", "fixed": 1,
                               "resolved_threads": 1, "resolved_thread_ids": ["T1"]})],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert methods["oracle"] == {
        "found": None, "unmatched": None, "ambiguous": None,
        "reason": "no_resolved_thread_positions"}
    # 拾えた件数と再現率は全方式で決まらない。**件数はそのまま出す。**
    assert methods["single"]["codex"] == {
        "found": 1, "matched": None, "of_oracle": None}
    assert methods["majority"] == {"found": 0, "matched": None, "of_oracle": None}
    assert methods["proposed"] == {
        "found": 1, "matched": None, "of_oracle": None,
        "oracle_scope": "all_rounds", "oracle_base": None}


def test_of_oracle_is_null_when_the_oracle_is_empty(measure_mod):
    """上限の方式が 0 件なら、再現率は割れない。

    `0.0` にすると「拾えなかった」と読めるが、実際は比べる相手がいない。
    """
    st = _state(
        evidence_rounds=[1],
        rounds=[_round(1, fix=_fix())],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert methods["oracle"] == {"found": 0, "unmatched": 0, "ambiguous": 0}
    assert methods["single"]["codex"] == {"found": 1, "matched": 0, "of_oracle": None}
    assert methods["proposed"] == {
        "found": 1, "matched": 0, "of_oracle": None,
        "oracle_scope": "all_rounds", "oracle_base": 0}


def test_proposed_base_is_zero_when_marked_rounds_fixed_nothing(measure_mod):
    """印のあるラウンドに修正された指摘が 1 件も無いとき。

    分母は `0` で、再現率は `null` である。**`found` と `matched` は数えた件数を
    そのまま出す。**
    """
    st = _state(
        evidence_rounds=[2],
        rounds=[
            _round(1, fix=_fix(_position("T1", "a.py", 10))),
            _round(2, fix=_fix()),
        ],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
            _finding("codex-r2-0", 2, "b.py", 20, classification="verified_blocking"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert methods["oracle"]["found"] == 1
    assert methods["proposed"] == {
        "found": 1, "matched": 0, "of_oracle": None,
        "oracle_scope": "evidence_rounds", "oracle_base": 0}


def test_reason_is_absent_while_the_value_is_decided(measure_mod):
    """**`reason` は値が `null` のときだけ置く。**

    決まった値に添えると、読む側が例外の有無を毎回見分けることになる。
    """
    st = _state(
        evidence_rounds=[1],
        rounds=[_round(1, fix=_fix(_position("T1", "a.py", 10)))],
        review_findings=[
            _finding("codex-r1-0", 1, "a.py", 10, classification="verified_blocking"),
        ],
    )

    methods = measure_mod.measure(st)["methods"]

    assert "reason" not in methods["oracle"]
    assert "reason" not in methods["proposed"]
    assert "reason" not in methods["majority"]
    assert "reason" not in methods["single"]["codex"]


def test_empty_state_reports_both_reasons(measure_mod):
    """記録が無いときも落ちず、2 つの理由が出る（受け入れ条件 12 / 15）。"""
    methods = measure_mod.measure({})["methods"]

    assert methods["single"] == {}
    assert methods["majority"] == {"found": 0, "matched": None, "of_oracle": None}
    assert methods["proposed"]["reason"] == "no_evidence_rounds"
    assert methods["oracle"]["reason"] == "no_resolved_thread_positions"


# ---------- 書き込み側と読み取り側をつなげて 1 度通す ----------


def test_state_writes_positions_that_measure_reads(monkeypatch, tmp_path, state_mod):
    """`state.py merge-fix` が書いた記録を、そのまま `measure.py` が読む。

    **落ちるのは形の組み合わせである。** 書く側と読む側を別々に試すと、書いた側が
    残した位置の形を読む側が受け取れなくても、失敗として現れない。
    """
    import argparse

    pr = 9919
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    state_file = tmp_path / f"cross-review-pr{pr}-state.json"
    state_file.write_text(json.dumps(_state(
        current_pr=pr,
        pr_history=[{"pr": pr, "rounds": 1}],
        rounds=[{"round": 1, "pr": pr, "reviewers": ["codex", "agy"],
                 "started_at": "2026-05-23T00:00:00+00:00"}],
        review_findings=[
            _finding("codex-r1-0", 1, "src/foo.py", 42, pr=pr,
                     origin_runtimes=["codex", "agy"],
                     classification="verified_blocking"),
            _finding("agy-r1-0", 1, "src/foo.py", 42, pr=pr, agent="agy",
                     merged_into="codex-r1-0"),
        ],
        evidence_rounds=[1],
        deferred_nits=[],
    )), encoding="utf-8")
    (tmp_path / f"fix-pr{pr}-result.json").write_text(json.dumps({
        "pr": pr, "fix_commit": "abc1234", "fixed_count": 1,
        "ci_status": "SUCCESS", "ci_failed_checks": [],
        "by_severity": {"critical": 0, "major": 1, "minor": 0, "nit": 0},
        "resolved_threads": [
            {"thread_id": "T1", "comment_id": 111, "path": "src/foo.py", "line": 42},
            {"thread_id": "T2", "comment_id": 222, "path": "src/gone.py", "line": 99},
        ],
        "deferred": [], "rejected": [],
    }), encoding="utf-8")

    state_mod.cmd_merge_fix(argparse.Namespace(pr=pr, file=None))
    proc = _run([str(state_file)])

    assert proc.returncode == 0, proc.stderr
    methods = json.loads(proc.stdout)["methods"]
    assert methods["oracle"] == {"found": 1, "unmatched": 1, "ambiguous": 0}
    assert methods["single"]["agy"] == {"found": 1, "matched": 1, "of_oracle": 1.0}
    assert methods["majority"]["matched"] == 1
    assert methods["proposed"]["of_oracle"] == 1.0
