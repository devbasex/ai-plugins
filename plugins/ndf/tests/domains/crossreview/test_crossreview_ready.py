"""始める前に担当すべてを起動できるかを確かめ、できなければレビューを流さず precondition で止める（#3907）。"""

from __future__ import annotations

from pathlib import Path

from ndf.adapters import training

from .test_crossreview_run import Fake, _run, wt  # noqa: F401 wt は fixture として使う


def test_runtime_that_cannot_launch_stops_before_any_review(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    refused = "学習に使わない設定と確かめられていないので codex を起動しない（確かめられなかった: トークンが無い）"
    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: refused if runtime == "codex" else None)
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})

    code, got = _run(capsys, monkeypatch, fake, wt)

    assert (got["status"], got["cause"]["kind"]) == ("stopped", "precondition")
    assert refused in got["cause"]["detail"]
    assert fake.calls == []
