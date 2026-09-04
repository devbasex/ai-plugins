"""収束の判定が継続的統合を見る（#327）。

判定は 2 つの外部 AI の判定だけを読んでおり、検査ジョブが落ちていても両者が承認すれば
収束していた。**収束の直前に 1 度だけ照会する。**

| 照会の結果 | 終了コード | 状態ファイルへ残すもの |
| --- | --- | --- |
| code-related の失敗が 1 件以上 | 2（修正へ） | `ci.verdict = "code_failure"` と失敗した名前 |
| meta-only の失敗だけ | 0（収束） | `ci.verdict = "meta_only"` |
| 完了した失敗が無く、未完了が 1 件以上 | 0（収束） | `ci.verdict = "pending"` と未完了の名前 |
| すべて成功 | 0（収束） | `ci.verdict = "success"` |
| 照会できなかった | 0（収束） | `ci.verdict = "unverified"` と理由 |

**進行を止めない側へ倒す。** 照会できないことは、承認されたラウンドを差し戻す理由にならない。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 7731
REPO = "o/r"
HEAD_SHA = "b87b3ae"


def _run(name: str, status: str = "completed", conclusion: str = "success") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def check_runs(monkeypatch, state_mod):
    """検査ジョブの照会を差し替え、呼ばれた回数を数える。"""
    calls: list[tuple[str, str]] = []

    def _set(runs):
        def _fetch(repo, sha):
            calls.append((repo, sha))
            return runs
        monkeypatch.setattr(state_mod, "_fetch_check_runs", _fetch)
        return calls

    _set.calls = calls  # type: ignore[attr-defined]
    return _set


def _approved_round(no: int = 1, **over) -> dict:
    entry = {
        "round": no,
        "pr": PR,
        "started_at": "2026-09-04T00:00:00+00:00",
        "codex": {"intent": "APPROVE", "by_severity": {}},
        "agy": {"intent": "APPROVE", "by_severity": {}},
        "head_sha": HEAD_SHA,
    }
    entry.update(over)
    return entry


def _state(rounds: list[dict], **over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": rounds,
        "deferred_nits": [],
        "carried_over": None,
        "final": None,
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


# ---------------- 条件 3: 落ちているラウンドを収束させない ----------------

def test_a_code_related_failure_sends_the_round_to_the_fix_step(tmp_dir, state_mod, check_runs):
    check_runs([_run("pytest", conclusion="failure"), _run("markdown-link-check")])
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 2
    st = _read(tmp_dir)
    assert st["final"] is None            # 中断しない。修正の機会を残す
    assert st["rounds"][-1]["verdict"] == "changes_requested"
    assert st["rounds"][-1]["ci"]["verdict"] == "code_failure"
    assert st["rounds"][-1]["ci"]["failed"] == ["pytest"]


def test_a_meta_only_failure_still_converges(tmp_dir, state_mod, check_runs):
    check_runs([_run("check_pr_requirements", conclusion="failure"), _run("pytest")])
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    st = _read(tmp_dir)
    assert st["final"] == "approved"
    assert st["rounds"][-1]["ci"]["verdict"] == "meta_only"
    assert st["rounds"][-1]["ci"]["meta_failed"] == ["check_pr_requirements"]


def test_all_green_converges(tmp_dir, state_mod, check_runs):
    check_runs([_run("pytest"), _run("markdown-link-check")])
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert _read(tmp_dir)["rounds"][-1]["ci"]["verdict"] == "success"


# ---------------- 条件 4: 実行中の検査ジョブを失敗として扱わない ----------------

def test_a_running_check_is_not_a_failure(tmp_dir, state_mod, check_runs, capsys):
    check_runs([_run("pytest", status="in_progress", conclusion=""), _run("lint")])
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    ci = _read(tmp_dir)["rounds"][-1]["ci"]
    assert ci["verdict"] == "pending"
    assert ci["pending"] == ["pytest"]
    # 未完了のまま収束したことを、判定の出力にも残す
    assert "pytest" in capsys.readouterr().err


# ---------------- 条件 5: 照会できないときに収束を止めない ----------------

def test_an_unavailable_query_still_converges(tmp_dir, state_mod, check_runs):
    check_runs(None)
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    st = _read(tmp_dir)
    assert st["final"] == "approved"
    assert st["rounds"][-1]["ci"]["verdict"] == "unverified"
    assert st["rounds"][-1]["ci"]["reason"]


def test_no_check_run_is_treated_as_unavailable(tmp_dir, state_mod, real_github, monkeypatch):
    """検査ジョブを 1 件も持たないリポジトリでも収束する。

    `_fetch_check_runs` は `total_count` が 0 のとき `None` を返す。ここでは
    REST の応答そのものから、その扱いになることを見る。
    """
    monkeypatch.setattr(
        state_mod, "_gh_rest",
        lambda path: state_mod.RestResponse(
            headers={}, body={"total_count": 0, "check_runs": []},
            rate_remaining=None, rate_reset=None,
        ),
    )
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert _read(tmp_dir)["rounds"][-1]["ci"]["verdict"] == "unverified"


# ---------------- 照会の位置 ----------------

def test_the_query_runs_only_on_the_converging_branch(tmp_dir, state_mod, check_runs):
    """修正へ回るラウンドでは照会しない。収束するラウンドでだけ 1 回投げる。"""
    calls = check_runs([_run("pytest")])

    requested = _approved_round(agy={"intent": "REQUEST_CHANGES", "by_severity": {"major": 1}})
    _write(tmp_dir, _state([requested]))
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))
    assert e.value.code == 2
    assert calls == []

    _write(tmp_dir, _state([_approved_round()]))
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))
    assert e.value.code == 0
    assert calls == [(REPO, HEAD_SHA)]


def test_the_query_is_skipped_when_no_result_relaunches(tmp_dir, state_mod, check_runs):
    """結果なしの検査の方が先である。照会は収束の枝の前にだけ置く。"""
    calls = check_runs([_run("pytest")])
    _write(tmp_dir, _state([_approved_round(codex={})]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 7
    assert calls == []


def test_the_round_records_what_was_checked(tmp_dir, state_mod, check_runs, capsys):
    """どの commit を照会したかを状態ファイルへ残す。"""
    check_runs([_run("pytest")])
    _write(tmp_dir, _state([_approved_round()]))

    with pytest.raises(SystemExit):
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert _read(tmp_dir)["rounds"][-1]["ci"]["sha"] == HEAD_SHA
    assert "CI_VERDICT=success" in capsys.readouterr().out


def test_the_head_commit_is_fetched_when_the_state_has_none(tmp_dir, state_mod, check_runs, monkeypatch):
    """状態ファイルに head のコミットが無いときだけ、REST を 1 回投げて補う。"""
    calls = check_runs([_run("pytest")])
    asked: list[int] = []

    def _meta(pr, repo=None):
        asked.append(pr)
        return state_mod.PrMetadata(
            repo=REPO, author="someone", head_branch="feat/x", head_sha="deadbee",
            base_branch="develop", is_fork=False, rate_remaining=None, rate_reset=None,
        )

    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", _meta)
    round_ = _approved_round()
    del round_["head_sha"]
    _write(tmp_dir, _state([round_]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert asked == [PR]
    assert calls == [(REPO, "deadbee")]
