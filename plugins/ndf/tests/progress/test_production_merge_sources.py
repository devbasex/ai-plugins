"""マージ先の設定の取得元リポジトリの確かめと本文の判定を、`ndf ci merge`・承認の対象の読み取り・MVV 判定の対象で確かめる（#4409）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import merge_hub
from ndf import ci_cli
from ndf.adapters import github
from ndf.domains.records import mvv_gate
from ndf.progress import gate

_MANUAL = merge_hub.project_text(merge_hub.strategy(deliveries="manual"))
_AUTO = merge_hub.project_text(merge_hub.strategy())

# （PR のマージ先・設定の本文・応答のリポジトリ名（None は同じ）・期待する判定の値・本番へ反映され得るか）
CASES = [
    pytest.param("main", _MANUAL, None, "manual", False, id="same-repo"),
    pytest.param("main", _MANUAL, "O/R", "manual", False, id="same-repo-other-case"),
    pytest.param("develop", _AUTO, None, "not_production", False, id="not-production-base"),
    pytest.param("main", _MANUAL, "other/r", "unknown", True, id="other-repo-manual"),
    pytest.param("develop", _AUTO, "other/r", "unknown", True, id="other-repo-not-production-base"),
    pytest.param("main", None, None, "unknown", True, id="no-file"),
    pytest.param("main", "壊れた", None, "unknown", True, id="broken-text"),
]


def _hub(base: str, text: str | None, file_repo: str | None) -> merge_hub.MergeHub:
    return merge_hub.MergeHub(pulls_of={7: merge_hub.pull(base=base)}, files={base: (text, "c1")}, file_repo=file_repo)


@pytest.mark.parametrize(("base", "text", "file_repo", "verdict", "production"), CASES)
def test_ci_merge_judges_by_the_source_repo_and_the_text(
    monkeypatch, capsys, last_result, base, text, file_repo, verdict, production
) -> None:
    hub = _hub(base, text, file_repo)
    monkeypatch.setattr(
        ci_cli.git, "Git", lambda *_: SimpleNamespace(remote_repo=lambda: merge_hub.REPO, rev_parse=lambda _: merge_hub.HEAD)
    )
    monkeypatch.setattr(ci_cli, "GitHub", lambda *_: hub)
    monkeypatch.setattr(github, "client", lambda **_: None)
    ci_cli.main(["merge", "--branch", "sprint/x"])
    found = last_result(capsys.readouterr().out)
    assert found["metrics"]["verdict"] == verdict
    assert (found["status"], hub.merged) == (("gate", []) if production else ("ok", [7]))


@pytest.mark.parametrize(("base", "text", "file_repo", "verdict", "production"), CASES)
def test_github_observe_reads_production_by_the_source_repo_and_the_text(
    tmp_path: Path, monkeypatch, base, text, file_repo, verdict, production
) -> None:
    from ndf.progress.gate import answering

    # 承認資料の保存先は見ない（資料の無い質問と同じ）
    monkeypatch.setattr(answering, "material_path", lambda root, q: tmp_path / "none.md")
    hub = _hub(base, text, file_repo)
    # 読み取った値は `dataclasses.replace` で対象に重ねるので、質問は実物を渡す
    q = gate.Question("q", "t", "デプロイ", "release-gate", "release", ("approved",), 7)
    seen = gate.github_observe(tmp_path, tmp_path, lambda: hub)(q)
    empty = gate.text_sha256("")  # 偽の PR の本文と題は空（#4446 の本文と題の sha256 も同じ取得で読む）
    assert seen == gate.Target(merge_hub.HEAD, None, merge_hub.REPO, 7, base, production, body=empty, title=empty)


@pytest.mark.parametrize(("base", "text", "file_repo", "verdict", "production"), CASES)
def test_mvv_gate_target_reads_production_by_the_source_repo_and_the_text(base, text, file_repo, verdict, production) -> None:
    hub = _hub(base, text, file_repo)
    got = mvv_gate._target(hub, hub.pulls_of[7])
    assert got == {"repo": merge_hub.REPO, "pr": 7, "base": base, "head": merge_hub.HEAD, "production": production}
