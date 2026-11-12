"""lib/branch_flow.py（#3121）: 宣言の読み分け・取り込み方の表・組み直しの判定・リリースブランチの組み方。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import branch_flow as bf  # noqa: E402

PLUGIN = SCRIPTS.parent
SELECTED = {"start": "production", "integrate": "check", "doc_prs": "sprint", "promotion": "selected"}


def test_missing_declaration_reads_as_the_default_row():
    """I2: キーが無ければ既定値の行（ベースブランチから切り、丸ごと出す）。"""
    flow = bf.of_decl({"base_branch": "develop"})
    assert flow.as_dict() == bf.DEFAULT and not flow.declared
    r = flow.resolve("develop", "main")
    assert (r.start_branch, r.doc_target("sprint/x"), r.check_target()) == ("develop", "develop", "develop")
    assert not r.row.keeps_sprint_branch and not r.row.selects_units


def test_selected_row_resolves_names_from_the_channels():
    r = bf.parse_flow(SELECTED).resolve("develop", "main")
    assert (r.start_branch, r.doc_target("sprint/x"), r.check_target()) == ("main", "sprint/x", "develop")
    snap = r.as_snapshot("宣言")
    assert bf.from_snapshot(snap, "x", "y").as_snapshot("宣言") == snap


@pytest.mark.parametrize(
    "value, keys",
    [
        ({"promotion": "selected"}, {"start", "doc_prs"}),
        ({"start": "production"}, {"start"}),
        ({"start": 1}, {"start"}),
        ({"extra": "x"}, {"extra"}),
        (["whole"], {"(全体)"}),
    ],
)
def test_broken_declarations_name_the_keys_to_fix(value, keys):
    """I1: 形の誤り・表に合わない組は FlowError で、直すキーを持つ（既定値へ倒さない）。"""
    with pytest.raises(bf.FlowError) as e:
        bf.parse_flow(value)
    assert {i["key"] for i in e.value.items} == keys


def test_snapshot_without_a_flow_reads_as_a_repository_without_a_declaration():
    """I3: スナップショットの無い（変更前の）目録は既定値の行。"""
    r = bf.from_snapshot(None, "develop", "main")
    assert r.flow.as_dict() == bf.DEFAULT and r.start_branch == "develop"


def test_schema_branch_flow_is_made_from_the_table():
    """スキーマの enum は表から作る（表を足してスキーマを直し忘れると落ちる）。"""
    schema = json.loads((PLUGIN / "skills/worktree/schemas/worktree.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["branch_flow"] == bf.schema_fragment()


def test_declaration_doc_example_passes_the_table_and_the_schema():
    """受け入れ条件 8: 手順書の branch_flow の例は表の検査とスキーマの enum に通る。"""
    text = (PLUGIN / "skills/worktree/references/declaration.md").read_text(encoding="utf-8")
    examples = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", text, re.S) if '"branch_flow"' in b]
    assert examples
    props = bf.schema_fragment()["properties"]
    for ex in examples:
        flow = bf.of_decl(ex)
        assert flow.declared
        assert all(v in props[k]["enum"] for k, v in ex["branch_flow"].items())


def test_whole_row_rebuilds_only_when_the_base_branch_moved():
    """受け入れ条件 3 と同じ判定: 丸ごと出す行はベースブランチの先頭だけを比べる。"""
    row = bf.PROMOTIONS["whole"]
    pres = {"base": "a", "production": "p", "units": {}}
    assert row.moved(pres, {"base": "a", "production": "q"}) == []
    assert row.moved(pres, {"base": "b"}) == ["base"]


def test_selected_row_ignores_the_base_branch_and_watches_units_and_production():
    """受け入れ条件 6・I7: ベースブランチだけが動いても組み直さない。選んだ単位か本番チャネルが動けば組み直す。"""
    row = bf.PROMOTIONS["selected"]
    pres = {"base": "a", "production": "p", "units": {"sprint/x": "1"}}
    assert row.moved(pres, {"base": "b", "production": "p", "units": {"sprint/x": "1"}}) == []
    assert row.moved(pres, {"base": "b", "production": "p", "units": {"sprint/x": "2"}}) == ["sprint/x"]
    assert row.moved(pres, {"production": "q", "units": {"sprint/x": "1"}}) == ["production"]


def test_selected_range_starts_at_the_previous_tag():
    """受け入れ条件 6: 選ぶ行の承認資料の範囲は前の本番のタグ..リリースブランチの先頭。"""
    assert bf.PROMOTIONS["selected"].release_range("origin/main", "v1.0.0", "abc") == ("v1.0.0", "abc")
    assert bf.PROMOTIONS["whole"].release_range("origin/main", "v1.0.0", "abc") == ("origin/main", "abc")


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def commit(repo: Path, path: str, text: str, msg: str) -> str:
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def history(tmp_path):
    """リリース 9 の形: main から切った 2 つのスプリント・develop にだけ入った別のスプリントの PR と開発版の版上げ。"""
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    # 組む側（lib）は利用者の設定でコミットするため、一時リポジトリに持たせる
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(repo, "config", k, v)
    commit(repo, "base.txt", "0\n", "init")
    git(repo, "branch", "develop")
    for name in ("a", "b"):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, f"{name}.txt", f"{name}\n", f"sprint {name}")
    git(repo, "checkout", "-q", "develop")
    for name in ("a", "b"):
        git(repo, "merge", "-q", "--no-ff", "--no-edit", f"sprint/{name}")
    other = commit(repo, "other.txt", "x\n", "PR #3078 aixrepo-1")
    bump = commit(repo, "version.txt", "1.1.0-dev.1\n", "chore: 1.1.0-dev.1")
    return repo, other, bump


def test_release_branch_holds_only_the_selected_units(history):
    """受け入れ条件 5・16・17・I5: 本番チャネルから切り、選んだ単位だけを集める。選ばない単位・develop にだけある PR・
    開発版の版上げはリリースブランチの祖先に入らない。"""
    repo, other, bump = history
    row = bf.PROMOTIONS["selected"]
    head = row.build_release_branch(repo, "release/v1.1.0", "main", ["sprint/a"])
    anc = lambda c: subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", c, head]).returncode == 0  # noqa: E731
    assert anc("sprint/a") and anc("main")
    assert not anc("sprint/b") and not anc(other) and not anc(bump)
    # 本番チャネルの先頭と、選んだ単位のマージだけからなる
    assert git(repo, "rev-list", "--first-parent", "--count", f"main..{head}") == "1"


def test_release_branch_stops_on_conflicts(history):
    """E10: 選んだ単位どうしが衝突すれば取り消して止める。"""
    repo, _, _ = history
    for name in ("c", "d"):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, "same.txt", f"{name}\n", f"sprint {name}")
    with pytest.raises(bf.ReleaseBuildError):
        bf.PROMOTIONS["selected"].build_release_branch(repo, "release/v2", "main", ["sprint/c", "sprint/d"])
    assert git(repo, "status", "--porcelain") == ""


def test_release_branch_reuses_rerere_resolution(history):
    """E10: 人が一度解いた衝突は、rerere の記録で解けたらマージを完了して次の単位へ進む。"""
    repo, _, _ = history
    for name in ("c", "d"):
        git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        commit(repo, "same.txt", f"{name}\n", f"sprint {name}")
    # 人が解いた記録を rerere に残す
    git(repo, "config", "rerere.enabled", "true")
    git(repo, "checkout", "-q", "--detach", "sprint/c")
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "--no-edit", "sprint/d"], capture_output=True)
    (repo / "same.txt").write_text("c\nd\n")
    git(repo, "add", "same.txt")
    git(repo, "commit", "-q", "--no-edit")
    head = bf.PROMOTIONS["selected"].build_release_branch(repo, "release/v2", "main", ["sprint/c", "sprint/d"])
    assert git(repo, "show", f"{head}:same.txt") == "c\nd"
    assert git(repo, "log", "-1", "--format=%s", head) == "Merge sprint/d into release/v2"


def test_release_branch_builds_while_branch_is_checked_out_elsewhere(history, tmp_path):
    """本番プランの worktree が同じリリースブランチを使っていても、detached HEAD で組み直せる。"""
    repo, _, _ = history
    git(repo, "branch", "release/v3", "main")
    git(repo, "worktree", "add", "-q", str(tmp_path / "prod"), "release/v3")
    before = git(repo, "rev-parse", "release/v3")
    head = bf.PROMOTIONS["selected"].build_release_branch(repo, "release/v3", "main", ["sprint/a"])
    assert head != before
    assert git(repo, "rev-parse", "release/v3") == before


def test_whole_row_does_not_build_release_branches(history):
    with pytest.raises(ValueError):
        bf.PROMOTIONS["whole"].build_release_branch(history[0], "release/v1", "main", ["sprint/a"])


def test_selected_keeps_sprint_branches_until_production_holds_them(history):
    """決定 7: 本番チャネルに含まれないスプリントブランチは残し、含まれたものは消してよい。丸ごと出す行は残さない。"""
    repo, _, _ = history
    sel, whole = bf.PROMOTIONS["selected"], bf.PROMOTIONS["whole"]
    assert bf.sprint_branch_kept(repo, "sprint/a", "main", sel)
    assert not bf.sprint_branch_kept(repo, "sprint/a", "main", whole)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "sprint/a")
    assert not bf.sprint_branch_kept(repo, "sprint/a", "main", sel)
