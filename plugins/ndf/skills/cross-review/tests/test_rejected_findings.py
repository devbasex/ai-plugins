"""却下した指摘を per-item で残す（#156 の 1 本目）。

**却下の理由と位置が残らないと、同じ論点が繰り返し提出される。** #69 では
「version bump をこの Pull Request でやるべき」が round 1〜5 の 5 回続けて出た。
どのファイルのどの箇所かが分からないまま次のラウンドへ渡しても、同じ指摘だと
判定できない。

`deferred_nits` が既に per-item を蓄積しているため、同じ形にする。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import pytest

PR = 6001
REPO = "o/r"


def _state(**over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": [{
            "round": 1,
            "pr": PR,
            "started_at": "2026-09-09T00:00:00+00:00",
            "codex": {"intent": "APPROVE", "by_severity": {}},
            "agy": {"intent": "APPROVE", "by_severity": {}},
        }],
        "deferred_nits": [],
        "pr_history": [{"pr": PR, "opened_at": "2026-09-09T00:00:00+00:00",
                        "closed_at": None, "rounds": 1}],
        "final": None,
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _fix_result(tmp_dir: pathlib.Path, **over) -> None:
    payload = {
        "pr": PR, "fix_commit": "abc1234", "ci_status": "SUCCESS",
        "fixed_count": 0, "resolved_threads": [], "deferred": [], "rejected": [],
    }
    payload.update(over)
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(payload))


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


REJECTED = {
    "comment_id": 3222849090,
    "path": "src/foo.py",
    "line": 42,
    "severity": "minor",
    "summary": "version bump をこの Pull Request でやるべき",
    "reason_for_rejection": "版を上げるのは配布の工程であり、この変更の範囲外である",
}


# ---------------- 蓄積 ----------------

def test_init_stores_an_empty_rejected_findings_list(tmp_dir, state_mod, monkeypatch):
    """新規初期化で保存される却下記録の初期値を固定する。"""
    worktree = tmp_dir / "worktree"
    worktree.mkdir()
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", lambda pr, repo:
                        state_mod.PrMetadata(REPO, "author", "feature/test", "abc",
                                             "develop", False, 4000, None))
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "viewer")
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(state_mod, "_is_registered_worktree", lambda path: True)
    monkeypatch.setattr(state_mod, "_sync_worktree", lambda *args: None)
    monkeypatch.setattr(state_mod.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""))
    monkeypatch.setattr(state_mod.auth, "check_auth", lambda *args, **kwargs: None)

    state_mod.cmd_init(argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=str(worktree),
        focus=None, extra_instructions_file=None, host="codex",
    ))

    saved = _read(tmp_dir)
    assert "rejected_findings" in saved
    assert saved["rejected_findings"] == []


def test_a_rejected_finding_is_kept_with_its_location(tmp_dir, state_mod):
    _write(tmp_dir, _state())
    _fix_result(tmp_dir, rejected=[REJECTED])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    kept = _read(tmp_dir)["rejected_findings"]
    assert len(kept) == 1
    for key in ("comment_id", "path", "line", "severity", "summary",
                "reason_for_rejection"):
        assert kept[0][key] == REJECTED[key], key


def test_the_record_carries_the_pr_and_round(tmp_dir, state_mod):
    """どのラウンドで却下したかが分からないと、再提出を数えられない。"""
    _write(tmp_dir, _state())
    _fix_result(tmp_dir, rejected=[REJECTED])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    kept = _read(tmp_dir)["rejected_findings"][0]
    assert kept["pr"] == PR
    assert kept["round"] == 1


def test_records_accumulate_across_rounds(tmp_dir, state_mod):
    """ラウンドをまたいで積む。既にある記録を書き換えない。"""
    _write(tmp_dir, _state())
    _fix_result(tmp_dir, rejected=[REJECTED])
    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    st = _read(tmp_dir)
    st["rounds"].append({
        "round": 2, "pr": PR, "started_at": "2026-09-09T01:00:00+00:00",
        "codex": {"intent": "APPROVE", "by_severity": {}},
        "agy": {"intent": "APPROVE", "by_severity": {}},
    })
    _write(tmp_dir, st)
    second = {**REJECTED, "comment_id": 3222849091, "line": 99}
    _fix_result(tmp_dir, rejected=[second])
    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    kept = _read(tmp_dir)["rejected_findings"]
    assert [k["round"] for k in kept] == [1, 2]
    assert [k["line"] for k in kept] == [42, 99]


def test_the_shape_matches_the_deferred_records(tmp_dir, state_mod):
    """`deferred_nits` と同じ形で読めること。"""
    _write(tmp_dir, _state())
    deferred = {
        "comment_id": 3222849092, "path": "src/bar.py", "line": 7,
        "severity": "nit", "summary": "末尾の空白",
        "reason_for_deferral": "好みの範囲",
    }
    _fix_result(tmp_dir, rejected=[REJECTED], deferred=[deferred])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    st = _read(tmp_dir)
    common = {"comment_id", "path", "line", "severity", "summary", "pr", "round"}
    assert common <= set(st["rejected_findings"][0])
    assert common <= set(st["deferred_nits"][0])


# ---------------- 既存の値を変えない ----------------

def test_the_round_level_count_is_unchanged(tmp_dir, state_mod):
    """ラウンドごとの報告が読む件数は残す。"""
    _write(tmp_dir, _state())
    _fix_result(tmp_dir, rejected=[REJECTED, {**REJECTED, "comment_id": 2}])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    assert _read(tmp_dir)["rounds"][-1]["fix"]["rejected"] == 2


def test_a_state_file_without_the_key_is_readable(tmp_dir, state_mod):
    """旧い状態ファイルを読める。`rejected_findings` を持たない。"""
    st = _state()
    assert "rejected_findings" not in st
    _write(tmp_dir, st)
    _fix_result(tmp_dir, rejected=[REJECTED])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    assert len(_read(tmp_dir)["rejected_findings"]) == 1


def test_an_int_count_leaves_the_records_empty(tmp_dir, state_mod):
    """件数だけが返る劣化表現では per-item を作れない。件数は失わない。"""
    _write(tmp_dir, _state())
    _fix_result(tmp_dir, rejected=3)

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    st = _read(tmp_dir)
    assert st["rejected_findings"] == []
    assert st["rounds"][-1]["fix"]["rejected"] == 3


def test_items_missing_the_location_are_still_kept(tmp_dir, state_mod):
    """項目が欠けた要素も落とさない。落とすと却下そのものが消える。"""
    _write(tmp_dir, _state())
    partial = {"comment_id": 5, "summary": "...", "reason_for_rejection": "..."}
    _fix_result(tmp_dir, rejected=[partial])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    kept = _read(tmp_dir)["rejected_findings"]
    assert len(kept) == 1
    assert kept[0]["comment_id"] == 5


def test_non_dict_items_in_rejected_list_are_filtered_out(tmp_dir, state_mod):
    """fix の rejected に辞書以外（文字列・None等）が混在しても辞書要素のみ抽出される。"""
    _write(tmp_dir, _state())
    _fix_result(tmp_dir, rejected=[REJECTED, "string-item", None, 123, ["nested"]])

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    kept = _read(tmp_dir)["rejected_findings"]
    assert len(kept) == 1
    assert kept[0]["comment_id"] == REJECTED["comment_id"]



# ---------------- 報告 ----------------

def test_the_report_lists_the_rejected_findings(tmp_dir, state_mod, capsys):
    _write(tmp_dir, _state(rejected_findings=[{**REJECTED, "pr": PR, "round": 1}]))

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    out = capsys.readouterr().out
    assert "却下" in out
    assert "src/foo.py:42" in out


# ---------------- 手順書と契約 ----------------

SKILLS = pathlib.Path(__file__).resolve().parents[2]


def test_the_fix_document_asks_for_the_location():
    """`fix` が返す `rejected[]` の例が 6 項目を持つこと。"""
    text = (SKILLS / "fix/SKILL.md").read_text(encoding="utf-8")
    start = text.index('"rejected": [')
    block = text[start:start + 400]
    for key in ("path", "line", "severity", "comment_id", "summary",
                "reason_for_rejection"):
        assert f'"{key}"' in block, key


def test_the_contract_documents_the_accumulated_records():
    """状態ファイルの契約が `rejected_findings` の形を持つこと。"""
    text = (SKILLS / "cross-review/docs/04-contracts.md").read_text(encoding="utf-8")
    assert '"rejected_findings"' in text
    assert "reason_for_rejection" in text
