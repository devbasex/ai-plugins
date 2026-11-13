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
        assert all(v in props[k]["enum"] for k, v in ex["branch_flow"].items() if k != bf.REVIEWS_KEY)
        _matches_reviews_schema(ex["branch_flow"].get(bf.REVIEWS_KEY, {}), props[bf.REVIEWS_KEY])
    assert any(bf.REVIEWS_KEY in ex["branch_flow"] for ex in examples)


def _matches_reviews_schema(value: dict, schema: dict) -> None:
    """`reviews` の値がスキーマの断片（種類・キー・enum・重複）に合う。"""
    assert set(value) <= set(schema["properties"])
    for kind, row in value.items():
        sub = schema["properties"][kind]
        assert set(row) == set(sub["required"])
        assert all(c in sub["properties"]["checks"]["items"]["enum"] for c in row["checks"])
        assert len(set(row["checks"])) == len(row["checks"])
        assert row["diff"] in sub["properties"]["diff"]["enum"]


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


# --- レビューの単位（#3163） ---------------------------------------------------------------

ALL_REVIEWS = {
    "impl": {"checks": ["cross-review"], "diff": "pr"},
    "sprint": {"checks": ["behavior", "cross-refactoring"], "diff": "unchecked"},
    "release": {"checks": ["cross-review"], "diff": "resolution"},
    "design": {"checks": [], "diff": "none"},
    "spec": {"checks": ["cross-review"], "diff": "pr"},
}


def _flow(reviews, **kw):
    return bf.parse_flow({**SELECTED, **kw, "reviews": reviews})


def test_written_rows_are_returned_regardless_of_bundles_and_survive_the_snapshot():
    """受け入れ条件 1: 5 種類に書いた値を読み分けが返し、スナップショットへ出して読み直しても同じ。"""
    flow = _flow(ALL_REVIEWS)
    for kind, v in ALL_REVIEWS.items():
        for bundled in (True, False):
            row = flow.reviews.row(kind, bundled)
            assert set(row.checks) == set(v["checks"]) and row.diff == v["diff"]
    assert flow.reviews.row("sprint", True).checks == ("cross-refactoring", "behavior")  # CHECKS の順
    snap = flow.resolve("develop", "main").as_snapshot("宣言")
    assert bf.from_snapshot(snap, "x", "y").flow.reviews == flow.reviews


def test_unwritten_kinds_resolve_from_the_default_table():
    """I5: reviews の無い宣言は既定値の表（ndf v10.27.61 の決め打ち）の 10 セル。"""
    units = bf.parse_flow({}).reviews
    got = {(k, b): (units.row(k, b).checks, units.row(k, b).diff) for k in bf.REVIEW_KINDS for b in (True, False)}
    assert got == {
        ("impl", True): (("cross-review",), "pr"),
        ("impl", False): ((), "none"),
        ("sprint", True): (("cross-refactoring", "behavior"), "unchecked"),
        ("sprint", False): (bf.CHECKS, "unchecked"),
        ("release", True): ((), "none"),
        ("release", False): ((), "none"),
        ("design", True): (("cross-review",), "pr"),
        ("design", False): (("cross-review",), "pr"),
        ("spec", True): ((), "none"),
        ("spec", False): ((), "none"),
    }


def test_snapshot_always_carries_reviews_and_old_snapshots_read_as_empty():
    """I6: スナップショットは宣言が無くても reviews: {} を持つ。reviews の無い目録は {} として読む。"""
    snap = bf.BranchFlow().resolve("develop", "main").as_snapshot("既定値")
    assert snap[bf.REVIEWS_KEY] == {}
    old = {k: v for k, v in snap.items() if k != bf.REVIEWS_KEY}
    assert bf.from_snapshot(old, "develop", "main").flow.reviews == bf.ReviewUnits()


def test_covers_impl_counts_only_the_covering_diffs():
    assert bf.ReviewRow("impl", ("cross-review",), "pr").covers_impl()
    assert not bf.ReviewRow("release", ("cross-review",), "resolution").covers_impl()
    assert not bf.ReviewRow("design", ("cross-review",), "pr").covers_impl()
    assert not bf.ReviewRow("impl", (), "none").runs("cross-review")


@pytest.mark.parametrize(
    "reviews, kw, keys",
    [
        ({"x": {"checks": [], "diff": "none"}}, {}, {"reviews.x"}),
        ({"impl": {"checks": [], "diff": "none", "extra": 1}}, {}, {"reviews.impl.extra"}),
        ({"impl": {"checks": ["nope"], "diff": "pr"}}, {}, {"reviews.impl.checks"}),
        ({"impl": {"checks": ["cross-review", "cross-review"], "diff": "pr"}}, {}, {"reviews.impl.checks"}),
        ({"impl": {"checks": ["cross-review"]}}, {}, {"reviews.impl.diff"}),
        (["impl"], {}, {"reviews"}),
        ({"impl": {"checks": ["cross-review"], "diff": "resolution"}}, {}, {"reviews.impl.diff"}),
        ({"sprint": {"checks": ["cross-review"], "diff": "pr"}}, {}, {"reviews.sprint.diff"}),
        ({"design": {"checks": ["cross-refactoring"], "diff": "pr"}}, {}, {"reviews.design.checks"}),
        ({"spec": {"checks": ["cross-review"], "diff": "none"}}, {}, {"reviews.spec.checks"}),
        ({"spec": {"checks": [], "diff": "pr"}}, {}, {"reviews.spec.checks"}),
    ],
)
def test_broken_reviews_name_the_keys_to_fix(reviews, kw, keys):
    """受け入れ条件 6・7・I1・I2: 形の誤り・種類に合わない値は直すキーと許す値で止まる（既定値へ倒さない）。"""
    with pytest.raises(bf.FlowError) as e:
        _flow(reviews, **kw)
    assert {i["key"] for i in e.value.items} == keys
    assert all(i["allowed"] for i in e.value.items)


def test_release_row_needs_a_promotion_that_selects_units():
    """受け入れ条件 6・I3: promotion: whole でリリースブランチの PR の値を書くと止まり、理由に取り込み方を添える。"""
    with pytest.raises(bf.FlowError) as e:
        bf.parse_flow({"reviews": {"release": {"checks": [], "diff": "none"}}})
    assert [(i["key"], i["because"]) for i in e.value.items] == [("reviews.release", "promotion = whole")]
    assert "理由: promotion = whole" in str(e.value)


@pytest.mark.parametrize(
    "reviews",
    [
        # まとまりの記録が無いときだけ外れる（impl の既定値は記録が有るときだけ cross-review を掛ける）
        {"sprint": {"checks": ["behavior"], "diff": "unchecked"}},
        # 衝突の解消の差分は実装の差分を覆わない
        {
            "impl": {"checks": [], "diff": "none"},
            "sprint": {"checks": ["cross-refactoring"], "diff": "unchecked"},
            "release": {"checks": ["cross-review"], "diff": "resolution"},
        },
    ],
)
def test_declarations_that_leave_impl_diff_unreviewed_stop(reviews):
    """受け入れ条件 5・I4: どの PR でも cross-review されない実装の差分が出る宣言は止まる。"""
    with pytest.raises(bf.FlowError) as e:
        _flow(reviews)
    assert {i["key"] for i in e.value.items} == {"reviews"}
    assert all("cross-review されない" in i["because"] for i in e.value.items)


def test_design_review_does_not_count_as_covering_impl():
    with pytest.raises(bf.FlowError):
        _flow({**ALL_REVIEWS, "impl": {"checks": [], "diff": "none"}, "sprint": {"checks": ["behavior"], "diff": "unchecked"},
               "release": {"checks": [], "diff": "none"}, "design": {"checks": ["cross-review"], "diff": "pr"}})


def test_release_pr_diff_covers_impl():
    flow = _flow({"impl": {"checks": [], "diff": "none"}, "sprint": {"checks": ["behavior"], "diff": "unchecked"},
                  "release": {"checks": ["cross-review"], "diff": "pr"}})
    assert flow.reviews.row("release", True).covers_impl()


def test_default_overlapping_a_written_row_stops_but_written_overlaps_pass():
    """I12: impl だけに cross-review を書くと既定値の sprint と重なり止まる。両方に書けば通る。"""
    with pytest.raises(bf.FlowError) as e:
        bf.parse_flow({"reviews": {"impl": {"checks": ["cross-review"], "diff": "pr"}}})
    assert {i["key"] for i in e.value.items} == {"reviews.sprint"}
    both = {"impl": {"checks": ["cross-review"], "diff": "pr"}, "sprint": {"checks": list(bf.CHECKS), "diff": "unchecked"}}
    assert bf.parse_flow({"reviews": both}).reviews.row("impl", False).covers_impl()


def test_four_keys_and_reviews_errors_come_in_one_error():
    with pytest.raises(bf.FlowError) as e:
        bf.parse_flow({"start": 1, "reviews": {"x": {}}})
    assert {i["key"] for i in e.value.items} == {"start", "reviews.x"}
    assert len(str(e.value).splitlines()) == 3  # 1 件 1 行と、直す場所の行


def test_empty_reviews_pass_and_review_allowed_is_a_copy():
    assert bf.parse_flow({"reviews": {}}).reviews == bf.ReviewUnits()
    table = bf.review_allowed()
    assert set(table) == set(bf.REVIEW_KINDS)
    table["impl"]["diff"] = ("x",)
    assert bf.review_allowed()["impl"]["diff"] == ("pr", "none")
