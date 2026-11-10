"""design-survey.py: 自前で作る決定に外部の調査で比べた候補があるかと、人へ残す判断がレッドラインを名指すかの検査。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "design-survey.py"
REPO = Path(__file__).resolve().parents[4]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def repo(tmp_path, files: dict[str, str]) -> Path:
    root = tmp_path / "r"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def run(root, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), "check", *args, "--root", str(root)], capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    return p.returncode, out


def rules(out) -> list[str]:
    return sorted(it["rule"] for it in out["items"])


SURVEY = """## 外部の調査

| 種類 | 調べるか | 理由 |
| --- | --- | --- |
| ライブラリとサービス | 調べる | 部品がある |

### ライブラリとサービス

| # | 候補 | 出典 |
| --- | --- | --- |
| LIB1 | foo | https://example.com/foo （2026-11-05 取得） |
"""

SKIP = """## 外部の調査

省く: 既存の形の延長で、外部の判断が要らない
"""


def decision(n: int, method: str | None, cands: str | None) -> str:
    lines = [f"### 決定 {n}: 何かのため、何かを決める", "", "理由。", ""]
    if method is not None:
        lines.append(f"- 作り方: {method}")
    if cands is not None:
        lines.append(f"- 比べた候補: {cands}")
    return "\n".join(lines) + "\n\n根拠: Value 7（MVV 版 5）\n"


def design(*parts: str) -> str:
    return "# 設計\n\n" + "\n".join(parts)


def decisions(*ds: str) -> str:
    return "## 決定の記録\n\n" + "\n".join(ds)


def test_self_built_without_candidates_is_flagged(tmp_path):
    root = repo(tmp_path, {"issues/issue-1-design.md": design(SURVEY, decisions(decision(1, "自前で作る", None)))})
    code, out = run(root, "--issue", "1")
    assert code == 1
    assert rules(out) == ["no_candidates"]
    assert out["metrics"]["self_built"] == 1


def test_candidates_resolved_or_survey_skipped_pass(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-1-design.md": design(SURVEY, decisions(decision(1, "自前で作る", "LIB1"))),
            "issues/issue-2-design.md": design(SKIP, decisions(decision(1, "自前で作る", None))),
        },
    )
    assert run(root, "--issue", "1")[0] == 0
    assert run(root, "--issue", "2")[0] == 0


def test_split_files_share_survey_and_skip(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-3-design.md": design(SKIP),
            "issues/issue-3-design-decisions.md": design(decisions(decision(1, "自前で作る", None))),
            "issues/issue-4-design.md": design(SURVEY),
            "issues/issue-4-design-decisions.md": design(decisions(decision(1, "自前で作る", "LIB1"))),
        },
    )
    assert run(root, "--issue", "3")[0] == 0
    assert run(root, "--issue", "4")[0] == 0


@pytest.mark.parametrize("issues", [["7"], ["8"], ["7", "8"]])
def test_multi_number_docs_form_one_group(tmp_path, issues):
    root = repo(
        tmp_path,
        {
            "issues/issue-7-design.md": design(SKIP),
            "issues/issue-7-8-design.md": design(decisions(decision(1, "自前で作る", None))),
            "issues/issue-8-design.md": design("## 概要\n\n本文\n"),
        },
    )
    code, out = run(root, "--issue", *issues)
    assert code == 0, out
    assert out["metrics"]["docs"] == 3
    assert out["items"] == []


@pytest.mark.parametrize("issues", [["7"], ["8"], ["7", "8"]])
def test_candidates_resolve_across_the_group(tmp_path, issues):
    survey8 = SURVEY.replace("LIB1", "LIB2")
    root = repo(
        tmp_path,
        {
            "issues/issue-7-design.md": design(SURVEY, decisions(decision(1, "自前で作る", "LIB2"))),
            "issues/issue-7-8-design.md": design(decisions(decision(2, "自前で作る", "LIB1"))),
            "issues/issue-8-design.md": design(survey8),
        },
    )
    code, out = run(root, "--issue", *issues)
    assert code == 0, out
    assert out["items"] == []


def test_candidates_do_not_resolve_in_another_group(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-5-design.md": design(
                "## 外部の調査\n\n| # | 候補 |\n| --- | --- |\n| STD1 | x |\n", decisions(decision(1, "自前で作る", "LIB1"))
            ),  # fmt: skip
            "issues/issue-6-design.md": design(SURVEY),
        },
    )
    code, out = run(root, "--issue", "5", "6")
    assert code == 1
    assert rules(out) == ["unknown_candidate"]


def test_unknown_method_and_combined_method(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-1-design.md": design(SURVEY, decisions(decision(1, "自前でつくる", "LIB1"))),
            "issues/issue-2-design.md": design(SURVEY, decisions(decision(1, "自前で作る・標準に倣う", None))),
        },
    )
    code, out = run(root, "--issue", "1")
    assert code == 1 and rules(out) == ["unknown_method"]
    code, out = run(root, "--issue", "2")
    assert code == 1 and rules(out) == ["no_candidates"]


def test_code_blocks_are_not_read(tmp_path):
    body = design(SURVEY, decisions("### 決定 1: 例を示す\n\n```markdown\n- 作り方: 自前で作る\n```\n"))
    root = repo(tmp_path, {"issues/issue-1-design.md": body})
    code, out = run(root, "--issue", "1")
    assert code == 0, out
    assert out["metrics"]["with_method"] == 0


def test_file_without_issue_number_is_its_own_group(tmp_path):
    root = repo(tmp_path, {"notes/run.md": design(decisions(decision(1, "自前で作る", None)))})
    code, out = run(root, "--file", "notes/run.md")
    assert code == 1 and rules(out) == ["no_candidates"]


def test_no_documents_for_issue_returns_3(tmp_path):
    root = repo(tmp_path, {"README.md": "x\n"})
    assert run(root, "--issue", "99")[0] == 3


def test_works_without_ndf_declarations(tmp_path):
    """プロジェクトの MVV の宣言（`.ndf/`）が無くても同じ結果になる（受け入れ条件 9）。"""
    files = {"issues/issue-1-design.md": design(SURVEY, decisions(decision(1, "自前で作る", None)))}
    bare = repo(tmp_path / "a", files)
    with_mvv = repo(tmp_path / "b", {**files, ".ndf/mvv.md": "# MVV\n\n## Value\n\n1. 調べる\n"})
    assert run(bare, "--issue", "1")[1]["items"] == run(with_mvv, "--issue", "1")[1]["items"]


def test_existing_issue_documents_pass():
    """このリポジトリの既存の設計文書と受け入れ条件のファイルで、外部の調査の規則が落ちない（受け入れ条件 10）。

    unnamed_redline は規則より前に書いた文書（判断を承認ゲートへ残す書き方だった）を指摘するため、ここでは数えない。
    """
    files = [f for f in git(REPO, "ls-files", "issues/").splitlines() if f.endswith(".md")]
    assert files
    _, out = run(REPO, "--file", *files)
    survey = [it for it in out["items"] if it["rule"] != "unnamed_redline"]
    assert survey == [], survey[:5]


def test_this_design_document_passes():
    """規則を定めた設計文書（#2912）と要求のコピーは、委ねる語を説明していても指摘されない。"""
    files = ["issues/issue-2912-design.md", "issues/issue-2912-requirements.md"]
    if not all((REPO / f).exists() for f in files):
        pytest.skip("#2912 の設計文書が無い")
    code, out = run(REPO, "--file", *files)
    assert code == 0, out["items"]


def _load_survey():
    import importlib.util

    spec = importlib.util.spec_from_file_location("design_survey_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_judge_group_items_and_counts_are_fixed():
    """judge_group の指摘の名前・規則・行の文と、数えた値を固定する（I-004 の現状固定）。"""
    mod = _load_survey()
    doc = mod.Doc(
        "issues/issue-1-design.md",
        design(
            SURVEY,
            decisions(
                decision(1, "自前で作る", None),
                decision(2, "自前で作る", "LIB1・LIB9"),
                decision(3, "手で書く", None),
                decision(4, None, "PRD2"),
                decision(5, "ライブラリやサービスを使う", "LIB1"),
            ),
        ),
    )
    items, counts = mod.judge_group([doc])
    assert counts == {"decisions": 5, "with_method": 4, "self_built": 2, "skipped": 0}
    got = [(it["rule"], it["name"].split(" 決定 ")[1], it["text"]) for it in items]
    assert got == [
        ("no_candidates", "1", "- 作り方: 自前で作る"),
        ("unknown_candidate", "2", "- 比べた候補: LIB1・LIB9"),
        ("unknown_method", "3", "- 作り方: 手で書く"),
        ("unknown_candidate", "4", "- 比べた候補: PRD2"),
    ]
    doc_lines = design(SURVEY, decisions(decision(1, "自前で作る", None))).splitlines()
    first = items[0]["name"]
    path, rest = first.split(":", 1)
    assert path == "issues/issue-1-design.md"
    line_no = int(rest.split(" ")[0])
    assert doc_lines[line_no - 1] == "- 作り方: 自前で作る"


def test_judge_group_skip_and_group_wide_candidates_are_fixed():
    """省いたまとまりは no_candidates を出さず、候補は同じまとまりの別の文書から解決する。"""
    mod = _load_survey()
    skip_doc = mod.Doc("issues/issue-2-design.md", design(SKIP))
    dec_doc = mod.Doc("issues/issue-2-design-decisions.md", design(decisions(decision(1, "自前で作る", None))))
    items, counts = mod.judge_group([skip_doc, dec_doc])
    assert items == []
    assert counts == {"decisions": 1, "with_method": 1, "self_built": 1, "skipped": 1}

    survey_doc = mod.Doc("issues/issue-3-design.md", design(SURVEY))
    other = mod.Doc("issues/issue-3-design-decisions.md", design(decisions(decision(1, "自前で作る / 標準に倣う", "LIB1"))))
    items, counts = mod.judge_group([survey_doc, other])
    assert items == []
    assert counts == {"decisions": 1, "with_method": 1, "self_built": 1, "skipped": 0}


def test_judge_group_empty_method_value_is_unknown_method():
    mod = _load_survey()
    doc = mod.Doc("x.md", design(SURVEY, decisions(decision(1, "", None))))
    items, counts = mod.judge_group([doc])
    assert [it["rule"] for it in items] == ["unknown_method"]
    assert counts == {"decisions": 1, "with_method": 1, "self_built": 0, "skipped": 0}


# --- unnamed_redline（人へ残す判断がレッドラインを名指すか） ---


def _delegate_words() -> list[str]:
    """委ねる語の一覧（design-survey.py の DELEGATE）から、テストの入力の言い回しを組む。"""
    mod = _load_survey()
    words = ["利用者が決める", "利用者が選ぶ", "利用者が確かめる", "設計承認ゲートで人が決める", "人へ委ねる"]
    got = [w for w in words if mod.DELEGATE.search(w)]
    assert got, "委ねる語の一覧が空"
    return got


def left(*rows: str) -> str:
    head = "## 人へ残す判断\n\n| 判断 | 当たるレッドライン | なぜ当たるか |\n| --- | --- | --- |\n"
    return head + "".join(r + "\n" for r in rows)


def unnamed(out) -> list[str]:
    return [it["text"] for it in out["items"] if it["rule"] == "unnamed_redline"]


@pytest.mark.parametrize("word", _delegate_words())
def test_delegating_line_without_redline_is_flagged(tmp_path, word):
    line = f"既定値を 3 にするかは{word}。"
    body = design(decisions(f"### 決定 1: 既定値を決める\n\n{line}\n"))
    root = repo(tmp_path, {"issues/issue-1-design.md": body})
    code, out = run(root, "--file", "issues/issue-1-design.md")
    assert code == 1
    assert unnamed(out) == [line]
    name = out["items"][0]["name"]
    assert body.splitlines()[int(name.rsplit(":", 1)[1]) - 1] == line
    assert out["metrics"]["unnamed_redline"] == 1


@pytest.mark.parametrize(
    "title", ["## 未確認のまま残ること", "## 人へ残す判断", "## 決定の記録\n\n### 決定 2: 何かを決める"]
)
def test_delegating_line_is_flagged_in_each_section(tmp_path, title):
    body = design(f"{title}\n\n| 項目 | 内容 |\n| --- | --- |\n| 既定値 | 利用者が決める |\n")
    root = repo(tmp_path, {"notes/d.md": body})
    code, out = run(root, "--file", "notes/d.md")
    assert code == 1
    assert len(unnamed(out)) == 1


def test_left_table_rows_need_number_and_reason(tmp_path):
    body = design(
        left(
            "| `.ndf/pace.json` へ 1 行足すか | C7 | 利用者の設定を書き換える |",
            "| 既定値を 3 にするか | 無し | 何となく |",
            "| 鍵を入れ替えるか | C1 |  |",
        )
    )
    root = repo(tmp_path, {"notes/d.md": body})
    code, out = run(root, "--file", "notes/d.md")
    assert code == 1
    assert unnamed(out) == ["| 既定値を 3 にするか | 無し | 何となく |", "| 鍵を入れ替えるか | C1 |  |"]


@pytest.mark.parametrize(
    "body",
    [
        design(decisions("### 決定 1: 何かを決める\n\n既定値は 3 にする。理由。\n"), "## 人へ残す判断\n\n無し\n"),
        design(decisions("### 決定 1: 何かを決める\n\n`.ndf/` を変えるかは利用者が決める（C7）。\n")),
        design(left("| 秘密を入れ替えるか | C1・P1 | 鍵に触れる |")),
        design("## 起票者の推定\n\n| # | 条件 |\n| --- | --- |\n| S1 | 設計承認ゲートで利用者が確かめる |\n"),
        design(decisions("### 決定 1: 語を定める\n\n委ねる語は `利用者が決める` などである。\n")),
        design(decisions("### 決定 1: 例を示す\n\n```markdown\n既定値は利用者が決める\n```\n")),
        design(decisions("### 決定 1: 経緯\n\nこれは承認ゲートの答えで利用者が決めた。\n")),
    ],
    ids=["no-delegation", "named-line", "named-table", "outside-section", "inline-code", "code-block", "past-record"],
)
def test_documents_without_unnamed_delegation_pass(tmp_path, body):
    root = repo(tmp_path, {"notes/d.md": body})
    code, out = run(root, "--file", "notes/d.md")
    assert code == 0, out["items"]
    assert out["metrics"]["unnamed_redline"] == 0


def test_unnamed_redline_is_judged_per_document(tmp_path):
    """番号を共有するまとまりでも、名指しの有無は文書ごとに判定する。"""
    root = repo(
        tmp_path,
        {
            "issues/issue-1-design.md": design(left("| `.ndf/` を変えるか | C7 | 設定を書き換える |")),
            "issues/issue-1-design-decisions.md": design(decisions("### 決定 1: x\n\n範囲は利用者が選ぶ。\n")),
        },
    )
    code, out = run(root, "--issue", "1")
    assert code == 1
    assert [it["name"].split(":")[0] for it in out["items"]] == ["issues/issue-1-design-decisions.md"]
