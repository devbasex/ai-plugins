"""drive.py の最終スイープの検証コマンド: 不正な `test` 宣言は既定の探し方へ流さず止める（#1483 I13）。
テストの無い宣言は止めずに今の探し方へ進み、件数に最後のラウンドの継続的統合の判定が載る（#1593）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load():
    """同じ名前で 1 度だけ読む（耐久ワークフローの登録を 1 つのモジュールに保つ）。"""
    if "cross_review_drive" in sys.modules:
        return sys.modules["cross_review_drive"]
    spec = importlib.util.spec_from_file_location("cross_review_drive", SCRIPTS / "drive.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cross_review_drive"] = mod
    spec.loader.exec_module(mod)
    return mod


cr = _load()


def _decl(root: Path, decl: dict) -> Path:
    (root / ".ndf").mkdir()
    (root / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    return root


def test_no_declaration_keeps_the_default_search(tmp_path):
    assert cr.sweep_verify_lines(tmp_path) == ""


def test_a_valid_declaration_names_the_commands(tmp_path):
    root = _decl(tmp_path, {"test": {"suites": [{"name": "py", "command": "pytest ."}]}})
    assert "  - pytest ." in cr.sweep_verify_lines(root)


def test_an_invalid_declaration_stops_with_the_key(tmp_path):
    root = _decl(tmp_path, {"test": {"suites": [{"name": "py", "kind": "bogus", "command": "pytest ."}]}})
    with pytest.raises(cr.Stop, match=r"^最終スイープの指示を組めない: .*test\.suites\[0\]\.kind") as e:
        cr.sweep_verify_lines(root)
    assert e.value.code == 2


def test_a_declaration_without_tests_keeps_the_default_search(tmp_path):
    """#1593 I1 — 解析が「テストが無い」と測った宣言では止めず、今の探し方の段落（空）を返す。"""
    assert cr.sweep_verify_lines(_decl(tmp_path, {"test": {"suites": []}})) == ""


def test_a_wrong_kind_without_command_still_stops(tmp_path):
    """#1593 I2 — command の無い suite でも、種別の誤りは終了コード 2 で止める。"""
    with pytest.raises(cr.Stop, match=r"test\.suites\[0\]\.kind") as e:
        cr.sweep_verify_lines(_decl(tmp_path, {"test": {"suites": [{"name": "py", "kind": "unit"}]}}))
    assert e.value.code == 2


UNVERIFIED = {"verdict": "unverified", "reason": "チェックジョブを照会できない（未 push・権限・チェックジョブ 0 件のいずれか）"}


@pytest.mark.parametrize(
    "ci, expected",
    [
        (UNVERIFIED, ("unverified", UNVERIFIED["reason"])),
        ({"verdict": "meta_only", "note": "メタチェックのみ失敗", "pending": []}, ("meta_only", "メタチェックのみ失敗")),
        ({"verdict": "pending", "pending": ["build", "lint"]}, ("pending", "build lint")),
        ({"verdict": "success", "sha": "abc"}, ("success", None)),
        (None, (None, None)),
    ],
)
def test_counts_carry_the_last_round_ci(ci, expected):
    """#1593 I3 — 件数の `ci`・`ci_reason` は最後のラウンドの判定から写す（最初のラウンドは読まない）。"""
    rounds = [{"ci": {"verdict": "code_failure", "failed": ["x"]}}, {"ci": ci} if ci else {}]
    got = cr.ci_counts(rounds)
    assert (got["ci"], got["ci_reason"]) == expected
    assert cr.ci_counts([]) == {"ci": None, "ci_reason": None}


def test_counts_keep_the_existing_keys(monkeypatch):
    """#1593 I4 — 足すのは `ci`・`ci_reason` だけで、`review_status` ほかの値は変えない。"""
    state = {"final": "approved", "sweep": None, "rounds": [{"fix": {"fixed": 1}, "ci": UNVERIFIED}]}
    d = cr.Drive(1, "light", [])
    monkeypatch.setattr(d, "state", lambda: state)
    counts = d.counts()
    assert counts["review_status"] == cr.review_status(state) == "unverified"
    assert (counts["final"], counts["fixed"], counts["rounds"], counts["ci"]) == ("approved", 1, 1, "unverified")
    assert counts["ci_reason"] == UNVERIFIED["reason"]
