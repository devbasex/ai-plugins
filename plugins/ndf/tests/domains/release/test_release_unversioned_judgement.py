"""バージョン番号を持たないリポジトリかの判定（I-001 の前に今の振る舞いを固定する）。

`records` の `_without_versions`・`release` の `_unversioned_store`・`_record_args` の判定は、どれも
`Strategy.has_version_number` の否定と同じ答えを返し、設定が無い・壊れているリポジトリでは「バージョン番号を持つ」側に倒す。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import strategy_fixture
from strategy_fixture import KINDS

from ndf.core import branch_strategy
from ndf.domains.records import cli as records_cli
from ndf.domains.release import cli as release_cli
from ndf.domains.release import record

from ._fakes import strategy

UNVERSIONED = {"web-trunk", "docs"}


def _release_args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(root=str(root))


def _cli_args(**more: object) -> argparse.Namespace:
    base = {
        "version": None,
        "environment": [],
        "target": [],
        "observed": [],
        "correct_targets": False,
        "reason": None,
        "production_sha": None,
    }
    return argparse.Namespace(**{**base, **more})


@pytest.mark.parametrize("kind", KINDS)
def test_has_version_number_follows_the_version_store_kind(kind: str) -> None:
    assert strategy(kind).has_version_number is (kind not in UNVERSIONED)


@pytest.mark.parametrize("kind", KINDS)
def test_records_and_release_judge_the_same_as_has_version_number(tmp_path: Path, kind: str) -> None:
    strategy_fixture.write(tmp_path, kind)
    expected = not branch_strategy.load(tmp_path).has_version_number
    assert records_cli._without_versions(tmp_path) is expected
    assert release_cli._unversioned_store(_release_args(tmp_path)) is expected


@pytest.mark.parametrize("kind", KINDS)
def test_record_args_accept_the_unversioned_arguments_only_without_version_numbers(kind: str) -> None:
    given = _cli_args(version="main abc1234", environment=["web"])
    if kind in UNVERSIONED:
        assert release_cli._record_args(given, strategy(kind)) is not None
    else:
        with pytest.raises(record.RecordError, match="none でない"):
            release_cli._record_args(given, strategy(kind))


@pytest.mark.parametrize("kind", KINDS)
def test_record_args_accept_correct_targets_only_without_version_numbers(kind: str) -> None:
    given = _cli_args(correct_targets=True, version="main abc1234", target=["web"], reason="r")
    if kind in UNVERSIONED:
        assert release_cli._record_args(given, strategy(kind)) is None
    else:
        with pytest.raises(record.RecordError, match="none のリポジトリでだけ"):
            release_cli._record_args(given, strategy(kind))


def test_a_repository_without_the_setting_is_judged_to_have_version_numbers(tmp_path: Path) -> None:
    assert records_cli._without_versions(tmp_path) is False
    assert release_cli._unversioned_store(_release_args(tmp_path)) is False
    strategy_fixture.write(tmp_path, None)
    assert records_cli._without_versions(tmp_path) is False
    assert release_cli._unversioned_store(_release_args(tmp_path)) is False


def test_a_broken_setting_is_judged_to_have_version_numbers(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text("{壊れた", encoding="utf-8")
    assert records_cli._without_versions(tmp_path) is False
    assert release_cli._unversioned_store(_release_args(tmp_path)) is False
