"""#3308: スプリントに属さないリリース（まとめたリリース・`new release`）は、取り込み方とリリースのレビューの単位を
`main` 宛の検査と同じ本番チャネルの宣言（`origin/<本番チャネル>:.ndf/worktree.json`）から読む。

- ベースブランチだけが `selected` なら `whole` で組む（受け入れ条件 1・2・I1）。両方が `selected` なら今と同じ（受け入れ条件 3）
- 本番チャネルの宣言を読めなければ `whole`、`branch_flow` が壊れていれば止まる（受け入れ条件 4・I2・I3）
- 取り込み直しに失敗しても手元の `origin/<本番チャネル>` を読む（可用性）。ブランチの名前は今の宣言から解く（I5）
- スプリントの開始時の記録を持つプランは本番チャネルの宣言を読まない（受け入れ条件 7・I4）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_flow  # noqa: E402
import parallel_release  # noqa: E402
import repo as repo_lib  # noqa: E402
from supervise_lib import decl as sdecl  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}
WHOLE = dict(branch_flow.DEFAULT)
RELEASE = {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}
NO_FILE = object()
# 実装の差分がどこかで cross-review される組（#3163 の I4）。release の行だけを本番チャネルの宣言の値として確かめる
REVIEWS = {
    "impl": {"checks": ["cross-review"], "diff": "pr"},
    "sprint": {"checks": ["behavior", "cross-refactoring"], "diff": "unchecked"},
    "release": {"checks": ["cross-review"], "diff": "resolution"},
}

# new release は --design に無い課題の本文を gh で読む。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def decl(flow=None, **extra) -> dict:
    return {"version": 1, "base_branch": "develop", "production_branch": "main", **extra, **({"branch_flow": flow} if flow else {})}


def write_decl(tree: Path, value) -> None:
    path = tree / ".ndf" / "worktree.json"
    if value is NO_FILE:
        path.unlink(missing_ok=True)
    else:
        path.write_text(value if isinstance(value, str) else json.dumps(value))


def make_repo(tmp_path: Path, main_decl=None, develop_decl=None, with_main: bool = True) -> Path:
    """bare の origin に `main`（`main_decl`）と `develop`（`develop_decl`）を置き、`develop` のメインディレクトリを返す。"""
    seed = tmp_path / "seed"
    (seed / ".ndf").mkdir(parents=True)
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    (seed / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}, "release": RELEASE}))
    (seed / "plugins" / "ndf" / ".claude-plugin").mkdir(parents=True)
    (seed / "plugins" / "ndf" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1.0.0"}))
    write_decl(seed, decl() if main_decl is None else main_decl)
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "main")
    git(seed, "checkout", "-q", "-b", "develop")
    write_decl(seed, decl(SELECTED) if develop_decl is None else develop_decl)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "--allow-empty", "-m", "develop")  # 両方の宣言が同じでもブランチを分ける
    if not with_main:
        git(seed, "branch", "-q", "-D", "main")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", "-b", "develop", str(origin), str(main))
    return main


def new_release(tmp_path: Path, main: Path, channel: str = "prod"):
    out = tmp_path / f"release-{channel}.json"
    version = "1.1.0" if channel == "prod" else "1.1.0-dev.1"
    wt = main / ".worktrees" / "release" / "v1.1.0"
    args = ["new", "release", "--version", version, "--prs", "1", "--channel", channel, "--worktree", str(wt), "--out", str(out)]
    return inproc.run(SUPERVISE, *args, cwd=main), out


def parallel_plans(tmp_path: Path, main: Path) -> tuple[dict, dict]:
    return parallel_release.plan_parallel_release(main, "p1", tmp_path / "releases" / "1" / "1", "1.1.0")


def assert_whole(plan: dict) -> None:
    text = json.dumps(plan, ensure_ascii=False)
    assert "--promotion selected" not in text and "release-branch" not in text


def snapshot(main: Path, **names) -> dict:
    """種別リリースのプランが受け取るスナップショット（`apply_decls` と同じ読み先と名前）。"""
    a = SimpleNamespace(base=names.get("base", "develop"), production_branch=names.get("production", "main"), flow=None)
    return sdecl.flow_snapshot(a, *sdecl.production_flow_decl(a, [main]))


@pytest.mark.parametrize("main_decl", [decl(), decl(WHOLE)], ids=["no-branch-flow", "explicit-whole"])
def test_only_the_base_branch_selected_builds_the_whole_release(tmp_path, main_decl):
    """受け入れ条件 1・2・I1: ベースブランチだけが selected なら、まとめたリリースと new release は whole で組む。"""
    main = make_repo(tmp_path, main_decl)
    present, release = parallel_plans(tmp_path, main)
    assert_whole(present)
    assert_whole(release)
    for channel in ("prod", "dev"):
        p, out = new_release(tmp_path, main, channel)
        assert p.returncode == 0, p.stdout + p.stderr
        assert_whole(json.loads(out.read_text()))
    snap = snapshot(main)
    assert snap["promotion"] == "whole" and snap["source"].startswith(("origin/main", "既定値（origin/main"))


def test_both_selected_keeps_the_selected_flow(tmp_path):
    """受け入れ条件 3: 両方が selected なら今と同じ。単位を持たないプランは今と同じく単位が無いと止まる。"""
    main = make_repo(tmp_path, decl(SELECTED))
    with pytest.raises(sdecl.DeclError, match="単位"):
        parallel_plans(tmp_path, main)
    p, _ = new_release(tmp_path, main)
    assert p.returncode == 2 and "単位" in p.stderr
    assert snapshot(main)["promotion"] == "selected"


@pytest.mark.parametrize(
    "main_decl, why",
    [
        (NO_FILE, "origin/main に .ndf/worktree.json が無い"),
        ("{not json", "JSON のオブジェクトとして読めない"),
        ("[1]", "JSON のオブジェクトとして読めない"),
        ({"version": 2, "branch_flow": SELECTED}, "version が 1 でない"),
    ],
    ids=["no-file", "not-json", "array", "version-2"],
)
def test_an_unreadable_production_declaration_builds_the_whole_release(tmp_path, main_decl, why):
    """受け入れ条件 4・I2: 本番チャネルの宣言を読めなければ whole で組み、出所に読めない理由を書く。"""
    main = make_repo(tmp_path, main_decl)
    assert_whole(parallel_plans(tmp_path, main)[1])
    p, out = new_release(tmp_path, main)
    assert p.returncode == 0, p.stdout + p.stderr
    assert_whole(json.loads(out.read_text()))
    snap = snapshot(main)
    assert snap["promotion"] == "whole" and why in snap["source"]


def test_without_production_on_origin_the_release_is_whole(tmp_path):
    """受け入れ条件 4・I2: origin に本番チャネルが無ければ whole で組む。"""
    main = make_repo(tmp_path, with_main=False)
    snap = snapshot(main)
    assert snap["promotion"] == "whole" and "origin/main が無い" in snap["source"]


@pytest.mark.parametrize("flow, key", [({"promotion": "selected"}, "start"), ({**WHOLE, "unknown": 1}, "unknown")])
def test_a_broken_production_declaration_stops_with_the_key_to_fix(tmp_path, flow, key):
    """受け入れ条件 4・I3: 本番チャネルの branch_flow が表に合わなければ、既定値へ倒さずに止める。"""
    main = make_repo(tmp_path, decl(flow))
    with pytest.raises(sdecl.DeclError, match="origin/main の .ndf/worktree.json の branch_flow: ") as e:
        parallel_plans(tmp_path, main)
    assert f"branch_flow.{key}" in str(e.value)
    p, out = new_release(tmp_path, main)
    assert p.returncode == 2 and f"branch_flow.{key}" in p.stderr and "origin/main" in p.stderr
    assert not out.exists()


def test_an_unreachable_origin_reads_the_local_tracking_branch(tmp_path):
    """可用性: origin へ取り込めなくても、手元の origin/main の宣言で組む。"""
    main = make_repo(tmp_path, decl(SELECTED))
    git(main, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert repo_lib.production_decl(main, "main")[0]["branch_flow"] == SELECTED
    assert snapshot(main)["promotion"] == "selected"


def test_the_fetch_moves_the_tracking_branch_to_the_origin_tip(tmp_path):
    """前提 4: 組む前に origin から本番チャネルを取り込み直す（手元の origin/main が古くても origin の先頭を読む）。"""
    main = make_repo(tmp_path)
    seed = tmp_path / "seed"
    git(seed, "checkout", "-q", "main")
    write_decl(seed, decl(SELECTED))
    git(seed, "commit", "-qam", "switch")
    git(seed, "push", "-q", str(tmp_path / "origin.git"), "main")
    assert snapshot(main)["promotion"] == "selected"


def test_release_reviews_come_from_the_production_declaration(tmp_path):
    """決定 2: リリースのレビューの単位も本番チャネルの宣言から読み、ベースブランチの宣言と混ぜない。"""
    main = make_repo(tmp_path, decl({**SELECTED, "reviews": REVIEWS}), decl())
    snap = snapshot(main)
    assert snap["promotion"] == "selected" and snap[branch_flow.REVIEWS_KEY]["release"] == REVIEWS["release"]


def test_branch_names_come_from_the_current_declaration(tmp_path):
    """I5: 本番チャネルの宣言に別の base_branch・production_branch を書いても、名前はメインディレクトリの宣言の値。"""
    main = make_repo(tmp_path, decl(WHOLE, base_branch="trunk-x", production_branch="prod-x"))
    p, out = new_release(tmp_path, main)
    assert p.returncode == 0, p.stdout + p.stderr
    text = out.read_text()
    assert "trunk-x" not in text and "prod-x" not in text and "origin/develop" in text


def test_a_sprint_plan_reads_its_start_record_not_the_production_declaration(tmp_path):
    """受け入れ条件 7・I4: スプリントの打ち直しは開始時の記録で組み、壊れた本番チャネルの宣言を読まない。"""
    main = make_repo(tmp_path, decl({"unknown": 1}))
    args = ["new", "sprint", "--name", "s1", "--worktree", str(main), "--issue", "11", "--version", "1.1.0-dev.1"]
    p = inproc.run(SUPERVISE, *args, "--out", str(tmp_path / "s1"), cwd=main)
    assert p.returncode == 0, p.stdout + p.stderr
    write_decl(main, decl())  # 開始の後にベースブランチの宣言から branch_flow を外す
    p = inproc.run(SUPERVISE, *args, "--out", str(tmp_path / "s1"), cwd=main)
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = json.loads((tmp_path / "s1" / "sprint.json").read_text(encoding="utf-8"))
    assert manifest[branch_flow.SNAPSHOT_KEY]["promotion"] == "selected"
