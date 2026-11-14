"""宣言の `workflow.launch` の形（#3473）。雛形の差し込み `review`・`rebuild`・`refactor` の出所で、値は語の並び。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import schema  # noqa: E402
from project_lib import model  # noqa: E402


def test_launch_commands_pass_the_declaration_check():
    launch = {"review": ["ndf", "review"], "rebuild": ["ndf", "rebuild"], "refactor": ["ndf", "refactor"]}
    model.validate_decl({"version": 1, "workflow": {"launch": launch}})


@pytest.mark.parametrize("launch", [{"review": []}, {"rebuild": [""]}, {"review": "ndf review"}, {"other": ["x"]}])
def test_launch_rejects_empty_non_list_and_unknown_keys(launch):
    with pytest.raises(schema.ShapeError):
        model.validate_decl({"version": 1, "workflow": {"launch": launch}})
