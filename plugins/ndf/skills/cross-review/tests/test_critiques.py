"""反証の取り込み（#156 の 3 本目 Task 3）。

**提案者以外が各指摘へ 1 つの値を返す。** 自己支持を数に入れると、1 者が出した指摘が
常に 1 票を持つ。統合された指摘では `origin_runtimes` に載る担当すべてが提案者である。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 6003


def _finding(fid, agent, **over):
    f = {
        "finding_id": fid, "agent": agent, "path": "a.py", "line": 1,
        "body": "x", "severity": "major", "pr": PR, "round": 1,
        "origin_runtimes": [agent],
    }
    f.update(over)
    return f


def _state(findings, **over):
    st = {
        "current_pr": PR, "repo": "o/r", "max_rounds": 12, "rotate_after": 8,
        "only": None, "host": "claude",
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-10T00:00:00+00:00"}],
        "review_findings": list(findings), "final": None,
    }
    st.update(over)
    return st


def _write(tmp_dir, st):
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st))


def _read(tmp_dir):
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _critique_file(tmp_dir, agent, items):
    (tmp_dir / f"{agent}-critique-pr{PR}-round1.json").write_text(
        json.dumps({"critiques": items}))


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def collect(state_mod, expect_rc: int | None = None) -> int:
    """反証を取り込み、終了コードを返す。

    **揃わないときの 7 は「揃ったことの確認」の節で見る。** 結び方のテストで対象の
    全件へ反証を用意すると、確かめたい 1 件が他の値に埋もれる。
    """
    rc = 0
    try:
        state_mod.cmd_collect_critiques(argparse.Namespace(pr=PR))
    except SystemExit as exc:
        rc = int(exc.code or 0)
    if expect_rc is not None:
        assert rc == expect_rc, f"終了コードが {rc}"
    return rc


# ---------- 取り込み ----------

def test_a_critique_is_attached_to_the_finding(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "None を弾く"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert got == [{"agent": "kiro", "verdict": "refute", "reason": "None を弾く"}]


def test_the_agent_comes_from_the_file_name(tmp_dir, state_mod):
    """**本文の申告を採らない。** 別の担当を名乗った値をそのまま数えることになる。"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "確認した",
         "agent": "agy"}])

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0]["critiques"][0]["agent"] == "kiro"


def test_critiques_from_several_agents_accumulate(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "a"}])
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "b"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert sorted(c["agent"] for c in got) == ["agy", "kiro"]


# ---------- 自分の指摘へは返さない ----------

def test_a_critique_on_own_finding_is_dropped(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "codex", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "自分で確認"}])

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0].get("critiques", []) == []


def test_a_critique_from_a_merged_proposer_is_dropped(tmp_dir, state_mod):
    """統合された指摘では、`origin_runtimes` の担当すべてが提案者である。"""
    _write(tmp_dir, _state([
        _finding("codex-r1-0", "codex", origin_runtimes=["codex", "kiro"])]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "自分も出した"}])

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0].get("critiques", []) == []


# ---------- 結び先が無いとき ----------

def test_an_unknown_finding_id_is_kept_separately(tmp_dir, state_mod):
    """**黙って捨てない。** 反証 0 件と、結び先を誤ったラウンドが同じに見える。"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "nowhere-r1-9", "verdict": "refute", "reason": "?"}])

    collect(state_mod)

    st = _read(tmp_dir)
    assert st["review_findings"][0].get("critiques", []) == []
    assert st["unmatched_critiques"][0]["finding_id"] == "nowhere-r1-9"
    assert st["unmatched_critiques"][0]["agent"] == "kiro"


def test_a_duplicate_keeps_its_target(tmp_dir, state_mod):
    """`duplicate` は相手の `finding_id` を持つ。2 段目の統合が読む。"""
    _write(tmp_dir, _state([
        _finding("codex-r1-0", "codex"), _finding("kiro-r1-0", "kiro")]))
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "duplicate",
         "duplicate_of": "kiro-r1-0", "reason": "同じ箇所"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"][0]
    assert got["duplicate_of"] == "kiro-r1-0"


# ---------- 形が違うとき ----------

def test_an_unknown_verdict_is_dropped(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "maybe", "reason": "?"}])

    collect(state_mod)

    st = _read(tmp_dir)
    assert st["review_findings"][0].get("critiques", []) == []
    assert st["unmatched_critiques"][0]["verdict"] == "maybe"


def test_a_broken_file_does_not_stop_the_import(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    (tmp_dir / f"kiro-critique-pr{PR}-round1.json").write_text("not json")
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "a"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert [c["agent"] for c in got] == ["agy"]


def test_no_files_leaves_the_findings_untouched(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0].get("critiques", []) == []


# ---------- 同じ担当の反証は置き換える（#549 レビュー対応） ----------
#
# 同じラウンドの反証を取り直して `collect-critiques` を再実行すると、古い値へ
# 積み増していた。`refute` を `support` へ訂正しても両方が並び、区分の順で `refute`
# が先に当たって指摘が `rejected` のままになる。

def test_recollecting_replaces_the_same_agents_critique(tmp_dir, state_mod):
    """**`(ラウンド, finding_id, 担当)` が持つ値は 1 つである。**"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "誤りだと思う"}])
    collect(state_mod)

    # 取り直して訂正する（refute → support）。
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "読み直した"}])
    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert got == [{"agent": "kiro", "verdict": "support", "reason": "読み直した"}]


def test_a_corrected_verdict_stops_the_finding_from_staying_rejected(
        tmp_dir, state_mod):
    """訂正が効かないと `rejected` のまま残る。区分まで見て固定する。"""
    _write(tmp_dir, _state([
        _finding("codex-r1-0", "codex", has_evidence=True)]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "誤りだと思う"}])
    collect(state_mod)
    assert state_mod._classify_finding(
        _read(tmp_dir)["review_findings"][0]) == "rejected"

    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "読み直した"}])
    collect(state_mod)

    assert state_mod._classify_finding(
        _read(tmp_dir)["review_findings"][0]) == "needs_human_judgment"


def test_recollecting_does_not_inflate_the_unmatched_list(tmp_dir, state_mod):
    """取り直しは同じ結果ファイルを読み直す。**結び先なしを二重に数えない。**"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "nowhere-r1-9", "verdict": "refute", "reason": "?"}])

    collect(state_mod)
    collect(state_mod)

    assert len(_read(tmp_dir)["unmatched_critiques"]) == 1


def test_the_other_agents_critique_is_kept(tmp_dir, state_mod):
    """置き換えるのは同じ担当の値だけである。"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "a"}])
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "b"}])
    collect(state_mod)

    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "c"}])
    collect(state_mod)

    got = {c["agent"]: c["verdict"] for c in
           _read(tmp_dir)["review_findings"][0]["critiques"]}
    assert got == {"agy": "support", "kiro": "support"}


# ---------- 揃ったことを確かめてから印を付ける（#549 レビュー対応） ----------
#
# 結果ファイルが欠落・不正でも印が付くと、実行検証を持たない単独の major が
# `insufficient_evidence` へ落ち、新規 0 件のまま未検証で収束する。

def _cover(tmp_dir, agents, fid="codex-r1-0"):
    for agent in agents:
        _critique_file(tmp_dir, agent, [
            {"finding_id": fid, "verdict": "insufficient_evidence", "reason": "?"}])


def test_the_marker_is_written_when_every_target_is_covered(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _cover(tmp_dir, ("agy", "kiro"))

    collect(state_mod, expect_rc=0)

    assert _read(tmp_dir)["evidence_rounds"] == [1]


@pytest.mark.parametrize("setup", ["missing", "broken", "partial"])
def test_a_missing_or_invalid_result_leaves_the_round_unmarked(
        tmp_dir, state_mod, setup):
    """**印を付けず、終了コード 7 で再取得へ戻す。**"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    if setup == "broken":
        _cover(tmp_dir, ("agy",))
        (tmp_dir / f"kiro-critique-pr{PR}-round1.json").write_text("not json")
    elif setup == "partial":
        _cover(tmp_dir, ("agy",))
        _critique_file(tmp_dir, "kiro", [
            {"finding_id": "codex-r1-0", "verdict": "maybe", "reason": "?"}])

    collect(state_mod, expect_rc=7)

    st = _read(tmp_dir)
    assert st.get("evidence_rounds", []) == []
    assert state_mod._evidence_completed(st, 1) is False


def test_an_unmarked_round_still_counts_every_finding(tmp_dir, state_mod):
    """印が無いラウンドは全件を数える。**未検証のまま収束しない。**

    実行検証も支持も無い単独の `major` は `insufficient_evidence` へ落ちる。印が
    付いていれば数える 2 つから外れて新規 0 件になり、そのまま収束する。
    """
    _write(tmp_dir, _state([_finding("agy-r1-0", "agy")]))
    # 新規性は担当の payload から数える（`_finding_keys`）。round 1 の担当は agy / kiro。
    (tmp_dir / f"agy-review-pr{PR}-round1-payload.json").write_text(json.dumps(
        {"comments": [{"path": "a.py", "line": 1, "body": "x",
                       "severity": "major"}]}))

    collect(state_mod, expect_rc=7)

    st = _read(tmp_dir)
    count, measurable = state_mod._new_finding_count(st, PR)
    assert measurable is True
    assert count == 1


def test_the_retry_agents_are_returned(tmp_dir, state_mod, capsys):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _cover(tmp_dir, ("agy",))

    collect(state_mod, expect_rc=7)

    out = capsys.readouterr().out
    assert "CRITIQUE_RETRY_AGENTS='kiro'" in out
    assert "CRITIQUE_RETRY_AGENTS_CSV=kiro" in out


def test_the_retry_happens_once_per_round(tmp_dir, state_mod):
    """**取り直しは同じラウンドで 1 度だけである。** 2 度目は 0 で返して工程を進める。"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))

    collect(state_mod, expect_rc=7)
    collect(state_mod, expect_rc=0)

    st = _read(tmp_dir)
    assert sorted(st["rounds"][0]["critique_relaunched"]) == ["agy", "kiro"]
    # 揃わないまま進むが、印は付かないので全件が数えられる。
    assert st.get("evidence_rounds", []) == []


def test_a_proposer_only_round_is_marked_without_any_file(tmp_dir, state_mod):
    """**反証の対象が無い担当は不足に数えない。** 全員が提案者なら印が付く。"""
    _write(tmp_dir, _state([
        _finding("agy-r1-0", "agy", origin_runtimes=["agy", "kiro"])]))

    collect(state_mod, expect_rc=0)

    assert _read(tmp_dir)["evidence_rounds"] == [1]


def test_a_merged_finding_is_not_counted_as_missing(tmp_dir, state_mod):
    """束ねられた側は対象から外れる。**不足は統合の後に数える。**"""
    _write(tmp_dir, _state([
        _finding("codex-r1-0", "codex", body="片方の本文"),
        _finding("kiro-r1-0", "kiro", body="もう片方の本文"),
    ]))
    for agent, fid, other in (("kiro", "codex-r1-0", "kiro-r1-0"),
                              ("agy", "kiro-r1-0", "codex-r1-0")):
        _critique_file(tmp_dir, agent, [
            {"finding_id": fid, "verdict": "duplicate",
             "duplicate_of": other, "reason": "同じ主張である"}])

    # kiro は codex-r1-0 だけ、agy は kiro-r1-0 だけへ返している。統合の前に数えると
    # 双方 1 件ずつ不足するが、束ねた後は代表 1 件だけが残る。
    rc = collect(state_mod)

    st = _read(tmp_dir)
    findings = {f["finding_id"]: f for f in st["review_findings"]}
    assert "merged_into" in findings["kiro-r1-0"]
    assert rc == 7  # 代表へ返していない担当が 1 者残る
    assert st["rounds"][0]["critique_relaunched"] == ["agy"]


# ---------- 起動（critique.sh） ----------

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _state_file(tmp_dir, findings, worktree):
    st = _state(findings)
    st["worktree_path"] = str(worktree)
    _write(tmp_dir, st)


def run_critique(tmp_dir, agent, worktree, launcher=None):
    import subprocess
    env = dict(os.environ, CROSS_REVIEW_TMP_DIR=str(tmp_dir))
    if launcher:
        env["PATH"] = f"{launcher}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPTS / "critique.sh"), agent, str(PR), "1"],
        capture_output=True, text=True, env=env,
    )


import os


@pytest.fixture()
def fake_launcher(tmp_path):
    """`launch-cli.sh` の呼び出しを記録するだけの置き換え。"""
    lib = tmp_path / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "launch-cli.sh").write_text(
        '#!/usr/bin/env bash\necho "launched $1" > "$6/launched.txt"\n')
    (lib / "launch-cli.sh").chmod(0o755)
    return lib


def test_the_prompt_lists_only_other_agents_findings(tmp_dir, tmp_path):
    """**自分が出した指摘は渡さない。**"""
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex"),
        _finding("kiro-r1-0", "kiro"),
    ], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "codex-r1-0" in prompt
    assert "kiro-r1-0" not in prompt


def test_a_merged_proposer_is_excluded(tmp_dir, tmp_path):
    """統合された指摘では `origin_runtimes` の担当すべてが提案者である。"""
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex", origin_runtimes=["codex", "kiro"]),
    ], work)

    result = run_critique(tmp_dir, "kiro", work)

    assert "対象がありません" in result.stdout
    assert not (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").exists()


@pytest.mark.parametrize("findings", [
    [],
    [_finding("codex-r2-0", "codex", round=2)],
], ids=["empty-findings", "other-round-only"])
def test_no_targets_exits_successfully_without_launching(tmp_dir, tmp_path, findings):
    """現状固定: 対象 0 件では案内だけを出し、生成・起動を行わない。"""
    work = tmp_path / "work"
    work.mkdir()
    _state_file(tmp_dir, findings, work)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "kiro-cli"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    before = set(tmp_dir.rglob("*"))

    result = run_critique(tmp_dir, "kiro", work, launcher=bin_dir)

    assert result.returncode == 0, result.stderr
    assert "反証の対象がありません" in result.stdout
    assert result.stderr == ""
    assert not (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").exists()
    assert set(tmp_dir.rglob("*")) == before


# ---------- 1 ラウンドの通し（critique-round.sh / #549 レビュー対応） ----------
#
# **未起動の担当を監視へ渡すと 30 秒止まる。** critique.sh は反証の対象が無い担当で
# `launch-cli.sh` を呼ばずに終わるため `<stem>.pid` を作らない。monitor.py はその
# pid ファイルを 30 秒ポーリングしたうえで PIDFILE_BAD (exit 6) を返す。
#
# 上限を 15 秒に置くのは、**30 秒の停止が起きていないこと**を測るためである
# （起きていれば必ず 30 秒を超える）。

MONITOR_GRACE_SECONDS = 30
ROUND_TIME_LIMIT = 15


def run_round(tmp_dir, agents, round_no=1):
    import subprocess
    env = dict(os.environ, CROSS_REVIEW_TMP_DIR=str(tmp_dir))
    return subprocess.run(
        ["bash", str(SCRIPTS / "critique-round.sh"), str(PR), str(round_no), *agents],
        capture_output=True, text=True, env=env,
    )


def test_a_round_without_targets_does_not_block(tmp_dir, tmp_path):
    """**指摘 0 件で収束するラウンドで止まらない。**"""
    import time as _time
    work = tmp_path / "work"
    work.mkdir()
    _state_file(tmp_dir, [], work)

    started = _time.monotonic()
    result = run_round(tmp_dir, ["agy", "kiro"])
    elapsed = _time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < ROUND_TIME_LIMIT, f"{elapsed:.1f} 秒かかった（監視が待っている）"


def test_a_stale_pidfile_is_not_read_as_a_launch(tmp_dir, tmp_path):
    """**`<stem>` はラウンドを名前に持たない。** 残骸を起動済みと読まない。"""
    import time as _time
    work = tmp_path / "work"
    work.mkdir()
    _state_file(tmp_dir, [], work)
    stale = tmp_dir / f"agy-critique-pr{PR}.pid"
    stale.write_text("999999\n")

    started = _time.monotonic()
    result = run_round(tmp_dir, ["agy", "kiro"])
    elapsed = _time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert not stale.exists(), "前のラウンドの pid ファイルが残っている"
    assert elapsed < ROUND_TIME_LIMIT, f"{elapsed:.1f} 秒かかった（監視が待っている）"


def test_the_stale_pidfile_is_removed_even_without_targets(tmp_dir, tmp_path):
    """捨てるのは、対象が無くて起動しない経路より前である。"""
    work = tmp_path / "work"
    work.mkdir()
    _state_file(tmp_dir, [_finding("kiro-r1-0", "kiro")], work)
    stale = tmp_dir / f"kiro-critique-pr{PR}.pid"
    stale.write_text("999999\n")

    result = run_critique(tmp_dir, "kiro", work)

    assert result.returncode == 0, result.stderr
    assert "反証の対象がありません" in result.stdout
    assert not stale.exists()


def test_a_merged_side_is_not_offered(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex"),
        _finding("codex-r1-1", "codex", merged_into="codex-r1-0"),
    ], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "codex-r1-0" in prompt
    assert "codex-r1-1" not in prompt


def test_the_prompt_carries_the_verification_result(tmp_dir, tmp_path):
    """**実行検証の結果を射影から落とさない**（#549 レビュー対応）。

    反証は実行検証の後にあり、担当は直前に実行したコマンド・終了コード・再現の結果を
    読んだうえで賛否を決める。落とすと、結果を見ないまま賛否を返すことになる。
    """
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex",
                 suggested_check="pytest tests/t.py::test_x",
                 verification={"command": "pytest tests/t.py::test_x",
                               "exit_code": 1, "result": "reproduced",
                               "finding_id": "kiro-r1-2",
                               "ran_at": "2026-09-10T00:00:00+00:00"}),
    ], work)

    result = run_critique(tmp_dir, "kiro", work)

    assert result.returncode == 0, result.stderr
    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert '"verification"' in prompt
    assert '"result": "reproduced"' in prompt
    assert '"exit_code": 1' in prompt
    assert "pytest tests/t.py::test_x" in prompt
    # 出所（束ねた組から選んだ手順を書いた指摘）も残る。
    assert "kiro-r1-2" in prompt


def test_the_prompt_explains_not_run(tmp_dir, tmp_path):
    """**`not_run` を「再現しなかった」と読ませない。**"""
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "not_run" in prompt
    assert "実行していないだけ" in prompt


def test_the_prompt_names_the_five_verdicts(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    for verdict in ("support", "refute", "insufficient_evidence",
                    "duplicate", "out_of_scope"):
        assert verdict in prompt


def test_the_prompt_forbids_editing(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "編集しない" in prompt


def test_an_unknown_runtime_is_refused(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    result = run_critique(tmp_dir, "nowhere", work)

    assert result.returncode != 0


@pytest.mark.parametrize("state_setup", ["missing", "empty"], ids=["missing", "empty"])
def test_missing_or_empty_state_file_exits_with_error(tmp_dir, tmp_path, state_setup):
    """現状固定: state.json が存在しない、または空ファイルの場合は終了コード 1 で終了する。"""
    work = tmp_path / "work"
    work.mkdir()
    if state_setup == "empty":
        (tmp_dir / f"cross-review-pr{PR}-state.json").write_text("")

    result = run_critique(tmp_dir, "kiro", work)

    assert result.returncode == 1
    assert "state.json not found" in result.stderr



# ---------- 通し経路（critique.sh → launch-cli.sh） ----------
#
# 現状固定: 他の担当の指摘があるとき、critique.sh はプロンプトを組み立てて
# 共通層の `launch-cli.sh` を呼び、終了コード 0 で正常終了する。launch-cli.sh は
# `kiro` ランタイムに対し `kiro-cli` を作業ツリーで背景起動するので、CLI 本体を
# 記録するだけのスタブへ差し替え、経路が通ることと渡された引数を固定する。

import subprocess  # noqa: E402  (通し経路のテストが使う)
import time  # noqa: E402


@pytest.fixture()
def kiro_cli_stub(tmp_path):
    """`launch-cli.sh` が `kiro` に対して起動する `kiro-cli` を記録するだけの置き換え。

    実物の `launch-cli.sh` を通したうえで、CLI 本体だけを無害なスタブへ差し替える。
    呼び出し引数と作業ディレクトリ、標準入力（プロンプト本文）を記録する。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "kiro-cli-calls.log"
    stub = bin_dir / "kiro-cli"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "cwd=%s\\n" "$(pwd)" >> "$NDF_TEST_KIRO_LOG"\n'
        'printf "argv=%s\\n" "$*" >> "$NDF_TEST_KIRO_LOG"\n'
        'cat >> "$NDF_TEST_KIRO_LOG"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    log.write_text("")
    return {"bin": bin_dir, "log": log}


def test_normal_path_builds_prompt_and_launches_the_cli(tmp_dir, tmp_path, kiro_cli_stub):
    """現状固定: 他の担当の指摘があると、プロンプトを組み立てて launch-cli.sh を
    呼び、終了コード 0 で終わる。launch-cli.sh は作業ツリーで生成された
    プロンプトを渡して `kiro-cli` を起動する。"""
    work = tmp_path / "work"
    work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    env = dict(
        os.environ,
        CROSS_REVIEW_TMP_DIR=str(tmp_dir),
        PATH=f"{kiro_cli_stub['bin']}{os.pathsep}{os.environ['PATH']}",
        NDF_TEST_KIRO_LOG=str(kiro_cli_stub["log"]),
    )
    result = subprocess.run(
        ["bash", str(SCRIPTS / "critique.sh"), "kiro", str(PR), "1"],
        capture_output=True, text=True, env=env,
    )

    # 経路が通り、終了コード 0 で正常終了する。
    assert result.returncode == 0, result.stderr

    # プロンプトが作業ツリーのパスと対象の指摘を載せて生成される。
    prompt_path = tmp_dir / f"kiro-critique-pr{PR}-prompt.md"
    assert prompt_path.is_file()
    prompt = prompt_path.read_text()
    assert str(work) in prompt
    assert "codex-r1-0" in prompt

    # launch-cli.sh は作業ツリーへ cd してから STEM に沿った成果物（pid ファイル）を
    # 作る。**STEM は絶対パスで渡す。** 相対の値を渡すと、pid ファイルと標準出力の
    # 記録が作業ツリーの直下へ落ちて差分に現れる。
    pid_file = tmp_dir / f"kiro-critique-pr{PR}.pid"
    for _ in range(100):
        if pid_file.is_file():
            break
        time.sleep(0.05)
    assert pid_file.is_file(), "launch-cli.sh が起動していない"
    assert not (work / f"kiro-critique-pr{PR}.pid").exists(), "作業ツリーを汚している"
    assert not (work / f"kiro-critique-pr{PR}-stdout.log").exists(), \
        "作業ツリーを汚している"

    # kiro-cli が作業ツリーで、生成されたプロンプトを標準入力に受けて起動される。
    for _ in range(100):
        recorded = kiro_cli_stub["log"].read_text()
        if "codex-r1-0" in recorded:
            break
        time.sleep(0.05)
    assert f"cwd={work}" in recorded
    assert "codex-r1-0" in recorded
