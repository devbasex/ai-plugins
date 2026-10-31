"""受け入れ条件の確認の区分（#2144）。完了判定が `acceptance-check.py record` で本文へ書き、承認資料
（release_lib/materials.read_unverified）と sprint-close.py の閉じる条件 2 が同じ記録を読む。

本文の読み書き（gh_rest.fetch_body / update_body）は代役に差し替え、課題ごとの錠は一時の置き場で取る。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import deps  # noqa: E402

deps.require("md", "mdtable", "locks")
import acceptance  # noqa: E402
from release_lib import materials  # noqa: E402


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


check = _load("acceptance_check", "acceptance-check.py")
sprint_close = _load("sprint_close_for_checks", "sprint-close.py")

BODY = "# 課題\n\n## 受け入れ条件\n\n- [ ] 1. 一\n- [ ] 2. 二\n- [x] 3. 三\n- [ ] 4. 四\n\n## 進行\n\nモード: standard\n"


@pytest.fixture
def issue(monkeypatch, tmp_path):
    """課題 o/r#7 の本文の代役。bodies["o/r#7"] を読み書きする。"""
    monkeypatch.setenv("NDF_LOCK_DIR", str(tmp_path / "locks"))
    store = {"o/r#7": BODY, "writes": 0}

    def fetch(repo, n):
        return store.get(f"{repo}#{n}")

    def update(repo, n, body):
        store[f"{repo}#{n}"] = body
        store["writes"] += 1
        return True

    monkeypatch.setattr(check.gh_rest, "fetch_body", fetch)
    monkeypatch.setattr(check.gh_rest, "update_body", update)
    return store


def run(capsys, *argv):
    with pytest.raises(SystemExit) as e:
        check.main(list(argv))
    return e.value.code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# --- 記録を書く・読む（受け入れ条件 1） -------------------------------------------


def test_record_writes_a_table_that_read_check_kinds_reads(issue, capsys):
    code, out = run(
        capsys, "record", "--issue", "7", "--repo", "o/r", "--pre-merge", "1=pytest tests/test_x.py::test_a", "--post-release", "4"
    )
    assert code == 0 and out["status"] == "ok"
    body = issue["o/r#7"]
    assert acceptance.read_check_kinds(body) == {
        1: acceptance.Check("マージ前", "pytest tests/test_x.py::test_a"),
        4: acceptance.Check("リリース後", ""),
    }
    assert [(i["name"], i["result"]) for i in out["items"]] == [("1", "マージ前"), ("4", "リリース後")]
    assert acceptance.criteria(body) == acceptance.criteria(BODY)  # 受け入れ条件の節は変えない


def test_record_again_overrides_and_keeps_the_rest(issue, capsys):
    run(capsys, "record", "--issue", "7", "--repo", "o/r", "--pre-merge", "1=a", "--post-release", "2")
    code, _ = run(capsys, "record", "--issue", "7", "--repo", "o/r", "--pre-merge", "2=b")
    assert code == 0
    assert acceptance.read_check_kinds(issue["o/r#7"]) == {1: acceptance.Check("マージ前", "a"), 2: acceptance.Check("マージ前", "b")}
    writes = issue["writes"]
    code, out = run(capsys, "record", "--issue", "7", "--repo", "o/r", "--pre-merge", "2=b")
    assert code == 0 and "変更なし" in out["summary"] and issue["writes"] == writes


@pytest.mark.parametrize(
    "args",
    [
        ["--pre-merge", "9=a"],  # 受け入れ条件に無い番号
        ["--pre-merge", "1"],  # 手段が無い
        ["--pre-merge", "1=a", "--post-release", "1"],  # 両方
        [],  # 何も渡さない
    ],
)
def test_record_rejects_bad_arguments_without_writing(issue, capsys, args):
    code, out = run(capsys, "record", "--issue", "7", "--repo", "o/r", *args)
    assert code == 2 and out["status"] == "stopped"
    assert issue["o/r#7"] == BODY


def test_record_without_criteria_stops(issue, capsys):
    issue["o/r#7"] = "# 課題\n\n本文だけ\n"
    code, _ = run(capsys, "record", "--issue", "7", "--repo", "o/r", "--post-release", "1")
    assert code == 3


def test_show_reads_the_record(issue, capsys):
    run(capsys, "record", "--issue", "7", "--repo", "o/r", "--pre-merge", "2=`ruff check .`")
    code, out = run(capsys, "show", "--issue", "7", "--repo", "o/r")
    assert code == 0 and out["items"] == [{"kind": "criterion", "name": "2", "result": "マージ前", "means": "`ruff check .`"}]


def test_read_check_kinds_skips_rows_without_means_or_kind():
    body = (
        BODY
        + "\n## 受け入れ条件の確認\n\n| 条件 | 区分 | 手段 |\n| --- | --- | --- |\n| 1 | マージ前 | — |\n| 2 | 済み | x |\n| 4 | リリース後 | — |\n"
    )
    assert acceptance.read_check_kinds(body) == {4: acceptance.Check("リリース後", "")}
    assert acceptance.read_check_kinds(BODY) == {}


# --- 承認資料（受け入れ条件 2・3） ---------------------------------------------------


def _recorded(pre, post):
    return check.recorded_body(BODY, pre, post)


def test_unverified_leaves_out_pre_merge_and_keeps_post_release():
    body = _recorded({1: "pytest -k a", 2: "pytest -k b"}, [4])
    got = materials.read_unverified({("o/r", 7): body}, {}, "o/r")
    assert [u.number for u in got] == [4]


def test_unverified_without_a_record_lists_every_unchecked_criterion():
    got = materials.read_unverified({("o/r", 7): BODY}, {}, "o/r")
    assert [u.number for u in got] == [1, 2, 4]


# --- 閉じる条件 2（受け入れ条件 4） --------------------------------------------------


def _judge(body, rows):
    return sprint_close.judge_acceptance(rows, acceptance.criteria(body), acceptance.pre_merge_numbers(body))


def test_pre_merge_criteria_need_no_verification_row():
    body = _recorded({1: "pytest -k a", 2: "pytest -k b"}, [4])
    ok, why, to_check = _judge(body, acceptance.IssueRows(True, frozenset({3, 4})))
    assert ok and why is None and to_check == [1, 2, 4]


def test_post_release_criteria_still_need_a_row():
    body = _recorded({1: "pytest -k a"}, [2, 4])
    ok, why, _ = _judge(body, acceptance.IssueRows(True, frozenset({4})))
    assert not ok and "2" in why


def test_all_pre_merge_closes_without_rows_and_unrecorded_still_needs_rows():
    body = _recorded({1: "a", 2: "b", 3: "c", 4: "d"}, [])
    assert _judge(body, None)[0]
    assert _judge(BODY, None) == (False, sprint_close.NO_ROWS, [])


# --- 版ごとのリリース後テストの表（version_verdicts の現状固定） ------------------------


def _verify(ver, rows, verdict="合格"):
    table = "| 課題 | 受け入れ条件 | 結果 |\n| --- | --- | --- |\n" + "".join(f"| {i} | {c} | {r} |\n" for i, c, r in rows)
    return f"## リリース後テスト\n\n対象の版: {ver}\n\n{table}\n合否: {verdict}\n"


def test_version_verdicts_reads_the_block_whose_base_version_matches():
    text = _verify("1.0.1-dev.1", [("#7", "2. 二", "合格"), ("x/y#8", "1. 一", "不合格")])
    assert acceptance.version_verdicts(text, "v1.0.1", "o/r") == {
        ("o/r", 7): acceptance.IssueRows(True, frozenset({2})),
        ("x/y", 8): acceptance.IssueRows(False, frozenset()),
    }


def test_version_verdicts_uses_the_last_matching_block_and_ignores_other_versions():
    text = "\n".join(
        [
            _verify("1.0.1", [("#7", "1. 一", "不合格")]),
            _verify("1.0.1（注記）", [("#7", "1. 一", "合格")]),
            _verify("1.0.2", [("#9", "1. 一", "合格")]),
        ]
    )
    assert acceptance.version_verdicts(text, "1.0.1", "o/r") == {("o/r", 7): acceptance.IssueRows(True, frozenset({1}))}


@pytest.mark.parametrize(
    "text, version",
    [
        ("", "1.0.1"),
        ("本文だけ\n", "1.0.1"),
        (_verify("1.0.2", [("#7", "1. 一", "合格")]), "1.0.1"),
        (_verify("main c427284", [("#7", "1. 一", "合格")]), "1.0.1"),
        (_verify("1.0.1", [("#7", "1. 一", "合格")]), "main"),
        (_verify("1.0.1", [("—", "導入", "合格")]), "1.0.1"),
        ("## リリース後テスト\n\n対象の版: 1.0.1\n\n| 課題 | 結果 |\n| --- | --- |\n| #7 | 合格 |\n", "1.0.1"),
    ],
)
def test_version_verdicts_returns_empty_when_nothing_matches(text, version):
    assert acceptance.version_verdicts(text, version, "o/r") == {}


def test_version_verdicts_skips_a_block_without_a_version_line():
    text = "## リリース後テスト\n\n| 課題 | 結果 |\n| --- | --- |\n| #7 | 合格 |\n\n合否: 合格\n"
    assert acceptance.version_verdicts(text, "1.0.1", "o/r") == {}
