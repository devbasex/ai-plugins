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
        ({"auto": []}, "auto"),
        ({"auto": {"enabled": True, "modes": "light"}}, "auto.modes"),
        ({"triggers": [1, 2]}, "triggers"),
        ({"triggers": "score"}, "triggers"),
        ({"triggers": {"score": -1}}, "triggers"),
    ],
)
def test_a_malformed_declaration_fails_closed(tmp_path, patch, message):
    with pytest.raises(pace.PaceError) as e:
        pace.read_pace(write(tmp_path, {**DECL, **patch}))
    assert message in str(e.value)


def test_the_auto_section_is_read_on_its_own(tmp_path):
    """#1370: auto の節は fast と同じ形で読み、無ければ不許可。fast の値は auto に写さない。"""
    d = pace.read_pace(write(tmp_path, DECL))
    assert d["auto"] == {"enabled": False, "verify": "", "modes": list(pace.DEFAULT_MODES)}
    d = pace.read_pace(write(tmp_path, {**DECL, "auto": {"enabled": True, "verify": "v", "modes": ["light", "operation"]}}))
    assert d["auto"] == {"enabled": True, "verify": "v", "modes": ["light"]} and d["fast"]["verify"] == "true"


def test_a_missing_version_fails_closed(tmp_path):
    decl = {k: v for k, v in DECL.items() if k != "version"}
    with pytest.raises(pace.PaceError, match="version"):
        pace.read_pace(write(tmp_path, decl))


def test_a_null_value_takes_the_default(tmp_path):
    d = pace.read_pace(write(tmp_path, {**DECL, "boundary_paths": None, "triggers": None, "areas": None}))
    assert d["boundary_paths"] == [] and d["areas"] == [] and d["triggers"] == pace.DEFAULT_TRIGGERS


@pytest.mark.parametrize("read", [pace.read_pace, pace.read_loop])
def test_a_missing_or_unreadable_declaration_fails_closed(tmp_path, read):
    """read_pace と read_loop はファイルの読み取りで同じ誤りを返す（現状固定）。"""
    with pytest.raises(pace.PaceError, match="進め方の宣言が無い"):
        read(tmp_path)
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / pace.DECL_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(pace.PaceError, match="進め方の宣言を読めない"):
        read(tmp_path)
    (tmp_path / ".ndf" / pace.DECL_NAME).unlink()
    (tmp_path / ".ndf" / pace.DECL_NAME).mkdir()
    with pytest.raises(pace.PaceError, match="進め方の宣言を読めない"):
        read(tmp_path)


def test_the_loop_section_is_read_with_defaults(tmp_path):
    assert pace.read_loop(write(tmp_path, DECL)) == pace.LOOP_DEFAULTS
    assert pace.read_loop(write(tmp_path, {**DECL, "loop": None})) == pace.LOOP_DEFAULTS
    d = pace.read_loop(write(tmp_path, {**DECL, "loop": {"enabled": True, "budget": {"laps": 5}, "max_issues": 1}}))
    assert d == {"enabled": True, "budget": {"laps": 5, "usd": 60, "hours": 24}, "same_failure": 2, "max_issues": 1}
    assert pace.read_loop(write(tmp_path, {**DECL, "loop": {"enabled": "yes"}}))["enabled"] is False


def test_the_loop_section_ignores_the_rest_of_the_declaration(tmp_path):
    """read_loop は version などを検証せず、最上位がオブジェクトでなければ既定を返す（現状固定）。"""
    assert pace.read_loop(write(tmp_path, {"version": 2, "loop": {"enabled": True}}))["enabled"] is True
    assert pace.read_loop(write(tmp_path, [1, 2])) == pace.LOOP_DEFAULTS


@pytest.mark.parametrize(
    "loop, message",
    [
        ([], "loop は"),
        ({"budget": []}, "loop.budget"),
        ({"budget": {"laps": 0}}, "loop.budget"),
        ({"budget": {"usd": True}}, "loop.budget"),
        ({"budget": {"hours": "1"}}, "loop.budget"),
        ({"budget": {"extra": 1}}, "loop.budget"),
        ({"same_failure": 0}, "loop.same_failure"),
        ({"same_failure": True}, "loop.same_failure"),
        ({"max_issues": 1.5}, "loop.max_issues"),
    ],
)
def test_a_malformed_loop_section_fails_closed(tmp_path, loop, message):
    with pytest.raises(pace.PaceError) as e:
        pace.read_loop(write(tmp_path, {**DECL, "loop": loop}))
    assert message in str(e.value)
