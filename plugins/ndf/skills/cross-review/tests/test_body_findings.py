"""総評だけの指摘（スレッドを持たず、レビュー本文にだけ書かれた指摘）の決着と件数（#1980）。

入力は PR 1973 のラウンド 1 と同じ形（未解決のスレッド 0 件・CI 成功・本文に `major` 5 件）を
`fixtures/pr1973-round1-state.json` から読む。修正担当の雛形と `finalize` は
`skills/fix/tests/test_fix_steps.py` が同じ入力で確かめる。

| 経路 | 確かめること |
| --- | --- |
| `body_findings.pending` | `posted_to` の無い記録・統合済み・決着済み・見送りや却下の再提出を外す |
| `body_findings.settle` | 置き場所から振り分けを決め、同じ `finding_id` を重ねない |
| 取り込み（`merge-fix`） | 決着を記録し、まとめの「レビュー本文の指摘への返事」に 1 行ずつ載る。駆動の件数に入る |
| `judge` | 決着していない重大な総評だけの指摘があれば収束させない |
| `verify-sweep` | 決着していない総評だけの指摘を残数に足す |
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import pathlib
import sys

import body_findings
import pytest
import result_posts
import review_lib.ci
import review_lib.commands.judge
import review_lib.commands.merge_fix
import review_lib.commands.report
import review_lib.github

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "pr1973-round1-state.json"
SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
PR = 1973


def _pr1973() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _ids(findings) -> list[str]:
    return [f["finding_id"] for f in findings]


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _write(tmp_dir: pathlib.Path, st: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st, ensure_ascii=False))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _entry(finding: dict, **over) -> dict:
    """修正の戻り値ファイルの要素（`fix-steps.py finalize` が写す形）。"""
    e = {
        "finding_id": finding["finding_id"],
        "comment_id": None,
        "thread_id": None,
        "path": finding["path"],
        "line": finding["line"],
        "severity": finding["severity"],
        "category": "",
        "summary": finding["body"].splitlines()[0],
        "mvv_basis": [],
    }
    e.update(over)
    return e


def _fix_result(st: dict) -> dict:
    """5 件を fixed 3・deferred 1・rejected 1 に振り分けた戻り値ファイル。"""
    f = st["review_findings"]
    return {
        "pr": PR,
        "fix_commit": None,
        "ci_status": "SUCCESS",
        "ci_failed_checks": [],
        "fixed_count": 3,
        "by_severity": {"critical": 0, "major": 3, "minor": 0, "nit": 0},
        "resolved_threads": [_entry(x) for x in f[:3]],
        "deferred": [_entry(f[3], reason_for_deferral="次の版で入れ替える")],
        "rejected": [_entry(f[4], reason_for_rejection="環境変数は呼び出し元が渡す")],
    }


# ---------------- pending ----------------


def test_the_pr1973_shape_has_five_pending_body_findings():
    assert _ids(body_findings.pending(_pr1973())) == [f"codex-r1-{i}" for i in range(5)]


def test_a_finding_without_posted_to_is_inline():
    st = _pr1973()
    for f in st["review_findings"]:
        f.pop("posted_to")
    assert body_findings.pending(st) == []


def test_merged_and_settled_findings_are_not_pending():
    st = _pr1973()
    st["review_findings"][0]["merged_into"] = "kiro-r1-0"
    st["body_settlements"] = [{"finding_id": "codex-r1-1", "decision": "fixed"}]
    assert _ids(body_findings.pending(st)) == ["codex-r1-2", "codex-r1-3", "codex-r1-4"]


def _resubmitted(st: dict, source: dict) -> dict:
    again = {**copy.deepcopy(source), "finding_id": "codex-r2-0", "round": 2, "line": source["line"] + 1}
    st["review_findings"].append(again)
    return again


@pytest.mark.parametrize(("decision", "pending"), [("rejected", False), ("deferred", False), ("fixed", True)])
def test_a_resubmission_of_a_let_go_finding_is_not_pending(decision, pending):
    """見送り・却下した指摘の再提出は振り分け直さない。直したはずの指摘の再提出はもう一度振り分ける。"""
    st = _pr1973()
    _resubmitted(st, st["review_findings"][0])
    st["body_settlements"] = [{"finding_id": f"codex-r1-{i}", "decision": decision if i == 0 else "fixed"} for i in range(5)]
    assert ("codex-r2-0" in _ids(body_findings.pending(st))) is pending


# ---------------- settle ----------------


def test_settle_maps_the_place_to_the_decision_and_does_not_repeat():
    st = _pr1973()
    f = st["review_findings"]
    result = {
        "resolved_threads": [_entry(f[0]), {"thread_id": "PRRT_1"}],
        "deferred": [
            _entry(f[1], reason_for_deferral="後で"),
            _entry(f[2], waived="wording", reason_for_deferral="言い回し"),
            _entry(f[3], issue="#2001", reason_for_deferral="別 PR で対応（#2001）"),
        ],
        "rejected": [_entry(f[4], reason_for_rejection="誤り"), {"finding_id": "nobody-r1-0"}],
    }
    assert body_findings.settle(st, body_findings.entries(result), 1, PR, "fix") == 5
    assert body_findings.settle(st, body_findings.entries(result), 2, PR, "sweep") == 0  # I2
    got = {s["finding_id"]: (s["decision"], s["reason"], s["issue"], s["round"], s["source"]) for s in st["body_settlements"]}
    assert got == {
        "codex-r1-0": ("fixed", "", "", 1, "fix"),
        "codex-r1-1": ("deferred", "後で", "", 1, "fix"),
        "codex-r1-2": ("waived", "言い回し", "", 1, "fix"),
        "codex-r1-3": ("separate_pr", "別 PR で対応（#2001）", "#2001", 1, "fix"),
        "codex-r1-4": ("rejected", "誤り", "", 1, "fix"),
    }
    assert body_findings.pending(st) == []


# ---------------- 取り込み（AC4・AC5） ----------------


def test_the_summary_comment_answers_each_let_go_body_finding(tmp_path):
    """見送り・別 PR・基準外・却下の総評だけの指摘が、まとめの返事に summary と理由を 1 行ずつ持つ。"""
    f = _pr1973()["review_findings"]
    res = {
        "fix_commit": None,
        "resolved_threads": [],
        "deferred": [
            _entry(f[0], reason_for_deferral="次の版で入れ替える"),
            _entry(f[1], issue="#2001", reason_for_deferral="別 PR で対応（#2001）", resolve=True),
            _entry(f[2], waived="wording", reply="言い回しの好みのため見送ります", reason_for_deferral="言い回し", resolve=True),
        ],
        "rejected": [_entry(f[3], reason_for_rejection="環境変数は呼び出し元が渡す")],
    }
    path = tmp_path / "fix.json"
    path.write_text(json.dumps(res, ensure_ascii=False))
    items = result_posts.fix_posts(path, "devbasex/ai-plugins", PR, 1)
    assert [i["kind"] for i in items] == ["pr-comment"]  # スレッドが無いため返信も決着も積まない
    lines = [ln for ln in items[0]["fields"]["body"].splitlines() if ln.startswith("- ")]
    assert lines == [
        "- [major / logic] 差分の外: 退避の分岐が空の配列を返す: 見送ります。次の版で入れ替える",
        "- [major / logic] 差分の外: 再開のときに記録を二重に積む: 見送ります。別 PR で対応（#2001）",
        "- [major / docs] 差分の外: 手順の順序が実装と逆: 言い回しの好みのため見送ります",
        "- [major / logic] 差分の外: 終了コードが 0 のまま: この指摘は採らない判断です。環境変数は呼び出し元が渡す",
    ]


def _drive(tmp_dir: pathlib.Path):
    name = "cross_review_drive"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / "drive.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    d = sys.modules[name].Drive(PR, "light", [])
    d.v["TMP_DIR"] = str(tmp_dir)
    return d


def test_the_pr1973_shape_is_counted_after_the_fix(tmp_dir, state_mod):
    """5 件を fixed 3・deferred 1・rejected 1 で取り込むと、件数が 5 と 3 になり、5 件とも決着する。"""
    st = _pr1973()
    review_lib.commands.merge_fix._merge_fix_records(st, _fix_result(st), PR)
    review_lib.commands.merge_fix._merge_fix_records(st, _fix_result(st), PR)  # 打ち直しても決着は重ねない
    assert len(st["body_settlements"]) == 5 and body_findings.pending(st) == []
    _write(tmp_dir, st)
    counts = _drive(tmp_dir).counts()
    assert (counts["findings"], counts["fixed"], counts["deferred"], counts["rejected"]) == (5, 3, 1, 1)


# ---------------- judge（AC7） ----------------


def _approved(st: dict) -> dict:
    st["rounds"][-1]["codex"] = {"intent": "APPROVE", "comments": 0, "by_severity": {}}
    return st


@pytest.fixture()
def green(monkeypatch, state_mod):
    monkeypatch.setattr(review_lib.ci, "_fetch_check_runs", lambda repo, sha: [{"name": "pytest", "status": "completed", "conclusion": "success"}])


def _judge(tmp_dir, st, capsys) -> tuple[int, str]:
    _write(tmp_dir, st)
    with pytest.raises(SystemExit) as e:
        review_lib.commands.judge.cmd_judge(argparse.Namespace(pr=PR))
    return e.value.code, capsys.readouterr().out


def test_an_unsettled_major_body_finding_keeps_the_loop_going(tmp_dir, green, capsys):
    st = _approved(_pr1973())
    st["body_settlements"] = [{"finding_id": f"codex-r1-{i}", "decision": "fixed"} for i in range(4)]
    code, out = _judge(tmp_dir, st, capsys)
    assert code == 2 and "BODY_BLOCKING=1" in out
    assert _read(tmp_dir)["rounds"][-1]["verdict"] == "changes_requested"


@pytest.mark.parametrize(
    "change",
    [{"severity": "minor"}, {"critiques": [{"agent": "kiro", "verdict": "refute", "reason": "呼び出し元が渡す"}]}],
    ids=["minor", "refuted"],
)
def test_a_minor_or_refuted_body_finding_does_not_hold_convergence(tmp_dir, green, capsys, change):
    st = _approved(_pr1973())
    st["review_findings"] = st["review_findings"][:1]
    st["review_findings"][0].update(change)
    code, out = _judge(tmp_dir, st, capsys)
    assert code == 0 and "BODY_BLOCKING" not in out


def test_judge_without_body_findings_prints_no_new_line(tmp_dir, green, capsys):
    st = _approved(_pr1973())
    st["review_findings"] = []
    code, out = _judge(tmp_dir, st, capsys)
    assert code == 0 and "BODY_BLOCKING" not in out


# ---------------- verify-sweep（AC6・AC8） ----------------


def _sweep(tmp_dir, st, monkeypatch, capsys, threads=(), **result) -> tuple[int, str]:
    _write(tmp_dir, st)
    (tmp_dir / f"sweep-pr{PR}-result.json").write_text(json.dumps({"resolved": 0, "remaining_open": 0, **result}, ensure_ascii=False))
    monkeypatch.setattr(review_lib.github, "_fetch_unresolved_threads", lambda repo, pr: list(threads))
    with pytest.raises(SystemExit) as e:
        review_lib.commands.report.cmd_verify_sweep(argparse.Namespace(pr=PR, file=None))
    return e.value.code, capsys.readouterr().out


def test_unsettled_body_findings_remain_after_the_sweep(tmp_dir, state_mod, monkeypatch, capsys):
    """スレッド 0 件でも、決着していない総評だけの指摘 2 件が残数とまとめの unresolved に入る。"""
    st = _pr1973()
    f = st["review_findings"]
    code, out = _sweep(tmp_dir, st, monkeypatch, capsys, resolved_threads=[_entry(x) for x in f[:3]])
    assert code == 6
    assert "REMAINING_OPEN=2" in out and "REMAINING_BODY=2" in out
    saved = _read(tmp_dir)
    assert saved["sweep"]["remaining_body"] == 2 and "総評だけの指摘 2 件" in saved["sweep"]["remaining_reason"]
    assert [s["source"] for s in saved["body_settlements"]] == ["sweep"] * 3
    assert _drive(tmp_dir).counts()["unresolved"] == 2


def test_body_findings_add_to_open_threads(tmp_dir, state_mod, monkeypatch, capsys):
    st = _pr1973()
    code, out = _sweep(tmp_dir, st, monkeypatch, capsys, threads=[{"id": "PRRT_a", "path": "a.py", "line": "1"}])
    assert code == 6 and "REMAINING_OPEN=6" in out


def test_a_sweep_that_settles_every_body_finding_leaves_nothing(tmp_dir, state_mod, monkeypatch, capsys):
    st = _pr1973()
    f = st["review_findings"]
    code, out = _sweep(
        tmp_dir,
        st,
        monkeypatch,
        capsys,
        resolved_threads=[_entry(x) for x in f[:4]],
        deferred=[_entry(f[4], reason_for_deferral="次の版", resolve=True)],
    )
    assert code == 0 and "REMAINING_OPEN=0" in out and "REMAINING_BODY" not in out
    assert _read(tmp_dir)["sweep"]["remaining_body"] == 0
