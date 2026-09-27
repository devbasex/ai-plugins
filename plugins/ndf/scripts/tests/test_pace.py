"""pace.py: 進め方の宣言（`.ndf/pace.json`）の形の検証。

形が誤った宣言は PaceError で関門側へ倒す。文字列を `list(...)` で 1 文字ずつのパターンに
してしまうと、mvv-gate.py のレッドライン照合が全件不一致になって fast の承認省略を許す。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import pace  # noqa: E402

DECL = {
    "version": 1,
    "fast": {"enabled": True, "verify": "true"},
    "areas": [{"name": "駆動", "common": True, "paths": ["core/**"]}],
    "boundary_paths": ["lib/auth.py", ".github/workflows/**"],
}


def write(tmp_path, decl) -> Path:
    (tmp_path / ".ndf").mkdir(exist_ok=True)
    (tmp_path / ".ndf" / pace.DECL_NAME).write_text(json.dumps(decl, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_a_well_formed_declaration_is_read_with_defaults(tmp_path):
    d = pace.read_pace(write(tmp_path, DECL))
    assert d["boundary_paths"] == ["lib/auth.py", ".github/workflows/**"]
    assert d["fast"]["modes"] == list(pace.DEFAULT_MODES) and d["triggers"] == pace.DEFAULT_TRIGGERS
    assert pace.matches(".github/workflows/ci.yml", d["boundary_paths"])


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"version": 2}, "version"),
        ({"version": "1"}, "version"),
        ({"version": True}, "version"),
        ({"boundary_paths": "lib/auth.py"}, "boundary_paths"),
        ({"boundary_paths": ["lib/auth.py", 1]}, "boundary_paths"),
        ({"boundary_paths": [""]}, "boundary_paths"),
        ({"areas": [{"name": "駆動", "paths": "core/**"}]}, "areas"),
        ({"areas": [{"name": "駆動", "paths": ["core/**", None]}]}, "areas"),
        ({"fast": {"enabled": True, "modes": "light"}}, "fast.modes"),
        ({"boundary_paths": ""}, "boundary_paths"),
        ({"boundary_paths": False}, "boundary_paths"),
        ({"boundary_paths": 0}, "boundary_paths"),
        ({"boundary_paths": {}}, "boundary_paths"),
        ({"areas": ""}, "areas"),
        ({"areas": {}}, "areas"),
        ({"fast": {"enabled": True, "modes": ""}}, "fast.modes"),
        ({"fast": []}, "fast"),
        ({"triggers": [1, 2]}, "triggers"),
        ({"triggers": "score"}, "triggers"),
        ({"triggers": {"score": -1}}, "triggers"),
    ],
)
def test_a_malformed_declaration_fails_closed(tmp_path, patch, message):
    with pytest.raises(pace.PaceError) as e:
        pace.read_pace(write(tmp_path, {**DECL, **patch}))
    assert message in str(e.value)


def test_a_missing_version_fails_closed(tmp_path):
    decl = {k: v for k, v in DECL.items() if k != "version"}
    with pytest.raises(pace.PaceError, match="version"):
        pace.read_pace(write(tmp_path, decl))


def test_a_null_value_takes_the_default(tmp_path):
    d = pace.read_pace(write(tmp_path, {**DECL, "boundary_paths": None, "triggers": None, "areas": None}))
    assert d["boundary_paths"] == [] and d["areas"] == [] and d["triggers"] == pace.DEFAULT_TRIGGERS
