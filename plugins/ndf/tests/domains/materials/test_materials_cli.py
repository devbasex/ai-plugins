"""`ndf materials` の材料の組み立てと、本文を読めない課題・git の外の扱い（#3569）。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ndf.domains.materials import cli, collect

BODY = """## 種類

不具合

## 対象範囲

`src/app.py` と [設定](conf/app.toml) を直し、`src/new_mod.py` を足す。`--flag` と `$HOME` と `some word` は候補でない。

```text
`src/fenced.py`
```

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない |
"""
DESIGN = """# 設計

## 設計の結果

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #7 | 実装する | — | `lib/extra.py`、`src/app.py` |
| #8 | 実装する | — | `other.py` |
"""
FILES = ["src/app.py", "tests/test_app.py", "conf/app.toml", "lib/extra.py", "issues/issue-7-design.md"]


def test_issue_materials_reads_candidates_kind_and_interface(tmp_path):
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "issue-7-design.md").write_text(DESIGN, encoding="utf-8")
    designs = collect.design_files(tmp_path, 7, FILES)
    assert designs == [("issues/issue-7-design.md", ["lib/extra.py", "src/app.py"])]
    got = collect.issue_materials(7, BODY, FILES, designs)
    assert got["kind"] == "不具合"
    assert got["public_interface"] == {"value": "変わらない", "cell": "変わらない"}
    paths = {f["path"]: f for f in got["files"]}
    assert set(paths) == {"src/app.py", "conf/app.toml", "src/new_mod.py", "lib/extra.py"}
    assert paths["src/app.py"]["tests"] == ["tests/test_app.py"]
    assert paths["src/app.py"]["section"] == "対象範囲"
    assert paths["src/new_mod.py"]["exists"] is False
    assert paths["lib/extra.py"]["source"] == "設計"


def test_empty_body_has_no_candidates():
    got = collect.issue_materials(1, "", [], [])
    assert got == {"number": 1, "kind": None, "public_interface": {"value": "記載なし", "cell": ""}, "files": [], "files_found": 0}


def test_draft_leaves_row_mode_condition_empty(tmp_path):
    items = [collect.issue_materials(7, BODY, FILES, [])]
    out = tmp_path / "j" / "judgments.json"
    collect.write_draft(out, collect.draft(items))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["issues"][0]["row"] is None and data["issues"][0]["mode"] is None and data["issues"][0]["condition"] == ""
    assert data["issues"][0]["files"] == ["src/app.py", "conf/app.toml"]


def test_gather_puts_unreadable_issues_in_gaps(tmp_path):
    def body_of(n: int) -> str:
        if n == 2:
            raise ValueError("無い番号")
        return BODY

    found = collect.gather(tmp_path, [1, 2], FILES, body_of)
    assert [i["number"] for i in found.items] == [1]
    assert found.gaps[0]["name"] == "#2"


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    return path


def test_main_collects_and_writes_the_draft(tmp_path, monkeypatch, capsys, last_result):
    repo = _repo(tmp_path / "repo")
    monkeypatch.setattr(cli, "_body_reader", lambda _repo: lambda n: BODY)
    out = tmp_path / "draft.json"
    assert cli.main(["--issue", "7", "--root", str(repo), "--out", str(out)]) == 0
    got = last_result(capsys.readouterr().out)
    assert got["metrics"] == {"issues": 1, "gaps": 0, "out": str(out)}
    assert out.is_file()


def test_main_reports_gaps_as_incomplete(tmp_path, monkeypatch, capsys, last_result):
    repo = _repo(tmp_path / "repo")

    def reader(_repo):
        def body_of(n: int) -> str:
            raise ValueError("届かない")

        return body_of

    monkeypatch.setattr(cli, "_body_reader", reader)
    assert cli.main(["--issue", "7", "--root", str(repo)]) == 6
    got = last_result(capsys.readouterr().out)
    assert got["cause"]["kind"] == "incomplete"
    assert got["gaps"][0]["name"] == "#7"


def test_main_outside_git_is_a_precondition(tmp_path, capsys, last_result):
    assert cli.main(["--issue", "7", "--root", str(tmp_path)]) == 3
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "precondition"


def test_issue_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def _impact(*rows: str, after: str = "") -> str:
    table = "| 項目 | 内容 |\n| --- | --- |\n" + "".join(f"{r}\n" for r in rows)
    return f"# 題\n\n## 影響\n\n{table}{after}"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (_impact("| 公開インタフェース | 変わる（引数を足す） |"), {"value": "変わる", "cell": "変わる（引数を足す）"}),
        (_impact("| 公開インタフェース | 変わらない（内部だけ） |"), {"value": "変わらない", "cell": "変わらない（内部だけ）"}),
        (_impact("| 公開インタフェース | 未定 |"), {"value": "記載なし", "cell": "未定"}),
        (_impact("| ほか | 変わる |"), {"value": "記載なし", "cell": ""}),
        (_impact("|  公開インタフェース  |  変わる  |", "| 公開インタフェース | 変わらない |"), {"value": "変わる", "cell": "変わる"}),
        # 「影響」のセクションが無ければ、ほかの表に行があっても読まない
        ("# 題\n\n## 背景\n\n| 項目 | 内容 |\n| --- | --- |\n| 公開インタフェース | 変わる |\n", {"value": "記載なし", "cell": ""}),
        # 下の水準の見出しはセクションに含み、同じ水準の次の見出しから後は含まない
        ("## 影響\n\n### 内訳\n\n| a | b |\n| --- | --- |\n| 公開インタフェース | 変わる |\n", {"value": "変わる", "cell": "変わる"}),
        ("## 影響\n\nなし\n\n## 次\n\n| a | b |\n| --- | --- |\n| 公開インタフェース | 変わる |\n", {"value": "記載なし", "cell": ""}),
        # セクションの中の 2 つ目の表も読む。コードの囲みの中の表は読まない
        (
            "## 影響\n\n```\n| 公開インタフェース | 変わる |\n```\n\n| a | b |\n| --- | --- |\n| x | y |\n\n"
            "| a | b |\n| --- | --- |\n| 公開インタフェース | 変わらない |\n",
            {"value": "変わらない", "cell": "変わらない"},
        ),
        ("", {"value": "記載なし", "cell": ""}),
    ],
)
def test_public_interface_reads_the_impact_table(body, expected):
    """現状固定（I-026）: 「影響」のセクションの表の `公開インタフェース` の行の読み方。"""
    assert collect.public_interface(body) == expected
