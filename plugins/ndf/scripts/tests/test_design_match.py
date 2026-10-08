"""design-match.py: 完了判定の設計との突き合わせの確認 (a) tests と確認 (b) specs（#1241）。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "design-match.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def write(root: Path, rel: str, text: str) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def repo(tmp_path, files: dict[str, str]) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


def commit(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "change")


def run(root, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)], capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    return p.returncode, out


def by_name(out) -> dict:
    return {it["name"]: it for it in out["items"]}


DESIGN = """# 設計

## 構成要素

| 要素 | 責務 |
| --- | --- |
| `tests/ci/test_ci_workflow.py` | 回帰テスト |
| `src/app.py` | 本体 |

## テスト設計

| 条件 | 振る舞い |
| --- | --- |
| 1 | `tests/test_app.py::test_runs` が通る |
| 2 | `test_pull_request_is_not_filtered` が落ちない |
| 3 | `tests/test_<名前>.py` の形で置く |

```text
`tests/in_fence/test_no.py`
```

> `tests/quoted/test_no.py`

## ドメインモデル

- `tests/elsewhere/test_not_picked.py`
"""


def test_tests_reports_missing_file_and_function_with_exit_1(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-216-design.md": DESIGN,
            "src/app.py": "x = 1\n",
            "tests/test_app.py": "def test_other():\n    pass\n",
        },
    )
    code, out = run(root, "tests", "--issue", "216")
    assert code == 1 and out["status"] == "stopped"
    got = by_name(out)
    # パスは `## 構成要素` にだけ書かれていても拾う（決定 2）
    assert got["tests/ci/test_ci_workflow.py"]["result"] == "missing" and got["tests/ci/test_ci_workflow.py"]["section"] == "構成要素"
    assert got["tests/test_app.py::test_runs"]["result"] == "missing"
    # 設計文書自身に名前があっても「ある」と読まない
    assert got["test_pull_request_is_not_filtered"]["result"] == "missing"
    # 本体のファイル・囲み・引用・拾わない節は判定しない。形の説明は skipped に数える
    assert "src/app.py" not in got and "tests/in_fence/test_no.py" not in got
    assert "tests/quoted/test_no.py" not in got and "tests/elsewhere/test_not_picked.py" not in got
    assert out["metrics"] == {"designs": 1, "names": 3, "missing": 3, "unverified": 0, "skipped": 1}


def test_tests_judges_against_head_not_the_worktree(tmp_path):
    root = repo(tmp_path, {"issues/issue-216-design.md": DESIGN})
    write(root, "tests/ci/test_ci_workflow.py", "def test_x():\n    pass\n")  # 作業ツリーにだけある
    code, out = run(root, "tests", "--issue", "216")
    assert code == 1 and by_name(out)["tests/ci/test_ci_workflow.py"]["result"] == "missing"


def test_tests_exit_0_when_all_names_are_present(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-216-design.md": DESIGN,
            "tests/ci/test_ci_workflow.py": "def test_pull_request_is_not_filtered():\n    pass\n",
            "tests/test_app.py": "def test_runs():\n    pass\n",
        },
    )
    code, out = run(root, "tests", "--issue", "216")
    assert code == 0 and out["status"] == "ok"
    assert {it["result"] for it in out["items"]} == {"present"} and out["metrics"]["names"] == 3


def test_tests_with_no_names_exits_0_and_counts_zero(tmp_path):
    root = repo(tmp_path, {"issues/issue-5-design-x.md": "# 設計\n\n## テスト設計\n\n散文だけで書く。\n"})
    code, out = run(root, "tests", "--issue", "5")
    assert code == 0 and out["metrics"]["names"] == 0 and out["metrics"]["designs"] == 1


def test_tests_reads_every_design_of_the_issue_including_decisions(tmp_path):
    root = repo(
        tmp_path,
        {
            "issues/issue-7-8-design.md": "# 設計\n\n## テスト設計\n\n- `tests/test_a.py`\n",
            "issues/issue-7-design-decisions.md": "# 決定\n\n## 決定の記録\n\n### 決定 1\n\n`tests/test_b.py` を作る\n",
            "issues/issue-70-design.md": "## テスト設計\n\n- `tests/test_c.py`\n",
            "tests/test_a.py": "",
        },
    )
    code, out = run(root, "tests", "--issue", "7")
    assert code == 1 and out["metrics"]["designs"] == 2
    assert {n: it["result"] for n, it in by_name(out).items()} == {"tests/test_a.py": "present", "tests/test_b.py": "missing"}


def test_tests_without_design_exits_3_and_judges_nothing(tmp_path):
    root = repo(tmp_path, {"README.md": "x\n"})
    code, out = run(root, "tests", "--issue", "9")
    assert code == 3 and out["items"] == [] and out["metrics"]["names"] == 0


def test_tests_unreadable_design_or_git_exits_2(tmp_path):
    root = repo(tmp_path, {"README.md": "x\n"})
    code, out = run(root, "tests", "--design", "issues/none.md")
    assert code == 2 and out["status"] == "stopped"
    outside = tmp_path / "plain"
    outside.mkdir()
    write(outside, "d.md", DESIGN)
    code, out = run(outside, "tests", "--design", "d.md")
    assert code == 2 and out["status"] == "stopped"


# --- specs ------------------------------------------------------------------------


def test_specs_lists_existing_specs_and_changelog_by_term_count(tmp_path):
    root = repo(
        tmp_path,
        {
            "containers/base/Dockerfile": "FROM x\n",
            "docs/specifications/base-image-shellcheck.md": "# shellcheck\n\n`containers/base/Dockerfile` は版を固定しない。\n\n"
            "SHELLCHECK_VERSION は使わない。\n",
            "docs/specifications/other.md": "# other\n\n`containers/base/Dockerfile` を読む。\n",
            "docs/specifications/unrelated.md": "# x\n\nSHELLCHECK_VERSIONS とは別。\n",
            "CHANGELOG.md": "# 変更履歴\n\n- containers/base/Dockerfile を変えた\n",
        },
    )
    git(root, "branch", "base")
    commit(root, {"containers/base/Dockerfile": "FROM x\nARG SHELLCHECK_VERSION=0.10.0\n"})
    code, out = run(root, "specs", "--base", "base")
    assert code == 0 and out["status"] == "ok"
    names = [it["name"] for it in out["items"]]
    assert names == ["docs/specifications/base-image-shellcheck.md", "CHANGELOG.md", "docs/specifications/other.md"]
    top = out["items"][0]
    assert top["terms"] == ["SHELLCHECK_VERSION", "containers/base/Dockerfile"] and top["lines"] == [3, 5]
    assert top["kind"] == "spec" and out["items"][1]["kind"] == "changelog" and top["changed_in_diff"] is False


def test_specs_skips_specs_added_in_the_same_diff(tmp_path):
    root = repo(tmp_path, {"src/run.py": "x = 1\n", "docs/specifications/old.md": "# old\n"})
    git(root, "branch", "base")
    commit(root, {"src/run.py": "def run_all():\n    pass\n", "docs/specifications/new.md": "`src/run.py` の run_all\n"})
    code, out = run(root, "specs", "--base", "base")
    assert code == 0 and out["items"] == [] and out["metrics"]["symbols"] == 1


def test_specs_caps_lines_per_document(tmp_path):
    spec = "".join(f"- src/a.py {i}\n" for i in range(15))
    root = repo(tmp_path, {"src/a.py": "", "docs/specifications/a.md": spec})
    git(root, "branch", "base")
    commit(root, {"src/a.py": "x = 1\n"})
    code, out = run(root, "specs", "--base", "base", "--max-lines", "3")
    assert code == 0 and out["items"][0]["lines"] == [1, 2, 3] and out["items"][0]["line_count"] == 15


def test_specs_unknown_base_exits_2(tmp_path):
    root = repo(tmp_path, {"README.md": "x\n"})
    code, out = run(root, "specs", "--base", "no-such-ref")
    assert code == 2 and out["status"] == "stopped"


def test_specs_finishes_within_10_seconds_on_70_specs_and_2500_terms(tmp_path):
    specs = {f"docs/specifications/s{i:02d}.md": "".join(f"- CONST_{i}_{j} と src/m{j}.py の記述\n" for j in range(300)) for i in range(70)}
    root = repo(tmp_path, {**specs, "src/consts.py": ""})
    git(root, "branch", "base")
    commit(root, {"src/consts.py": "".join(f"CONST_NAME_{k:04d} = {k}\n" for k in range(2500))})
    start = time.monotonic()
    code, out = run(root, "specs", "--base", "base")
    assert code == 0 and out["metrics"]["symbols"] == 2500
    assert time.monotonic() - start < 10
