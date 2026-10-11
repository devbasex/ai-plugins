"""レビューのプロファイル（`.ndf/review.json` の version 2 の `profile`）が cross-review の観点と基準に効く（#1910）。

- 自動レビュー観点: プロファイルの分類の glob に当たれば名前と観点の文を足し、`replace_auto_categories` で既定を外す
- `review_kind` は既定の分類から決める（I6）
- 新規の init は worktree を用意した後に宣言を読み、再開は読み直して違うときだけ状態ファイルを書く（I7）
- 壊れたプロファイルは既定で続け、報告に理由を出す（I4）
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

import pytest
import review_lib
import review_lib.commands.init
import review_lib.commands.report
import review_lib.github
import review_lib.review_focus as rf
import review_lib.store
import review_lib.workspace

PR = 7310
REPO = "o/r"
CHAPTER = "works/novel-a/chapters/011/text.md"
PROFILE = {
    "criteria": {"1": "設定と本文が食い違う", "5": "人物の口調が設定とずれる"},
    "not_written": ["好みの文体"],
    "categories": {"manuscript": {"paths": ["works/*/chapters/*/text.md"], "instructions": "## 自動追加レビュー観点: 原稿"}},
    "replace_auto_categories": True,
}


def _profile(**over):
    return review_lib.commands.init.review_criteria.profile_from({**PROFILE, **over})


def _declare(root: pathlib.Path, profile: dict | None = None, **extra) -> None:
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    decl = {"version": 2, **({"profile": profile} if profile is not None else {}), **extra}
    (root / ".ndf" / "review.json").write_text(json.dumps(decl, ensure_ascii=False), encoding="utf-8")


def _files(*paths: str) -> list[dict]:
    return [{"paths": [p], "status": "M"} for p in paths]


# ---------------- 自動レビュー観点 ----------------


def test_without_a_profile_the_auto_review_is_unchanged():
    """受け入れ条件 1・I1。"""
    cats = ["common", "docs_only"]
    names, text = rf._auto_review(cats, _files(CHAPTER))
    assert names == cats and text == rf._auto_review_instructions(cats)


def test_profile_category_replaces_the_default_ones():
    """受け入れ条件 5・6: glob に当たる分類の観点だけが入る。"""
    names, text = rf._auto_review(["common", "docs_only"], _files(CHAPTER), _profile())
    assert names == ["manuscript"] and text == "## 自動追加レビュー観点: 原稿"
    assert rf.COMMON_REVIEW_TEMPLATE not in text and rf.DOCS_ONLY_REVIEW_TEMPLATE not in text


def test_profile_category_is_added_after_the_default_ones():
    """受け入れ条件 6: replace_auto_categories が偽なら既定の観点の後ろに足す。"""
    names, text = rf._auto_review(["common", "docs_only"], _files(CHAPTER), _profile(replace_auto_categories=False))
    assert names == ["common", "docs_only", "manuscript"]
    assert text == rf._auto_review_instructions(["common", "docs_only"]) + "\n\n## 自動追加レビュー観点: 原稿"


def test_profile_category_does_not_cross_directories():
    """決定 2: `*` は `/` をまたがない。"""
    names, text = rf._auto_review(["common"], _files("works/novel-a/x/chapters/011/text.md"), _profile(replace_auto_categories=False))
    assert names == ["common"] and text == rf.COMMON_REVIEW_TEMPLATE


def test_same_name_as_a_default_category_is_listed_once():
    profile = _profile(categories={"code": {"paths": ["**/*.py"], "instructions": "追加の観点"}}, replace_auto_categories=False)
    names, text = rf._auto_review(["common", "code"], _files("a/b.py"), profile)
    assert names == ["common", "code"] and text.endswith(rf.CODE_REVIEW_TEMPLATE + "\n\n追加の観点")


# ---------------- 新規の init ----------------


@pytest.fixture()
def init_env(monkeypatch, state_mod, tmp_path, fake_gh):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(review_lib.github, "_viewer_login", lambda: "takemi")
    monkeypatch.setattr(review_lib.workspace, "_create_worktree", lambda *a: None)
    monkeypatch.setattr(review_lib.workspace, "_is_registered_worktree", lambda p: True)
    monkeypatch.setattr(review_lib.workspace, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
    real_run = subprocess.run

    def _run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and "fetch-pr-comments.sh" in str(cmd[0]):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)

    def run(path: str, focus: str | None = None):
        fake_gh.set_rules(
            [
                {"match": f"repos/{REPO}/pulls/{PR}/files", "stdout": "", "exit": 1},
                {"match": f"pr view {PR} --json files", "stdout": json.dumps({"files": [{"path": path, "changeType": "MODIFIED"}]})},
            ]
        )
        monkeypatch.setattr(
            review_lib.github,
            "_fetch_pr_metadata",
            lambda pr, repo=None: review_lib.github.PrMetadata(REPO, "takemi", "feat/x", "abc123", "develop", False, 4000, None),
        )
        review_lib.commands.init.cmd_init(
            argparse.Namespace(
                pr=PR,
                max_rounds=None,
                rotate_after=None,
                only=None,
                worktree=str(worktree),
                focus=focus,
                extra_instructions_file=None,
                host="claude",
            )
        )
        return json.loads((tmp_path / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))

    return worktree, run


def test_new_init_reads_the_profile_after_preparing_the_worktree(init_env, capsys):
    """受け入れ条件 2・5・6・10: 状態ファイルの節と観点がプロファイルから組まれ、手で足した観点はその後ろに付く。"""
    worktree, run = init_env
    _declare(worktree, PROFILE)
    st = run(CHAPTER, focus="手で足した観点")
    assert "REVIEW_PROFILE=declared" in capsys.readouterr().out
    crit = st["review_criteria"]
    assert crit["profile_status"] == "declared" and crit["profile"] == PROFILE
    assert "5. 人物の口調が設定とずれる" in crit["reviewer_block"] and "字句や言い回し" not in crit["reviewer_block"]
    assert st["auto_review_categories"] == ["manuscript"]
    assert st["review_instructions"] == "## 自動追加レビュー観点: 原稿\n\n手で足した観点"


def test_review_kind_comes_from_the_default_categories(init_env):
    """I6: 観点の列から design が外れても、設計 PR の上限で回る。"""
    worktree, run = init_env
    _declare(worktree, {**PROFILE, "categories": {"doc": {"paths": ["issues/*"], "instructions": "文書の観点"}}})
    st = run("issues/1-design.md")
    assert st["auto_review_categories"] == ["doc"] and "design" not in st["auto_review_categories"]
    assert st["review_kind"] == "design" and st["max_rounds"] == 3
    assert st["review_instructions_by_stage"]["detail"] == "文書の観点"


def test_new_init_with_a_broken_profile_continues_with_defaults(init_env, capsys):
    """受け入れ条件 8・I4。"""
    worktree, run = init_env
    _declare(worktree, {**PROFILE, "criteria": {"2": "上書き"}}, focus=["重点 A"])
    st = run(CHAPTER)
    out = capsys.readouterr()
    assert "REVIEW_PROFILE=unreadable" in out.out and "レビューのプロファイルを読めない" in out.err
    crit = st["review_criteria"]
    assert crit["profile_status"] == "unreadable" and crit["profile_error"] and "profile" not in crit
    assert crit["status"] == "declared" and "重点 A" in crit["reviewer_block"] and "字句や言い回し" in crit["reviewer_block"]
    assert st["auto_review_categories"][0] == "common"


def test_new_init_without_a_declaration_keeps_the_state_shape(init_env, capsys):
    """I1・決定 9: プロファイルが無ければ状態ファイルにプロファイルの項目を置かない。"""
    _, run = init_env
    st = run(CHAPTER)
    assert "REVIEW_PROFILE=none" in capsys.readouterr().out
    assert not {"profile_status", "profile", "profile_error"} & set(st["review_criteria"])
    assert st["auto_review_categories"][:2] == ["common", "docs_only"]


# ---------------- 再開 ----------------


@pytest.fixture()
def saved_state(monkeypatch, state_mod, tmp_path):
    """プロファイルの無い宣言で組んだ状態ファイル。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    wt = tmp_path / "wt"
    wt.mkdir()
    changed = _files(CHAPTER)
    default = review_lib.commands.init.categories_mod._classify_changed_files(changed)
    names, auto = rf._auto_review(default, changed)
    st = {
        "current_pr": PR,
        "worktree_path": str(wt),
        "changed_files": changed,
        "auto_review_categories": names,
        "auto_review_instructions": auto,
        "manual_extra_review_instructions": "手で足した観点",
        "review_instructions": rf._combined_review_instructions(auto, "手で足した観点"),
        "review_criteria": rf._review_criteria(wt),
    }
    state_file = tmp_path / f"cross-review-pr{PR}-state.json"
    state_file.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    writes = []
    real = review_lib.store._write_state
    monkeypatch.setattr(review_lib.store, "_write_state", lambda p, s: (writes.append(p), real(p, s)))
    return wt, state_file, writes


def test_resume_without_changes_does_not_rewrite_the_state(saved_state):
    """I7: 宣言を変えなければ書かない（古い状態ファイルからの再開を含む）。"""
    wt, state_file, writes = saved_state
    assert review_lib.commands.init._rewrite_review_criteria(state_file, str(wt)) == ("none", "none")
    assert writes == []


def test_resume_picks_up_a_rewritten_profile(saved_state):
    """受け入れ条件 9・10: 書き換えたプロファイルで次のラウンドの節と観点を組み直し、2 回目は書かない。"""
    wt, state_file, writes = saved_state
    _declare(wt, PROFILE)
    assert review_lib.commands.init._rewrite_review_criteria(state_file, str(wt)) == ("none", "declared")
    st = json.loads(state_file.read_text(encoding="utf-8"))
    assert "5. 人物の口調が設定とずれる" in st["review_criteria"]["reviewer_block"]
    assert st["auto_review_categories"] == ["manuscript"]
    assert st["review_instructions"] == "## 自動追加レビュー観点: 原稿\n\n手で足した観点"
    assert len(writes) == 1
    review_lib.commands.init._rewrite_review_criteria(state_file, str(wt))
    assert len(writes) == 1


# ---------------- 報告 ----------------


def test_the_report_names_an_unreadable_profile(state_mod, capsys):
    st = {"review_criteria": {"status": "none", "profile_status": "unreadable", "profile_error": "reply に {inner} が無い"}}
    review_lib.commands.report._print_review_focus(st)
    out = capsys.readouterr().out
    assert "## レビューのプロファイル: 読めなかった" in out and "reply に {inner} が無い" in out
