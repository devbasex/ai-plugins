"""#2097: production_branch を宣言しないリポジトリでも、既定ブランチを本番チャネルにしてリリースプランを組む。

既定ブランチは、まだ作っていない worktree のパスではなく、宣言を探す木の並びのうち最初に存在する git の木から解く。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import repo as repo_lib  # noqa: E402
from supervise_lib import decl as sdecl  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
RELEASE = {"form": "package-plugin", "plugin": "foo", "runtimes": ["claude"]}
NO_PROD = "リリース承認ゲートと本番は置いていない"

# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def make_repo(tmp_path: Path, head: str = "main", wt: dict | None = None, set_head: bool = True, base: str | None = None) -> Path:
    """bare の origin と clone。clone の `.ndf/` は worktree.json・supervise.json・pace.json を持つ。"""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", head)
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(seed, "config", k, v)
    ndf = seed / ".ndf"
    ndf.mkdir()
    (ndf / "worktree.json").write_text(json.dumps(wt or {"version": 1, "base_branch": base or head}))
    (ndf / "supervise.json").write_text(json.dumps({"version": 1, "test": {"command": "true {paths}"}, "release": RELEASE}))
    pace = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "true"}
    (ndf / "pace.json").write_text(json.dumps({"version": 1, "fast": pace, "auto": pace}))
    (seed / "plugins" / "foo" / ".claude-plugin").mkdir(parents=True)
    (seed / "plugins" / "foo" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "0.9.0"}))
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "base")
    if base and base != head:
        git(seed, "branch", base)
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    main = tmp_path / "main"
    git(tmp_path, "clone", "-q", str(origin), str(main))
    if set_head:
        git(main, "remote", "set-head", "origin", head)
    else:
        git(main, "remote", "set-head", "origin", "-d")
    return main


def release(main: Path, out: Path, channel: str = "prod", *extra: str, worktree: Path | None = None):
    version = "1.0.0" if channel == "prod" else "1.0.0-dev.1"
    wt = worktree or main / ".worktrees" / "release" / "v1.0.0"
    return inproc.run(
        SUPERVISE,
        *("new", "release", "--version", version, "--prs", "1", "--channel", channel, "--worktree", str(wt), "--out", str(out)),
        *extra,
        cwd=main,
    )


def steps_of(out: Path) -> dict:
    return {s["id"]: s for s in json.loads(out.read_text())["steps"]}


def verify_ref(steps: dict) -> str:
    cmd = steps["verify"]["cmd"]
    return cmd.split("--ref ", 1)[1].split()[0]


@pytest.mark.parametrize("head", ["main", "trunk"])
def test_prod_release_falls_back_to_the_default_branch_before_the_worktree_exists(tmp_path, head):
    """受け入れ条件 1〜3・I1・I2: worktree の有無にかかわらず、既定ブランチを本番チャネルにして同じプランを書く。"""
    main = make_repo(tmp_path, head)
    out = tmp_path / "before.json"
    p = release(main, out)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["status"] == "ok"
    before = steps_of(out)
    assert {"approve", "snapshot", "release", "verify"} <= set(before)
    assert verify_ref(before) == head
    wt = main / ".worktrees" / "release" / "v1.0.0"
    git(main, "worktree", "add", "-q", str(wt), "-b", "release/v1.0.0")
    after_out = tmp_path / "after.json"
    p = release(main, after_out)
    assert p.returncode == 0, p.stderr
    after = steps_of(after_out)
    assert list(after) == list(before) and verify_ref(after) == head


@pytest.mark.parametrize(
    ("wt", "extra", "want"),
    [
        ({"version": 1, "base_branch": "main", "production_branch": "release"}, (), "release"),
        ({"version": 1, "base_branch": "main"}, ("--production-branch", "stable"), "stable"),
        ({"version": 1, "base_branch": "main", "production_branch": "release"}, ("--production-branch", "stable"), "stable"),
    ],
)
def test_argument_and_declaration_win_over_the_default_branch(tmp_path, wt, extra, want):
    """受け入れ条件 6・I1: 引数 → 宣言 → 既定ブランチの順。宣言が既定ブランチと違っても宣言の値を使う。"""
    main = make_repo(tmp_path, wt=wt)
    out = tmp_path / "rel.json"
    p = release(main, out, "prod", *extra)
    assert p.returncode == 0, p.stderr
    assert verify_ref(steps_of(out)) == want


def new_args(main: Path, kind: str = "sprint", **kw) -> argparse.Namespace:
    wt = main / ".worktrees" / "feat" / "m"
    base = dict(kind=kind, worktree=str(wt), repo=None, production_branch=None, release=None, glossary_ready=False)
    return argparse.Namespace(**{**base, **kw})


def test_template_and_route_decision_use_the_same_production_branch(tmp_path, monkeypatch):
    """受け入れ条件 7・I3: 同じ起動の中で、雛形の引数とリリースの判定の宣言の本番チャネルが同じ値になる。"""
    main = make_repo(tmp_path, "trunk")
    monkeypatch.chdir(main)
    a = new_args(main)
    sdecl.apply_decls(a)
    assert a.production_branch == "trunk"
    a.production_branch = None  # 引数を外しても、デプロイの判定は同じ木から同じ既定ブランチを解く
    assert sdecl.delivery_decl(a).production == "trunk"


def test_declared_production_branch_does_not_ask_git_for_the_default_branch(tmp_path, monkeypatch):
    """I4: production_branch を宣言したリポジトリでは、既定ブランチも木の選択も問い合わせない。"""
    main = make_repo(tmp_path, wt={"version": 1, "base_branch": "main", "production_branch": "release"})
    monkeypatch.chdir(main)

    def boom(*_a, **_k):
        raise AssertionError("既定ブランチを問い合わせた")

    monkeypatch.setattr(repo_lib, "default_branch", boom)
    monkeypatch.setattr(repo_lib, "first_git_tree", boom)
    a = new_args(main)
    sdecl.apply_decls(a)
    assert a.production_branch == "release"


def test_first_git_tree_skips_missing_and_non_git_paths(tmp_path):
    """I2: 存在しないパスと git でないディレクトリを飛ばし、最初に存在する git の木を返す。無ければ None。"""
    main = make_repo(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert repo_lib.first_git_tree([main / ".worktrees" / "x", plain, main]) == main
    assert repo_lib.first_git_tree([tmp_path / "none", plain]) is None
    assert repo_lib.production_branch(None, {})[0] is None
