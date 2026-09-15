"""cross-review の骨組みは 600 秒を超える監視を区切って待つ（#598 / #537 の AC41 / AC42）。

レビューの監視の上限は 1200 秒で、Claude Code の Bash ツールの 1 回（600 秒）に収まらない。
骨組みはレビューの監視（起動し直しを含む）と `critique-round.sh` を `bg-wait.sh run` で
背景に回し、`bg-wait.sh wait`（1 回 540 秒以内）を 124 のあいだ繰り返す。
"""
from __future__ import annotations

import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parents[1]
SKILL = HERE / "SKILL.md"
SCRIPTS = HERE / "scripts"
DOCS = HERE / "docs"


def _skeleton() -> list[str]:
    """`SKILL.md` の収束ループの骨組み（`while :; do` を含む bash ブロック）の行。"""
    blocks = re.findall(r"```bash\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    loops = [b for b in blocks if "while :; do" in b and "state.py\" judge" in b]
    assert len(loops) == 1
    return loops[0].splitlines()


def _code(line: str) -> str:
    return line.split("#", 1)[0] if line.lstrip().startswith("#") else line


LONG_RUNNERS = ('"$SCRIPTS/monitor.py"', '"$SCRIPTS/critique-round.sh"')


def _long_runner_lines() -> list[tuple[int, str]]:
    lines = _skeleton()
    return [(i, line) for i, line in enumerate(lines)
            if not line.lstrip().startswith("#") and any(r in line for r in LONG_RUNNERS)]


def test_the_skeleton_has_the_three_long_runners() -> None:
    """レビューの監視・起動し直しの監視・反証の 1 ラウンド。"""
    found = _long_runner_lines()
    assert len(found) == 3
    assert sum('"$SCRIPTS/monitor.py"' in line for _, line in found) == 2
    assert sum('"$SCRIPTS/critique-round.sh"' in line for _, line in found) == 1


@pytest.mark.parametrize("index", range(3))
def test_each_long_runner_is_started_by_bg_wait_run(index: int) -> None:
    _, line = _long_runner_lines()[index]
    runner = next(r for r in LONG_RUNNERS if r in line)
    head = line.split(runner, 1)[0]
    assert re.search(r'"\$SCRIPTS/bg-wait\.sh" run "[^"]+" -- $', head), line


@pytest.mark.parametrize("index", range(3))
def test_each_run_is_followed_by_a_wait_loop_on_the_same_rc(index: int) -> None:
    lines = _skeleton()
    i, line = _long_runner_lines()[index]
    rc = re.search(r'"\$SCRIPTS/bg-wait\.sh" run ("[^"]+")', line).group(1)
    following = next(l for l in lines[i + 1:] if l.strip() and not l.lstrip().startswith("#"))
    assert re.search(
        rf'while "\$SCRIPTS/bg-wait\.sh" wait {re.escape(rc)}( --max-wait (\d+))?; '
        r'\[ \$\? -eq 124 \]; do :; done', following), following
    max_wait = re.search(r"--max-wait (\d+)", following)
    assert max_wait is None or int(max_wait.group(1)) <= 540


def test_the_review_monitor_passes_the_review_phase() -> None:
    for _, line in _long_runner_lines():
        if '"$SCRIPTS/monitor.py"' in line:
            assert "--phase review" in line, line


def test_critique_round_monitors_with_the_critique_phase() -> None:
    body = (SCRIPTS / "critique-round.sh").read_text(encoding="utf-8")
    call = re.search(r'"\$SCRIPT_DIR/monitor\.py".*?(?<!\\)\n', body, re.DOTALL).group(0)
    assert "--phase critique" in call


def test_the_skeleton_explains_the_separate_bash_calls() -> None:
    """ホストは wait を **別の Bash の呼び出し** として呼び直す。1 回に並べると 600 秒を超える。"""
    text = "\n".join(_skeleton())
    assert "600 秒" in text and "別の Bash" in text


# ---------- AC42 ----------

def test_wait_review_header_states_the_table_values() -> None:
    head = (SCRIPTS / "wait-review.sh").read_text(encoding="utf-8").split("set -euo pipefail")[0]
    assert "1200" in head
    assert "1800s" not in head and "600s" not in head


def test_the_skeleton_monitor_comment_states_the_table_values() -> None:
    lines = _skeleton()
    i, _ = _long_runner_lines()[0]
    comments = "\n".join(l for l in lines[max(0, i - 6):i] if l.lstrip().startswith("#"))
    assert "1200" in comments
    assert "7 分" not in comments and "3 分" not in comments


def test_the_review_output_antipattern_states_the_table_values() -> None:
    body = (DOCS / "03-review-output.md").read_text(encoding="utf-8")
    line = next(l for l in body.splitlines() if "タイムアウトなしで wait" in l)
    assert "1200" in line
    assert "30 分既定" not in line and "10 分既定" not in line


def test_the_procedure_no_longer_says_seven_minutes() -> None:
    body = (DOCS / "01-state-and-review.md").read_text(encoding="utf-8")
    assert "既定 **7 分**" not in body
