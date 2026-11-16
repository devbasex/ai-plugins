"""#3308: スプリントに属さないリリース（まとめたリリース・`new release`）は、取り込み方とリリースのレビューの単位を
`main` 宛の検査と同じ本番ブランチの宣言（`origin/<本番ブランチ>:.ndf/project.json` の `branch_strategy`）から読む。

- ベースブランチだけが `release_branch` なら、本番ブランチの `merge_base_branch` で組む（受け入れ条件 1・2・I1）。両方が
  `release_branch` なら単位を選ぶ取り込み方のまま（受け入れ条件 3）
- 本番ブランチの宣言を読めない・旧いキーが残る・本番ブランチが無いときは、既定値へ倒さずに止まる（#3672 の I3・I4）
- 取り込み直しに失敗しても手元の `origin/<本番ブランチ>` を読む（可用性）。ブランチの名前は今の宣言から解く（I5）
- スプリントの開始時の記録を持つプランは本番ブランチの宣言を読まない（受け入れ条件 7・I4）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import strategy_fixture  # noqa: E402
from git_fixture import git, main_and_develop  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import branch_strategy  # noqa: E402
import branch_strategy_legacy as legacy  # noqa: E402
import parallel_release  # noqa: E402
import repo as repo_lib  # noqa: E402
from supervise_lib import decl as sdecl  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
# 単位を選ぶ取り込み方（release_branch）は mobile の種類、ベースブランチを丸ごと出す取り込み方（merge_base_branch）は
# web-develop の種類の写し。リリースの形はどちらも package-plugin（雛形でプランを組む）
SELECTED, WHOLE = "mobile", "web-develop"
RELEASE = {**strategy_fixture.section("plugin")["release"], "plugin": "ndf", "runtimes": ["claude"]}
NO_FILE = object()
# 旧い宣言のキー（project.json に残ると止まる）。名前は移す行の表（branch_strategy_legacy）から引く
LEGACY_KEY = next(m.old for m in legacy.load_moves() if m.file == "project" and m.old.count("/") == 1).lstrip("/")
# 実装の差分がどこかで cross-review される組（#3163 の I4）。release の行だけを本番ブランチの宣言の値として確かめる
REVIEWS = {
    "impl": {"checks": ["cross-review"], "diff": "pr"},
    "sprint": {"checks": ["behavior", "cross-refactoring"], "diff": "unchecked"},
    "release": {"checks": ["cross-review"], "diff": "resolution"},
}

# new release は --design に無い課題の本文を gh で読む。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def write_decl(tree: Path, value=WHOLE, **edits) -> None:
    """`value` が種類の名前ならその宣言を、文字列でなければ `NO_FILE`（宣言のファイルを消す）、それ以外の文字列はそのまま書く。"""
    path = tree / ".ndf" / "project.json"
    path.unlink(missing_ok=True)
    if value is NO_FILE:
        return
    if value in strategy_fixture.KINDS:
        strategy_fixture.write_strategy(
            tree, value, base=edits.pop("base", "develop"), production=edits.pop("production", "main"), release=RELEASE, **edits
        )
    else:
        path.write_text(value)


def make_repo(tmp_path: Path, main_decl=WHOLE, develop_decl=SELECTED, with_main: bool = True, **main_edits) -> Path:
    """bare の origin に `main`（`main_decl`）と `develop`（`develop_decl`）を置き、`develop` のメインディレクトリを返す。"""
    seed = tmp_path / "seed"

    def write_main(tree: Path) -> None:
        (tree / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
        (tree / "plugins" / "ndf" / ".claude-plugin").mkdir(parents=True)
        (tree / "plugins" / "ndf" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "1.0.0"}))
        write_decl(tree, main_decl, **main_edits)

    main_and_develop(seed, write_main, lambda tree: write_decl(tree, develop_decl))
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
    assert "--release-strategy release_branch" not in text and "release-branch" not in text


def snapshot(main: Path) -> dict:
    """種別リリースのプランが受け取るスナップショット（`apply_decls` と同じ読み先）。"""
    return sdecl._strategy_snapshot(SimpleNamespace(kind="release", production=None, flow=None), [main])


def test_only_the_base_branch_selected_builds_the_whole_release(tmp_path):
    """受け入れ条件 1・2・I1: ベースブランチだけが release_branch なら、まとめたリリースと new release は本番ブランチの
    merge_base_branch で組む。"""
    main = make_repo(tmp_path)
    present, release = parallel_plans(tmp_path, main)
    assert_whole(present)
    assert_whole(release)
    for channel in ("prod", "dev"):
        p, out = new_release(tmp_path, main, channel)
        assert p.returncode == 0, p.stdout + p.stderr
        assert_whole(json.loads(out.read_text()))
    snap = snapshot(main)
    assert snap["section"]["release_strategy"] == "merge_base_branch" and snap["source"].startswith("origin/main")


def test_both_selected_keeps_the_selected_flow(tmp_path):
    """受け入れ条件 3: 両方が release_branch なら単位を選ぶ取り込み方のまま。単位を持たないプランは単位が無いと止まる。"""
    main = make_repo(tmp_path, SELECTED)
    with pytest.raises(sdecl.DeclError, match="単位"):
        parallel_plans(tmp_path, main)
    p, _ = new_release(tmp_path, main)
    assert p.returncode == 2 and "単位" in p.stderr
    assert snapshot(main)["section"]["release_strategy"] == "release_branch"


@pytest.mark.parametrize(
    "main_decl, why",
    [
        (NO_FILE, "セクション `branch_strategy` がありません"),
        ("{not json", "JSON として読めない"),
        ("[1]", "セクション `branch_strategy` がありません"),
        (json.dumps({"version": 1, LEGACY_KEY: []}), "旧い宣言のキー"),
    ],
    ids=["no-file", "not-json", "array", "legacy-key"],
)
def test_an_unreadable_production_declaration_stops(tmp_path, main_decl, why):
    """#3672 の I3・I4: 本番ブランチの宣言を読めなければ、既定値へ倒さずに読めない理由を出して止まる。"""
    main = make_repo(tmp_path, main_decl)
    with pytest.raises(sdecl.DeclError, match="本番ブランチの宣言を読めない") as e:
        parallel_plans(tmp_path, main)
    assert why in str(e.value) and "origin/main" in str(e.value)
    p, out = new_release(tmp_path, main)
    assert p.returncode == 2 and why in p.stderr
    assert not out.exists()


def test_without_production_on_origin_the_release_stops(tmp_path):
    """#3672 の I3: origin に本番ブランチが無ければ、宣言を読めない理由を返す（呼び手が止まる）。"""
    main = make_repo(tmp_path, with_main=False)
    got, why = repo_lib.production_strategy(main, "main")
    assert got is None and "origin/main" in why
    with pytest.raises(sdecl.DeclError, match="本番ブランチの宣言を読めない"):
        snapshot(main)


def test_an_unreachable_origin_reads_the_local_tracking_branch(tmp_path):
    """可用性: origin へ取り込めなくても、手元の origin/main の宣言で組む。"""
    main = make_repo(tmp_path, SELECTED)
    git(main, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    got, _ = repo_lib.production_strategy(main, "main")
    assert got is not None and got.release_strategy == "release_branch"
    assert snapshot(main)["section"]["release_strategy"] == "release_branch"


def test_the_fetch_moves_the_tracking_branch_to_the_origin_tip(tmp_path):
    """前提 4: 組む前に origin から本番ブランチを取り込み直す（手元の origin/main が古くても origin の先頭を読む）。"""
    main = make_repo(tmp_path)
    seed = tmp_path / "seed"
    git(seed, "checkout", "-q", "main")
    write_decl(seed, SELECTED)
    git(seed, "commit", "-qam", "switch")
    git(seed, "push", "-q", str(tmp_path / "origin.git"), "main")
    assert snapshot(main)["section"]["release_strategy"] == "release_branch"


def test_release_reviews_come_from_the_production_declaration(tmp_path):
    """決定 2: リリースのレビューの単位も本番ブランチの宣言から読み、ベースブランチの宣言と混ぜない。"""
    main = make_repo(tmp_path, SELECTED, WHOLE, reviews=REVIEWS)
    snap = snapshot(main)
    assert snap["section"]["release_strategy"] == "release_branch"
    assert snap["section"]["reviews"]["release"] == REVIEWS["release"]


def test_branch_names_come_from_the_current_declaration(tmp_path):
    """I5: 本番ブランチの宣言に別のブランチ名を書いても、名前はメインディレクトリの宣言の値。"""
    main = make_repo(tmp_path, WHOLE, base="trunk-x", production="prod-x")
    assert snapshot(main)["section"]["branches"] == {"production": "main", "base": "develop"}
    p, out = new_release(tmp_path, main)
    assert p.returncode == 0, p.stdout + p.stderr
    text = out.read_text()
    assert "trunk-x" not in text and "prod-x" not in text and "origin/develop" in text


def test_a_sprint_plan_reads_its_start_record_not_the_production_declaration(tmp_path):
    """受け入れ条件 7・I4: スプリントの打ち直しは開始時の記録で組み、壊れた本番ブランチの宣言を読まない。"""
    main = make_repo(tmp_path, "{not json")
    args = ["new", "sprint", "--name", "s1", "--worktree", str(main), "--issue", "11", "--version", "1.1.0-dev.1"]
    p = inproc.run(SUPERVISE, *args, "--out", str(tmp_path / "s1"), cwd=main)
    assert p.returncode == 0, p.stdout + p.stderr
    write_decl(main, WHOLE)  # 開始の後にベースブランチの宣言の取り込み方を変える
    p = inproc.run(SUPERVISE, *args, "--out", str(tmp_path / "s1"), cwd=main)
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = json.loads((tmp_path / "s1" / "sprint.json").read_text(encoding="utf-8"))
    assert manifest[branch_strategy.SNAPSHOT_KEY]["section"]["release_strategy"] == "release_branch"
