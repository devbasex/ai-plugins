"""引継ぎ文書のスクリプト（`handoff.py`・`lib/handoff_doc.py`、#1560）。

一時の git リポジトリ（メインディレクトリと worktree）で打ち、置き場・名の形・節の形・行数・一致の規則を縛る。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "handoff.py"
REPO_ROOT = SCRIPTS.parents[2]
sys.path.insert(0, str(SCRIPTS / "lib"))
import handoff_doc  # noqa: E402

TEMPLATE = (SCRIPTS / "data" / "handoff-template.md").read_text()


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def run(root: Path, *args: str, stdin: str = "") -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--root", str(root)], input=stdin, capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


@pytest.fixture
def main(tmp_path) -> Path:
    m = tmp_path / "main"
    m.mkdir()
    git(m, "init", "-q")
    git(m, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "init")
    (m / ".ndf").mkdir()
    return m


def body_of(main: Path, name: str) -> Path:
    return main / ".ndf" / "handoff" / f"{name}.md"


@pytest.mark.parametrize("name", ["issue-1560", "milestone-26", "sprint-m1142c"])
def test_init_creates_body_from_template(main, name):
    code, out = run(main, "init", name, "--title", "題")
    assert code == 0 and out["items"] == [{"path": str(body_of(main, name)), "created": True}]
    assert body_of(main, name).read_text() == TEMPLATE.replace("{title}", "題")


@pytest.mark.parametrize("name", ["handoff", "issue-x", "../a", "milestone-26-history"])
def test_init_rejects_bad_names(main, name):
    code, _ = run(main, "init", name, "--title", "題")
    assert code == 2 and not (main / ".ndf" / "handoff").exists()


def test_init_from_worktree_writes_to_main(main):
    wt = main / ".worktrees" / "feat"
    assert git(main, "worktree", "add", "-q", "-b", "feat", str(wt)).returncode == 0
    (wt / ".ndf").mkdir()
    code, out = run(wt, "init", "issue-1", "--title", "題")
    assert code == 0 and body_of(main, "issue-1").is_file() and not (wt / ".ndf" / "handoff").exists()
    code, out = run(wt, "path", ".ndf/handoff/issue-1.md", "--exists")
    assert code == 0 and out["items"][0]["path"] == str(body_of(main, "issue-1"))


def test_body_is_untracked_and_not_ignored(main):
    run(main, "init", "issue-1", "--title", "題")
    assert "?? .ndf/" in git(main, "status", "--short").stdout
    assert git(main, "check-ignore", ".ndf/handoff/issue-1.md").returncode == 1


def test_no_git_or_no_ndf_is_precondition(tmp_path, main):
    (tmp_path / "plain").mkdir()
    assert run(tmp_path / "plain", "init", "issue-1", "--title", "題")[0] == 3
    (main / ".ndf").rmdir()
    assert run(main, "init", "issue-1", "--title", "題")[0] == 3


def test_init_keeps_existing_body(main):
    run(main, "init", "issue-1", "--title", "題")
    body_of(main, "issue-1").write_text("既存\n")
    code, out = run(main, "init", "issue-1", "--title", "別")
    assert code == 0 and out["items"][0]["created"] is False and body_of(main, "issue-1").read_text() == "既存\n"


def test_next_places_command_verbatim_once(main):
    run(main, "init", "issue-1", "--title", "題")
    cmd = "/goal /ndf:development-workflow .ndf/handoff/issue-1.md の続きから\n  2 行目  "
    for _ in range(2):
        code, out = run(main, "next", "issue-1", stdin=f"\n\n{cmd}\n\n")
        assert code == 0
    text = body_of(main, "issue-1").read_text()
    block = out["items"][0]["block"]
    assert block == f"```ndf-next\n{cmd}\n```\n" and text.count("## 次に実行するコマンド") == 1
    _, start, end, _ = handoff_doc.find_section(text, "次に実行するコマンド")
    assert text[start:end].strip("\n") == block.strip("\n") and out["items"][0]["changed"] is False


def test_next_without_section_keeps_doc(main):
    run(main, "init", "issue-1", "--title", "題")
    body_of(main, "issue-1").write_text("# 題\n\n## 現在地\n")
    assert run(main, "next", "issue-1", stdin="x\n")[0] == 1
    assert body_of(main, "issue-1").read_text() == "# 題\n\n## 現在地\n"


def test_check_passes_template_and_optional_sections_can_go(main):
    run(main, "init", "issue-1", "--title", "題")
    assert run(main, "check", "issue-1")[0] == 0
    text = body_of(main, "issue-1").read_text()
    for word in ("今の会話の進み", "運用の知見"):
        head, _, end, _ = handoff_doc.find_section(text, word)
        text = text[:head] + text[end:]
    body_of(main, "issue-1").write_text(text.replace("## 現在地", "## 現在地（2026-09-30 セッション 42）"))
    assert run(main, "check", "issue-1")[0] == 0


@pytest.mark.parametrize(
    "edit, kind",
    [
        (lambda t: t.replace("## 課題の順\n", ""), "missing"),
        (lambda t: t.replace("## 課題の順", "## 次にやること", 1).replace("## 次にやること\n\n## 今の", "## 課題の順\n\n## 今の"), "order"),
        (lambda t: t + "\n## 現在地\n", "duplicate"),
        (lambda t: t + "\n## 雑記\n", "unknown"),
    ],
)
def test_check_reports_shape_violations(main, edit, kind):
    run(main, "init", "issue-1", "--title", "題")
    body_of(main, "issue-1").write_text(edit(body_of(main, "issue-1").read_text()))
    code, out = run(main, "check", "issue-1")
    assert code == 1 and kind in [v["kind"] for v in out["items"][0]["violations"]]


def test_check_line_limit_and_trim_moves_demoted_section(main):
    run(main, "init", "issue-1", "--title", "題")
    text = body_of(main, "issue-1").read_text()
    long = text.replace("## 課題の順\n", "## 課題の順\n" + "- 行\n" * 290)
    body_of(main, "issue-1").write_text(long)
    code, out = run(main, "check", "issue-1")
    assert code == 5 and out["items"][0]["lines"] > 300
    history = main / ".ndf" / "handoff" / "issue-1-history.md"
    history.write_text("# 履歴\n\n古い\n")
    demoted = long.replace("## 運用の知見", "## 前の会話の進み（1）\n\n| 計画 |\n\n## 運用の知見")
    body_of(main, "issue-1").write_text(demoted)
    code, out = run(main, "check", "issue-1", "--trim")
    assert out["items"][0]["moved"] == ["前の会話の進み（1）"] and code == 5
    assert "前の会話の進み" not in body_of(main, "issue-1").read_text()
    assert history.read_text() == "# 履歴\n\n古い\n\n## 前の会話の進み（1）\n\n| 計画 |\n"
    assert run(main, "check", "issue-1", "--max-lines", "400")[0] == 0


@pytest.mark.parametrize(
    "text, names, code, hits",
    [
        ("#1560 を進める", ["issue-1560", "milestone-26"], 0, ["issue-1560"]),
        ("#15 を進める", ["issue-1560"], 1, []),
        ("マイルストーン 26 を進める", ["milestone-2", "milestone-26"], 0, ["milestone-26"]),
        ("Milestone26", ["milestone-26"], 0, ["milestone-26"]),
        ("m1142c の続き", ["sprint-m1142c", "sprint-m1142"], 0, ["sprint-m1142c"]),
        ("マイルストーン 26 の #1560", ["issue-1560", "milestone-26"], 4, ["issue-1560", "milestone-26"]),
    ],
)
def test_find_match_rules(main, text, names, code, hits):
    for n in names:
        run(main, "init", n, "--title", n)
    (main / ".ndf" / "handoff" / "issue-1560-history.md").write_text("#1560\n")
    got, out = run(main, "find", "--text", text)
    assert got == code and [Path(i["path"]).stem for i in out["items"]] == hits


def test_path_exists_does_not_guess(main):
    run(main, "init", "issue-15", "--title", "題")
    code, out = run(main, "path", "issue-1560", "--exists")
    assert code == 1 and out["items"][0]["path"] == str(body_of(main, "issue-1560"))
    assert run(main, "path", "docs/issue-15.md")[0] == 2


def test_remove_deletes_body_and_history(main):
    run(main, "init", "issue-1", "--title", "題")
    history = main / ".ndf" / "handoff" / "issue-1-history.md"
    history.write_text("x\n")
    code, out = run(main, "remove", "issue-1")
    assert code == 0 and [i["path"] for i in out["items"]] == [str(body_of(main, "issue-1")), str(history)]
    assert not body_of(main, "issue-1").exists() and not history.exists()
    assert run(main, "remove", "../../x")[0] == 2


def test_untracked_body_does_not_fail_local_doc_checks(main):
    """受け入れ条件 4: 手元の検査は `.ndf/handoff/` の追跡外の文書を理由に落ちない。"""
    (main / "README.md").write_text("# 題\n")
    (main / "CHANGELOG.md").write_text("- 行\n" * 501)  # check-doc-line-limit.py の除外が指す先
    git(main, "add", "README.md", "CHANGELOG.md")
    git(main, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "readme")
    run(main, "init", "issue-1", "--title", "題")
    body_of(main, "issue-1").write_text("[切れ](nowhere.md)\n" + "- 行\n" * 501)
    for check in ("check-markdown-links.py", "check-doc-line-limit.py"):
        p = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / check), "--root", str(main)], capture_output=True, text=True)
        assert p.returncode == 0, (check, p.stdout[-500:], p.stderr[-500:])
