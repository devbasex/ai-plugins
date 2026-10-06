"""取り消しの判定（`ledger`）と、それを呼ぶ取り消し・公開を**実際の git** で確かめる（#1482）。

再現手順は子の課題から写した。一時ディレクトリのリポジトリと bare の origin で組む。

| 子 | 何を縛るか |
| --- | --- |
| #817 | push の直前に、記録に無いコミット（見放した担当の残留コミット）を公開しない |
| #1399 | 取り消しをくり返してもコミット数が伸びず、最終ゲートより前には push しない |
| #1237 | 積み直しの衝突で広げるのは同じファイルを触った項目までで、全件を取り消さない |
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

from crossref_helpers import commit_with_trailers, git, item_trailers, make_state_v2, read_state, write_state

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
HEAD_BRANCH = "refactor/target"
LINES = [f"line{i}\n" for i in range(1, 41)]


def _run(*args: str, cwd) -> str:
    return git(*args, cwd=cwd).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """bare の origin と、head ブランチを持つ書き込み用の作業ディレクトリ。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    git("checkout", "-q", "-b", HEAD_BRANCH, cwd=work)
    return {"origin": origin, "work": work}


def _write(work: pathlib.Path, rel: str, text: str) -> None:
    p = work / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _change(work: pathlib.Path, rel: str, line: int, text: str) -> None:
    p = work / rel
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[line] = text + "\n"
    p.write_text("".join(lines), encoding="utf-8")


def _base(work: pathlib.Path, files: list[str], push: bool = True) -> str:
    for rel in files:
        _write(work, rel, "".join(LINES))
    base = commit_with_trailers(work, "init", {})
    if push:
        git("push", "-q", "origin", f"HEAD:{HEAD_BRANCH}", cwd=work)
    return base


def _item(item_id: str, rank: int, path: str, sha: str, status: str = "implemented") -> dict:
    return {
        "id": item_id,
        "rank": rank,
        "path": path,
        "symbol": item_id,
        "status": status,
        "commits": {"test": None, "implement": sha, "fix": []},
    }


def _state(tmp_path, work, base, items, **over):
    over.setdefault("phase", "verify")
    return make_state_v2(tmp_path, work, head_branch=HEAD_BRANCH, plan={"base_sha": base}, items=items, **over)


def _origin_tip(repo) -> str:
    return _run("rev-parse", HEAD_BRANCH, cwd=repo["origin"])


def _no_comment(patch_lib):
    patch_lib("publish_plan_comment", lambda state: None)


# ---------- AC-1 / AC-2: 判定は 1 か所 ----------


def _tree(rel: str) -> ast.Module:
    return ast.parse((SCRIPTS / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel", ["refactor_lib/undo.py", "refactor_lib/publish.py"])
def test_undo_and_publish_do_not_judge_the_item_status_themselves(rel):
    """AC-1: 改善項目の `status` と `LIVE` / `REVERTED` を比べるのは `ledger` だけ。"""
    tree = _tree(rel)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names
    }
    assert not names & {"LIVE", "REVERTED"}
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "status"]


def test_the_judgement_is_defined_only_in_the_ledger():
    """AC-1: 判定の関数は `ledger` にだけ定義がある。"""
    judged = {"is_live", "mark_dropped", "plan_rebuild", "unpublishable", "keepers"}
    for path in (SCRIPTS / "refactor_lib").rglob("*.py"):
        defined = {n.name for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.FunctionDef)}
        if path.name == "ledger.py":
            assert judged <= defined
        else:
            assert not judged & defined, path


def test_commands_do_not_revert_without_the_judgement():
    """AC-2: `commands/` は `revert_range` を直接呼ばず、取り消しは `undo.drop` / `undo.discard_range` を通る。"""
    for path in (SCRIPTS / "refactor_lib" / "commands").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "revert_range" not in text and "revert_item_commits" not in text, path


# ---------- #817: 残留コミットを公開しない ----------


def _published_run(tmp_path, repo, patch_lib, cmd_setup=None):
    """I-001 を採用、I-002 を取り消した後に最終ゲートへ入り、公開した状態（手順 1）。"""
    work = repo["work"]
    base = _base(work, ["src/a.py", "src/b.py"])
    _change(work, "src/a.py", 2, "by-I-001")
    c1 = commit_with_trailers(work, "I-001", item_trailers("I-001"))
    _change(work, "src/b.py", 2, "by-I-002")
    c2 = commit_with_trailers(work, "I-002", item_trailers("I-002"))
    path = _state(tmp_path, work, base, [_item("I-001", 1, "src/a.py", c1), _item("I-002", 2, "src/b.py", c2)])
    _no_comment(patch_lib)
    return path, c1


def _enter_final(refactor, path, drop_ids=("I-002",)):
    undo = sys.modules["refactor_lib.undo"]
    publish = sys.modules["refactor_lib.publish"]
    state = read_state(path)
    undo.drop(path, state, list(drop_ids), "範囲テストが落ちた")
    state["phase"] = "final"
    write_state(path, state)
    publish.enter_final_gate(path, state)
    return read_state(path)


@pytest.mark.parametrize(
    "trailers",
    [
        pytest.param(item_trailers("I-002"), id="dropped-item"),
        pytest.param({}, id="no-item-id"),
        pytest.param(item_trailers("I-999"), id="unknown-item-id"),
    ],
)
def test_a_leftover_commit_is_not_pushed(refactor, publish, patch_lib, tmp_path, repo, capsys, trailers):
    """AC-817-1 / AC-817-2: 見放した担当の残留コミットがあれば push せず終了コード 4。"""
    path, _ = _published_run(tmp_path, repo, patch_lib)
    state = _enter_final(refactor, path)
    published = _origin_tip(repo)
    work = repo["work"]
    _write(work, "src/leftover.py", "x = 1\n")
    leftover = commit_with_trailers(work, "leftover", trailers)
    capsys.readouterr()

    with pytest.raises(SystemExit) as e:
        publish.push_with_retry_marker(path, state, state)

    assert e.value.code == 4
    assert _origin_tip(repo) == published
    err = capsys.readouterr().err
    assert leftover[:12] in err
    assert f"Item-Id={trailers.get('Item-Id', '-')}" in err
    assert read_state(path)["pending_push"] is True, "やり残しとして残す"


def test_the_leftover_commit_is_caught_by_the_pushes_after_the_final_gate(refactor, publish, patch_lib, tmp_path, repo):
    """AC-817-4: 最終ゲートの後の公開（やり残しの反映）でも照合が働く。"""
    path, _ = _published_run(tmp_path, repo, patch_lib)
    state = _enter_final(refactor, path)
    published = _origin_tip(repo)
    gate = state["final_gate"]
    gate["pending_push"] = True
    _write(repo["work"], "src/leftover.py", "x = 1\n")
    commit_with_trailers(repo["work"], "leftover", item_trailers("I-002"))

    with pytest.raises(SystemExit) as e:
        publish.flush_pending_push(path, state, gate)

    assert e.value.code == 4
    assert _origin_tip(repo) == published


def test_adopted_orchestrator_and_revert_commits_are_pushed(refactor, publish, undo, patch_lib, tmp_path, repo):
    """AC-817-3 / I3: 採用した項目・同期・公開済みの取り消しの revert だけなら push する。revert は 1 度だけ。"""
    path, c1 = _published_run(tmp_path, repo, patch_lib)
    state = read_state(path)
    state["sync_command"] = "printf 'x = 2\\n' > src/generated.py"
    write_state(path, state)
    state = _enter_final(refactor, path)
    assert _origin_tip(repo) == _run("rev-parse", "HEAD", cwd=repo["work"])
    assert state["ledger"]["orchestrator_commits"], "同期のコミットを台帳へ記録する"

    # 最終ゲートの後に I-001 を取り消す（寄せた危険フラグの取り消し）。公開済みなので revert する
    record = undo.drop(path, state, ["I-001"], "寄せた危険フラグ")
    assert (record["removed"], record["reverted"]) == (0, 1)
    assert "by-I-001" not in (repo["work"] / "src" / "a.py").read_text(encoding="utf-8")
    state = read_state(path)
    publish.push_with_retry_marker(path, state, state["final_gate"])

    assert _origin_tip(repo) == _run("rev-parse", "HEAD", cwd=repo["work"])
    reverts = _run("log", "--format=%s", "--grep=^Revert", cwd=repo["work"]).splitlines()
    assert len(reverts) == 1


def test_publishing_before_the_final_gate_is_refused(publish, patch_lib, tmp_path, repo):
    """I6: 最終ゲートへ入る前に公開の入口が呼ばれたら push せずに終了コード 4。"""
    base = _base(repo["work"], ["src/a.py"])
    path = _state(tmp_path, repo["work"], base, [])
    state = read_state(path)
    state["pending_push"] = True
    pushed: list[bool] = []
    patch_lib("push_head", lambda state: pushed.append(True))

    for call in (publish.push_with_retry_marker, publish.flush_pending_push):
        with pytest.raises(SystemExit) as e:
            call(path, state, state)
        assert e.value.code == 4
    with pytest.raises(SystemExit):
        publish.enter_final_gate(path, state)
    assert pushed == []


def test_the_push_never_forces(publish, patch_lib, tmp_path, repo):
    """AC-R2: push の引数に `--force` / `--force-with-lease` / `+` の参照が無い。"""
    base = _base(repo["work"], ["src/a.py"])
    path = _state(tmp_path, repo["work"], base, [], phase="final")
    seen: list[list[str]] = []
    patch_lib("_push_with_credential_fallback", lambda args, cwd: seen.append(list(args)))
    _no_comment(patch_lib)

    publish.push_head(read_state(path))

    assert seen and all(not a.startswith("--force") and not a.startswith("+") for args in seen for a in args)


# ---------- #1399: 取り消しでコミット数が伸びない・途中の push ----------


def _thirteen_items(tmp_path, repo):
    """13 件の改善項目。I-005 と I-006 は同じファイルの隣の行を触る（取り消すと広がる）。"""
    work = repo["work"]
    files = [f"src/m{i:02d}.py" for i in range(1, 14)]
    base = _base(work, files, push=True)
    items, originals = [], {}
    for n in range(1, 14):
        rel = files[4] if n == 6 else files[n - 1]
        _change(work, rel, 3 if n == 6 else 2, f"by-I-{n:03d}")
        sha = commit_with_trailers(work, f"I-{n:03d}", item_trailers(f"I-{n:03d}"))
        items.append(_item(f"I-{n:03d}", n, rel, sha))
        originals[f"I-{n:03d}"] = sha
    return _state(tmp_path, work, base, items), base, originals


def _expected_tree(work: pathlib.Path, base: str, shas: list[str]) -> str:
    """起点に残す改善項目の元のコミットを古い順に当てた木。"""
    scratch = work.parent / "scratch"
    git("worktree", "add", "-q", "--detach", str(scratch), base, cwd=work)
    try:
        for sha in shas:
            git("cherry-pick", sha, cwd=scratch)
        return _run("rev-parse", "HEAD^{tree}", cwd=scratch)
    finally:
        git("worktree", "remove", "--force", str(scratch), cwd=work)


def test_repeated_drops_do_not_grow_the_history(undo, tmp_path, repo):
    """AC-1399-1〜3: 5 回の取り消し（うち 1 回は衝突した項目を外す）で revert を打たず、コミット数も内容も定義どおり。"""
    path, base, originals = _thirteen_items(tmp_path, repo)
    work = repo["work"]
    for target in ("I-012", "I-005", "I-009", "I-002", "I-013"):
        state = read_state(path)
        live_before = sum(1 for i in state["items"] if i["status"] == "implemented")
        record = undo.drop(path, state, [target], "範囲テストが落ちた")

        assert record["reverted"] == 0, "最終ゲートより前は revert しない"
        assert record["removed"] + record["replayed"] <= live_before, "前の取り消しの積み直しを 2 重に打たない"
        assert not _run("log", "--format=%s", "--grep=^Revert", f"{base}..HEAD", cwd=work)
        count = int(_run("rev-list", "--count", f"{base}..HEAD", cwd=work))
        assert count <= 13 * 2
        live = [i["id"] for i in read_state(path)["items"] if i["status"] == "implemented"]
        assert _run("rev-parse", "HEAD^{tree}", cwd=work) == _expected_tree(work, base, [originals[i] for i in live])

    items = {i["id"]: i for i in read_state(path)["items"]}
    assert items["I-006"]["status"] == "reverted", "I-005 と同じファイルの隣の行を触った I-006 は衝突して外れる"
    assert [d["mode"] for d in read_state(path)["drops"]].count("ejected") == 1
    assert _origin_tip(repo) == base, "取り消しは push を伴わない（AC-1399-5）"


def test_a_failed_drop_leaves_origin_untouched(undo, tmp_path, repo):
    """AC-1399-5 / #1793 の I4: 項目に属さないコミット（オーケストレーター）の積み直しが衝突した取り消しは中断し、
    origin の head は開始時のまま。"""
    work = repo["work"]
    base = _base(work, ["src/foo.py", "src/bar.py"])
    _change(work, "src/foo.py", 2, "by-I-001")
    c1 = commit_with_trailers(work, "I-001", item_trailers("I-001"))
    _change(work, "src/foo.py", 3, "by-orchestrator")
    c2 = commit_with_trailers(work, "sync", {})
    _change(work, "src/bar.py", 3, "by-I-003")
    c3 = commit_with_trailers(work, "I-003", item_trailers("I-003"))
    path = _state(
        tmp_path,
        work,
        base,
        [_item("I-001", 1, "src/foo.py", c1), _item("I-003", 3, "src/bar.py", c3)],
        ledger={"orchestrator_commits": [c2]},
    )

    with pytest.raises(SystemExit) as e:
        undo.drop(path, read_state(path), ["I-001"], "範囲テストが落ちた")

    assert e.value.code == 4
    assert _run("rev-parse", "HEAD", cwd=work) == c3
    assert _origin_tip(repo) == base
    assert all(i["status"] == "implemented" for i in read_state(path)["items"])


def test_the_first_push_comes_at_the_final_gate(cmd_converge, cmd_gate, publish, patch_lib, tmp_path, repo, monkeypatch):
    """AC-1399-4: 検証を終えて最終ゲートへ入る時点の push が、実行で最初の push になる。"""
    work = repo["work"]
    base = _base(work, ["src/a.py"])
    _change(work, "src/a.py", 2, "by-I-001")
    c1 = commit_with_trailers(work, "I-001", item_trailers("I-001"))
    path = _state(tmp_path, work, base, [_item("I-001", 1, "src/a.py", c1, status="verified")], phase="verify")
    monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(path.parent))
    calls: list[str] = []
    patch_lib("push_head", lambda state: calls.append(state["phase"]))
    patch_lib("_settle_scope", lambda path, state: None)
    patch_lib("_whole_test", lambda path, state, flags: False)

    cmd_converge.cmd_verify(type("A", (), {"id": 130})())

    assert calls == ["final"]


# ---------- #1237・#1793: 外すのは衝突したコミットの項目だけ ----------

FILES_1237 = {
    "I-002": "cross-refactoring/scripts/drive.py",
    "I-003": "supervise.py",
    "I-004": "cross-refactoring/scripts/drive.py",
    "I-005": "cross-review/scripts/drive.py",
    "I-006": "cost/phase_cost.py",
}


def _six_items(tmp_path, repo):
    """#1237 の 6 項目。I-001 は見送り、I-004 は I-002 の変更の隣の行を触る。"""
    work = repo["work"]
    base = _base(work, sorted(set(FILES_1237.values())))
    items = [
        {"id": "I-001", "rank": 1, "path": "supervise.py", "status": "deferred", "commits": {"test": None, "implement": None, "fix": []}}
    ]
    for n, (item_id, rel) in enumerate(FILES_1237.items(), start=2):
        _change(work, rel, 3 if item_id == "I-004" else 2, f"by-{item_id}")
        items.append(_item(item_id, n, rel, commit_with_trailers(work, item_id, item_trailers(item_id)), status="verified"))
    return _state(tmp_path, work, base, items), base


def test_a_conflict_ejects_only_the_item_whose_commit_conflicted(undo, ledger, tmp_path, repo):
    """AC-1237-1 / AC-1237-2 / AC-1237-5: I-002 を取り消すと I-004 だけが外れ、I-003・I-005・I-006 は残って確かめ直しを待つ。"""
    path, _ = _six_items(tmp_path, repo)
    work = repo["work"]

    record = undo.drop(path, read_state(path), ["I-002"], "範囲テストが落ちた")

    assert (record["mode"], record["dropped"]) == ("ejected", ["I-002", "I-004"])
    assert record["recheck"] == ["I-003", "I-005", "I-006"]
    state = read_state(path)
    items = {i["id"]: i for i in state["items"]}
    assert [items[i]["status"] for i in ("I-002", "I-004")] == ["reverted", "reverted"]
    for kept in ("I-003", "I-005", "I-006"):
        assert items[kept]["status"] == "implemented", "最終ゲートより前の取り消しの後は範囲テストで確かめ直す"
        assert not items[kept].get("failure_reason")
        assert f"by-{kept}" in (work / FILES_1237[kept]).read_text(encoding="utf-8")
    assert "I-002 の取り消しで" in items["I-004"]["failure_reason"]
    state["final_gate"]["status"] = "passed"
    assert ledger.adoption_confirmed(state) and ledger.remaining_count(state) == 3


def test_drops_after_an_ejecting_drop_keep_the_history_bounded(undo, tmp_path, repo):
    """AC-1237-3: 項目を外した取り消しの後に 2 件を続けて取り消しても、コミット数が上限を超えない。"""
    path, base = _six_items(tmp_path, repo)
    work = repo["work"]
    for target in ("I-002", "I-005", "I-003"):
        undo.drop(path, read_state(path), [target], "範囲テストが落ちた")
        assert int(_run("rev-list", "--count", f"{base}..HEAD", cwd=work)) <= 5 * 2
    assert _run("rev-list", "--count", f"{base}..HEAD", cwd=work) == "1", "残る I-006 のコミットだけ"
    assert "by-I-006" in (work / FILES_1237["I-006"]).read_text(encoding="utf-8")


def test_resuming_a_drop_gives_the_same_result(undo, tmp_path, repo):
    """AC-3 / I7: 積み直しの後・記録の前に落ちても、再開すれば落とさずに終えたときと同じになる。"""
    path, _ = _six_items(tmp_path, repo)
    work = repo["work"]
    pristine = read_state(path)
    undo.drop(path, read_state(path), ["I-002"], "範囲テストが落ちた")
    finished = read_state(path)
    finished_tree = _run("rev-parse", "HEAD^{tree}", cwd=work)
    # git は積み直しまで進み、状態は `pending_drop` を保存した直後のまま残った
    before = pristine["items"][-1]["commits"]["implement"]
    pristine["pending_drop"] = {"items": ["I-002"], "reason": "範囲テストが落ちた", "before": before}
    write_state(path, pristine)

    undo.resume_pending_drop(path, read_state(path))

    resumed = read_state(path)
    assert _run("rev-parse", "HEAD^{tree}", cwd=work) == finished_tree
    assert [i["status"] for i in resumed["items"]] == [i["status"] for i in finished["items"]]
    assert len(resumed["drops"]) == len(finished["drops"]) == 1
    assert resumed["pending_drop"] is None


# ---------- 退行しないこと ----------


def test_a_drop_without_commits_to_remove_does_not_touch_git(undo, tmp_path, repo):
    """AC-R1: 取り消すコミットが無ければ `mode: skip` で git に触れない。"""
    work = repo["work"]
    base = _base(work, ["src/a.py"])
    path = _state(
        tmp_path, work, base, [{"id": "I-001", "rank": 1, "status": "planned", "commits": {"test": None, "implement": None, "fix": []}}]
    )

    record = undo.drop(path, read_state(path), ["I-001"], "not_done")

    assert record["mode"] == "skip"
    assert _run("rev-parse", "HEAD", cwd=work) == base
    assert read_state(path)["items"][0]["status"] == "reverted"
