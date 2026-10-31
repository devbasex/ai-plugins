"""sprint-close.py の閉じる条件 2 と、本文の受け入れ条件の印（#1683）。

リリース後テストの表の `受け入れ条件` の列の番号と、課題の本文の `## 受け入れ条件` のチェックボックスを
突き合わせ、すべてに合格の行がある課題だけを閉じて印を付ける。gh の偽物は test_sprint_close_merge_green.py
のものを使う（`issue view --json state,body` は `issue_bodies` の本文を返し、`api -X PATCH` は本文を書き戻す）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("sprint_close_harness_acc", HERE / "test_sprint_close_merge_green.py")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)
call, issues_of, DIST_PROD, load_sprint_close = harness.call, harness.issues_of, harness.DIST_PROD, harness.load_sprint_close
repo, gh = harness.repo, harness.gh  # fixture

sys.path.insert(0, str(HERE.parent / "lib"))
import acceptance  # noqa: E402

CLOSE = ["--record-pr", "20", "--repo", "o/r", "--with-verification"]
THREE = "# 課題\n\n## 受け入れ条件\n\n- [ ] 1. 一\n- [ ] 2. 二\n- [ ] 3. 三\n\n## 進行\n\nモード: standard\n"


def table(rows):
    """rows は (課題, 受け入れ条件, 結果)。"""
    t = "| 課題 | 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |\n| --- | --- | --- | --- | --- |\n"
    t += "".join(f"| {k} | {c} | x | t | {r} |\n" for k, c, r in rows)
    return f"## リリース後テスト\n対象の版: 1.1.0（2026-09-24）\n\n{t}\n合否: 済\n"


def setup(gh, rows, bodies, states=None, prs="Fixes #1"):
    states = states or {}
    gh.set(
        records={"20": {"body": DIST_PROD, "comments": [{"body": table(rows)}]}},
        bodies={"11": prs, "12": ""},
        issues={k: [states.get(k, "OPEN")] for k in bodies},
        issue_bodies=bodies,
    )


def calls(gh, head):
    return [c for c in gh.get().get("calls", []) if c[: len(head)] == head]


# --- 表の読み方（受け入れ条件 1・2） ----------------------------------------------

PR1887_ROWS = [
    ("—", "導入の確認: 10.17.69 を入れ直して起動する", "合格"),
    ("#1867", "子の箇条が 1 項目にまとまる", "合格"),
    ("#1492", "記録が残る", "保留（次の版で見る）"),
    ("#1870", "close の前に release-verify が並ぶ", "合格"),
    ("#1870", "まとめのプランで閉じる", "保留（次の本番で見る）"),
    ("PR 1869", "表の形", "合格"),
]
PR1887_BODIES = {
    "o/r#1867": "## 受け入れ条件\n\n" + "".join(f"- [ ] {i}. 条件\n" for i in range(1, 12)),
    "o/r#1492": "## 受け入れ条件\n\n- [ ] 1. 条件\n",
    "o/r#1870": "## 受け入れ条件\n\n" + "".join(f"- [ ] {i}. 条件\n" for i in range(1, 8)),
}


def test_rows_that_do_not_point_to_an_issue_are_skipped(repo, gh):
    """`—` と `PR 1869` の行があっても、課題の判定はその 2 行の無い表と同じ。対応が読めないとは言わない（受け入れ条件 1）。"""
    prs = "Fixes #1867, fixes #1492, fixes #1870"
    closed = {k: "CLOSED" for k in PR1887_BODIES}
    setup(gh, PR1887_ROWS, PR1887_BODIES, closed, prs=prs)
    code, out, err = call("sprint-close.py", [*CLOSE, "--dry-run"], gh.env, repo)
    assert code == 0, (out, err)
    with_rows = {k: (v["result"], v["reason"]) for k, v in issues_of(out).items()}
    assert {r for r, _ in with_rows.values()} == {"closed_unverified"}
    assert not any("対応が読めない" in why for _, why in with_rows.values())

    setup(gh, [r for r in PR1887_ROWS if r[0].startswith("#")], PR1887_BODIES, closed, prs=prs)
    code, out, err = call("sprint-close.py", [*CLOSE, "--dry-run"], gh.env, repo)
    assert {k: (v["result"], v["reason"]) for k, v in issues_of(out).items()} == with_rows


def test_pr_cell_is_not_read_as_an_issue():
    """`PR #1869` のセルは課題 #1869 と読まない（頭に固定する）。"""
    got = load_sprint_close().verification_verdicts(table([("PR #1869", "c", "合格"), ("#5", "1. a", "合格")]), "o/r")
    assert set(got) == {("o/r", 5)}


def test_a_table_without_issue_rows_keeps_every_issue_open(repo, gh):
    """課題を指す行が 1 行も無ければ、全件が対応の読めない kept_open（受け入れ条件 2）。閉じた課題も同じ。"""
    setup(gh, [("—", "導入の確認", "合格")], {"o/r#1": THREE, "o/r#2": THREE}, {"o/r#2": "CLOSED"}, prs="Fixes #1, fixes #2")
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    assert code == 0, (out, err)
    assert {(i["result"], i["reason"]) for i in issues_of(out).values()} == {("kept_open", "条件と課題の対応が読めない")}
    assert not calls(gh, ["issue", "close"]) and not calls(gh, ["api"])


# --- 閉じる条件 2 と印（受け入れ条件 3〜11） ------------------------------------------


def test_a_criterion_without_a_passing_row_keeps_the_issue_open(repo, gh):
    """番号 3 の合格の行が無ければ閉じず、本文も書かない（受け入れ条件 3）。"""
    setup(gh, [("#1", "1. 一", "合格"), ("#1", "2. 二", "合格")], {"o/r#1": THREE})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and it["result"] == "kept_open" and it["reason"] == "合格の行が無い受け入れ条件: 3"
    assert not calls(gh, ["issue", "close"]) and not calls(gh, ["api"])
    assert gh.get()["issue_bodies"]["o/r#1"] == THREE


def test_a_pre_merge_criterion_needs_no_passing_row(repo, gh):
    """本文の `## 受け入れ条件の確認` でマージ前に確かめたと記録した条件 3 は、合格の行が無くても閉じる（#2144）。"""
    record = "\n## 受け入れ条件の確認\n\n| 条件 | 区分 | 手段 |\n| --- | --- | --- |\n| 3 | マージ前 | `pytest -k three` |\n"
    setup(gh, [("#1", "1. 一", "合格"), ("#1", "2. 二", "合格")], {"o/r#1": THREE + record})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0, (out, err)
    assert it["result"] == "closed" and it["checked"] == [1, 2, 3]


def test_all_criteria_passed_closes_and_marks_before_closing(repo, gh):
    """1〜3 がそろうと閉じ、本文の 3 個のチェックボックスに印が付く。書くのは閉じる前（受け入れ条件 4）。"""
    setup(gh, [("#1", f"{i}. c", "合格") for i in (1, 2, 3)], {"o/r#1": THREE})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0, (out, err)
    assert it["result"] == "closed" and it["checked"] == [1, 2, 3]
    assert acceptance.criteria(gh.get()["issue_bodies"]["o/r#1"]) == [acceptance.Criterion(i, True) for i in (1, 2, 3)]
    seq = [c[:2] for c in gh.get()["calls"]]
    assert seq.index(["api", "-X"]) < seq.index(["issue", "close"])


def test_marking_changes_only_the_check_characters(repo, gh):
    """節の外・取り消し線の行・説明の行・`\\r\\n` は 1 文字も変わらない（受け入れ条件 5）。"""
    body = (
        "# 課題\r\n\r\n- [ ] 節の外\r\n\r\n## 受け入れ条件\r\n\r\n説明の行\r\n- [ ] 1. 一\r\n- ~~旧~~ → 差し替え\r\n"
        "- [x] 2. 二\r\n- [ ] 3. 三\r\n\r\n## 進行\r\n\r\n- [ ] 設計\r\n"
    )
    setup(gh, [("#1", f"{i}. c", "合格") for i in (1, 2, 3)], {"o/r#1": body})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["checked"] == [1, 3]
    new = gh.get()["issue_bodies"]["o/r#1"]
    diff = [i for i, (a, b) in enumerate(zip(body, new)) if a != b]
    assert len(new) == len(body) and len(diff) == 2 and all(new[i] == "x" and body[i] == " " for i in diff)


def test_pass_and_hold_on_the_same_number_is_not_passed(repo, gh):
    """同じ番号に合格と保留があれば合格と扱わない（受け入れ条件 6）。"""
    rows = [("#1", "1. c", "合格"), ("#1", "2. c", "合格"), ("#1", "3. c", "合格"), ("#1", "3. c", "保留")]
    setup(gh, rows, {"o/r#1": THREE})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert it["result"] == "kept_open" and it["reason"] == "リリース後テストに合格でない条件がある（不合格・保留）"
    assert not calls(gh, ["api"])


def test_a_closed_issue_that_misses_a_criterion_is_closed_unverified(repo, gh):
    """既に閉じた課題が条件 2 を満たさなければ closed_unverified。開き直さず、止めない（受け入れ条件 7）。"""
    setup(gh, [("#1", "1. c", "合格"), ("#1", "2. c", "合格")], {"o/r#1": THREE}, {"o/r#1": "CLOSED"})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and out["status"] == "ok" and out["metrics"]["closed_unverified"] == 1
    assert it["result"] == "closed_unverified" and it["reason"].startswith("閉じているが") and it["reason"].endswith("3")
    assert not calls(gh, ["issue", "reopen"]) and not calls(gh, ["api"])


def test_a_closed_issue_that_meets_the_criteria_is_marked(repo, gh):
    """既に閉じた課題が条件 2 を満たせば already_closed で、印が付く（受け入れ条件 8）。"""
    setup(gh, [("#1", f"{i}. c", "合格") for i in (1, 2, 3)], {"o/r#1": THREE}, {"o/r#1": "CLOSED"})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and it["result"] == "already_closed" and it["checked"] == [1, 2, 3]
    assert "- [x] 3. 三" in gh.get()["issue_bodies"]["o/r#1"]


def test_dry_run_lists_the_numbers_to_mark_without_writing(repo, gh):
    """--dry-run は書かず、would_check に付ける予定の番号を載せる（受け入れ条件 9）。"""
    setup(gh, [("#1", f"{i}. c", "合格") for i in (1, 2, 3)], {"o/r#1": THREE.replace("- [ ] 2.", "- [x] 2.")})
    code, out, err = call("sprint-close.py", [*CLOSE, "--dry-run"], gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 0 and it["result"] == "would_close" and it["would_check"] == [1, 3]
    assert not calls(gh, ["api"]) and not calls(gh, ["issue", "close"])


def test_a_failed_write_does_not_close(repo, gh):
    """本文を書けなければ failed で閉じず、cmd は同じ呼び出し、終了コード 1（受け入れ条件 10）。"""
    setup(gh, [("#1", f"{i}. c", "合格") for i in (1, 2, 3)], {"o/r#1": THREE})
    st = gh.get()
    st["write_code"] = 1
    gh.set(**{k: v for k, v in st.items() if k != "calls"})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    it = issues_of(out)["o/r#1"]
    assert code == 1 and it["result"] == "failed" and it["reason"] == "受け入れ条件の印を書けない"
    assert it["cmd"].endswith("sprint-close.py " + " ".join(CLOSE))
    assert not calls(gh, ["issue", "close"])


@pytest.mark.parametrize("body", ["# 課題\n\n本文\n", "## 受け入れ条件\n\n文だけで書いた条件\n"])
def test_issue_without_checkboxes_is_judged_by_the_rows_only(repo, gh, body):
    """節が無い・チェックボックスが 0 個なら表の合否だけで閉じ、本文を書かない（受け入れ条件 11）。"""
    setup(gh, [("#1", "c", "合格")], {"o/r#1": body})
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "closed" and "checked" not in issues_of(out)["o/r#1"]
    assert not calls(gh, ["api"])


@pytest.mark.parametrize("args", [["--record-pr", "20", "--repo", "o/r"], ["--record-pr", "0", "--repo", "o/r", "--issues", "1"]])
def test_paths_without_verification_do_not_read_the_body(repo, gh, args):
    """--with-verification なし・--record-pr 0 は本文を読まず書かない（受け入れ条件 12）。"""
    setup(gh, [("#1", "1. c", "合格")], {"o/r#1": THREE})
    code, out, err = call("sprint-close.py", [*args, "--with-verification"] if "0" in args else args, gh.env, repo)
    assert code == 0 and issues_of(out)["o/r#1"]["result"] == "closed"
    assert not [c for c in gh.get()["calls"] if "state,body" in c] and not calls(gh, ["api"])


def test_issues_of_other_repositories_are_judged_but_not_written(repo, gh):
    """他のリポジトリの課題は同じ規則で決まり、本文は書かない（受け入れ条件 13）。"""
    full = [("x/y#2", f"{i}. c", "合格") for i in (1, 2, 3)]
    setup(gh, full + [("x/y#3", "1. c", "合格")], {"x/y#2": THREE, "x/y#3": THREE}, prs="Fixes x/y#2, fixes x/y#3")
    code, out, err = call("sprint-close.py", CLOSE, gh.env, repo)
    res = issues_of(out)
    assert code == 0 and res["x/y#2"]["result"] == "closed" and "checked" not in res["x/y#2"]
    assert res["x/y#3"]["result"] == "kept_open" and res["x/y#3"]["reason"].endswith("2, 3")
    assert not calls(gh, ["api"])


# --- 錠（非機能） --------------------------------------------------------------------


class _A:
    dry_run = False


def test_body_is_read_inside_the_issue_lock_and_a_held_lock_fails(tmp_path, monkeypatch):
    """本文の読み書きは課題ごとの錠の中で行う。錠を取れなければ failed（非機能の条件）。"""
    monkeypatch.setenv("NDF_LOCK_DIR", str(tmp_path))
    mod = load_sprint_close()
    ctx = mod.CloseContext(str(tmp_path), "o/r", "c")
    seen = []

    def read(root, repo_, n):
        try:
            with mod.gh_parts.issue_lock(repo_, n, 0):
                seen.append("free")
        except mod.locks.LockTimeout:
            seen.append("held")
        return "OPEN", "x"

    monkeypatch.setattr(mod, "_read_state_body", read)
    monkeypatch.setattr(mod, "close_one", lambda c, r, n: {"kind": "issue", "repo": r, "number": n, "result": "closed"})
    assert mod._close_verified(_A(), ctx, "o/r", 1, mod.IssueRows(True, frozenset()))["result"] == "closed"
    assert seen == ["held"]

    monkeypatch.setattr(mod, "LOCK_TIMEOUT", 0.1)
    with mod.gh_parts.issue_lock("o/r", 1, 0):
        it = mod._close_verified(_A(), ctx, "o/r", 1, mod.IssueRows(True, frozenset()))
    assert it["result"] == "failed" and it["reason"] == "本文の錠を取れない"


def test_a_check_that_changes_more_than_the_marks_is_not_written(tmp_path, monkeypatch):
    """印付けの結果が確かめに外れたら書かずに failed（I5）。"""
    mod = load_sprint_close()
    monkeypatch.setenv("NDF_LOCK_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_read_state_body", lambda *a: ("OPEN", THREE))
    monkeypatch.setattr(mod.acceptance, "check_criteria", lambda body, nums: body.replace("# 課題", "# 変えた"))
    wrote = []
    monkeypatch.setattr(mod.gh_rest, "update_body", lambda *a: wrote.append(a) or True)
    it = mod._close_verified(_A(), mod.CloseContext(str(tmp_path), "o/r", "c"), "o/r", 1, mod.IssueRows(True, frozenset({1, 2, 3})))
    assert it["result"] == "failed" and not wrote


# --- 受け入れ条件の数え方（I3） --------------------------------------------------------


def test_criteria_skip_fences_indented_boxes_and_strikethrough():
    body = "- [ ] 外\n## 受け入れ条件\n\n- [ ] 1. 一\n  - [ ] 子\n```\n- [ ] 囲みの中\n```\n- ~~旧~~ → 新\n- [x] 2. 二\n## 次\n- [ ] 外\n"
    assert acceptance.criteria(body) == [acceptance.Criterion(1, False), acceptance.Criterion(2, True)]
    assert acceptance.criteria("# 本文だけ\n") == []
    new = acceptance.check_criteria(body, [1, 2, 9])
    assert new == body.replace("- [ ] 1. 一", "- [x] 1. 一") and acceptance.only_marks(body, new)
    assert not acceptance.only_marks(body, body.replace("- [ ] 外\n## 受", "- [x] 外\n## 受"))


def test_criteria_skip_tilde_mixed_unclosed_fences_and_keep_crlf():
    """現状固定（I-003）: `~~~`・違う種類の囲みの混在・閉じない囲みの中は数えず、CRLF の本文は印の 1 文字だけ変わる。"""
    mixed = "## 受け入れ条件\n\n~~~\n- [ ] 囲み\n~~~\n- [ ] 1. 一\n```\n~~~\n- [ ] 混在\n```\n- [ ] 2. 二\n"
    assert acceptance.criteria(mixed) == [acceptance.Criterion(1, False), acceptance.Criterion(2, False)]
    assert acceptance.check_criteria(mixed, [1, 2]) == mixed.replace("- [ ] 1.", "- [x] 1.").replace("- [ ] 2.", "- [x] 2.")
    unclosed = "## 受け入れ条件\n\n- [ ] 1. 一\n~~~\n- [ ] 閉じない\n"
    assert acceptance.criteria(unclosed) == [acceptance.Criterion(1, False)]
    assert acceptance.check_criteria(unclosed, [1, 2]) == unclosed.replace("- [ ] 1.", "- [x] 1.")
    crlf = "## 受け入れ条件\r\n\r\n```\r\n- [ ] 囲み\r\n```\r\n- [ ] 1. 一\r\n- [x] 2. 二\r\n## 次\r\n"
    assert acceptance.criteria(crlf) == [acceptance.Criterion(1, False), acceptance.Criterion(2, True)]
    assert acceptance.check_criteria(crlf, [1, 2]) == crlf.replace("- [ ] 1.", "- [x] 1.")
