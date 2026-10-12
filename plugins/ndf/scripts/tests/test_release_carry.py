"""release-steps.py approval-facts: 出し直しで承認資料の手で足した節を残し、指示書の変更の節を書く（#1834）。

test_release_included.py の一時の git リポジトリ（origin は bare、ベースブランチは trunk）を使う。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from release_lib import carry  # noqa: E402
from test_release_included import VER, git, load, make_world  # noqa: E402

OWN = ("## 1. 対象を開くためのもの", "## 2. 承認の判断に使うもの", "## 指示書の変更", "## 同意を求めること", "## 戻し方")
EXTRA = "## 補足\n\n- 手で書いた補足\n  - 子の行\n\n"
RISK = "## 未検証・残る危険\n\n- 手で書いた危険\n\n"


@pytest.fixture
def world(tmp_path: Path) -> dict:
    return make_world(tmp_path)


def facts(mod, w: dict, approval=None):
    with pytest.raises(SystemExit) as e:
        mod.cmd_approval_facts(
            argparse.Namespace(root=str(w["work"]), version=VER, prs=[11], prev_tag=None, plugin=None, approval=approval)
        )
    obj, code = e.value.code
    return obj, code


def write_back(w: dict, obj) -> Path:
    """監督の写し（presentation_to）の代わりに、提示物を作業場所の承認資料へ写す。"""
    dst = w["work"] / "issues" / "approval.md"
    dst.parent.mkdir(exist_ok=True)
    dst.write_bytes(Path(obj["presentation_path"]).read_bytes())
    return dst


def headings(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.startswith("## ")]


def insert_after(text: str, heading: str, block: str) -> str:
    """heading の節の次の `## ` の前へ block を差し込む。"""
    lines = text.split("\n")
    at = lines.index(heading)
    nxt = next(i for i in range(at + 1, len(lines)) if lines[i].startswith("## "))
    return "\n".join(lines[:nxt]) + "\n" + block + "\n".join(lines[nxt:])


def test_hand_sections_survive_two_rebuilds(monkeypatch, world):
    """受け入れ条件 1・2: 手で足した節と手で書いた残る危険は、2 回作り直しても見出しも中身も同じ位置に残る。"""
    mod = load(monkeypatch, world)
    obj, code = facts(mod, world)
    assert code == mod.EXIT_GATE
    path = write_back(world, obj)
    text = insert_after(path.read_text(encoding="utf-8"), "## 指示書の変更", RISK + EXTRA)
    text += "## 戻した後の確認\n\n- 手の最後の節\n"
    path.write_text(text, encoding="utf-8")
    for _ in range(2):
        obj, code = facts(mod, world, "issues/approval.md")
        assert code == mod.EXIT_GATE
        out = Path(obj["presentation_path"]).read_text(encoding="utf-8")
        path.write_text(out, encoding="utf-8")
    assert (RISK + EXTRA) in out and "## 戻した後の確認\n\n- 手の最後の節\n" in out
    assert headings(out) == [
        "## 1. 対象を開くためのもの",
        "## 2. 承認の判断に使うもの",
        "## 指示書の変更",
        "## 未検証・残る危険",
        "## 補足",
        "## 同意を求めること",
        "## 戻し方",
        "## 戻した後の確認",
    ]
    assert obj["metrics"]["kept"] == ["## 未検証・残る危険", "## 補足", "## 戻した後の確認"]


def test_sections_whose_anchor_is_gone_go_before_consent():
    """受け入れ条件 2: 直前の自分の節が作り直しで無くなった節は、同意の節の前へ置く。"""
    old = ["# t", "", "## 1. 対象を開くためのもの", "", "- u", "", "## 消えた節", "", "## 補足", "", "- x", ""]
    keep = carry.kept(old, (*OWN, "## 消えた節"))
    new = ["# t", "", "## 1. 対象を開くためのもの", "", "- u", "", "## 同意を求めること", "", "- [ ] c", ""]
    keep = [carry.Kept(k.heading, k.lines, "## 消えた節") for k in keep]
    assert carry.merge(new, keep) == [*new[:6], "## 補足", "", "- x", "", *new[6:]]


def test_without_an_approval_the_sections_are_the_same_plus_instructions(monkeypatch, world):
    """受け入れ条件 3・7: 資料が無ければ今の節の構成に「指示書の変更」が加わるだけ。触れていなければ「- 無し」。"""
    mod = load(monkeypatch, world)
    obj, code = facts(mod, world, "issues/missing.md")
    out = Path(obj["presentation_path"]).read_text(encoding="utf-8")
    assert code == mod.EXIT_GATE and headings(out) == list(OWN)
    assert "## 指示書の変更\n\n- 無し\n\n## 同意を求めること" in out
    assert obj["metrics"]["instructions"] == 0 and obj["metrics"]["kept"] == []


@pytest.mark.parametrize("bad", ["## 補足\n\n- x\n", "# t\n\n## 補足\n\n- a\n\n## 補足\n\n- b\n"])
def test_unreadable_approval_writes_nothing(monkeypatch, world, bad):
    """I3: H1 が無い・同じ見出しが 2 つある資料では書かずに 2 で止め、資料のバイトは変わらない。"""
    mod = load(monkeypatch, world)
    path = world["work"] / "issues" / "approval.md"
    path.parent.mkdir()
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(mod.StepError) as e:
        facts(mod, world, "issues/approval.md")
    assert e.value.code == 2
    assert path.read_text(encoding="utf-8") == bad
    assert not world["present"].exists() or not list(world["present"].iterdir())


def commit_instructions(seed: Path, n: int, name: str, squash: bool = False) -> None:
    """指示書 name を変える PR #n を trunk へ入れる（merge commit か squash）。"""
    if squash:
        (seed / name).write_text(f"{n}\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", f"docs: 指示書 (#{n})")
        return
    git(seed, "checkout", "-q", "-b", f"feat/i{n}")
    (seed / name).write_text(f"{n}\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", f"inner {n}")
    git(seed, "checkout", "-q", "trunk")
    git(seed, "merge", "--no-ff", "-q", f"feat/i{n}", "-m", f"Merge pull request #{n} from o/feat/i{n}")


def push_and_pull(w: dict) -> None:
    git(w["seed"], "push", "-q", "origin", "HEAD:trunk")
    git(w["work"], "pull", "-q", "origin", "trunk")


def test_changed_instructions_are_listed_with_commit_pr_and_diff(monkeypatch, world):
    """受け入れ条件 6・8: 宣言の無いリポジトリでは AGENTS.md・CLAUDE.md の変更を、短い SHA と PR 番号と差分の
    コマンドで並べる。first-parent だけを数え、宣言の既定の外の KIRO.md は並べない。"""
    seed = world["seed"]
    commit_instructions(seed, 30, "CLAUDE.md")
    commit_instructions(seed, 31, "AGENTS.md", squash=True)
    commit_instructions(seed, 32, "KIRO.md")
    push_and_pull(world)
    mod = load(monkeypatch, world)
    obj, code = facts(mod, world)
    out = Path(obj["presentation_path"]).read_text(encoding="utf-8")
    sha = obj["metrics"]["approved_sha"]
    short = git(seed, "log", "-1", "--format=%h", "--grep", "Merge pull request #30")
    inner = git(seed, "log", "-1", "--format=%h", "--grep", "inner 30")
    short31 = git(seed, "log", "-1", "--format=%h", "--grep", "(#31)", "--fixed-strings")
    assert f"- `AGENTS.md`: {short31}（#31）\n  - 差分: `git diff foo--v1.0.0 {sha} -- AGENTS.md`" in out
    assert f"- `CLAUDE.md`: {short}（#30）\n  - 差分: `git diff foo--v1.0.0 {sha} -- CLAUDE.md`" in out
    assert "KIRO.md" not in out and inner not in out and obj["metrics"]["instructions"] == 2


def test_declared_files_decide_the_instructions(monkeypatch, world):
    """受け入れ条件 8 の裏: 宣言があれば宣言の files だけを見る。"""
    seed = world["seed"]
    (seed / ".ndf" / "instructions.json").write_text(json.dumps({"version": 1, "files": ["KIRO.md"]}))
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "decl")
    commit_instructions(seed, 30, "CLAUDE.md")
    commit_instructions(seed, 32, "KIRO.md")
    push_and_pull(world)
    mod = load(monkeypatch, world)
    obj, _ = facts(mod, world)
    out = Path(obj["presentation_path"]).read_text(encoding="utf-8")
    assert "- `KIRO.md`:" in out and "CLAUDE.md" not in out


def test_failing_git_log_writes_nothing(monkeypatch, world):
    """I4: 指示書のログが読めなければ空の節で書かずに 2 で止める。"""
    mod = load(monkeypatch, world)
    real = mod.instructions.proc.git

    def broken(root, *args, **kw):
        if args[:2] == ("log", "--first-parent") and "--" in args:
            return argparse.Namespace(returncode=128, stdout="", stderr="boom")
        return real(root, *args, **kw)

    monkeypatch.setattr(mod.instructions.proc, "git", broken)
    with pytest.raises(mod.StepError) as e:
        facts(mod, world)
    assert e.value.code == 2
    assert not world["present"].exists() or not list(world["present"].iterdir())
