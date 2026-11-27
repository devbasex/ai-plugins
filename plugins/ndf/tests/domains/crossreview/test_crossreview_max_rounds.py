"""ラウンドの上限と、1 ラウンド目の範囲（#3780。`test_crossreview_run.py` の行数の上限のため分けた）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.crossreview import cli
from ndf.domains.crossreview import finish_decide as fin

from .test_crossreview_run import Fake, Recording, _run, _state


@pytest.mark.parametrize("purpose", ["code", "design"])
def test_passed_max_rounds_is_the_limit(wt: Path, tmp_path: Path, capsys, monkeypatch, purpose: str) -> None:
    """受け入れ条件 3: `--max-rounds` を渡せば、目的によらずその数で止まる。"""
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"], ["二つ目"], ["三つ目"]], "kiro": [[], [], []]})
    _run(capsys, monkeypatch, fake, wt, "--purpose", purpose, "--max-rounds", "2")
    state = _state(fake.state_dir)
    assert len(state["rounds"]) == 2
    assert state["rounds"][-1]["verdict"] != fin.CONVERGED


@pytest.mark.parametrize("purpose", ["code", "design"])
def test_first_round_reads_the_full_diff(wt: Path, tmp_path: Path, capsys, monkeypatch, purpose: str) -> None:
    """受け入れ条件 4: 1 ラウンド目は、相手役の全員が差分の全体を読む。"""
    fake = Recording(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt, "--purpose", purpose)
    assert _state(fake.state_dir)["rounds"][0]["scope"] == "full"
    first = [prompt for _, name, prompt in fake.prompts if name.startswith("round1-")]
    assert len(first) == 2 and all("範囲: full" in p for p in first)


@pytest.mark.parametrize(
    ("extra", "limit"),
    [
        ((), 4),
        (("--purpose", "code"), 4),
        (("--purpose", "design"), 3),
        (("--purpose", "design", "--max-rounds", "7"), 7),
        (("--max-rounds", "1"), 1),
    ],
)
def test_default_max_rounds_depends_on_the_purpose(tmp_path: Path, monkeypatch, extra: tuple[str, ...], limit: int) -> None:
    """受け入れ条件 3: `--max-rounds` を省けば設計の PR は 3、ほか（`code`・省略時）は 4。渡せばその値が勝つ。"""
    # 一時の場所は git の作業場所でないので、ランタイムの設定は無い扱いにする
    monkeypatch.setattr(cli, "_policy", lambda wt: cli.Policy(wt, None))
    argv = ["run", "--pr", "1", "--state-dir", str(tmp_path / "st"), "--worktree", str(tmp_path), "--branch", "b"]
    argv += ["--base", "origin/main", "--reviewer", "codex", "--reviewer", "kiro", *extra]
    assert cli._run_config(cli._parser().parse_args(argv)).max_rounds == limit
