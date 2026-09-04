"""検査ジョブの振り分けを、判定と修正の取り込みが同じ 1 つの実装で行う（#327）。

振り分けは `cmd_merge_fix` の中にべた書きされており、収束の判定からは呼べなかった。
`_classify_ci` へ切り出し、両方が同じ名前の一覧へ同じ判断を返すことを固定する。

**分からない名前は code-related へ倒す。** 継続的統合の名前はリポジトリごとに違い、
一覧に無い名前を無害と決めつけると、落ちた検査を通したまま収束させることになる。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 7712
REPO = "o/r"


def _run(name: str, status: str = "completed", conclusion: str = "failure") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


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


# ---------------- 振り分けそのもの ----------------

def test_an_unknown_name_is_treated_as_code_related(state_mod):
    """一覧に無い名前は code-related として扱う（保守的な既定）。"""
    got = state_mod._classify_ci([_run("我々の知らない検査")])

    assert got.code_failed == ["我々の知らない検査"]
    assert got.meta_failed == []


def test_a_meta_name_is_separated_from_a_code_name(state_mod):
    got = state_mod._classify_ci([_run("check_pr_requirements"), _run("pytest")])

    assert got.code_failed == ["pytest"]
    assert got.meta_failed == ["check_pr_requirements"]


def test_a_code_name_that_starts_with_a_meta_word_is_not_meta(state_mod):
    """`meta` を含むだけの名前を meta-only にしない。

    部分一致で拾うと `metabase tests` / `metadata lint` のようなコード検査が
    meta-only になり、失敗したまま収束する。一覧に無い名前は code-related へ倒す。
    """
    got = state_mod._classify_ci([_run("metabase tests"), _run("metadata lint")])

    assert got.code_failed == ["metabase tests", "metadata lint"]
    assert got.meta_failed == []


def test_a_meta_word_between_separators_is_still_meta(state_mod):
    """区切りで挟まれた語は meta-only のままにする。"""
    got = state_mod._classify_ci([
        _run("meta"), _run("meta / labels"), _run("pr-meta"), _run("check_pr_requirements"),
    ])

    assert got.meta_failed == ["meta", "meta / labels", "pr-meta", "check_pr_requirements"]
    assert got.code_failed == []


def test_a_run_that_has_not_completed_is_neither(state_mod):
    got = state_mod._classify_ci([
        _run("pytest", status="in_progress", conclusion=""),
        _run("lint", status="queued", conclusion=""),
        _run("build", conclusion="success"),
    ])

    assert got.code_failed == []
    assert got.meta_failed == []
    assert got.pending == ["pytest", "lint"]


# ---------------- 判定と修正の取り込みが同じ実装を呼ぶ ----------------

def test_the_judge_and_the_merge_share_one_classification(tmp_dir, state_mod, monkeypatch):
    """同じ名前の一覧に対して、判定と修正の取り込みが同じ判断へ至る。"""
    seen: list[list[str]] = []
    real = state_mod._classify_ci

    def _spy(runs):
        seen.append([str(r.get("name")) for r in runs])
        return real(runs)

    monkeypatch.setattr(state_mod, "_classify_ci", _spy)
    monkeypatch.setattr(state_mod, "_fetch_check_runs", lambda repo, sha: [_run("pytest")])

    # 修正の取り込み側: 申告された失敗の名前を読む
    approved = {
        "round": 1, "pr": PR, "started_at": "2026-09-04T00:00:00+00:00",
        "codex": {"intent": "APPROVE", "by_severity": {}},
        "agy": {"intent": "APPROVE", "by_severity": {}},
        "head_sha": "b87b3ae",
    }
    _write(tmp_dir, _state([approved]))
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps({
        "pr": PR, "fix_commit": "abc1234", "ci_status": "FAILURE",
        "ci_failed_checks": ["pytest"], "fixed_count": 1,
    }))
    with pytest.raises(SystemExit) as merge_exit:
        state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    # 判定側: GitHub から読んだ検査ジョブを見る
    _write(tmp_dir, _state([approved]))
    with pytest.raises(SystemExit) as judge_exit:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert merge_exit.value.code == 3   # 修正の取り込みは中断する
    assert judge_exit.value.code == 2   # 判定は中断せず修正へ回す
    assert seen == [["pytest"], ["pytest"]]
