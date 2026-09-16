"""cross-refactoring の監視の呼び出しは `--phase` を渡し、`--timeout` を渡さない（#598 / #537 の AC37 / AC38）。

監視の上限は上限の表（`lib/limits.py`）が工程で決める。骨組みに秒数を書くと、表だけを
直したときに食い違う。修正（`fix`）と最終ゲートの修正（`final-fix`）は `--timeout` を
持たず、変更前は既定の 420 秒で打ち切られていた。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[1]
REPO = SKILL.parents[3]

# 呼び出しの置き場所と数（AC37）。
EXPECTED_CALLS = {
    "SKILL.md": 4,
    "docs/01-state-and-propose.md": 1,
    "docs/02-apply-and-review.md": 2,
    "docs/04-fix-and-report.md": 1,
}

# 監視の雛形（stem）から、渡すべき工程を決める。
STEM_TO_PHASE = [
    (re.compile(r"\{agent\}-propose-rf"), "propose"),
    (re.compile(r"\{agent\}-apply-r"), "apply"),
    (re.compile(r"\{agent\}-fix-r"), "fix"),
    (re.compile(r"\{agent\}-judge-test-changes-r"), "judge-test-changes"),
    (re.compile(r"\{agent\}-final-fix"), "final-fix"),
]


def _calls(rel: str) -> list[str]:
    """`"$LIB/monitor.py"` から始まり、行末の `\\` で続く 1 つのコマンドを返す。"""
    lines = (SKILL / rel).read_text(encoding="utf-8").splitlines()
    calls = []
    for i, line in enumerate(lines):
        if '"$LIB/monitor.py"' not in line:
            continue
        parts = [line]
        j = i
        while parts[-1].rstrip().endswith("\\"):
            j += 1
            parts.append(lines[j])
        calls.append(" ".join(p.rstrip().rstrip("\\") for p in parts))
    return calls


@pytest.mark.parametrize(("rel", "count"), sorted(EXPECTED_CALLS.items()))
def test_every_call_site_is_found(rel: str, count: int) -> None:
    assert len(_calls(rel)) == count


def _all_calls() -> list[tuple[str, str]]:
    return [(rel, call) for rel in EXPECTED_CALLS for call in _calls(rel)]


@pytest.mark.parametrize(("rel", "call"), _all_calls())
def test_the_call_passes_the_phase_of_its_stem(rel: str, call: str) -> None:
    stem = re.search(r'--stem-template\s+"([^"]+)"', call)
    assert stem, call
    expected = [phase for pat, phase in STEM_TO_PHASE if pat.search(stem.group(1))]
    assert len(expected) == 1, stem.group(1)
    assert re.search(rf"--phase\s+{re.escape(expected[0])}(\s|$)", call), call


@pytest.mark.parametrize(("rel", "call"), _all_calls())
def test_the_call_does_not_pass_a_timeout(rel: str, call: str) -> None:
    assert not re.search(r"--timeout\b", call), call


@pytest.mark.skipif(shutil.which("grep") is None, reason="grep が無い")
def test_the_acceptance_grep_returns_8() -> None:
    """要求の文書の AC38 のコマンドをそのまま実行する。"""
    command = (
        'grep -rn -A2 "monitor.py" plugins/ndf/skills/cross-refactoring/SKILL.md '
        'plugins/ndf/skills/cross-refactoring/docs | grep -c -- "--phase"'
    )
    r = subprocess.run(["bash", "-c", command], cwd=REPO, capture_output=True, text=True)
    assert r.stdout.strip() == "8", r.stdout + r.stderr
