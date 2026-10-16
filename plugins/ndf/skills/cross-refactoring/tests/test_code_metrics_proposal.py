"""#1319: 提案のプロンプトに指標のファイルを示し、提案の `evidence` を採否に使わずに持ち越す。

- AC16: 指標のファイルがあれば、提案のプロンプトにその絶対パスが載る
- AC18: 無ければ（止めた・書けない・言語が無い）指標に触れない
- AC17: `evidence` の有無と形は、候補の採否と並びを変えない
- 駆動は提案の同期の後、`start-phase propose` の前に `measure` を 1 回打ち、失敗でも止まらない
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

from crossref_helpers import make_state_v2

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
LAUNCH = SCRIPTS / "launch-cli.sh"
RUNTIME = "codex"


@pytest.fixture
def render(refactor, tmp_path):
    vocabulary = sys.modules["refactor_lib.vocabulary"].vocabulary()
    work = tmp_path / "work"
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    (stub_dir / RUNTIME).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (stub_dir / RUNTIME).chmod(0o755)

    def _render(phase: str, record: dict, **overrides) -> str:
        path = make_state_v2(tmp_path, work, runtimes=[RUNTIME], vocabulary=vocabulary, code_metrics=record, **overrides)
        subprocess.run(
            [str(LAUNCH), RUNTIME, phase, "130"],
            env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(path.parent), "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
            check=True,
            capture_output=True,
            text=True,
        )
        return (path.parent / f"{RUNTIME}-{phase}-rf130-prompt.md").read_text(encoding="utf-8")

    return _render, tmp_path


def test_propose_prompt_shows_the_metrics_file(render):
    """AC16: 絶対パスが載り、そのパスが実在する。"""
    _render, tmp_path = render
    target = tmp_path / ".cross_refactoring" / "code-metrics-rf130.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# 指標\n", encoding="utf-8")
    prompt = _render("propose", {"status": "written", "file": str(target)})
    assert f"`{target}`" in prompt and target.is_absolute() and target.is_file()
    assert "$RF_METRICS_BLOCK" not in prompt
    # 提案の手順だけ。改修計画のプロンプトには載せない
    assert str(target) not in _render("plan", {"status": "written", "file": str(target)})


@pytest.mark.parametrize(
    "record",
    [
        {"status": "disabled"},
        {"status": "write_failed", "file": None},
        {"status": "written", "file": "/nonexistent/code-metrics-rf130.md"},
        None,
    ],
)
def test_propose_prompt_without_a_file_does_not_mention_it(render, record):
    """AC18: ファイルが無ければ、指標の節もパスも無い。"""
    _render, _ = render
    prompt = _render("propose", record)
    assert "code-metrics" not in prompt and "evidence" not in prompt
    assert "$RF_METRICS_BLOCK" not in prompt


def test_plan_prompt_carries_evidence_of_candidates(render):
    _render, _ = render
    candidates = [
        {
            "path": "src/a.py",
            "symbol": "Foo",
            "smell": "long_method",
            "technique": "extract_method",
            "severity": "major",
            "rationale": "r",
            "plan": "p",
            "proposed_by": ["codex"],
            "evidence": {"cc": 26, "lines": 101},
        },
        {
            "path": "src/b.py",
            "symbol": "Bar",
            "smell": "long_method",
            "technique": "extract_method",
            "severity": "major",
            "rationale": "r",
            "plan": "p",
            "proposed_by": ["codex"],
        },
    ]
    prompt = _render("plan", None, candidates=candidates)
    assert '"cc": 26' in prompt and prompt.count('"evidence"') == 1


def _proposal(**extra):
    return {
        "path": "src/a.py",
        "symbol": "Foo",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "rationale": "r",
        "plan": "p",
        **extra,
    }


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {"cognitive": 74, "cc": 26, "lines": 101},
        "74",
        [1],
        {"cc": "big", "who": 3, "lines": True},
    ],
)
def test_evidence_never_changes_what_is_adopted(proposals, evidence):
    """AC17 I7: 書いた提案も書かない提案も形の悪い提案も、同じ規則で候補になる。"""
    extra = {} if evidence is None else {"evidence": evidence}
    base = {"codex": [_proposal(), _proposal(symbol="Bar", severity="minor")]}
    given = {"codex": [_proposal(**extra), _proposal(symbol="Bar", severity="minor")]}
    want, want_deferred = proposals.build_candidates(base)
    got, got_deferred = proposals.build_candidates(given)
    strip = lambda items: [{k: v for k, v in i.items() if k != "evidence"} for i in items]  # noqa: E731
    assert strip(got) == strip(want) and len(got_deferred) == len(want_deferred)
    kept = got[0].get("evidence")
    if isinstance(evidence, dict) and "cognitive" in evidence:
        assert kept == evidence
    else:
        assert kept is None


def test_merged_evidence_keeps_existing_keys(proposals):
    """統合では既にある鍵を残し、無い鍵だけ足す。"""
    got, _ = proposals.build_candidates(
        {
            "codex": [_proposal(evidence={"cc": 26})],
            "kiro": [_proposal(evidence={"cc": 99, "lines": 101})],
        }
    )
    assert got[0]["evidence"] == {"cc": 26, "lines": 101}
    assert got[0]["proposed_by"] == ["codex", "kiro"]


def _load_drive():
    spec = importlib.util.spec_from_file_location("cross_refactoring_drive_metrics", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("measure_rc", [0, 1])
def test_drive_measures_once_before_the_proposal(tmp_path, monkeypatch, capsys, measure_rc):
    """AC1 AC9: 同期の後・`start-phase propose` の前に 1 回。測定が落ちても提案を起動する。"""
    drive = _load_drive()
    seen: list[tuple] = []

    def fake(cmd, env=None, cwd=None):
        name = pathlib.Path(cmd[1]).name if cmd[0] in (sys.executable, "bash") else cmd[0]
        seen.append((name, *cmd[2:4]))
        if name != "refactor.py":
            return 0, "abc\n"
        sub = cmd[2]
        if sub == "init":
            return 0, (f"ID=7\nTMP_DIR={tmp_path}\nPHASE=propose\nIMPL=codex\nRUNTIMES=codex\nRUNTIMES_CSV=codex\nWORK={tmp_path}\n")
        if sub == "measure":
            return measure_rc, "CODE_METRICS=written\n"
        if sub in ("merge-proposals", "readopt"):
            return 2, ""
        if sub == "final-gate":
            return 0, "FINAL_GATE=passed\n"
        return 0, ""

    monkeypatch.setattr(drive, "call", fake)
    with pytest.raises(SystemExit):
        drive.main(["7", "--scope", "src", "--baseline-test", "pytest"])
    capsys.readouterr()
    names = [c[:2] for c in seen]
    sync = next(i for i, c in enumerate(seen) if c[:3] == ("prepare-worktrees.sh", "7", "sync"))
    measure = names.index(("refactor.py", "measure"))
    start = seen.index(("refactor.py", "start-phase", "7"))
    assert sync < measure < start
    assert names.count(("refactor.py", "measure")) == 1
    assert ("launch-cli.sh", "codex") in names
