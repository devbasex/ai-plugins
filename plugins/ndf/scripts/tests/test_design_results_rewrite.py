"""`supervise.py design-results` の補正の保持・設計文書のパス・一時的な失敗（#1651 の 1651-1・1651-2、#1941 の 1941-1〜1941-3）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import design_results as dr  # noqa: E402
import gh_call  # noqa: E402
from supervise_lib import templates  # noqa: E402

DOC = "issues/issue-11-design.md"
TABLE = """# 設計

## 設計の結果

| 課題 | 扱い | 取り込み先 | 触るファイル |
| --- | --- | --- | --- |
| #11 | 実装する | — | `pkg/a.py`、`pkg/b.py` |
| #12 | 取り込む | #11 | — |
"""
FIX_A = "- 補正: 先に pkg/a.py の既存テストを読む"
FIX_B = "課題どうしの重なり: #13 と pkg/b.py を共有する"


def results(text: str = TABLE, doc: str = DOC) -> dr.DesignResults:
    res = dr.DesignResults(design_prs=[40])
    res.add(dr.parse_results(text, doc))
    return res


def plan_of(n: int, prompt: str | None = None) -> dict:
    ns = argparse.Namespace(issue=[n], prompt=None, prompt_file=None, files=None)
    return {"課題": [n], "steps": [{"id": "impl", "type": "work", "prompt": prompt or templates.impl_prompt(ns)}]}


def impl_prompt_of(plan: dict) -> str:
    return next(s for s in plan["steps"] if s["id"] == "impl")["prompt"]


def with_fixes(plan: dict) -> dict:
    """conductor の補正: 規則の途中に 1 行、末尾に 1 行を足す。"""
    lines = impl_prompt_of(plan).splitlines()
    lines.insert(2, FIX_A)
    plan["steps"][0]["prompt"] = "\n".join([*lines, "", FIX_B]) + "\n"
    return plan


# ---------- 補正と設計文書のパス（1651-1・1651-2） ----------


# ---------- 読み取りの一時的な失敗（1941-1〜1941-3） ----------


class Fake:
    """gh と git の偽物。`fails` は先頭から何回 0 以外で終わるか（コマンドの頭の語ごと）。"""

    def __init__(
        self, fails: dict[str, int] | None = None, pr: str = "40\n", files: str = f"{DOC}\nissues/gone.md\n", shows: list[str] | None = None
    ):
        self.fails = dict(fails or {})
        self.pr, self.files, self.calls = pr, files, []
        self.shows = list(shows or [])  # git show が順に返す文書（尽きたら TABLE）

    def _fail(self, key: str):
        if self.fails.get(key, 0) > 0:
            self.fails[key] -= 1
            return subprocess.CompletedProcess([], 1, "", "HTTP 504: 504 Gateway Timeout")
        return None

    def gh(self, args, stdin=None, cwd=None):
        key = " ".join(args[:2])
        self.calls.append(key)
        bad = self._fail(key)
        if bad:
            return gh_call.GhResult(bad.returncode, bad.stdout, bad.stderr)
        return gh_call.GhResult(0, self.pr if args[1] == "list" else self.files, "")

    def run(self, cmd, **kw):
        key = "git " + cmd[3]
        self.calls.append(key)
        bad = self._fail(key)
        if bad:
            return bad
        show = self.shows.pop(0) if key == "git show" and self.shows else TABLE
        out = {"git fetch": "", "git ls-tree": f"{DOC}\0", "git show": show}[key]
        return subprocess.CompletedProcess(cmd, 0, out, "")


def sprint(tmp_path: Path) -> tuple[argparse.Namespace, dict[int, Path]]:
    return stage(tmp_path, issues=(11, 12))


# ---------- 書き出しの取り込み（new sprint --absorb）と表の取り込みを合わせる（#1426 の決定 8） ----------


# ---------- 設計の結果の食い違いのメッセージ（#2361） ----------

HEAD_FMT = "#{n} の設計の結果が 2 本の設計文書で違う（先: {a} / 後: {b} ）"


def mismatch(old: dr.Row, new: dr.Row) -> str:
    res = dr.DesignResults(rows=[old])
    with pytest.raises(dr.DesignResultsError) as e:
        res.add([new])
    assert res.rows == [old]  # I1: 行を足さない
    return str(e.value)


def test_mismatch_of_kind_names_every_field_in_order():
    """受け入れ条件 1・4・5、I3・I4: 3 つとも違えば扱い → 取り込み先 → 触るファイルの順で先 / 後。空のパスは記録なしと出す。"""
    msg = mismatch(dr.Row(12, dr.IMPLEMENT, None, ["pkg/a.py"], DOC), dr.Row(12, dr.ABSORB, 11))
    assert msg == (
        HEAD_FMT.format(n=12, a=DOC, b="パスの記録なし")
        + "。扱い: 実装する / 取り込む。取り込み先: — / #11。触るファイル: 1 個 / 0 個（先にだけある: pkg/a.py ）"
    )


def test_mismatch_of_host_only():
    """受け入れ条件 2・4: 取り込み先だけが違えば、その項目だけが載り、扱いは載らない。"""
    msg = mismatch(dr.Row(13, dr.ABSORB, 11, doc=DOC), dr.Row(13, dr.ABSORB, 12, doc="issues/issue-12-design.md"))
    assert msg == HEAD_FMT.format(n=13, a=DOC, b="issues/issue-12-design.md") + "。取り込み先: #11 / #12"


def test_mismatch_of_files_shows_counts_and_the_difference():
    """受け入れ条件 3・4: 実例の 5 個と 18 個。個数と、各設計文書にしか無いファイルを書かれた順で出す。"""
    common = [f"pkg/c{i}.py" for i in range(4)]
    old_files = [*common, "pkg/old.py"]
    new_files = [f"pkg/n{i}.py" for i in range(14)] + common[::-1]
    a, b = "issues/issue-2335-design.md", "issues/issue-2336-design.md"
    msg = mismatch(dr.Row(2336, dr.IMPLEMENT, None, old_files, a), dr.Row(2336, dr.IMPLEMENT, None, new_files, b))
    news = ", ".join(f"pkg/n{i}.py" for i in range(14))
    assert msg == HEAD_FMT.format(n=2336, a=a, b=b) + f"。触るファイル: 5 個 / 18 個（先にだけある: pkg/old.py / 後にだけある: {news} ）"
    assert "扱い:" not in msg


def test_mismatch_counts_duplicated_files():
    """決定 2: 片方で 2 回書いたファイルは、多く書いた側にだけあるとして出す。"""
    msg = mismatch(dr.Row(11, dr.IMPLEMENT, None, ["pkg/a.py"], DOC), dr.Row(11, dr.IMPLEMENT, None, ["pkg/a.py", "pkg/a.py"], DOC))
    assert msg.endswith("。触るファイル: 1 個 / 2 個（後にだけある: pkg/a.py ）")


def test_same_results_in_another_order_do_not_stop():
    """受け入れ条件 6・I2: 触るファイルの順序だけが違う・すべて同じなら止まらず、行は 1 つのまま。"""
    res = dr.DesignResults(rows=[dr.Row(11, dr.IMPLEMENT, None, ["a", "b"], DOC)])
    res.add([dr.Row(11, dr.IMPLEMENT, None, ["b", "a"], "issues/issue-12-design.md")])
    res.add([dr.Row(11, dr.IMPLEMENT, None, ["a", "b"], DOC)])
    assert len(res.rows) == 1


# ---------- 分割した設計文書と触るファイルのセルの形（#2453） ----------


def table_of(*rows: str) -> str:
    return "# 設計\n\n## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n" + "\n".join(rows) + "\n"


D1, LOOP, FAIL = "issues/issue-1-design.md", "issues/issue-1-design-loop.md", "issues/issue-1-design-fail.md"
SPLIT = "# 分割した文書\n\n## ループの側\n\n本文\n"
ROW1 = "| #1 | 実装する | — | `pkg/a.py` |"


class Repo:
    """ベースブランチ（`base`: パス → 中身）と、課題ごとのマージした設計 PR が変えたパス（`changed`）を持つ gh と git の偽物。"""

    def __init__(self, base: dict[str, str], changed: dict[int, list[str]]):
        self.base, self.changed = base, changed

    def gh(self, args, stdin=None, cwd=None):
        if args[1] == "list":
            n = int(args[args.index("--head") + 1].split("-")[-1])
            return gh_call.GhResult(0, f"{100 + n}\n" if n in self.changed else "", "")
        return gh_call.GhResult(0, "\n".join(self.changed[int(args[2]) - 100]) + "\n", "")

    def run(self, cmd, **kw):
        sub = cmd[3]
        if sub == "ls-tree":
            out = "".join(f"{p}\0" for p in self.base)
        elif sub == "show":
            out = self.base[cmd[4].split(":", 1)[1]]
        else:
            out = ""
        return subprocess.CompletedProcess(cmd, 0, out, "")


def stage(tmp_path: Path, issues=(1,)) -> tuple[argparse.Namespace, dict[int, Path]]:
    paths = {n: tmp_path / f"5-impl-{n}.json" for n in issues}
    for n, p in paths.items():
        p.write_text(json.dumps(plan_of(n), ensure_ascii=False))
    manifest = tmp_path / "sprint.json"
    manifest.write_text(json.dumps({"ステージ": [{"name": "実装", "plans": [str(p) for p in paths.values()]}]}))
    return argparse.Namespace(manifest=str(manifest), base="develop", root=str(tmp_path), design=list(issues)), paths


def written(paths: dict[int, Path]) -> dict[int, dict]:
    return {n: json.loads(p.read_text()) for n, p in paths.items()}


def split_base(**extra: str) -> dict[str, str]:
    base = {D1: table_of(ROW1), LOOP: SPLIT, FAIL: SPLIT, "issues/issue-1-requirements.md": "# 要求\n", "issues/issue-99-design.md": SPLIT}
    base.update(extra)
    return base


def test_the_head_is_the_doc_with_the_table():
    """2453 受け入れ条件 5: 表を分割した文書が持てば、その文書が先頭で、表の無い先頭の名前の文書も載る。"""
    ndf = "issues/issue-1-design-ndf.md"
    res = dr.DesignResults(base_docs=[D1, ndf])
    res.add(dr.parse_results(table_of(ROW1), ndf))
    assert res.docs_of([1]) == [ndf, D1]


CELL_2436 = (
    "`plugins/ndf/scripts/parallel.py`、`plugins/ndf/scripts/lib/parallel_plan.py`、`plugins/ndf/scripts/lib/parallel_run.py`、"
    "`plugins/ndf/scripts/lib/parallel_release.py`、`plugins/ndf/scripts/lib/parallel_lane.py`、`plugins/ndf/scripts/lib/durable.py`、"
    "`plugins/ndf/scripts/relay_lib/hub.py`、`plugins/ndf/scripts/relay_lib/hub_client.py`、`plugins/ndf/scripts/relay_lib/__init__.py`、"
    "`plugins/ndf/scripts/supervise.py`、`plugins/ndf/scripts/supervise_lib/sprint.py`、`plugins/ndf/scripts/supervise_lib/sprint_waves.py`、"
    "`plugins/ndf/scripts/supervise_lib/release_templates.py`、`plugins/ndf/scripts/supervise_lib/queue_fill.py`、"
    "`plugins/ndf/scripts/supervise_lib/paths.py`、`plugins/ndf/scripts/tests/`、`plugins/ndf/contracts/forms/parallel-request.json`、"
    "`plugins/ndf/skills/issue-plan-strategy/`、`plugins/ndf/skills/development-workflow/`、`docs/glossary/glossary.json`、`docs/glossary.md`。"
    "ループの側の触るファイルは [issue-2436-design-lane-loop.md](issue-2436-design-lane-loop.md)、"
    "異常系の側は [issue-2436-design-failures.md](issue-2436-design-failures.md)、"
    "承認ゲート 1 の側は [issue-2436-design-approvals.md](issue-2436-design-approvals.md)、"
    "リリースの規則の側は [issue-2436-design-release-rule.md](issue-2436-design-release-rule.md) の「触るファイル」にある"
)


@pytest.mark.parametrize(
    "cell",
    [
        "`a.py`。ループの側は [issue-1-design-loop.md](issue-1-design-loop.md) にある",
        "`a.py`（[issue-1-design-loop.md](issue-1-design-loop.md)）",
        "`a.py`。残りは別の文書にある",
        "`a.py`, b.py",
        "`a.py`（新設",
        CELL_2436,
    ],
    ids=["link", "link-in-parens", "words", "bare-path", "unclosed", "issue-2436"],
)
def test_cells_out_of_the_form_stop(cell):
    """2453 受け入れ条件 10・11・14: リンク（括弧の中を含む）と括弧の外の語は形の誤りで、表の最後まで読んでから止まる。"""
    with pytest.raises(dr.DesignCellError) as e:
        dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |", f"| #2 | 実装する | — | {cell} |"), D1)
    assert [(p.issue, p.doc) for p in e.value.problems] == [(1, D1), (2, D1)]


@pytest.mark.parametrize(
    ("cell", "files"),
    [
        ("`a.py`（新設）、`b/`", ["a.py", "b/"]),
        ("`a.py`, `b.py`", ["a.py", "b.py"]),
        ("`app/(g)/p.tsx`（`c.py` は読まない）", ["app/(g)/p.tsx"]),
        ("—", []),
        ("", []),
    ],
    ids=["note", "comma", "parens-in-path", "dash", "empty"],
)
def test_cells_in_the_form_give_only_the_code_spans(cell, files):
    """2453 受け入れ条件 15・I4: 区切り・括弧の注記・空・— は止まらず、`files` は括弧の外のコードスパンの中身だけ。"""
    assert dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |"), D1)[0].files == files


def test_cells_of_absorbed_and_closed_rows_are_not_checked():
    """2453 受け入れ条件 16・I5: 「取り込む」「閉じる」の行のセルは読まない。"""
    rows = dr.parse_results(table_of(ROW1, "| #2 | 取り込む | #1 | 残りは [x](x.md) |", "| #3 | 閉じる | — | 語 |"), D1)
    assert [r.files for r in rows] == [["pkg/a.py"], [], []]


# ---------- 現状固定: 触るファイルのセルの読み方（_files の分割の前に固定する） ----------


@pytest.mark.parametrize(
    ("cell", "part"),
    [
        (
            "`a.py`。ループの側は [issue-1-design-loop.md](issue-1-design-loop.md) にある",
            "。ループの側は / issue-1-design-loop.md / にある",
        ),
        ("`a.py`（[l](x.md)）と語", "x.md / と語"),
        ("`a.py`, b.py", "b.py"),
        ("`a.py`（新設", "閉じていない括弧"),
        ("`a.py`）、`b.py`", "）"),
        ("`a.py` **太字** `b.py`", "** / 太字 / **"),
        ("`a.py`<br>`b.py`", "<br>"),
        ("語1　語2,語3、`d.py`", "語1 / 語2 / 語3"),
        ("[`x.py`](x.py)", "x.py"),
    ],
    ids=["link-and-words", "link-in-parens", "bare-path", "unclosed", "stray-close", "emphasis", "html", "separators", "code-in-link"],
)
def test_characterize_the_unreadable_parts_of_a_cell(cell, part):
    """現状固定: 読めなかった部分は出た順に ` / ` で結ばれ、行の課題と文書が添う。"""
    with pytest.raises(dr.DesignCellError) as e:
        dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |"), D1)
    assert [(p.issue, p.doc, p.part) for p in e.value.problems] == [(1, D1, part)]
    assert str(e.value) == f"#1（{D1}）の触るファイルのセルを読めない: {part}。{dr.CELL_FIX}"


@pytest.mark.parametrize(
    ("cell", "files"),
    [
        ("`a.py`（注（入れ子）記）`b.py`", ["a.py", "b.py"]),
        ("`  `、`c.py`", ["c.py"]),
        ("`a.py`（`b.py`）`c.py`", ["a.py", "c.py"]),
        ("` d.py `", ["d.py"]),
    ],
    ids=["nested-parens", "blank-code-span", "code-in-parens", "stripped"],
)
def test_characterize_the_files_of_a_cell(cell, files):
    """現状固定: 入れ子の括弧は深さで読み飛ばし、空のコードスパンは捨て、中身は前後の空白を落とす。"""
    assert dr.parse_results(table_of(f"| #1 | 実装する | — | {cell} |"), D1)[0].files == files


# ---------- ディレクトリの宣言を揃えて知らせる（#1811） ----------
