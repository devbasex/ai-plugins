"""実行をまたいで残る状態の持ち主・寿命・片付け（`lib/run_state.py`。#2229・#2085・#1680）。

一時の git リポジトリ（origin は手元の bare）でレビュー worktree を作り、Pull Request の照会は差し替えて確かめる。
一時の置き場は `tempfile.tempdir` と `NDF_WORKTREE_BASE` で tmp_path の下へ向け、手元の `/tmp` に触れない。

| 何を確かめるか | 受け入れ条件 |
| --- | --- |
| 一覧に無い種類の作り手を落とす | AC1・I1 |
| merged の cleanup が rf<n> を pr<n> と同じく外す | AC2 |
| merged を通らずに閉じた PR の worktree を sweep が外す | AC3 |
| 持ち主の終わったセッション・deps・一時ファイルを外し、生きているもの・窓の中は残す | AC4〜AC6・AC9 |
| open の PR・生きている実行・作業の残る worktree を残す | AC7・AC8・AC10 |
| 開始が以前の実行の worktree を使い直す / 止める | AC15・I11・I12 |
| 判定できないものを残し、1 件の失敗でほかを止めない・照会は 1 本 1 回 | I5〜I7 |
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS / "lib"))

import run_owner  # noqa: E402
import run_state  # noqa: E402

OLD = time.time() - 2 * run_state.WINDOW


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def place(tmp_path, monkeypatch):
    """(root, レビュー worktree の置き場 `<base>/o--r`, 一時の置き場)。"""
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "CROSS_REVIEW_TMP_DIR"):
        monkeypatch.delenv(k, raising=False)
    remote = tmp_path / "remotes" / "o" / "r.git"
    remote.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    monkeypatch.setenv("NDF_WORKTREE_BASE", str(tmp_path / "wtbase"))
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    return root, tmp_path / "wtbase" / "o--r", systmp


def add_tree(root, path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-q", "--detach", str(path), "HEAD")
    (path / ".cross_review").mkdir()
    (path / ".cross_review" / "state.json").write_text("{}", encoding="utf-8")
    return path


def age(*paths) -> None:
    for p in paths:
        for q in (p, *(p / d for d in run_state.NDF_STATE_DIRS)):
            if q.exists():
                os.utime(q, (OLD, OLD))


class View:
    def __init__(self, states: dict[int, str | None]):
        self.states, self.calls = states, []

    def __call__(self, root, pr):
        self.calls.append(pr)
        st = self.states.get(pr)
        return None if st is None else {"state": st, "headRefOid": None}


def by_name(items):
    return {Path(i["name"]).name: i for i in items}


def listed(root) -> str:
    return git(root, "worktree", "list")


# ---------------- 片付け: レビュー worktree ----------------


def test_a_closed_pr_worktree_without_work_is_removed(place) -> None:
    """AC3: merged を通らずにマージされた・閉じた PR の作業の無い pr<n>・rf<n> は sweep の後に無い。"""
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    rf = base / "rf8"
    work, claude = add_tree(root, rf / "work"), add_tree(root, rf / "claude")
    age(pr, work, claude, rf)

    items = run_state.sweep_states(root, view=View({7: "MERGED", 8: "CLOSED"}))

    got = by_name(items)
    assert got["pr7"]["result"] == "removed" and got["work"]["result"] == "removed" and got["claude"]["result"] == "removed"
    assert got["rf8"] == {"kind": "run-dir", "name": str(rf), "result": "removed"}
    assert not pr.exists() and not rf.exists()
    assert "pr7" not in listed(root) and "rf8" not in listed(root)


def test_an_open_pr_worktree_stays_even_when_its_owner_finished(place) -> None:
    """AC7・I3: open の PR のレビュー worktree はどの片付けでも外れない。"""
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    run = run_owner.claim_owner(pr, tool="cross-review", pr=7)
    run_owner.finish_owner(pr, run)

    items = run_state.sweep_states(root, view=View({7: "OPEN"}))

    assert by_name(items)["pr7"]["result"] == "kept" and "open" in by_name(items)["pr7"]["reason"]
    assert pr.exists()


def test_a_running_owner_keeps_its_worktree_even_after_the_merge(place) -> None:
    """AC8・I2・I7: 生きている実行の worktree はマージ済みでも残り、照会もしない。"""
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    run = run_owner.claim_owner(pr, tool="cross-review", pr=7)
    view = View({7: "MERGED"})

    items = run_state.sweep_states(root, view=view)

    got = by_name(items)["pr7"]
    assert got["result"] == "kept" and run in got["reason"]
    assert pr.exists() and view.calls == []


@pytest.mark.parametrize("work", ["tracked", "untracked", "unpushed"])
def test_a_worktree_with_work_is_kept_with_the_reason(place, work) -> None:
    """AC10・I4: 未コミットの変更・未追跡のファイル・未 push のコミットを持つ worktree は外れない（git は未 push を守らない）。"""
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    if work == "tracked":
        (pr / "a.txt").write_text("changed\n", encoding="utf-8")
    elif work == "untracked":
        (pr / "note.txt").write_text("mine\n", encoding="utf-8")
    else:
        (pr / "b.txt").write_text("b\n", encoding="utf-8")
        git(pr, "add", "b.txt")
        git(pr, "commit", "-q", "-m", "local")
    age(pr)

    items = run_state.sweep_states(root, view=View({7: "MERGED"}))

    got = by_name(items)["pr7"]
    assert got["result"] == "kept"
    assert {"tracked": "未コミット", "untracked": "未追跡", "unpushed": "未 push"}[work] in got["reason"]
    assert pr.exists() and "pr7" in listed(root)


def test_undecidable_states_are_kept_and_do_not_stop_the_others(place) -> None:
    """I5・I6・I7: 壊れた記録・照会の失敗は kept「判定できない」。ほかは片付き、照会は 1 本 1 回まで。"""
    root, base, _ = place
    broken, failing, done = add_tree(root, base / "pr1"), add_tree(root, base / "pr2"), add_tree(root, base / "pr3")
    run_state.owner_path(broken).write_text("{", encoding="utf-8")
    age(broken, failing, done)
    view = View({2: None, 3: "MERGED"})

    items = run_state.sweep_states(root, view=view)

    got = by_name(items)
    assert "判定できない" in got["pr1"]["reason"] and "判定できない" in got["pr2"]["reason"]
    assert got["pr3"]["result"] == "removed"
    assert len(items) == 3 and sorted(view.calls) == [2, 3]


def test_skip_leaves_the_starting_worktree_unseen(place) -> None:
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    age(pr)

    assert run_state.sweep_states(root, skip=[pr], view=View({7: "MERGED"})) == []
    assert pr.exists()


# ---------------- 片付け: 一時の置き場 ----------------


def test_session_state_follows_its_owner_process(place) -> None:
    """AC4・AC9: 持ち主のプロセスが居ない状態ファイルは消え、生きているもの（開始時刻が一致）は残る。"""
    import procs

    _, _, tmp = place
    me = {"pid": os.getpid(), "started": procs.start_time(os.getpid())}
    (tmp / "ndf-worktree-live.json").write_text(json.dumps({"owner": me}), encoding="utf-8")
    (tmp / "ndf-worktree-reused.json").write_text(json.dumps({"owner": {**me, "started": 1.0}}), encoding="utf-8")
    (tmp / "ndf-worktree-gone.json").write_text(json.dumps({"owner": {"pid": 2**22 + 7, "started": 1.0}}), encoding="utf-8")
    (tmp / "ndf-worktree-old.json").write_text("{}", encoding="utf-8")
    os.utime(tmp / "ndf-worktree-old.json", (OLD, OLD))

    got = by_name(run_state.sweep_states(None, kinds=("session-state",)))

    assert got["ndf-worktree-live.json"]["result"] == "kept"
    assert {got[n]["result"] for n in ("ndf-worktree-reused.json", "ndf-worktree-gone.json", "ndf-worktree-old.json")} == {"removed"}
    assert sorted(p.name for p in tmp.iterdir()) == ["ndf-worktree-live.json"]


def test_deps_work_left_by_a_killed_script_is_removed(place) -> None:
    """AC5・AC9: trap が走らずに止められた worktree-deps.sh の ndf-deps.* は消え、動いているものは残る。"""
    _, _, tmp = place
    live = subprocess.Popen(["bash", "-c", "sleep 30; true", "worktree-deps.sh"])
    try:
        for name, pid in (("ndf-deps.live", live.pid), ("ndf-deps.dead", 2**22 + 7)):
            (tmp / name).mkdir()
            (tmp / name / "owner").write_text(f"{pid}\n", encoding="utf-8")

        got = by_name(run_state.sweep_states(None, kinds=("deps-work",)))
    finally:
        live.kill()
        live.wait()

    assert got["ndf-deps.live"]["result"] == "kept" and got["ndf-deps.dead"]["result"] == "removed"
    assert (tmp / "ndf-deps.live").exists() and not (tmp / "ndf-deps.dead").exists()


def test_deps_script_writes_its_owner(tmp_path) -> None:
    """AC5: worktree-deps.sh は一時の置き場の中に自分の pid を書く。"""
    text = (SCRIPTS / "worktree-deps.sh").read_text(encoding="utf-8")
    probe = tmp_path / "probe.sh"
    start = text.index('WORK=$(mktemp -d "${TMPDIR:-/tmp}/ndf-deps.XXXXXX")')
    end = text.index('LOG="$WORK/log"')
    probe.write_text(text[start:end].replace("trap 'rm -rf \"$WORK\"' EXIT", "") + 'cat "$WORK/owner"; echo "$$"\n', encoding="utf-8")
    out = subprocess.run(["bash", str(probe)], capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmp_path)}).stdout.split()

    assert len(out) == 2 and out[0] == out[1]


def test_fix_files_and_test_tmp_follow_the_window(place) -> None:
    """AC6・I6: 寿命の窓を過ぎた fix-pr<n>-*・ndf-tests-* は消え、窓の中のものは kept と理由で残る。"""
    _, _, tmp = place
    for name in ("fix-pr1-result.json", "fix-pr1-context.md", "fix-pr2-result.json"):
        (tmp / name).write_text("x", encoding="utf-8")
    for name in ("ndf-tests-usage-a", "ndf-tests-git-b"):
        (tmp / name).mkdir()
    for name in ("fix-pr1-result.json", "fix-pr1-context.md", "ndf-tests-usage-a"):
        os.utime(tmp / name, (OLD, OLD))

    items = run_state.sweep_states(None, tmp_only=True)

    got = by_name(items)
    assert got["fix-pr1-*"]["result"] == "removed" and got["ndf-tests-usage-a"]["result"] == "removed"
    assert got["fix-pr2-*"]["result"] == "kept" and "寿命の窓の中" in got["fix-pr2-*"]["reason"]
    assert got["ndf-tests-git-b"]["result"] == "kept"
    assert sorted(p.name for p in tmp.iterdir()) == ["fix-pr2-result.json", "ndf-tests-git-b"]


# ---------------- 持ち主の記録と開始 ----------------


def test_touch_and_finish_write_only_for_the_same_run(place) -> None:
    """I12: 違う run の touch / finish は記録を変えない。"""
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    run = run_owner.claim_owner(pr, tool="cross-review", pr=7)
    before = run_state.owner_path(pr).read_text(encoding="utf-8")

    assert run_owner.touch_owner(pr, "other")[0] is False and run_owner.finish_owner(pr, None)[0] is False
    assert run_state.owner_path(pr).read_text(encoding="utf-8") == before
    assert run_owner.finish_owner(pr, run) == (True, None)
    assert run_state.read_owner(pr)[0]["finished_at"]


def test_check_start_separates_continuation_other_run_and_previous_run(place) -> None:
    """AC15・I11・I12: 同じ実行の続きは通し、別の実行が動いていれば止め、以前の実行は作業の有無で分ける。"""
    root, base, _ = place
    pr = add_tree(root, base / "pr7")
    run = run_owner.claim_owner(pr, tool="cross-review", pr=7)

    assert run_state.check_start(pr, run) is None
    assert "別の実行" in run_state.check_start(pr, None) and "--run" in run_state.check_start(pr, "other")

    run_owner.finish_owner(pr, run)
    assert run_state.check_start(pr, None) is None
    (pr / "a.txt").write_text("changed\n", encoding="utf-8")
    assert "作業が残る" in run_state.check_start(pr, None)
    assert (pr / "a.txt").read_text(encoding="utf-8") == "changed\n"


def test_check_start_looks_at_the_work_tree_of_a_run_dir(place) -> None:
    root, base, _ = place
    rf = base / "rf9"
    work = add_tree(root, rf / "work")
    (work / "c.txt").write_text("c\n", encoding="utf-8")
    git(work, "add", "c.txt")
    git(work, "commit", "-q", "-m", "local")
    age(work, rf)

    assert "未 push" in run_state.check_start(rf, None)


# ---------------- merged の cleanup ----------------


FAKE_GH = """#!{py}
import json, os, sys
a = sys.argv[1:]
prs = json.loads(os.environ.get("FAKE_GH_PRS", "{{}}"))
if a[:2] == ["pr", "view"] and a[2] in prs:
    print(json.dumps(prs[a[2]]))
    sys.exit(0)
sys.exit(1)
"""


def test_merged_cleanup_removes_rf_like_pr(place, tmp_path) -> None:
    """AC2・AC16: マージ済みの PR で cleanup を打つと、作業の無い rf<n>（中の work を含む）が pr<n> と同じく外れる。"""
    root, base, systmp = place
    pr, work = add_tree(root, base / "pr5"), add_tree(root, base / "rf5" / "work")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH.format(py=sys.executable), encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    head = git(root, "rev-parse", "HEAD").strip()
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "TMPDIR": str(systmp)}
    env["FAKE_GH_PRS"] = json.dumps(
        {"5": {"headRefName": "feature/z", "state": "MERGED", "mergeCommit": {"oid": head}, "headRefOid": head}}
    )
    env["NDF_PRESENTATION_DIR"] = str(tmp_path / "pres")

    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "merged-steps.py"), "cleanup", "5", "--root", str(root)], capture_output=True, text=True, env=env
    )

    out = json.loads(p.stdout.strip().splitlines()[-1])
    got = {(i["kind"], Path(i["name"]).name): i["result"] for i in out["items"]}
    assert got[("worktree", "pr5")] == "removed" and got[("worktree", "work")] == "removed" and got[("run-dir", "rf5")] == "removed"
    assert not pr.exists() and not work.exists() and not (base / "rf5").exists()
    assert "pr5" not in listed(root) and "rf5" not in listed(root)
    assert {i["result"] for i in out["items"]} <= {"removed", "deleted", "absent", "kept", "stopped", "pulled"}
    assert {"swept_removed", "swept_kept"} <= set(out["metrics"])


# ---------------- 状態の一覧 ----------------


_CREATES = re.compile(r"gettempdir\(|\bTMPDIR\b|worktree_parent\(|\bmktemp\b")


def test_every_script_that_writes_to_the_temp_place_is_in_the_list() -> None:
    """AC1・I1: 一時の置き場へ作るスクリプトは KINDS のどれかの作り手に載り、各行は置き場・持ち主・寿命・時点を持つ。"""
    creators = {c for k in run_state.KINDS for c in k.creators}
    found = set()
    for path in [*PLUGIN.glob("scripts/**/*"), *PLUGIN.glob("skills/*/scripts/**/*")]:
        rel = path.relative_to(PLUGIN).as_posix()
        # 試行の置き場（scripts の下の experimental）は既定の振る舞いの外で、本体の一覧から指さない（AGENTS.md の「安定版と実験版」）
        trial = rel.split("/")[:2] == ["scripts", "experimental"]
        skipped = "/tests/" in f"/{rel}" or trial or path.name.startswith("test_") or path.name == "conftest.py"
        if path.suffix not in (".py", ".sh") or skipped:
            continue
        if _CREATES.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(rel)
    assert found - creators == set()
    assert all(k.name and k.place and k.owner and k.life and k.when for k in run_state.KINDS)
    assert all((PLUGIN / c).exists() or c == "conftest.py" for c in creators)


# ---------------- cross-review の開始（state.py init の再開の経路） ----------------


CR_STATE = PLUGIN / "skills" / "cross-review" / "scripts" / "state.py"
QUIET_GH = "#!/bin/sh\nexit 0\n"


def _cr_init(place, tmp_path, *extra):
    root, base, systmp = place
    git(root, "remote", "set-url", "origin", "https://github.com/o/r.git")
    crtmp = tmp_path / "crtmp"
    crtmp.mkdir(exist_ok=True)
    state = {"current_pr": 7, "repo": "o/r", "tmp_dir": str(crtmp), "head_branch": "feat/x", "base_branch": "main", "pr_author": "a"}
    state.update(viewer_login="a", is_own_pr=True, event_downgrade=True, worktree_path="", rounds=[], carried_over=None, final=None)
    state.update(auto_review_categories=["code"], auto_review_instructions="x", review_instructions="x")
    (crtmp / "cross-review-pr7-state.json").write_text(json.dumps(state), encoding="utf-8")
    bindir = tmp_path / "quiet-bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text(QUIET_GH, encoding="utf-8")
    (bindir / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "CROSS_REVIEW_TMP_DIR": str(crtmp), "TMPDIR": str(systmp)}
    env.setdefault("NDF_HOST", "claude")
    p = subprocess.run([sys.executable, str(CR_STATE), "init", "7", *extra], capture_output=True, text=True, env=env, cwd=root)
    return p, base / "pr7"


def test_cross_review_init_stops_while_another_run_owns_the_worktree(place, tmp_path) -> None:
    """I12: 窓の中の別の実行の記録があれば、--run 無し・違う --run の開始は止まり、記録は変わらない。一致すれば再開する。"""
    _, base, _ = place
    pr = base / "pr7"
    run = run_owner.claim_owner(pr, tool="cross-review", pr=7)
    before = run_state.owner_path(pr).read_text(encoding="utf-8")

    for extra in ((), ("--run", "other")):
        p, _ = _cr_init(place, tmp_path, *extra)
        assert p.returncode == 1 and "別の実行" in p.stderr
        assert run_state.owner_path(pr).read_text(encoding="utf-8") == before

    p, _ = _cr_init(place, tmp_path, "--run", run)
    assert p.returncode == 0, p.stderr
    assert "RESUMED=1" in p.stdout and f"RUN={run}" in p.stdout and "SWEPT=" in p.stdout


def test_cross_review_init_claims_after_a_finished_run(place, tmp_path) -> None:
    """AC15・I11: 以前の実行（終わった記録）なら作業の無い開始は通り、新しい実行の記録を書く。"""
    _, base, _ = place
    pr = base / "pr7"
    old = run_owner.claim_owner(pr, tool="cross-review", pr=7)
    run_owner.finish_owner(pr, old)

    p, _ = _cr_init(place, tmp_path)

    assert p.returncode == 0, p.stderr
    record = run_state.read_owner(pr)[0]
    assert record["run"] != old and record["finished_at"] is None and f"RUN={record['run']}" in p.stdout
