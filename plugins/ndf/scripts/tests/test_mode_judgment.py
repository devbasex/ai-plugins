"""モードの判定の根拠の検査と、判定の材料の収集（lib/mode_judgment.py・mode-materials.py。#2923）。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import md  # noqa: E402
import mode_judgment as mj  # noqa: E402

_spec = importlib.util.spec_from_file_location("mode_materials", SCRIPTS / "mode-materials.py")
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)


def j(n, row, mode=None, condition="条件", files=("a/b.py",)):
    return {"number": n, "row": row, "mode": mode or mj.ROWS.get(row), "condition": condition, "files": list(files)}


# ---------------- I1・I2: 根拠の検査 ----------------


def test_check_judgments_returns_the_selected_issues_in_number_order():
    got = mj.check_judgments({"issues": [j(12, 5), j(11, 3)], "other": 1}, [11, 12])
    assert [x["number"] for x in got] == [11, 12] and got[0]["mode"] == "standard"


@pytest.mark.parametrize(
    "items, why",
    [
        ([j(11, 3)], "#12: 選んだ課題の判定の根拠が無い"),
        ([j(11, 3), j(12, 5), j(13, 5)], "#13: 選んでいない課題の根拠がある"),
        ([j(11, 0), j(12, 5)], "#11: 順（row）"),
        ([j(11, 6), j(12, 5)], "#11: 順（row）"),
        ([j(11, 3, "light"), j(12, 5)], "#11: 順 3 のモードは standard"),
        ([j(11, 3, condition=" "), j(12, 5)], "#11: 当たった条件の文"),
        ([j(11, 3, files=()), j(12, 5)], "#11: 触るファイル"),
        ([j(11, 3), j(11, 3), j(12, 5)], "#11: 根拠が 2 件ある"),
    ],
)
def test_check_judgments_refuses_each_broken_contract(items, why):
    with pytest.raises(mj.JudgmentError) as e:
        mj.check_judgments({"issues": items}, [11, 12])
    assert any(r.startswith(why) for r in e.value.reasons), e.value.reasons


def test_check_judgments_refuses_a_file_without_issues():
    with pytest.raises(mj.JudgmentError):
        mj.check_judgments([], [11])


# ---------------- I3・I8: スプリントのモード ----------------


def test_sprint_mode_takes_the_highest_row():
    assert mj.sprint_mode([j(11, 5), j(12, 3), j(13, 4)]) == "standard"
    assert mj.sprint_mode([j(11, 5)]) == "light"


def test_sprint_mode_refuses_documentation_mixed_with_other_modes_and_names_the_issues():
    with pytest.raises(mj.JudgmentError) as e:
        mj.sprint_mode([j(11, 2), j(12, 3)])
    assert "documentation: #11" in str(e.value) and "standard: #12" in str(e.value)
    assert mj.sprint_mode([j(11, 2), j(12, 2)]) == "documentation"


def test_sprint_mode_refuses_no_judgment():
    with pytest.raises(mj.JudgmentError):
        mj.sprint_mode([])


# ---------------- I4: 決め直し ----------------


def test_redecide_reads_only_the_kept_issues():
    assert mj.redecide([j(11, 3), j(12, 5)], [12], "standard") == "light"
    assert mj.redecide([j(11, 3), j(12, 3)], [12], "standard") == "standard"


def test_redecide_refuses_a_missing_judgment_and_a_higher_mode():
    with pytest.raises(mj.JudgmentError):
        mj.redecide([j(11, 3)], [12], "standard")
    with pytest.raises(mj.JudgmentError):
        mj.redecide([j(11, 3), j(12, 5)], [11], "light")


# ---------------- md.code_spans ----------------


def test_code_spans_skip_fences_and_include_table_cells():
    text = "# 題\n\n本文の `a/b.py` と\n\n```\n`c/d.py`\n```\n\n| x | y |\n| --- | --- |\n| `e/f.py` | 1 |\n"
    assert [(c.content, c.line) for c in md.code_spans(text)] == [("a/b.py", 2), ("e/f.py", 10)]


# ---------------- 受け入れ条件 2・3・I7: 判定の材料 ----------------

FILES = [
    "plugins/ndf/scripts/lib/sprint_loop.py",
    "plugins/ndf/scripts/tests/test_sprint_loop.py",
    "plugins/ndf/skills/development-workflow/SKILL.md",
    "README.md",
]

BODY = """## 種類

不具合

## 事象

`plugins/ndf/scripts/lib/sprint_loop.py:120` と `./plugins/ndf/skills/development-workflow/` を直す。
`--mode` と `cmd_sprint` と `https://example.com/a.py` と `#2923` と `uv run pytest` は候補にしない。
新しく `plugins/ndf/scripts/new-thing.py` を作る。[説明](README.md#節) を読む。

```text
cat plugins/ndf/scripts/lib/fenced.py
`plugins/ndf/scripts/lib/fenced2.py`
```

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。内部だけ |
"""


def test_collect_reads_candidates_tests_kind_and_public_interface():
    got = mm.issue_materials(7, BODY, FILES, [("issues/issue-7-design.md", ["plugins/ndf/scripts/lib/md.py", "README.md"])])
    paths = {f["path"]: f for f in got["files"]}
    assert list(paths) == [
        "plugins/ndf/scripts/lib/sprint_loop.py",
        "plugins/ndf/skills/development-workflow/",
        "plugins/ndf/scripts/new-thing.py",
        "README.md",
        "plugins/ndf/scripts/lib/md.py",
    ]
    assert paths["plugins/ndf/scripts/lib/sprint_loop.py"]["tests"] == ["plugins/ndf/scripts/tests/test_sprint_loop.py"]
    assert paths["plugins/ndf/scripts/lib/sprint_loop.py"]["section"] == "事象"
    assert paths["plugins/ndf/skills/development-workflow/"]["exists"] is True
    assert paths["plugins/ndf/scripts/new-thing.py"]["exists"] is False
    assert paths["README.md"]["source"] == "本文"  # 本文で先に見つけたものを残す
    assert paths["plugins/ndf/scripts/lib/md.py"] == {
        "path": "plugins/ndf/scripts/lib/md.py",
        "exists": False,
        "source": "設計",
        "section": "issues/issue-7-design.md",
        "tests": [],
    }
    assert got["kind"] == "不具合" and got["files_found"] == 5
    assert got["public_interface"] == {"value": "変わらない", "cell": "変わらない。内部だけ"}


def test_collect_shows_zero_candidates_without_guessing():
    got = mm.issue_materials(8, "## 種類\n\n機能\n\n触るのは sprint_loop のあたり。`--mode` を足す。\n", FILES, [])
    assert got["files"] == [] and got["files_found"] == 0
    assert got["public_interface"] == {"value": "記載なし", "cell": ""}


def test_test_file_itself_is_its_own_test():
    assert mm.tests_of("plugins/ndf/scripts/tests/test_sprint_loop.py", FILES, set(FILES)) == [
        "plugins/ndf/scripts/tests/test_sprint_loop.py"
    ]


def test_draft_leaves_the_row_to_the_conductor_and_keeps_existing_files():
    items = [mm.issue_materials(7, BODY, FILES, [])]
    d = mm.draft(items)["issues"][0]
    assert (d["number"], d["row"], d["mode"], d["condition"]) == (7, None, None, "")
    assert "plugins/ndf/scripts/new-thing.py" not in d["files"] and "README.md" in d["files"]
    with pytest.raises(mj.JudgmentError):  # 埋める前の下書きは loop sprint の検査を通らない
        mj.check_judgments(mm.draft(items), [7])


def test_cli_stops_with_gaps_when_the_issue_body_cannot_be_read(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    env = {"PATH": "/usr/bin:/bin", "GH_TOKEN": "x", "HOME": str(tmp_path)}
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "gh").write_text("#!/bin/sh\necho 'not found' >&2\nexit 1\n")
    (fake / "gh").chmod(0o755)
    env["PATH"] = f"{fake}:{env['PATH']}"
    out = tmp_path / "j.json"
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "mode-materials.py"), "--issue", "5", "--root", str(tmp_path), "--out", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )
    got = json.loads(p.stdout)
    assert p.returncode == 1 and got["status"] == "stopped" and got["gaps"][0]["name"] == "#5" and not out.exists()
