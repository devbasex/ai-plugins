"""lib/decl.py と、設定の 1 か所化・読む場所（#2336 の決定 7・8・9）。

仮のリポジトリと worktree を作り、`.ndf/` の設定をどちらの木から読むか、本番のブランチを 1 か所で決めるか、
古い置き場だけの宣言を移行の読みで受けるかを確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import decl  # noqa: E402
import delivery  # noqa: E402
import project_decl  # noqa: E402
import repo  # noqa: E402
import runtime_policy  # noqa: E402

GIT = ["git", "-c", "user.email=a@b", "-c", "user.name=a", "-c", "init.defaultBranch=main", "-c", "protocol.file.allow=always"]


def git(cwd: Path, *a: str) -> str:
    return subprocess.run([*GIT, "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()


def wj(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))


@pytest.fixture
def repo_wt(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q")
    git(main, "commit", "-q", "--allow-empty", "-m", "init")
    wt = tmp_path / "wt"
    git(main, "worktree", "add", "-q", str(wt), "-b", "feat")
    return main, wt


def test_every_setting_has_a_place():
    p = decl.places()
    assert p["project.json"] == "worktree" and p["worktree.local.json"] == "main"


def test_team_setting_is_read_from_the_called_tree_first(repo_wt):
    main, wt = repo_wt
    wj(main / ".ndf/project.json", {"test": "main"})
    assert decl.setting_path(wt, "project.json") == main / ".ndf/project.json"
    wj(wt / ".ndf/project.json", {"test": "wt"})
    assert decl.setting_path(wt, "project.json") == wt / ".ndf/project.json"
    # #2317・#1709: Pull Request の worktree が変えた宣言を使う
    assert project_decl.read_project_decl(wt) == {"test": "wt"}


def test_team_setting_found_in_the_called_tree_does_not_resolve_the_main_directory(repo_wt, monkeypatch):
    main, wt = repo_wt
    wj(wt / ".ndf/project.json", {"test": "wt"})
    calls = []
    monkeypatch.setattr(decl, "main_dir", lambda root: calls.append(root) or main)
    assert decl.setting_path(wt, "project.json") == wt / ".ndf/project.json"
    assert calls == []


def test_personal_setting_is_read_from_the_main_directory_first(repo_wt):
    main, wt = repo_wt
    wj(wt / ".ndf/worktree.local.json", {"v": "wt"})
    assert decl.setting_path(wt, "worktree.local.json") == wt / ".ndf/worktree.local.json"
    wj(main / ".ndf/worktree.local.json", {"v": "main"})
    assert decl.setting_path(wt, "worktree.local.json") == main / ".ndf/worktree.local.json"


def test_unknown_setting_raises():
    with pytest.raises(KeyError):
        decl.setting_path(".", "nope.json")


def test_main_dir_of_a_submodule_worktree_is_the_submodule(tmp_path: Path):
    """#2054: サブモジュールでは --git-common-dir の親が .git/modules を指す。core.worktree を先に見る。"""
    sub_src = tmp_path / "subsrc"
    sub_src.mkdir()
    git(sub_src, "init", "-q")
    git(sub_src, "commit", "-q", "--allow-empty", "-m", "s")
    top = tmp_path / "top"
    top.mkdir()
    git(top, "init", "-q")
    git(top, "submodule", "add", "-q", str(sub_src), "sub")
    sub = top / "sub"
    assert decl.main_dir(sub).resolve() == sub.resolve()
    wt = tmp_path / "subwt"
    git(sub, "worktree", "add", "-q", str(wt), "-b", "x")
    wj(sub / ".ndf/project.json", {"from": "sub"})
    assert decl.main_dir(wt).resolve() == sub.resolve()
    assert project_decl.read_project_decl(wt) == {"from": "sub"}


def test_production_branch_has_one_resolver():
    """#2097: 宣言に production_branch が無ければ、どの読み手も既定ブランチを使う。"""
    assert repo.production_of({"production_branch": "release"}, "main")[0] == "release"
    assert repo.production_of({}, "main")[0] == "main"
    d = delivery.build({"base_branch": "develop"}, {}, default_branch="main")
    assert d.production == "main"


def _rows(rows: list[dict], wt: dict | None = None):
    return delivery.build(wt or {"base_branch": "develop", "production_branch": "main"}, {"delivery": rows}, default_branch="main")


ROW = {"target": "本番", "kind": "auto", "trigger": "main へのマージ", "versioned": True}


def test_channel_row_resolves_to_the_production_branch():
    d = _rows([dict(ROW, channel="production")])
    assert d.rows[0]["branch"] == "main"
    assert delivery.reaches_by_merge(d.rows[0], d.production)
    d = _rows([dict(ROW, channel="base")])
    assert d.rows[0]["branch"] == "develop"


def test_channel_wins_over_branch_and_branch_only_rows_still_read():
    d = _rows([dict(ROW, channel="production", branch="old"), dict(ROW, branch="main")])
    assert [r["branch"] for r in d.rows] == ["main", "main"]
    assert [r.route for r in delivery.routes(d)] == [r.route for r in delivery.routes(_rows([dict(ROW, branch="main")] * 2))]


def test_null_channel_reads_the_branch():
    d = _rows([dict(ROW, channel=None, branch="main")])
    assert not d.problems and d.rows[0]["branch"] == "main"


def test_bad_channel_is_a_problem():
    d = _rows([dict(ROW, channel="staging")])
    assert d.rows is None and any("channel" in p for p in d.problems)


def test_old_supervise_test_command_is_read_only_without_project_suites(repo_wt):
    """決定 8: supervise.json の test.command は project.json に test が無いときだけ読む（移行の読み）。"""
    sys.path.append(str(SCRIPTS))
    import supervise_lib.decl as sdecl  # noqa: PLC0415

    main, wt = repo_wt
    wj(wt / ".ndf/supervise.json", {"version": 1, "test": {"command": "make test"}})
    got, note = sdecl._project_decl([wt], sdecl.read_decl([wt], sdecl.SUPERVISE_DECL))
    assert "make test" in json.dumps(got)
    wj(wt / ".ndf/project.json", {"test": {"suites": [{"name": "p", "runner": "pytest", "command": "pytest -q"}]}})
    got, note = sdecl._project_decl([wt], sdecl.read_decl([wt], sdecl.SUPERVISE_DECL))
    assert "make test" not in json.dumps(got)


# 認可に関わる値（本番マージの判定・本番のブランチ・使ってよいランタイム）はメインディレクトリから読む（#2363）。
# Pull Request の変更だけで承認ゲート 2 やランタイムの制限を緩められないようにする。

PROD_ROW = dict(ROW, channel="production")


def test_authorization_values_are_declared_main_in_the_contract():
    assert decl.places()["runtimes.json"] == "main"
    assert decl.key_places("project.json")["delivery"] == "main"
    assert decl.key_places("worktree.json")["production_branch"] == "main"
    assert "test" not in decl.key_places("project.json") and "base_branch" not in decl.key_places("worktree.json")


def test_pr_head_delivery_does_not_loosen_the_merge_gate(repo_wt):
    main, wt = repo_wt
    wj(main / ".ndf/worktree.json", {"base_branch": "main", "production_branch": "main"})
    wj(main / ".ndf/project.json", {"test": "main", "delivery": [PROD_ROW]})
    wj(wt / ".ndf/project.json", {"test": "wt", "delivery": []})
    assert delivery.judge_target(delivery.load_delivery(wt), "main").value == delivery.PRODUCTION
    # 認可に関わらない値は今のまま Pull Request の head を先に読む（#2317・#1709）
    assert project_decl.read_project_decl(wt)["test"] == "wt"


def test_pr_head_cannot_drop_delivery_absent_in_the_main_directory(repo_wt):
    """メインディレクトリの宣言に delivery が無ければ、head が足した delivery でも判定を緩めない（undetermined）。"""
    main, wt = repo_wt
    wj(main / ".ndf/project.json", {"test": "main"})
    wj(wt / ".ndf/project.json", {"test": "wt", "delivery": []})
    assert "delivery" not in project_decl.read_project_decl(wt)
    assert delivery.judge_target(delivery.load_delivery(wt), "main").value == delivery.UNDETERMINED


def test_pr_head_cannot_move_the_production_branch(repo_wt):
    main, wt = repo_wt
    wj(main / ".ndf/worktree.json", {"base_branch": "main", "production_branch": "main"})
    wj(main / ".ndf/project.json", {"delivery": [PROD_ROW]})
    wj(wt / ".ndf/worktree.json", {"base_branch": "feat", "production_branch": "release"})
    d = delivery.load_delivery(wt)
    assert d.production == "main" and d.base == "feat"
    assert repo.production_branch(wt)[0] == "main"
    assert delivery.judge_target(d, "main").value == delivery.PRODUCTION


def test_pr_head_cannot_add_authorization_values_absent_in_the_main_directory(repo_wt):
    """メインディレクトリにファイルが無いときも、head が足したファイルの認可の値で判定を緩めない（#2363 round 5）。"""
    main, wt = repo_wt
    wj(main / ".ndf/project.json", {"delivery": [PROD_ROW]})
    wj(wt / ".ndf/project.json", {"delivery": [PROD_ROW]})
    wj(wt / ".ndf/worktree.json", {"base_branch": "feat", "production_branch": "x"})
    d = delivery.load_delivery(wt)
    assert d.production == "main" and d.base == "feat"
    assert delivery.judge_target(d, "main").value == delivery.PRODUCTION
    (main / ".ndf/project.json").unlink()
    assert delivery.judge_target(delivery.load_delivery(wt), "main").value == delivery.UNDETERMINED


def test_authorization_values_are_read_in_the_main_directory_itself(repo_wt):
    main, _ = repo_wt
    wj(main / ".ndf/worktree.json", {"production_branch": "release"})
    wj(main / ".ndf/project.json", {"delivery": [PROD_ROW]})
    d = delivery.load_delivery(main)
    assert d.production == "release" and d.rows and d.rows[0]["branch"] == "release"


def test_main_dir_is_resolved_once_per_root(repo_wt, monkeypatch):
    main, wt = repo_wt
    wj(main / ".ndf/worktree.json", {"production_branch": "main"})
    wj(main / ".ndf/project.json", {"delivery": [PROD_ROW]})
    decl._MAIN_DIRS.clear()
    calls = []
    real = decl.proc.git_out
    monkeypatch.setattr(decl.proc, "git_out", lambda root, *a: calls.append(a) or real(root, *a))
    delivery.load_delivery(wt)
    assert calls.count(("rev-parse", "--path-format=absolute", "--git-common-dir")) == 1


def test_broken_main_declaration_is_a_problem_not_a_fallback(repo_wt):
    main, wt = repo_wt
    (main / ".ndf").mkdir()
    (main / ".ndf/project.json").write_text("{")
    wj(wt / ".ndf/project.json", {"delivery": [PROD_ROW]})
    assert delivery.judge_target(delivery.load_delivery(wt), "main").value == delivery.UNDETERMINED


def test_pr_head_runtimes_does_not_widen_the_allowed_runtimes(repo_wt):
    main, wt = repo_wt
    wj(main / ".ndf/runtimes.json", {"allowed": ["claude"]})
    wj(wt / ".ndf/runtimes.json", {"allowed": ["claude", "codex"]})
    assert runtime_policy.read_policy(wt).allowed == ("claude",)
    with pytest.raises(runtime_policy.RuntimePolicyError):
        runtime_policy.check_runtime("codex", wt)


def test_supervise_plan_reads_the_production_branch_from_the_main_directory(repo_wt):
    """雛形の引数（supervise_lib/decl.read_decl）も、head の production_branch で本番チャネルを動かせない。"""
    sys.path.append(str(SCRIPTS))
    import supervise_lib.decl as sdecl  # noqa: PLC0415

    main, wt = repo_wt
    wj(main / ".ndf/worktree.json", {"base_branch": "main", "production_branch": "main"})
    wj(wt / ".ndf/worktree.json", {"base_branch": "feat", "production_branch": "release"})
    assert sdecl.read_decl([wt, main], sdecl.WORKTREE_DECL) == {"base_branch": "feat", "production_branch": "main"}
