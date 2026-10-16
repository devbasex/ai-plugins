"""リファクタリング計画のコメントを結果の出口で書き直す（#1692 #1684 #1648 #1652）。

コメントの本文（件数の行・公開の行・項目の状態）、子コマンド `plan-comment`、push が落ちたときの公開の結果の記録、
プランの外の取り消しの読み取り（`--scan-reverts`）を確かめる。git は一時ディレクトリのリポジトリと bare の origin で組み、
`gh` は `sh` を差し替えて呼ばない。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

from crossref_helpers import commit_with_trailers, git, item_trailers, make_state_v2, read_state, write_state

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
HEAD_BRANCH = "refactor/target"
COMMENT_URL = "https://github.com/acme/demo/pull/130#issuecomment-1"
TOKEN = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
COUNTS = re.compile(r"採用 (\d+)・未確認 (\d+)・取り消し (\d+)・見送り (\d+)")


def _item(item_id: str, status: str = "verified", sha: str | None = None) -> dict:
    return {
        "id": item_id,
        "rank": int(item_id[-1]),
        "path": f"src/{item_id}.py",
        "symbol": item_id,
        "smell": "long_method",
        "technique": "extract_method",
        "status": status,
        "commits": {"test": None, "implement": sha, "fix": []},
    }


def _state(tmp_path, work=None, **over):
    over.setdefault("plan_mode", "comment")
    over.setdefault("plan", {"base_sha": "0" * 40})
    over.setdefault("phase", "final")
    over.setdefault("head_branch", HEAD_BRANCH)
    path = make_state_v2(tmp_path, work or tmp_path / "work", **over)
    return path, read_state(path)


def _mod(name: str):
    return sys.modules[f"refactor_lib.{name}"]


@pytest.fixture
def gh(refactor, patch_lib):
    """`gh` だけを差し替える（`git` は本物の `sh` で打つ）。応答は `responses` の部分一致で返す。"""
    real_sh = _mod("paths").sh
    calls: list[list[str]] = []
    responses: dict[str, str] = {}

    def fake_sh(cmd, cwd=None, check=True):
        if cmd[0] != "gh":
            return real_sh(cmd, cwd=cwd, check=check)
        calls.append(list(cmd))
        joined = " ".join(cmd)
        return next((v for k, v in responses.items() if k in joined), "")

    patch_lib("sh", fake_sh)
    return calls, responses


def _ok(responses):
    payload = json.dumps({"id": 1, "html_url": COMMENT_URL})
    responses["-X POST"] = payload
    responses["-X PATCH"] = payload


def _plan_comment(env_tmp_dir, path, capsys, scan=False) -> tuple[int, dict, str]:
    """`plan-comment` を同じプロセスで打ち、`(終了コード, KEY=VALUE, 書いた本文)` を返す。"""
    if env_tmp_dir is not None:
        env_tmp_dir(path)
    code = 0
    try:
        _mod("commands.plan_comment").cmd_plan_comment(argparse.Namespace(id=130, scan_reverts=scan))
    except SystemExit as e:
        code = int(e.code or 0)
    out = capsys.readouterr().out
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    values = {k: v.strip("'") for k, v in values.items()}
    return code, values, out


def _body(calls) -> str:
    sent = [c for c in calls if "-X" in c]
    assert sent, "コメントを書いていない"
    return next(a for a in sent[-1] if a.startswith("body="))


def _counts(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in COUNTS.search(text).groups())


# ---------- コメントの中身（AC1・AC2・AC3） ----------


@pytest.mark.parametrize(("gate", "label"), [("passed", "採用"), ("failed", "未確認"), (None, "未確認")])
def test_a_verified_item_is_adopted_only_when_the_final_gate_passed(refactor, tmp_path, gate, label):
    """AC1・I2: 最終ゲートが `passed` でなければ `verified` を「採用」と書かない（#1684 の 16 件「採用」）。"""
    final_gate = {"status": gate} if gate else {}
    _, state = _state(tmp_path, items=[_item("I-001")], final_gate=final_gate)
    body = _mod("plan").format_plan(state)
    assert f"| {label} |" in body
    other = "未確認" if label == "採用" else "採用"
    assert f"| {other} |" not in body


ITEMS = [
    _item("I-001"),
    _item("I-002"),
    _item("I-003", "implemented"),
    _item("I-004", "reverted"),
    _item("I-005", "deferred"),
]


def _drive_counts(monkeypatch, path):
    spec = importlib.util.spec_from_file_location("drive_for_counts", SCRIPTS / "drive.py")
    drive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drive)
    monkeypatch.setattr(drive, "_read_json_step", lambda p: ("ok", json.loads(pathlib.Path(p).read_text())))
    d = drive.Drive(130, [])
    d.v.update({"TMP_DIR": str(path.parent), "ID": "130"})
    return d.counts()


@pytest.mark.parametrize(("gate", "expected"), [("passed", (2, 0, 1, 1)), ("failed", (0, 3, 1, 1))])
def test_the_counts_line_and_the_result_json_come_from_the_same_tally(refactor, tmp_path, monkeypatch, gate, expected):
    """AC2・I1: 件数の行の 4 つと結果 JSON の `metrics` の 4 つが一致する（未確認が出ない実行は 0）。"""
    path, state = _state(tmp_path, items=ITEMS, final_gate={"status": gate})
    line = _counts(_mod("plan").format_plan(state))
    m = _drive_counts(monkeypatch, path)
    assert line == expected
    assert line == (m["adopted"], m.get("unconfirmed", 0), m["reverted"], m["deferred"])
    shown = [_mod("ledger").display_status(state, i) for i in ITEMS]
    assert shown.count("unconfirmed") == line[1]  # 「未確認」と書く項目の数と件数の行が食い違わない


def test_the_metrics_keep_their_keys_and_values_after_a_passed_final_gate(refactor, tmp_path, monkeypatch):
    """AC15: 最終ゲートを通った実行の件数は、変更前の数え方（verified を採用・status ごと）と同じ値になる。"""
    items = [_item("I-001"), {**_item("I-002", "reverted"), "fix_count": 2}, _item("I-003", "deferred")]
    path, _ = _state(tmp_path, items=items, final_gate={"status": "passed"})
    m = _drive_counts(monkeypatch, path)
    assert m == {"items": 3, "adopted": 1, "reverted": 1, "deferred": 1, "fix_rounds": 2, "final_gate": "passed", "reassigned": 0}


@pytest.mark.parametrize(
    ("publication", "unpublished", "expected"),
    [
        (None, None, "まだ push していない"),
        ({"status": "pushed", "sha": "a" * 40}, False, "aaaaaaaaaaaa を push した"),
        ({"status": "pushed", "sha": "a" * 40}, True, "その後の手元のコミットは未公開"),
        (
            {"status": "refused", "reason": "pre-push: ruff format --check"},
            True,
            "push できなかった（pre-push: ruff format --check）。未公開の改善項目がある",
        ),
        ({"status": "observed", "sha": "b" * 40, "head": HEAD_BRANCH}, False, f"origin の {HEAD_BRANCH} は bbbbbbbbbbbb"),
    ],
)
def test_the_comment_states_the_publication(refactor, tmp_path, publication, unpublished, expected):
    """AC3: 公開の行に push した SHA、または落ちた理由と「未公開の改善項目がある」を書く。"""
    _, state = _state(tmp_path, items=[_item("I-001")], publication=publication)
    body = _mod("plan").plan_comment_body(state, unpublished)
    line = next(x for x in body.splitlines() if x.startswith("- 公開: "))
    assert expected in line


def test_the_plan_file_does_not_carry_the_publication_or_the_time(refactor, tmp_path):
    """ファイルの置き場所へ書く本文は同じ状態から同じになる（公開のたびにコミットを積まない）。"""
    _, state = _state(tmp_path, items=[_item("I-001")], publication={"status": "pushed", "sha": "a" * 40})
    body = _mod("plan").format_plan(state)
    assert "- 公開: " not in body and "- 書き直した時刻: " not in body and "- 件数: " in body


# ---------- 子コマンド plan-comment（AC9・AC13・AC14・AC16・I3・I8） ----------


def test_no_comment_is_made_before_the_plan(refactor, tmp_path, gh, env_tmp_dir, capsys):
    """AC9・I3: 計画の前に止まった実行では `gh` を呼ばずに skipped で 0 を返す。"""
    calls, _ = gh
    path, _ = _state(tmp_path, plan=None, items=[])
    code, values, _ = _plan_comment(env_tmp_dir, path, capsys)
    assert (code, values["PLAN_COMMENT"], calls) == (0, "skipped", [])


@pytest.mark.parametrize("mode", [{"plan_mode": "file", "plan_file": "issues/plan.md"}, {"plan_mode": "none"}])
def test_a_run_whose_plan_is_not_a_comment_makes_no_comment(refactor, tmp_path, gh, env_tmp_dir, capsys, mode):
    """AC16: 置き場所が `--plan-file` か「記録しない」なら `gh` を呼ばない。"""
    calls, _ = gh
    path, _ = _state(tmp_path, items=[_item("I-001")], **mode)
    code, values, _ = _plan_comment(env_tmp_dir, path, capsys)
    assert (code, values["PLAN_COMMENT"], calls) == (0, "skipped", [])


def test_rewriting_twice_keeps_one_comment(refactor, tmp_path, gh, env_tmp_dir, capsys):
    """AC13・I4・I8: 2 度目は編集で、作成は 1 度だけ。ID を持てば `gh` は 1 回、持たなければ 2 回まで。"""
    calls, responses = gh
    _ok(responses)
    path, _ = _state(tmp_path, items=[_item("I-001")])
    assert _plan_comment(env_tmp_dir, path, capsys)[1]["PLAN_COMMENT"] == "created"
    first = len(calls)
    assert _plan_comment(env_tmp_dir, path, capsys)[1]["PLAN_COMMENT"] == "updated"
    assert first <= 2 and len(calls) - first == 1
    assert sum(1 for c in calls if "POST" in c) == 1

    # 記録が失われても目印で引き当てて編集する
    state = read_state(path)
    state["plan_comment"] = None
    write_state(path, state)
    marker = _mod("plan").plan_comment_marker(state)
    responses["--paginate"] = json.dumps([{"id": 1, "body": f"{marker}\n\n# 改修計画"}])
    assert _plan_comment(env_tmp_dir, path, capsys)[1]["PLAN_COMMENT"] == "updated"
    assert sum(1 for c in calls if "POST" in c) == 1


def test_a_failed_post_returns_1_and_keeps_the_state(refactor, tmp_path, gh, env_tmp_dir, capsys):
    """AC14: 投稿に失敗したら 1 と `failed` を返す（駆動はこの終了コードで分岐しない）。"""
    path, _ = _state(tmp_path, items=[_item("I-001")])
    code, values, _ = _plan_comment(env_tmp_dir, path, capsys)
    assert (code, values["PLAN_COMMENT"]) == (1, "failed")
    assert read_state(path).get("plan_comment") in (None, {})


def test_a_missing_state_file_returns_4(refactor, tmp_path, gh, monkeypatch, capsys):
    monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(_mod("paths"), "github_repo_from_origin", lambda: None)
    with pytest.raises(SystemExit) as e:
        _mod("commands.plan_comment").cmd_plan_comment(argparse.Namespace(id=999, scan_reverts=False))
    assert e.value.code == 4


def test_the_report_uses_the_same_status_and_counts(refactor, tmp_path, env_tmp_dir, capsys):
    """F8: 報告の項目の表は表示の状態の呼び名を持ち、件数の行がコメントと一致する。"""
    path, state = _state(tmp_path, items=ITEMS, final_gate={"status": "failed"})
    env_tmp_dir(path)
    _mod("commands.report").cmd_report(argparse.Namespace(id=130, metrics=False))
    out = capsys.readouterr().out
    assert _counts(out) == _counts(_mod("plan").format_plan(state)) == (0, 3, 1, 1)
    assert "| 未確認 |" in out and "| verified |" not in out


# ---------- push が落ちた結果の出口（AC5・AC11・I6。#1648 の再現） ----------


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    git("checkout", "-q", "-b", HEAD_BRANCH, cwd=work)
    (work / "src").mkdir()
    (work / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    base = commit_with_trailers(work, "init", {})
    git("push", "-q", "origin", f"HEAD:{HEAD_BRANCH}", cwd=work)
    return {"origin": origin, "work": work, "base": base}


def _commit_item(work: pathlib.Path, item_id: str) -> str:
    (work / "src" / f"{item_id}.py").write_text(f"name = '{item_id}'\n", encoding="utf-8")
    return commit_with_trailers(work, item_id, item_trailers(item_id))


def _run_with_items(tmp_path, repo, *ids):
    shas = {i: _commit_item(repo["work"], i) for i in ids}
    items = [_item(i, sha=s) for i, s in shas.items()]
    path, _ = _state(tmp_path / "run", repo["work"], items=items, plan={"base_sha": repo["base"]}, final_gate={"status": "failed"})
    return path, shas


def _push(path) -> int:
    state = read_state(path)
    try:
        _mod("publish")._push_and_save(path, state)
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def _refuse_with_pre_push(work: pathlib.Path, message: str) -> None:
    hook = work / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\necho '{message}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def test_a_refused_push_leaves_a_comment_that_says_so(refactor, tmp_path, repo, gh, env_tmp_dir, capsys):
    """AC5・AC11（#1648）: pre-push が拒否しても、コメントが 1 件でき「push できなかった」と未公開を記す。"""
    calls, responses = gh
    _ok(responses)
    path, _ = _run_with_items(tmp_path, repo, "I-001", "I-002")
    _refuse_with_pre_push(repo["work"], "ruff format --check: 2 files would be reformatted")

    assert _push(path) == 4
    pub = read_state(path)["publication"]
    assert pub["status"] == "refused" and "ruff format --check" in pub["reason"]

    code, values, _ = _plan_comment(env_tmp_dir, path, capsys)
    body = _body(calls)
    assert (code, values["PLAN_COMMENT"], values["UNPUBLISHED"]) == (0, "created", "1")
    assert "push できなかった（" in body and "未公開の改善項目がある" in body
    assert _counts(body) == (0, 2, 0, 0)
    assert sum(1 for c in calls if "POST" in c) == 1


@pytest.mark.parametrize("stop", ["publishable", "tool-paths"])
def test_a_push_stopped_before_git_push_is_recorded_as_refused(refactor, tmp_path, repo, gh, monkeypatch, stop):
    """AC5: `_require_publishable` と `_require_no_tool_paths` の中断も、公開の結果に理由を残す。"""
    path, _ = _run_with_items(tmp_path, repo, "I-001")
    if stop == "publishable":
        (repo["work"] / "stray.py").write_text("x = 1\n", encoding="utf-8")
        commit_with_trailers(repo["work"], "stray", {})
    else:
        monkeypatch.setattr(sys.modules["tool_paths"], "before_push", lambda work, head, git_run: "ツールのパスが入っている")
    assert _push(path) == 4
    pub = read_state(path)["publication"]
    assert pub["status"] == "refused" and pub["reason"]
    assert _mod("ledger").published_point(read_state(path)) == repo["base"]  # 照合の起点は変えない


def test_the_refused_reason_does_not_carry_a_token(refactor, tmp_path, repo, gh):
    """I6: push の失敗の出力に URL へ埋めたトークンがあっても、記録する理由に残さない。"""
    path, _ = _run_with_items(tmp_path, repo, "I-001")
    _refuse_with_pre_push(repo["work"], f"fatal: https://x-access-token:{TOKEN}@github.com/acme/demo.git")
    assert _push(path) == 4
    reason = read_state(path)["publication"]["reason"]
    assert TOKEN not in reason and "***@github.com" in reason


def test_a_push_that_went_through_is_not_unpublished_until_head_moves(refactor, tmp_path, repo, gh, env_tmp_dir, capsys):
    """AC4・AC11: push が通れば公開の行に SHA を書き、HEAD が進んでいなければ未公開ではない。"""
    calls, responses = gh
    _ok(responses)
    path, _ = _run_with_items(tmp_path, repo, "I-001")
    assert _push(path) == 0
    head = git("rev-parse", "HEAD", cwd=repo["work"]).stdout.strip()
    assert read_state(path)["publication"]["sha"] == head

    _, values, _ = _plan_comment(env_tmp_dir, path, capsys)
    assert values["UNPUBLISHED"] == "0" and f"{head[:12]} を push した" in _body(calls)
    _commit_item(repo["work"], "I-009")
    assert _plan_comment(env_tmp_dir, path, capsys)[1]["UNPUBLISHED"] == "1"


# ---------- プランの外の取り消し（AC10・I7。#1684 の手での取り消しの再現） ----------


def test_still_reverted_reads_reverts_of_reverts(refactor):
    still = _mod("outside_reverts").still_reverted
    a, b, c, r1, r2, r3 = ("a" * 40, "b" * 40, "c" * 40, "1" * 40, "2" * 40, "3" * 40)
    entries = [
        (a, "a"),
        (b, "b"),
        (c, "c"),
        (r1, f"Revert a\n\nThis reverts commit {a}.\n"),
        (r2, f"Revert b\n\nThis reverts commit {b[:12]}.\n"),
        (r3, f'Revert "Revert b"\n\nThis reverts commit {r2}.\n'),
    ]
    assert still(entries) == {a: r1, r2: r3}


def _default_place(tmp_path, monkeypatch):
    """既定の worktree の置き場を一時ディレクトリへ向け、環境変数と現在地からは探せないようにする。"""
    paths = _mod("paths")
    monkeypatch.setattr(paths, "default_worktree_base", lambda: tmp_path / "worktrees")
    monkeypatch.setattr(paths, "github_repo_from_origin", lambda: "acme/demo")
    monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return tmp_path / "worktrees" / "acme--demo" / "rf130" / "work"


def _revert_and_push(work: pathlib.Path, sha: str) -> str:
    git("revert", "--no-edit", sha, cwd=work)
    git("push", "-q", "origin", f"HEAD:{HEAD_BRANCH}", cwd=work)
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def test_scan_reverts_marks_and_restores_items_reverted_outside_the_plan(refactor, tmp_path, repo, gh, monkeypatch, capsys):
    """AC10・I7: `plan-comment <PR> --scan-reverts` を状態の置き場の外から打つと、取り消した項目が「取り消し」になり、
    取り消しを取り消すと元の状態へ戻る。"""
    calls, responses = gh
    _ok(responses)
    place = _default_place(tmp_path, monkeypatch)
    shas = {i: _commit_item(repo["work"], i) for i in ("I-001", "I-002")}
    git("push", "-q", "origin", f"HEAD:{HEAD_BRANCH}", cwd=repo["work"])
    items = [_item(i, sha=s) for i, s in shas.items()]
    path, _ = _state(place, repo["work"], items=items, plan={"base_sha": repo["base"]}, final_gate={"status": "failed"})

    revert = _revert_and_push(repo["work"], shas["I-001"])
    code, values, _ = _plan_comment(None, path, capsys, scan=True)
    state = read_state(path)
    body = _body(calls)
    assert (code, values["REVERTED_OUTSIDE"], values["RESTORED_OUTSIDE"]) == (0, "I-001", "")
    assert state["items"][0]["status"] == "reverted" and state["items"][0]["outside_revert"]["revert"] == revert
    assert _counts(body) == (0, 1, 1, 0) and "プランの外の取り消しを読んだ地点" in body
    assert state["publication"]["status"] == "observed" and state["publication"]["sha"] == revert

    _revert_and_push(repo["work"], revert)
    code, values, _ = _plan_comment(None, path, capsys, scan=True)
    state = read_state(path)
    assert (code, values["REVERTED_OUTSIDE"], values["RESTORED_OUTSIDE"]) == (0, "", "I-001")
    assert state["items"][0]["status"] == "verified" and "outside_revert" not in state["items"][0]
    assert "failure_reason" not in state["items"][0]
    assert _counts(_body(calls)) == (0, 2, 0, 0)


def test_scan_reverts_does_not_restore_items_the_script_reverted(refactor, tmp_path, repo, gh, env_tmp_dir, capsys):
    """I7: `outside_revert` を持たない取り消し（スクリプトが取り消した項目）は走査の結果によらず戻さない。"""
    _, responses = gh
    _ok(responses)
    path, _ = _run_with_items(tmp_path, repo, "I-001")
    git("push", "-q", "origin", f"HEAD:{HEAD_BRANCH}", cwd=repo["work"])
    state = read_state(path)
    state["items"][0]["status"] = "reverted"
    write_state(path, state)
    code, values, _ = _plan_comment(env_tmp_dir, path, capsys, scan=True)
    assert (code, values["RESTORED_OUTSIDE"]) == (0, "")
    assert read_state(path)["items"][0]["status"] == "reverted"


def test_scan_reverts_stops_with_4_when_origin_cannot_be_fetched(refactor, tmp_path, repo, gh, env_tmp_dir, capsys):
    """AC10: origin を取り込めなければ 4 で止まり、状態ファイルも `gh` の呼び出しも変えない。"""
    calls, _ = gh
    path, _ = _run_with_items(tmp_path, repo, "I-001")
    state = read_state(path)
    state["head_branch"] = "no-such-branch"
    write_state(path, state)
    before = path.read_text(encoding="utf-8")
    code, _, _ = _plan_comment(env_tmp_dir, path, capsys, scan=True)
    assert (code, calls, path.read_text(encoding="utf-8")) == (4, [], before)
