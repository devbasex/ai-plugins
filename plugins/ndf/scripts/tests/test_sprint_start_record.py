"""宣言を変える前に始めたスプリントの後の工程が、開始時の記録（目録の `ブランチ運用`）で動くこと（#3310）。

スプリントブランチの作成は開始時の起点を常に `--from` で渡し、作る時点の宣言を読まない（受け入れ条件 1）。
後片付けの残す判断は開始時の記録で決め、記録の無いスプリントだけ今の宣言で決める（受け入れ条件 2・#3308 の設計の決定 6）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_flow  # noqa: E402
import repo as repo_mod  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}

pytestmark = pytest.mark.usefixtures("issue_bodies")


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True, check=True
    )
    return p.stdout.strip()


def write_flow(repo: Path, flow) -> None:
    wt = {"version": 1, "base_branch": "develop", "production_branch": "main"}
    if flow is not None:
        wt["branch_flow"] = flow
    (repo / ".ndf" / "worktree.json").write_text(json.dumps(wt))


def make_repo(tmp_path: Path, flow=None) -> Path:
    """bare の origin を持ち、`main` と（1 コミット進んだ）`develop` を送ったリポジトリ。メインディレクトリは `develop`。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    write_flow(repo, flow)
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
    (repo / ".gitignore").write_text(".worktrees/\n.ndf/sprint-*/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    git(repo, "checkout", "-q", "-b", "develop")
    git(repo, "commit", "-q", "--allow-empty", "-m", "develop")
    git(repo, "push", "-q", "origin", "develop")
    return repo


def change_flow(repo: Path, flow) -> None:
    """宣言を書き換えてコミットし、`develop` を origin へ送る（再現手順 2）。"""
    write_flow(repo, flow)
    git(repo, "commit", "-q", "--allow-empty", "-am", "decl")  # 同じ宣言へ書き直しても通す
    git(repo, "push", "-q", "origin", "develop")


def new_sprint(tmp_path: Path, repo: Path, out: Path, name: str = "s1") -> Path:
    args = ["new", "sprint", "--name", name, "--worktree", str(repo), "--issue", "11", "--design", "11", "--out", str(out)]
    p = inproc.run(SUPERVISE, *args, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return out


def sprint_branch_cmd(out: Path) -> str:
    for e in json.loads((out / "sprint.json").read_text(encoding="utf-8"))["ステージ"]:
        for path in e.get("plans") or []:
            plan = json.loads(Path(path).read_text(encoding="utf-8"))
            for s in plan["steps"]:
                if s["id"] == "sprint-branch":
                    return s["cmd"]
    raise AssertionError("スプリントブランチのステップが無い")


def test_the_sprint_branch_is_cut_from_the_start_record_after_the_declaration_changes(tmp_path):
    """受け入れ条件 1（再現手順 1〜3）: 宣言の無いときに始めた s1 は、宣言を `start: production` に変えた後も
    開始時の起点（`origin/develop`）から切られる。"""
    repo = make_repo(tmp_path)
    out = new_sprint(tmp_path, repo, tmp_path / "s1")
    change_flow(repo, SELECTED)
    cmd = sprint_branch_cmd(out)
    assert "--from develop" in cmd
    p = subprocess.run(["bash", "-c", cmd], cwd=repo, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "起点: develop" in p.stdout
    assert git(repo, "rev-parse", "sprint/s1") == git(repo, "rev-parse", "origin/develop")
    assert git(repo, "rev-parse", "sprint/s1") != git(repo, "rev-parse", "origin/main")


def selected_sprint(tmp_path: Path, name: str = "s1") -> Path:
    """`selected` で始め、本番に入っていない `sprint/<name>` を持つリポジトリ（再現手順 4）。目録は `.ndf/sprint-<name>/` に置く。"""
    repo = make_repo(tmp_path, SELECTED)
    new_sprint(tmp_path, repo, repo / ".ndf" / f"sprint-{name}", name)
    git(repo, "branch", f"sprint/{name}", "origin/main")
    git(repo, "checkout", "-q", f"sprint/{name}")
    git(repo, "commit", "-q", "--allow-empty", "-m", "work")
    git(repo, "checkout", "-q", "develop")
    return repo


def test_cleanup_keeps_a_selected_sprint_branch_by_its_start_record(tmp_path):
    """受け入れ条件 2（再現手順 4）: 宣言を外した後も、開始時の記録が `selected` の `sprint/s1` は本番に入るまで残す。"""
    repo = selected_sprint(tmp_path)
    change_flow(repo, None)
    record = repo_mod.sprint_flow_record(repo, "sprint/s1")
    assert record and record["promotion"] == "selected"
    assert repo_mod.kept_sprint_branch(repo, "sprint/s1", record)
    # 本番チャネルに含まれたら消してよい
    git(repo, "push", "-q", "origin", "sprint/s1:main")
    git(repo, "fetch", "-q", "origin")
    assert repo_mod.kept_sprint_branch(repo, "sprint/s1", record) is None


@pytest.mark.parametrize("flow, kept", [(SELECTED, True), (None, False)], ids=["selected", "no-declaration"])
def test_a_sprint_without_a_record_follows_the_current_declaration(tmp_path, flow, kept):
    """受け入れ条件 2: 開始時の記録の無いスプリントは、今と同じく今の宣言で決める。"""
    repo = selected_sprint(tmp_path)
    git(repo, "branch", "sprint/old", "sprint/s1")
    change_flow(repo, flow)
    assert repo_mod.sprint_flow_record(repo, "sprint/old") is None
    assert bool(repo_mod.kept_sprint_branch(repo, "sprint/old", None)) is kept


def test_the_record_search_skips_plan_state_directories(tmp_path):
    """プランの状態ディレクトリ（`<プラン>-state`）の中の目録（作業の写し）は記録として拾わない。"""
    repo = make_repo(tmp_path)
    copy = repo / ".ndf" / "sprint-x" / "6-impl-1-state" / "work"
    copy.mkdir(parents=True)
    snap = {"promotion": "selected"}
    (copy / "sprint.json").write_text(json.dumps({"スプリント": "s9", branch_flow.SNAPSHOT_KEY: snap}))
    assert repo_mod.sprint_flow_record(repo, "sprint/s9") is None
    (repo / ".ndf" / "sprint-x" / "sprint.json").write_text(json.dumps({"スプリント": "s9", branch_flow.SNAPSHOT_KEY: snap}))
    assert repo_mod.sprint_flow_record(repo, "sprint/s9") == snap


def load_merged_steps():
    spec = importlib.util.spec_from_file_location("merged_steps_3310", SCRIPTS / "merged-steps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cleanup_passes_the_start_record_to_the_keep_decision(tmp_path, monkeypatch):
    """後片付け（merged-steps.py の PR 1 件分）が目録の開始時の記録で残す判断をし、宣言を外した後もブランチを消さない。"""
    repo = selected_sprint(tmp_path)
    change_flow(repo, None)
    ms = load_merged_steps()
    monkeypatch.setattr(ms, "merged_pr_info", lambda root, n, add: ("sprint/s1", git(repo, "rev-parse", "sprint/s1")))
    items = []
    ms._cleanup_pr(ms.CleanupContext(main_dir=str(repo), slug=None, wt_base=tmp_path / "wt", add=lambda *i: items.append(i)), 5)
    assert [i[:3] for i in items] == [("branch", "sprint/s1", "kept")]
    assert git(repo, "rev-parse", "--verify", "--quiet", "refs/heads/sprint/s1")


# --- 現状固定テスト（I-002: sprint_flow_record の分岐） ---------------------------------------------


def plain_repo(tmp_path: Path) -> Path:
    """origin を持たない、`.ndf/` だけの git リポジトリ（記録の探索だけを見る）。"""
    repo = tmp_path / "plain"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def put_manifest(repo: Path, rel: str, body, mtime: float | None = None) -> Path:
    d = repo / ".ndf" / rel
    d.mkdir(parents=True, exist_ok=True)
    path = d / "sprint.json"
    path.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.mark.parametrize("branch", [None, ""], ids=["none", "empty"])
def test_characterize_record_without_branch_is_none(tmp_path, branch):
    repo = plain_repo(tmp_path)
    put_manifest(repo, "sprint-a", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: {"promotion": "selected"}})
    assert repo_mod.sprint_flow_record(repo, branch) is None


@pytest.mark.parametrize("root", [None, ""], ids=["none", "empty"])
def test_characterize_record_without_root_is_none(root):
    assert repo_mod.sprint_flow_record(root, "sprint/a") is None


def test_characterize_record_outside_git_is_none(tmp_path):
    d = tmp_path / "nogit"
    (d / ".ndf" / "sprint-a").mkdir(parents=True)
    (d / ".ndf" / "sprint-a" / "sprint.json").write_text(
        json.dumps({"スプリント": "a", branch_flow.SNAPSHOT_KEY: {"promotion": "selected"}})
    )
    assert repo_mod.sprint_flow_record(d, "sprint/a") is None


def test_characterize_record_without_ndf_dir_is_none(tmp_path):
    repo = tmp_path / "no-ndf"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    assert repo_mod.sprint_flow_record(repo, "sprint/a") is None


def test_characterize_record_matches_explicit_branch_key_over_sprint_name(tmp_path):
    """`ブランチ` を持つ目録は `ブランチ` で照合し、`sprint/<スプリント>` では照合しない。"""
    repo = plain_repo(tmp_path)
    snap = {"promotion": "selected"}
    put_manifest(repo, "sprint-a", {"スプリント": "a", "ブランチ": "feature/x", branch_flow.SNAPSHOT_KEY: snap})
    assert repo_mod.sprint_flow_record(repo, "feature/x") == snap
    assert repo_mod.sprint_flow_record(repo, "sprint/a") is None


def test_characterize_record_falls_back_to_sprint_name_when_branch_key_is_empty(tmp_path):
    repo = plain_repo(tmp_path)
    snap = {"promotion": "selected"}
    put_manifest(repo, "sprint-a", {"スプリント": "a", "ブランチ": "", branch_flow.SNAPSHOT_KEY: snap})
    assert repo_mod.sprint_flow_record(repo, "sprint/a") == snap


def test_characterize_record_without_sprint_name_matches_sprint_none(tmp_path):
    """`ブランチ` も `スプリント` も無い目録は `sprint/None` として照合される（現状の振る舞い）。"""
    repo = plain_repo(tmp_path)
    snap = {"promotion": "selected"}
    put_manifest(repo, "sprint-a", {branch_flow.SNAPSHOT_KEY: snap})
    assert repo_mod.sprint_flow_record(repo, "sprint/None") == snap


@pytest.mark.parametrize(
    "body",
    [
        "{not json",
        json.dumps(["list"]),
        json.dumps({"スプリント": "a"}),
        json.dumps({"スプリント": "a", branch_flow.SNAPSHOT_KEY: {}}),
        json.dumps({"スプリント": "a", branch_flow.SNAPSHOT_KEY: "selected"}),
        json.dumps({"スプリント": "a", branch_flow.SNAPSHOT_KEY: None}),
    ],
    ids=["broken-json", "not-dict", "no-snapshot", "empty-snapshot", "str-snapshot", "null-snapshot"],
)
def test_characterize_record_skips_unusable_manifests(tmp_path, body):
    """読めない目録・辞書でない目録・記録の無い（空・辞書でない）目録は飛ばし、残りの目録の記録を返す。"""
    repo = plain_repo(tmp_path)
    put_manifest(repo, "sprint-bad", body, mtime=2_000_000_000)
    assert repo_mod.sprint_flow_record(repo, "sprint/a") is None
    good = {"promotion": "selected"}
    put_manifest(repo, "sprint-good", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: good}, mtime=1_000_000_000)
    assert repo_mod.sprint_flow_record(repo, "sprint/a") == good


def test_characterize_record_picks_the_newest_manifest(tmp_path):
    repo = plain_repo(tmp_path)
    put_manifest(repo, "sprint-z", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: {"p": "old"}}, mtime=1_000_000_000)
    put_manifest(repo, "sprint-b", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: {"p": "new"}}, mtime=1_500_000_000)
    assert repo_mod.sprint_flow_record(repo, "sprint/a") == {"p": "new"}


def test_characterize_record_breaks_mtime_ties_by_the_larger_path(tmp_path):
    repo = plain_repo(tmp_path)
    put_manifest(repo, "sprint-a", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: {"p": "a"}}, mtime=1_000_000_000)
    put_manifest(repo, "sprint-b", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: {"p": "b"}}, mtime=1_000_000_000)
    assert repo_mod.sprint_flow_record(repo, "sprint/a") == {"p": "b"}


def test_characterize_record_finds_manifest_at_ndf_top_and_nested(tmp_path):
    """`.ndf/sprint.json` 自体と、`-state` で終わらない深い階層の目録も探す。"""
    repo = plain_repo(tmp_path)
    (repo / ".ndf" / "sprint.json").write_text(json.dumps({"スプリント": "top", branch_flow.SNAPSHOT_KEY: {"p": 1}}))
    put_manifest(repo, "x/y/z", {"スプリント": "deep", branch_flow.SNAPSHOT_KEY: {"p": 2}})
    put_manifest(repo, "x/statefull", {"スプリント": "st", branch_flow.SNAPSHOT_KEY: {"p": 3}})
    assert repo_mod.sprint_flow_record(repo, "sprint/top") == {"p": 1}
    assert repo_mod.sprint_flow_record(repo, "sprint/deep") == {"p": 2}
    assert repo_mod.sprint_flow_record(repo, "sprint/st") == {"p": 3}


def test_characterize_record_from_a_worktree_reads_the_main_directory(tmp_path):
    """worktree から呼んでも、メインディレクトリの `.ndf/` を探す。"""
    repo = plain_repo(tmp_path)
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    snap = {"promotion": "selected"}
    put_manifest(repo, "sprint-a", {"スプリント": "a", branch_flow.SNAPSHOT_KEY: snap})
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "w", str(wt))
    assert repo_mod.sprint_flow_record(wt, "sprint/a") == snap
